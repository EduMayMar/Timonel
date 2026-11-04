from pathlib import Path

from timonel.ledger import Ledger, data_dir


def test_runs_attempts_and_route_stats(ledger: Ledger) -> None:
    ledger.create_run(
        "abc12345",
        task="t",
        repo="/r",
        profile="balanced",
        status="running",
        plan={"a": 1},
        write=True,
    )
    ledger.update_run("abc12345", status="succeeded", worktree=Path("/w"))
    run = ledger.get_run("abc12345")
    assert run is not None
    assert run["plan"] == {"a": 1}
    assert run["write"] is True
    assert run["worktree"] == str(Path("/w"))
    assert ledger.find_run("abc1")["id"] == "abc12345"  # type: ignore[index]
    assert ledger.find_run("zzz") is None
    for outcome in ("succeeded", "failed", "rate_limited"):
        attempt = ledger.start_attempt(
            "abc12345",
            role="execute",
            tier=2,
            kind="edit",
            agent="codex",
            model="",
            effort="",
            billing="subscription",
        )
        ledger.finish_attempt(
            attempt, outcome=outcome, duration_s=2.0, cost_usd=0.1, output="x" * 300_000
        )
    rows = ledger.attempts("abc12345")
    assert [row["seq"] for row in rows] == [1, 2, 3]
    assert len(rows[0]["output"]) == 200_000
    stats = ledger.route_stats("edit")
    assert len(stats) == 1
    assert (stats[0].total, stats[0].successes, stats[0].success_rate) == (2, 1, 0.5)
    assert ledger.route_stats("debug") == []


def test_filters_children_cooldowns_events(ledger: Ledger) -> None:
    ledger.create_run("parent00", task="build the thing", repo="/r", profile="max", status="failed")
    ledger.create_run(
        "child000", parent_id="parent00", task="part", repo="/r", profile="max", status="succeeded"
    )
    assert [run["id"] for run in ledger.list_runs()] == ["parent00"]
    assert [run["id"] for run in ledger.list_runs(parent=False, status="succeeded")] == ["child000"]
    assert ledger.list_runs(search="thing")[0]["id"] == "parent00"
    assert [run["id"] for run in ledger.child_runs("parent00")] == ["child000"]
    ledger.set_cooldown("codex", 200.0, "limit")
    ledger.set_cooldown("codex", 300.0, "limit again")
    assert ledger.cooldowns(now=250.0) == {"codex": 300.0}
    assert ledger.cooldowns(now=301.0) == {}
    ledger.clear_cooldown("codex")
    assert ledger.cooldowns(now=0) == {}
    first = ledger.add_event("parent00", "plan", {"x": "ñ"})
    ledger.add_event("parent00", "done", {})
    assert [event["kind"] for event in ledger.events("parent00")] == ["plan", "done"]
    assert [event["kind"] for event in ledger.events("parent00", after=first)] == ["done"]
    ledger.update_run("parent00")


def test_data_dir_override(home: Path) -> None:
    assert data_dir() == home
