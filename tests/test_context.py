import json
import time
from pathlib import Path

from timonel.config import ContextSettings
from timonel.engine import Engine, RunOptions, history_text
from timonel.ledger import Ledger

from .conftest import Fakes, make_config, run_git

SIMPLE = json.dumps(
    {"complexity": 1, "kind": "edit", "risk": "low", "needs_write": True, "estimated_files": 1}
)
FIRST = "add a greeting [[edit all:greet.py=print('hi')]]"
SECOND = "now add a test [[edit all:test_greet.py=assert True]]"


def engine_for(fakes: Fakes, config_extra: str = "") -> Engine:
    return Engine(
        make_config(config_extra), Ledger(), environ={**fakes.env, "FAKE_CLASSIFICATION": SIMPLE}
    )


def agent_prompts(fakes: Fakes) -> list[str]:
    return [
        str(call["prompt"])
        for call in fakes.calls()
        if "TIMONEL_CLASSIFY" not in str(call["prompt"])
    ]


def test_follow_up_sees_the_earlier_task_and_its_unmerged_changes(fakes: Fakes, repo: Path) -> None:
    engine = engine_for(fakes)
    since = time.time() - 1
    first = engine.run(RunOptions(task=FIRST, repo=repo, since=since))
    second = engine.run(RunOptions(task=SECOND, repo=repo, since=since))
    assert first["status"] == second["status"] == "succeeded"
    prompt = agent_prompts(fakes)[-1]
    assert "Earlier work in this repository" in prompt
    assert "add a greeting" in prompt
    assert f"branch {first['branch']} and not merged yet" in prompt
    assert prompt.index("Earlier work") < prompt.index("Task:\nnow add a test")
    assert (Path(second["worktree"]) / "greet.py").exists()
    assert run_git(repo, "rev-parse", first["branch"]).strip() == second["base_ref"]


def test_first_task_of_a_session_gets_no_context(fakes: Fakes, repo: Path) -> None:
    engine = engine_for(fakes)
    engine.run(RunOptions(task=FIRST, repo=repo, since=time.time() - 1))
    assert "Earlier work" not in agent_prompts(fakes)[-1]


def test_without_a_session_start_nothing_is_shared(fakes: Fakes, repo: Path) -> None:
    engine = engine_for(fakes)
    engine.run(RunOptions(task=FIRST, repo=repo))
    second = engine.run(RunOptions(task=SECOND, repo=repo))
    assert "Earlier work" not in agent_prompts(fakes)[-1]
    assert not (Path(second["worktree"]) / "greet.py").exists()


def test_continue_flag_shares_context_across_the_repository(fakes: Fakes, repo: Path) -> None:
    engine = engine_for(fakes)
    engine.run(RunOptions(task=FIRST, repo=repo))
    second = engine.run(RunOptions(task=SECOND, repo=repo, carry=True))
    assert "add a greeting" in agent_prompts(fakes)[-1]
    assert (Path(second["worktree"]) / "greet.py").exists()


def test_no_context_flag_wins_over_the_repo_scope(fakes: Fakes, repo: Path) -> None:
    engine = engine_for(fakes, "context: {scope: repo}\n")
    engine.run(RunOptions(task=FIRST, repo=repo))
    engine.run(RunOptions(task=SECOND, repo=repo, carry=False))
    assert "Earlier work" not in agent_prompts(fakes)[-1]


def test_repo_scope_shares_without_any_flag(fakes: Fakes, repo: Path) -> None:
    engine = engine_for(fakes, "context: {scope: repo}\n")
    engine.run(RunOptions(task=FIRST, repo=repo))
    engine.run(RunOptions(task=SECOND, repo=repo))
    assert "add a greeting" in agent_prompts(fakes)[-1]


def test_scope_off_disables_context_even_in_a_session(fakes: Fakes, repo: Path) -> None:
    engine = engine_for(fakes, "context: {scope: 'off'}\n")
    since = time.time() - 1
    engine.run(RunOptions(task=FIRST, repo=repo, since=since))
    engine.run(RunOptions(task=SECOND, repo=repo, since=since))
    assert "Earlier work" not in agent_prompts(fakes)[-1]


