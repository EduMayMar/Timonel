import json
import os
import sys
import threading
import time
from pathlib import Path

import pytest

from timonel.classify import Classification
from timonel.config import Rung
from timonel.engine import (
    Engine,
    RunError,
    RunOptions,
    parse_review,
    parse_subtasks,
    trim,
    waves,
)
from timonel.ledger import Ledger
from timonel.router import build_plan

from .conftest import Fakes, make_config, run_git

CHECK_GOOD = (
    (
        sys.executable,
        "-c",
        "import pathlib,sys; p=pathlib.Path('result.txt'); "
        "sys.exit(0 if p.exists() and p.read_text()=='good' else 1)",
    ),
)
SIMPLE = json.dumps(
    {"complexity": 1, "kind": "edit", "risk": "low", "needs_write": True, "estimated_files": 1}
)


def outcomes(engine: Engine, run_id: str) -> list[tuple[str, str, str]]:
    return [(row["role"], row["agent"], row["outcome"]) for row in engine.ledger.attempts(run_id)]


def executes(engine: Engine, run_id: str) -> list[tuple[str, str]]:
    return [
        (agent, outcome) for role, agent, outcome in outcomes(engine, run_id) if role == "execute"
    ]


STRICT_ESCALATION = "escalation: {answer_without_changes_max_complexity: 0}\n"


def with_env(fakes: Fakes, config_extra: str = "", **values: str) -> Engine:
    store = Ledger()
    legacy = "" if "escalation:" in config_extra else STRICT_ESCALATION
    return Engine(make_config(legacy + config_extra), store, environ={**fakes.env, **values})


def test_read_only_task_runs_in_place(fakes: Fakes, repo: Path) -> None:
    explain = json.dumps(
        {
            "complexity": 2,
            "kind": "explain",
            "risk": "low",
            "needs_write": False,
            "estimated_files": 0,
        }
    )
    engine = with_env(fakes, FAKE_CLASSIFICATION=explain)
    record = engine.run(RunOptions(task="[[say:it prints hello]] explain app.py", repo=repo))
    assert record["status"] == "succeeded"
    assert record["summary"] == "it prints hello"
    assert record["worktree"] is None
    call = [c for c in fakes.calls() if "TIMONEL_CLASSIFY" not in c["prompt"]][-1]
    assert Path(str(call["cwd"])).resolve() == repo.resolve()
    assert call["name"] == "codex"
    assert "read-only" in call["argv"]
    assert run_git(repo, "status", "--porcelain") == ""


def test_write_task_escalates_after_failed_verification(fakes: Fakes, repo: Path) -> None:
    engine = with_env(fakes, FAKE_CLASSIFICATION=SIMPLE)
    task = "[[edit copilot:result.txt=bad]] [[edit claude:result.txt=good]] add result file"
    record = engine.run(RunOptions(task=task, repo=repo, verify=CHECK_GOOD))
    assert record["status"] == "succeeded", record
    assert executes(engine, record["id"]) == [
        ("copilot", "verify_failed"),
        ("codex", "failed"),
        ("claude", "succeeded"),
    ]
    assert record["branch"] == f"timonel/{record['id']}"
    assert (Path(record["worktree"]) / "result.txt").read_text() == "good"
    assert not (repo / "result.txt").exists()
    events = [event["kind"] for event in engine.ledger.events(record["id"])]
    assert "escalate" in events
    prompts = [
        c["prompt"]
        for c in fakes.calls()
        if c["name"] != "copilot" and "TIMONEL" not in c["prompt"]
    ]
    assert any("failed verification" in prompt for prompt in prompts)


def test_escalation_order_and_context(fakes: Fakes, repo: Path) -> None:
    engine = with_env(fakes, FAKE_CLASSIFICATION=SIMPLE)
    task = "[[edit copilot:result.txt=bad]] [[edit codex:result.txt=good]] write result"
    record = engine.run(RunOptions(task=task, repo=repo, verify=CHECK_GOOD))
    assert record["status"] == "succeeded"
    assert executes(engine, record["id"]) == [("copilot", "verify_failed"), ("codex", "succeeded")]


