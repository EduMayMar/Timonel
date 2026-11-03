from timonel.config import validate_text
from timonel.ledger import Ledger
from timonel.redact import MASK, Redactor, redact
from timonel.stats import group_attempts, savings, start_of_day, summary


def add(
    ledger: Ledger,
    run_id: str,
    tier: int,
    agent: str,
    outcome: str,
    cost: float | None,
    estimated: bool = False,
    role: str = "execute",
) -> None:
    attempt = ledger.start_attempt(
        run_id,
        role=role,
        tier=tier,
        kind="edit",
        agent=agent,
        model="",
        effort="",
        billing="subscription",
    )
    ledger.finish_attempt(
        attempt, outcome=outcome, cost_usd=cost, cost_estimated=estimated, duration_s=1.5
    )


def test_savings_against_top_tier(ledger: Ledger) -> None:
    config = validate_text("")
    ledger.create_run("r1", task="a", repo="/", profile="balanced", status="applied")
    ledger.create_run("r2", task="b", repo="/", profile="balanced", status="succeeded")
    ledger.create_run("r3", task="c", repo="/", profile="balanced", status="failed")
    add(ledger, "r1", 1, "copilot", "succeeded", 0.01)
    add(ledger, "r1", 1, "claude", "succeeded", 0.002, role="classify")
    add(ledger, "r2", 2, "codex", "succeeded", 0.05)
    add(ledger, "r3", 4, "claude", "succeeded", 1.0)
    result = savings(config, ledger)
    assert result.runs == 2
    assert round(result.actual_usd, 4) == 0.062
    assert result.baseline_per_run_usd == 1.0
    assert result.saved_usd is not None and round(result.saved_usd, 3) == 1.938
    data = summary(config, ledger)
    assert {group["key"] for group in data["by_agent"]} == {"claude", "codex", "copilot"}
    assert data["savings"]["runs"] == 2


def test_savings_without_cost_data(ledger: Ledger) -> None:
    config = validate_text("")
    assert savings(config, ledger).saved_usd is None
    priced = validate_text("agents:\n  claude:\n    cost_per_attempt_usd: 0.5\n")
    assert savings(priced, ledger).baseline_source == "cost_per_attempt_usd of claude"


def test_group_attempts_ignores_running(ledger: Ledger) -> None:
    ledger.create_run("r1", task="a", repo="/", profile="balanced", status="running")
    ledger.start_attempt(
        "r1", role="execute", tier=1, kind="edit", agent="x", model="", effort="", billing="api"
    )
    add(ledger, "r1", 1, "x", "failed", None)
    groups = group_attempts(ledger.all_attempts(), "agent")
    assert len(groups) == 1 and groups[0].attempts == 1 and groups[0].success_rate == 0
    assert start_of_day(86400 * 365) <= 86400 * 365


def test_redaction_patterns_and_env_values() -> None:
    redactor = Redactor({"MY_API_KEY": "supersecretvalue123", "PATH": "/usr/bin:/bin/longpath"})
    text = (
        "key supersecretvalue123 sk-ant-api03-abcdefghijklmnop ghp_abcdefghijklmnopqrstuvwxyz0123 "
        "AKIAABCDEFGHIJKLMNOP Bearer abcdefghijklmnopqrstuvwxyz token=abcdef1234567890 /usr/bin:/bin/longpath "
        "-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY-----"
    )
    cleaned = redactor(text)
    for secret in (
        "supersecretvalue123",
        "sk-ant-api03",
        "ghp_abc",
        "AKIAABCD",
        "abcdefghijklmnopqrstuvwxyz",
        "abcdef1234567890",
        "PRIVATE KEY",
    ):
        assert secret not in cleaned
    assert "/usr/bin:/bin/longpath" in cleaned
    assert cleaned.count(MASK) >= 6
    assert redact("nothing secret here") == "nothing secret here"