def test_stack_false_tells_the_agent_but_keeps_the_base_branch(fakes: Fakes, repo: Path) -> None:
    engine = engine_for(fakes, "context: {stack: false}\n")
    since = time.time() - 1
    first = engine.run(RunOptions(task=FIRST, repo=repo, since=since))
    second = engine.run(RunOptions(task=SECOND, repo=repo, since=since))
    assert "add a greeting" in agent_prompts(fakes)[-1]
    assert second["base_ref"] == run_git(repo, "rev-parse", "HEAD").strip()
    assert second["base_ref"] != run_git(repo, "rev-parse", first["branch"]).strip()


def test_merged_work_is_not_stacked_again(fakes: Fakes, repo: Path) -> None:
    engine = engine_for(fakes)
    since = time.time() - 1
    first = engine.run(RunOptions(task=FIRST, repo=repo, since=since))
    engine.apply(first["id"])
    second = engine.run(RunOptions(task=SECOND, repo=repo, since=since))
    assert "succeeded and merged into the working branch" in agent_prompts(fakes)[-1]
    assert second["base_ref"] == run_git(repo, "rev-parse", "HEAD").strip()


def test_discarded_runs_are_not_context(fakes: Fakes, repo: Path) -> None:
    engine = engine_for(fakes)
    since = time.time() - 1
    first = engine.run(RunOptions(task=FIRST, repo=repo, since=since))
    engine.discard(first["id"])
    engine.run(RunOptions(task=SECOND, repo=repo, since=since))
    assert "Earlier work" not in agent_prompts(fakes)[-1]


def test_other_repositories_are_never_shared(fakes: Fakes, repo: Path, tmp_path: Path) -> None:
    other = tmp_path / "other"
    other.mkdir()
    for args in (("init", "-q"), ("commit", "-q", "--allow-empty", "-m", "base")):
        run_git(other, *args)
    engine = engine_for(fakes, "context: {scope: repo}\n")
    engine.run(RunOptions(task=FIRST, repo=other))
    engine.run(RunOptions(task=SECOND, repo=repo))
    assert "Earlier work" not in agent_prompts(fakes)[-1]


def row(task: str, **extra: object) -> dict[str, object]:
    return {"task": task, "status": "succeeded", "write": 0, **extra}


def test_history_text_keeps_the_newest_tasks_within_the_budget() -> None:
    rows = [row(f"task number {number} " + "x" * 200) for number in range(10)]
    text = history_text(rows, ContextSettings(max_chars=700))
    assert "task number 9" in text
    assert "task number 0" not in text
    assert text.index("task number 8") < text.index("task number 9")


def test_history_text_respects_include_and_summary_limit() -> None:
    rows = [row("fix it", summary="y" * 500, diff_stat="2 files, +3 -1")]
    only_task = history_text(rows, ContextSettings(include=("task",)))
    assert "fix it" in only_task and "Outcome" not in only_task and "Result" not in only_task
    shortened = history_text(rows, ContextSettings(summary_chars=100))
    assert "y" * 100 in shortened and "y" * 200 not in shortened
    assert "Changes: 2 files, +3 -1" in shortened


def test_failed_runs_show_their_reason() -> None:
    text = history_text([row("break", status="failed", error="tests failed")], ContextSettings())
    assert "Outcome: failed: tests failed" in text


def test_nothing_to_say_gives_an_empty_block() -> None:
    assert history_text([], ContextSettings()) == ""


def test_greeting_is_answered_read_only() -> None:
    from timonel.classify import heuristic

    for text in ("Hola", "hola, gracias!", "Hello"):
        guess = heuristic(text)
        assert guess.kind == "explain" and not guess.needs_write
    assert heuristic("hola, añade un botón").kind == "edit"
    assert heuristic("añade un botón").needs_write


def test_answer_without_changes_does_not_escalate(fakes: Fakes, repo: Path) -> None:
    engine = engine_for(fakes)
    outcome = engine.run(RunOptions(task="please do something vague", repo=repo))
    attempts = [call for call in fakes.calls() if "TIMONEL_CLASSIFY" not in str(call["prompt"])]
    assert outcome["status"] == "succeeded"
    assert len(attempts) == 1


def test_escalation_still_happens_when_the_limit_is_zero(fakes: Fakes, repo: Path) -> None:
    engine = engine_for(fakes, "escalation: {answer_without_changes_max_complexity: 0}\n")
    engine.run(RunOptions(task="please do something vague", repo=repo))
    attempts = [call for call in fakes.calls() if "TIMONEL_CLASSIFY" not in str(call["prompt"])]
    assert len(attempts) > 1