def test_rate_limit_puts_agent_in_cooldown_and_skips(fakes: Fakes, repo: Path) -> None:
    engine = with_env(fakes, FAKE_CLASSIFICATION=SIMPLE, FAKE_MODE_COPILOT="rate_limit")
    record = engine.run(
        RunOptions(task="[[edit all:result.txt=good]] go", repo=repo, verify=CHECK_GOOD)
    )
    assert record["status"] == "succeeded"
    assert executes(engine, record["id"]) == [("copilot", "rate_limited"), ("claude", "succeeded")]
    assert "copilot" in engine.ledger.cooldowns()
    second = engine.run(
        RunOptions(task="[[edit all:result.txt=good]] again", repo=repo, verify=CHECK_GOOD)
    )
    assert executes(engine, second["id"])[0][0] == "claude"


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("garbage", "garbage"),
        ("invalid_json", "garbage"),
        ("partial", "failed"),
        ("crash", "failed"),
        ("error", "failed"),
    ],
)
def test_bad_outputs_fall_through_to_next_agent(
    fakes: Fakes, repo: Path, mode: str, expected: str
) -> None:
    engine = with_env(
        fakes, FAKE_CLASSIFICATION=SIMPLE, FAKE_MODE_COPILOT="success", FAKE_MODE_CLAUDE=mode
    )
    task = "[[edit codex:result.txt=good]] [[edit claude:result.txt=good]] go"
    record = engine.run(
        RunOptions(
            task=task, repo=repo, verify=CHECK_GOOD, force=None, plan=None, profile="balanced"
        )
    )
    assert record["status"] == "succeeded"
    claude = [outcome for agent, outcome in executes(engine, record["id"]) if agent == "claude"]
    assert claude and claude[0] == expected


def test_hang_hits_idle_timeout_and_moves_on(fakes: Fakes, repo: Path) -> None:
    extra = "execution:\n  idle_timeout_s: 2\n  total_timeout_s: 20\n"
    engine = with_env(fakes, extra, FAKE_CLASSIFICATION=SIMPLE, FAKE_MODE_COPILOT="silent_hang")
    started = time.monotonic()
    record = engine.run(
        RunOptions(task="[[edit all:result.txt=good]] go", repo=repo, verify=CHECK_GOOD)
    )
    assert record["status"] == "succeeded"
    assert executes(engine, record["id"])[0] == ("copilot", "idle_timeout")
    assert time.monotonic() - started < 25


def test_total_timeout(fakes: Fakes, repo: Path) -> None:
    extra = "execution:\n  idle_timeout_s: 30\n  total_timeout_s: 2\n"
    engine = with_env(fakes, extra, FAKE_CLASSIFICATION=SIMPLE, FAKE_MODE="hang")
    plan = build_plan(
        engine.config, Classification(1, "edit", "low", True, 1), available={"copilot"}
    )
    record = engine.run(RunOptions(task="go", repo=repo, plan=plan))
    assert record["status"] == "failed"
    assert executes(engine, record["id"]) == [("copilot", "timeout")]


def test_no_changes_counts_as_failure(fakes: Fakes, repo: Path) -> None:
    engine = with_env(fakes, FAKE_CLASSIFICATION=SIMPLE)
    plan = build_plan(
        engine.config, Classification(1, "edit", "low", True, 1), available={"copilot"}
    )
    record = engine.run(RunOptions(task="do nothing", repo=repo, plan=plan))
    assert record["status"] == "failed"
    assert "without changing any file" in record["error"]


def test_classifier_garbage_falls_back_to_heuristic(fakes: Fakes, repo: Path) -> None:
    engine = with_env(fakes, FAKE_CLASSIFICATION="definitely not json")
    record = engine.run(RunOptions(task="[[say:ok]] explain app.py", repo=repo))
    assert record["classification"]["source"] == "heuristic"
    assert record["classification"]["kind"] == "explain"


def test_cross_review_rejection_triggers_one_fix_round(
    fakes: Fakes, repo: Path, tmp_path: Path
) -> None:
    review = json.dumps({"approve": False, "issues": ["missing newline"]})
    classification = json.dumps(
        {"complexity": 3, "kind": "edit", "risk": "low", "needs_write": True, "estimated_files": 1}
    )
    engine = with_env(
        fakes,
        FAKE_CLASSIFICATION=classification,
        FAKE_REVIEW=review,
        FAKE_REVIEW_ONCE=str(tmp_path / "reviewed-once"),
    )
    record = engine.run(
        RunOptions(task="[[edit claude:result.txt=good]] go", repo=repo, verify=CHECK_GOOD)
    )
    assert record["status"] == "succeeded"
    roles = [(role, agent) for role, agent, _ in outcomes(engine, record["id"])]
    assert ("execute", "claude") in roles
    reviewers = [agent for role, agent in roles if role == "review"]
    assert reviewers and "claude" not in reviewers
    assert ("fix", "claude") in roles
    fix_prompt = [c["prompt"] for c in fakes.calls() if "reviewer rejected" in str(c["prompt"])]
    assert fix_prompt and "missing newline" in fix_prompt[0]
    assert len(reviewers) == 2


def test_fixed_change_is_reviewed_again_and_can_still_fail(fakes: Fakes, repo: Path) -> None:
    review = json.dumps({"approve": False, "issues": ["still wrong"]})
    classification = json.dumps(
        {"complexity": 3, "kind": "edit", "risk": "low", "needs_write": True, "estimated_files": 1}
    )
    engine = with_env(fakes, FAKE_CLASSIFICATION=classification, FAKE_REVIEW=review)
    record = engine.run(
        RunOptions(task="[[edit claude:result.txt=good]] go", repo=repo, verify=CHECK_GOOD)
    )
    assert record["status"] == "failed"
    roles = [role for role, _, _ in outcomes(engine, record["id"])]
    assert roles[:5] == ["classify", "execute", "review", "fix", "review"]


def test_cross_review_approval_skips_fix(fakes: Fakes, repo: Path) -> None:
    classification = json.dumps(
        {"complexity": 3, "kind": "edit", "risk": "low", "needs_write": True, "estimated_files": 1}
    )
    engine = with_env(fakes, FAKE_CLASSIFICATION=classification)
    record = engine.run(RunOptions(task="[[edit claude:result.txt=good]] go", repo=repo))
    roles = [role for role, _, _ in outcomes(engine, record["id"])]
    assert "review" in roles and "fix" not in roles
    review = [e for e in engine.ledger.events(record["id"]) if e["kind"] == "review"]
    assert review[0]["data"]["approve"] is True


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_symlink_escaping_repo_is_rejected(fakes: Fakes, repo: Path) -> None:
    engine = with_env(fakes, FAKE_CLASSIFICATION=SIMPLE)
    plan = build_plan(
        engine.config, Classification(1, "edit", "low", True, 1), available={"copilot"}
    )
    record = engine.run(
        RunOptions(
            task="[[link copilot:leak=/etc/passwd]] [[edit copilot:x.txt=1]] go",
            repo=repo,
            plan=plan,
        )
    )
    assert record["status"] == "failed"
    assert "symlink points outside the repository" in record["error"]


def test_apply_merges_and_discard_cleans(fakes: Fakes, repo: Path) -> None:
    engine = with_env(fakes, FAKE_CLASSIFICATION=SIMPLE)
    first = engine.run(
        RunOptions(task="[[edit all:result.txt=good]] go", repo=repo, verify=CHECK_GOOD)
    )
    assert "result.txt" in engine.diff(first["id"])
    applied = engine.apply(first["id"][:6])
    assert applied["status"] == "applied"
    assert (repo / "result.txt").read_text() == "good"
    assert not Path(first["worktree"]).exists()
    assert engine.diff(first["id"]) == ""
    with pytest.raises(RunError):
        engine.apply(first["id"])
    with pytest.raises(RunError):
        engine.discard(first["id"])
    second = engine.run(RunOptions(task="[[edit all:other.txt=x]] go", repo=repo))
    discarded = engine.discard(second["id"])
    assert discarded["status"] == "discarded"
    assert "timonel/" + second["id"] not in run_git(repo, "branch")
    with pytest.raises(RunError):
        engine.require("nope")


def test_apply_refuses_dirty_repo(fakes: Fakes, repo: Path) -> None:
    engine = with_env(fakes, FAKE_CLASSIFICATION=SIMPLE)
    record = engine.run(RunOptions(task="[[edit all:result.txt=good]] go", repo=repo))
    (repo / "app.py").write_text("changed\n", encoding="utf-8")
    from timonel.gitops import GitError

    with pytest.raises(GitError, match="uncommitted"):
        engine.apply(record["id"])


def test_secrets_are_redacted_and_scoped(fakes: Fakes, repo: Path) -> None:
    engine = with_env(fakes, FAKE_CLASSIFICATION=SIMPLE)
    secret = fakes.env["DEEPSEEK_API_KEY"]
    plan = build_plan(
        engine.config, Classification(1, "explain", "low", False, 0), available={"deepseek"}
    )
    record = engine.run(RunOptions(task=f"[[say:key is {secret}]] explain", repo=repo, plan=plan))
    assert secret not in json.dumps(record)
    assert "[REDACTED]" in record["summary"]
    for row in engine.ledger.attempts(record["id"]):
        assert secret not in (row["output"] or "")
    for event in engine.ledger.events(record["id"]):
        assert secret not in json.dumps(event)
    deepseek_calls = [c for c in fakes.calls() if c["name"] == "claude"]
    assert deepseek_calls[-1]["env"]["ANTHROPIC_AUTH_TOKEN"] == secret
    assert deepseek_calls[-1]["env"]["ANTHROPIC_BASE_URL"] == "https://api.deepseek.com/anthropic"


def test_other_agents_never_see_deepseek_key(fakes: Fakes, repo: Path) -> None:
    engine = with_env(fakes, FAKE_CLASSIFICATION=SIMPLE)
    plan = build_plan(
        engine.config, Classification(1, "explain", "low", False, 0), available={"claude"}
    )
    engine.run(RunOptions(task="[[say:ok]] explain", repo=repo, plan=plan))
    call = [c for c in fakes.calls() if "TIMONEL" not in c["prompt"]][-1]
    assert call["env"] == {}


def test_missing_secret_is_launch_error(fakes: Fakes, repo: Path) -> None:
    env = dict(fakes.env)
    env.pop("DEEPSEEK_API_KEY")
    engine = Engine(make_config(), Ledger(), environ=env)
    plan = build_plan(
        engine.config, Classification(1, "explain", "low", False, 0), available={"deepseek"}
    )
    record = engine.run(RunOptions(task="explain", repo=repo, plan=plan))
    assert record["status"] == "failed"
    assert {outcome for _, outcome in executes(engine, record["id"])} == {"launch_error"}


def test_cancel_stops_running_agent(fakes: Fakes, repo: Path) -> None:
    engine = with_env(fakes, FAKE_CLASSIFICATION=SIMPLE, FAKE_MODE="silent_hang")
    plan = build_plan(
        engine.config, Classification(1, "edit", "low", True, 1), available={"copilot"}
    )
    options = RunOptions(task="go", repo=repo, plan=plan)
    run_id = engine.start(options)
    threading.Timer(1.5, lambda: engine.cancel(run_id)).start()
    record = engine.run(options, run_id)
    assert record["status"] == "cancelled"
    assert not engine.cancel("unknown")


def test_resume_continues_after_interrupt(fakes: Fakes, repo: Path) -> None:
    engine = with_env(fakes, FAKE_CLASSIFICATION=SIMPLE)
    task = "[[edit copilot:result.txt=bad]] [[edit claude:result.txt=good]] go"
    plan = build_plan(engine.config, Classification(1, "edit", "low", True, 1))
    options = RunOptions(task=task, repo=repo, plan=plan, verify=CHECK_GOOD)
    run_id = engine.start(options)
    engine.ledger.update_run(run_id, plan=plan.to_dict(), status="interrupted", write=True)
    first = engine.ledger.start_attempt(
        run_id,
        role="execute",
        tier=1,
        kind="edit",
        agent="copilot",
        model="auto",
        effort="low",
        billing="subscription",
    )
    engine.ledger.finish_attempt(first, outcome="failed")
    resumed = engine.resume(run_id)
    assert resumed["status"] == "succeeded", resumed
    assert [agent for agent, _ in executes(engine, run_id)] == ["copilot", "claude"]
    with pytest.raises(RunError, match="only interrupted"):
        engine.resume(run_id)


def test_resume_index_rules(fakes: Fakes, repo: Path) -> None:
    engine = with_env(fakes)
    plan = build_plan(engine.config, Classification(1, "edit", "low", True, 1))
    engine.ledger.create_run(
        "r1", task="t", repo=str(repo), profile="balanced", status="interrupted"
    )
    assert engine.resume_index("r1", plan) == 0
    steps = plan.steps()

    def attempt(step_index: int, outcome: str) -> None:
        step = steps[step_index]
        ident = engine.ledger.start_attempt(
            "r1",
            role="execute",
            tier=step.tier,
            kind="edit",
            agent=step.agent,
            model=step.model,
            effort="",
            billing=step.billing,
        )
        engine.ledger.finish_attempt(ident, outcome=outcome)

    attempt(0, "running")
    assert engine.resume_index("r1", plan) == 0
    attempt(0, "failed")
    assert engine.resume_index("r1", plan) == 1
    attempt(1, "verify_failed")
    assert steps[engine.resume_index("r1", plan)].tier == 2


def test_race_keeps_smaller_passing_diff(fakes: Fakes, repo: Path) -> None:
    engine = with_env(fakes, FAKE_CLASSIFICATION=SIMPLE)
    big = "good" + "\\n" * 0
    task = (
        "[[edit copilot:result.txt=good]] [[edit copilot:extra.txt=lots of extra text]] "
        f"[[edit claude:result.txt={big}]] go"
    )
    record = engine.run(RunOptions(task=task, repo=repo, verify=CHECK_GOOD, race=True))
    assert record["status"] == "succeeded", record
    children = engine.ledger.child_runs(record["id"])
    assert len(children) == 2
    winner = next(e for e in engine.ledger.events(record["id"]) if e["kind"] == "race.winner")
    assert winner["data"]["diff_size"] == 1
    statuses = sorted(child["status"] for child in children)
    assert statuses == ["discarded", "succeeded"]
    engine.apply(record["id"])
    assert (repo / "result.txt").read_text() == "good"
    assert not (repo / "extra.txt").exists()


def test_race_with_single_agent_falls_back(fakes: Fakes, repo: Path) -> None:
    engine = with_env(fakes, FAKE_CLASSIFICATION=SIMPLE)
    plan = build_plan(
        engine.config, Classification(1, "edit", "low", True, 1), available={"copilot"}
    )
    record = engine.run(
        RunOptions(task="[[edit all:result.txt=good]] go", repo=repo, plan=plan, race=True)
    )
    assert record["status"] == "succeeded"
    kinds = [event["kind"] for event in engine.ledger.events(record["id"])]
    assert "race.skipped" in kinds


def test_decomposition_runs_waves_and_merges(fakes: Fakes, repo: Path) -> None:
    subtasks = {
        "subtasks": [
            {"id": "a", "task": "[[edit all:a.txt=A]] make a", "depends_on": []},
            {"id": "b", "task": "[[edit all:b.txt=B]] make b", "depends_on": []},
            {"id": "c", "task": "[[edit all:c.txt=C]] make c", "depends_on": ["a", "b"]},
        ]
    }
    engine = with_env(fakes, FAKE_CLASSIFICATION=SIMPLE, FAKE_PLAN=json.dumps(subtasks))
    record = engine.run(RunOptions(task="build three files", repo=repo, decompose=True))
    assert record["status"] == "succeeded", record
    worktree = Path(record["worktree"])
    assert {(worktree / name).read_text() for name in ("a.txt", "b.txt", "c.txt")} == {
        "A",
        "B",
        "C",
    }
    plan_event = next(
        e for e in engine.ledger.events(record["id"]) if e["kind"] == "decompose.plan"
    )
    assert plan_event["data"]["waves"] == [["a", "b"], ["c"]]
    assert all(child["status"] == "applied" for child in engine.ledger.child_runs(record["id"]))
    engine.apply(record["id"])
    assert (repo / "c.txt").read_text() == "C"


def test_decomposition_failure_skips_dependents(fakes: Fakes, repo: Path) -> None:
    subtasks = {
        "subtasks": [
            {"id": "a", "task": "[[mode all:crash]] make a", "depends_on": []},
            {"id": "b", "task": "[[edit all:b.txt=B]] make b", "depends_on": ["a"]},
        ]
    }
    engine = with_env(fakes, FAKE_CLASSIFICATION=SIMPLE, FAKE_PLAN=json.dumps(subtasks))
    record = engine.run(RunOptions(task="two steps", repo=repo, decompose=True))
    assert record["status"] == "failed"
    assert "a, b" in record["error"]


def test_invalid_planner_output_falls_back_to_single_run(fakes: Fakes, repo: Path) -> None:
    engine = with_env(fakes, FAKE_CLASSIFICATION=SIMPLE, FAKE_PLAN="nonsense")
    record = engine.run(
        RunOptions(task="[[edit all:result.txt=good]] go", repo=repo, decompose=True)
    )
    assert record["status"] == "succeeded"
    kinds = [event["kind"] for event in engine.ledger.events(record["id"])]
    assert "decompose.skipped" in kinds


def test_forced_route(fakes: Fakes, repo: Path) -> None:
    engine = with_env(fakes)
    record = engine.run(RunOptions(task="[[say:hi]] explain", repo=repo, force=Rung("codex", "o3")))
    assert record["status"] == "succeeded"
    assert executes(engine, record["id"]) == [("codex", "succeeded")]
    call = [c for c in fakes.calls() if c["name"] == "codex"][-1]
    assert "o3" in call["argv"]


def test_not_a_repo_fails_cleanly(engine: Engine, tmp_path: Path) -> None:
    from timonel.gitops import GitError

    with pytest.raises(GitError):
        engine.run(RunOptions(task="x", repo=tmp_path))


def test_agy_runs_through_pty(fakes: Fakes, repo: Path) -> None:
    if os.name == "nt":
        pytest.importorskip("winpty")
    engine = with_env(fakes, FAKE_MODE_AGY="need_tty")
    plan = build_plan(
        engine.config, Classification(3, "explain", "low", False, 0), available={"agy"}
    )
    record = engine.run(RunOptions(task="[[say:from a tty]] explain", repo=repo, plan=plan))
    assert record["status"] == "succeeded"
    assert record["summary"] == "from a tty"


def test_helpers() -> None:
    assert trim("abc", 10) == "abc"
    assert trim("x" * 20, 5).endswith("xxxxx") and "trimmed" in trim("x" * 20, 5)
    assert parse_review('{"approve": false, "issues": ["a"]}') == (False, ["a"])
    assert parse_review('{"approve": "no"}') is None
    assert parse_review('{"approve": true, "issues": "x"}') is None
    good = '{"subtasks": [{"id": "a", "task": "x"}, {"id": "b", "task": "y", "depends_on": ["a"]}]}'
    assert [item["id"] for item in parse_subtasks(good, 6) or []] == ["a", "b"]
    cyclic = '{"subtasks": [{"id": "a", "task": "x", "depends_on": ["b"]}, {"id": "b", "task": "y", "depends_on": ["a"]}]}'
    assert parse_subtasks(cyclic, 6) is None
    assert parse_subtasks('{"subtasks": [{"id": "a", "task": "x"}]}', 6) is None
    unknown = (
        '{"subtasks": [{"id": "a", "task": "x", "depends_on": ["z"]}, {"id": "b", "task": "y"}]}'
    )
    assert parse_subtasks(unknown, 6) is None
    duplicate = '{"subtasks": [{"id": "a", "task": "x"}, {"id": "a", "task": "y"}]}'
    assert parse_subtasks(duplicate, 6) is None
    assert waves([{"id": "a", "depends_on": ["a"]}]) is None


def one_rung(engine: Engine, agent: str, write: bool = True, complexity: int = 1) -> object:
    kind = "edit" if write else "explain"
    return build_plan(
        engine.config, Classification(complexity, kind, "low", write, 1), available={agent}
    )


def test_unexpected_exception_marks_run_failed(
    fakes: Fakes, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from timonel import gitops

    engine = with_env(fakes, FAKE_CLASSIFICATION=SIMPLE)

    def boom(*args: object, **kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(gitops, "create_worktree", boom)
    record = engine.run(RunOptions(task="go", repo=repo, plan=one_rung(engine, "copilot")))  # type: ignore[arg-type]
    assert record["status"] == "failed"
    assert "disk full" in record["error"]


def test_failure_mentioning_429_is_not_a_rate_limit(fakes: Fakes, repo: Path) -> None:
    engine = with_env(fakes, FAKE_MODE="noisy_failure")
    record = engine.run(RunOptions(task="go", repo=repo, plan=one_rung(engine, "copilot")))  # type: ignore[arg-type]
    assert executes(engine, record["id"])[0] == ("copilot", "failed")
    assert engine.ledger.cooldowns() == {}


def test_usage_limit_in_json_result_triggers_cooldown(fakes: Fakes, repo: Path) -> None:
    engine = with_env(fakes, FAKE_MODE="limit_json")
    plan = one_rung(engine, "claude", write=False)
    engine.run(RunOptions(task="explain", repo=repo, plan=plan))  # type: ignore[arg-type]
    assert "claude" in engine.ledger.cooldowns()


def test_cooling_agent_is_skipped_inside_the_ladder(fakes: Fakes, repo: Path) -> None:
    engine = with_env(fakes, FAKE_MODE_CLAUDE="rate_limit")
    plan = build_plan(
        engine.config,
        Classification(1, "explain", "low", False, 0),
        available={"claude", "codex"},
    )
    assert [step.agent for step in plan.steps()].count("claude") >= 2
    record = engine.run(RunOptions(task="[[say:ok]] explain", repo=repo, plan=plan))
    assert [agent for agent, _ in executes(engine, record["id"])].count("claude") == 1


def test_cancel_during_verification_is_cancelled(fakes: Fakes, repo: Path) -> None:
    engine = with_env(fakes)
    slow = ((sys.executable, "-c", "print('verifying', flush=True); import time; time.sleep(60)"),)
    options = RunOptions(
        task="[[edit all:a.txt=1]] go",
        repo=repo,
        plan=one_rung(engine, "copilot"),
        verify=slow,  # type: ignore[arg-type]
    )
    run_id = engine.start(options)

    def watch(identifier: str, kind: str, data: dict[str, object]) -> None:
        if kind == "verify.started":
            threading.Timer(0.5, lambda: engine.cancel(identifier)).start()

    engine.on_event = watch
    record = engine.run(options, run_id)
    assert record["status"] == "cancelled"
    assert executes(engine, run_id) == [("copilot", "succeeded")]


def test_cancel_during_review_is_cancelled(fakes: Fakes, repo: Path) -> None:
    engine = with_env(fakes, FAKE_MODE_CODEX="silent_hang")
    plan = build_plan(
        engine.config, Classification(3, "edit", "low", True, 1), available={"claude", "codex"}
    )
    options = RunOptions(task="[[edit claude:a.txt=1]] go", repo=repo, plan=plan)
    run_id = engine.start(options)

    def watch(identifier: str, kind: str, data: dict[str, object]) -> None:
        if kind == "attempt.started" and data["role"] == "review":
            threading.Timer(0.5, lambda: engine.cancel(identifier)).start()

    engine.on_event = watch
    record = engine.run(options, run_id)
    assert record["status"] == "cancelled"


def test_verification_gets_a_minimal_environment(fakes: Fakes, repo: Path, tmp_path: Path) -> None:
    dump = tmp_path / "env.json"
    script = f"import json, os; open(r'{dump}', 'w').write(json.dumps(dict(os.environ)))"
    engine = with_env(
        fakes, AWS_SECRET_ACCESS_KEY="aws-secret-value-123", OTHER_TOKEN="tok-123456789"
    )
    engine.run(
        RunOptions(
            task="[[edit all:a.txt=1]] go",
            repo=repo,
            plan=one_rung(engine, "copilot"),  # type: ignore[arg-type]
            verify=((sys.executable, "-c", script),),
        )
    )
    seen = json.loads(dump.read_text())
    assert "PATH" in seen
    for name in ("AWS_SECRET_ACCESS_KEY", "OTHER_TOKEN", "DEEPSEEK_API_KEY", "GH_TOKEN"):
        assert name not in seen


def test_nested_event_data_is_redacted(fakes: Fakes, repo: Path) -> None:
    secret = fakes.env["DEEPSEEK_API_KEY"]
    review = json.dumps({"approve": True, "issues": [f"key {secret} leaked"]})
    classification = json.dumps(
        {"complexity": 3, "kind": "edit", "risk": "low", "needs_write": True, "estimated_files": 1}
    )
    engine = with_env(fakes, FAKE_CLASSIFICATION=classification, FAKE_REVIEW=review)
    record = engine.run(RunOptions(task="[[edit claude:a.txt=1]] go", repo=repo))
    events = json.dumps(engine.ledger.events(record["id"]))
    assert secret not in events
    assert "[REDACTED] leaked" in events


def test_resolved_secrets_are_added_to_the_redactor(
    fakes: Fakes, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import timonel.adapters as adapters

    env = dict(fakes.env)
    env.pop("DEEPSEEK_API_KEY")
    monkeypatch.setattr(adapters, "lookup_secret", lambda name, environ: "keyring-secret-987654")
    engine = Engine(make_config(), Ledger(), environ=env)
    plan = one_rung(engine, "deepseek", write=False)
    record = engine.run(
        RunOptions(task="[[say:keyring-secret-987654]] explain", repo=repo, plan=plan)
    )  # type: ignore[arg-type]
    assert record["summary"] == "[REDACTED]"


def test_oversized_argument_prompt_is_a_launch_error(fakes: Fakes, repo: Path) -> None:
    engine = with_env(fakes)
    plan = one_rung(engine, "copilot", write=False)
    record = engine.run(RunOptions(task="x" * 200_000, repo=repo, plan=plan))  # type: ignore[arg-type]
    assert {outcome for _, outcome in executes(engine, record["id"])} == {"launch_error"}
    assert "prompt_via: stdin" in engine.ledger.attempts(record["id"])[0]["error"]


def test_decomposed_read_only_subtask_still_runs_in_a_worktree(fakes: Fakes, repo: Path) -> None:
    subtasks = {
        "subtasks": [
            {
                "id": "a",
                "task": "[[edit all:a.txt=A]] explain and document the parser",
                "depends_on": [],
            },
            {"id": "b", "task": "[[edit all:b.txt=B]] review the cache", "depends_on": []},
        ]
    }
    engine = with_env(fakes, FAKE_PLAN=json.dumps(subtasks), FAKE_CLASSIFICATION=SIMPLE)
    record = engine.run(RunOptions(task="split me", repo=repo, decompose=True))
    assert record["status"] == "succeeded", record
    assert not (repo / "a.txt").exists()
    assert all(child["write"] for child in engine.ledger.child_runs(record["id"]))


def test_cancelled_race_is_cancelled(fakes: Fakes, repo: Path) -> None:
    engine = with_env(fakes, FAKE_CLASSIFICATION=SIMPLE, FAKE_MODE="silent_hang")
    options = RunOptions(task="go", repo=repo, race=True)
    run_id = engine.start(options)

    def watch(identifier: str, kind: str, data: dict[str, object]) -> None:
        if kind == "race.started":
            threading.Timer(1.0, lambda: engine.cancel(run_id)).start()

    engine.on_event = watch
    record = engine.run(options, run_id)
    assert record["status"] == "cancelled"
    assert {child["status"] for child in engine.ledger.child_runs(run_id)} <= {
        "cancelled",
        "discarded",
    }


def test_resume_uses_stored_verification_and_refuses_parents(fakes: Fakes, repo: Path) -> None:
    engine = with_env(fakes)
    plan = build_plan(engine.config, Classification(1, "edit", "low", True, 1))
    options = RunOptions(
        task="[[edit copilot:result.txt=bad]] [[edit claude:result.txt=good]] go",
        repo=repo,
        plan=plan,
        verify=CHECK_GOOD,
    )
    run_id = engine.start(options)
    engine.ledger.update_run(
        run_id,
        plan={**plan.to_dict(), "verify_commands": [list(CHECK_GOOD[0])]},
        status="interrupted",
        write=True,
    )
    resumed = engine.resume(run_id)
    assert resumed["status"] == "succeeded"
    assert executes(engine, run_id)[0] == ("copilot", "verify_failed")
    engine.ledger.create_run(
        "kid00000", parent_id=run_id, task="t", repo=str(repo), profile="balanced", status="failed"
    )
    engine.ledger.update_run(run_id, status="interrupted")
    with pytest.raises(RunError, match="start it again"):
        engine.resume(run_id)


def test_tampered_worktree_pointer_is_refused(fakes: Fakes, repo: Path) -> None:
    from timonel import gitops
    from timonel.gitops import GitError

    engine = with_env(fakes)
    record = engine.run(
        RunOptions(task="[[edit all:a.txt=1]] go", repo=repo, plan=one_rung(engine, "copilot"))  # type: ignore[arg-type]
    )
    worktree = Path(record["worktree"])
    (worktree / "evil").mkdir()
    (worktree / ".git").unlink()
    (worktree / ".git").write_text(f"gitdir: {worktree / 'evil'}\n", encoding="utf-8")
    with pytest.raises(GitError, match="tampered"):
        gitops.git(worktree, "status")
    (worktree / ".git").unlink()
    with pytest.raises(GitError, match="missing or replaced"):
        gitops.git(worktree, "status")


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_symlink_into_dot_git_or_relative_escape_is_rejected(fakes: Fakes, repo: Path) -> None:
    engine = with_env(fakes)
    for target in ("sub/../../outside", ".git/config"):
        record = engine.run(
            RunOptions(
                task=f"[[link copilot:leak={target}]] [[edit copilot:x.txt=1]] go",
                repo=repo,
                plan=one_rung(engine, "copilot"),  # type: ignore[arg-type]
            )
        )
        assert record["status"] == "failed", target
        assert "symlink points outside" in record["error"]


def test_next_ladder_start() -> None:
    from timonel.engine import next_ladder_start

    plan = build_plan(make_config(), Classification(1, "edit", "low", True, 1))
    sizes = [len(ladder) for ladder in plan.ladders]
    assert next_ladder_start(plan, 0) == sizes[0]
    assert next_ladder_start(plan, sizes[0]) == sizes[0] + sizes[1]
    assert next_ladder_start(plan, 999) == sum(sizes)
