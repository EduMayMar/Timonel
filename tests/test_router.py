import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from timonel.classify import KINDS, Classification
from timonel.config import Rung, validate_text
from timonel.ledger import RouteStats
from timonel.router import Plan, RoutingError, build_plan, parse_force

CONFIG = validate_text("")


def item(
    complexity: int = 2, kind: str = "edit", risk: str = "low", write: bool = True
) -> Classification:
    return Classification(complexity, kind, risk, write, 1)  # type: ignore[arg-type]


def labels(plan: Plan) -> list[tuple[int, str]]:
    return [(step.tier, step.label) for step in plan.steps()]


def test_balanced_plan_starts_at_complexity_and_escalates() -> None:
    plan = build_plan(CONFIG, item(2))
    assert plan.start_tier == 2
    assert [ladder[0].tier for ladder in plan.ladders] == [2, 3, 4]
    assert labels(plan)[0] == (2, "codex:default")


def test_subscription_before_api_within_tier() -> None:
    config = validate_text(
        "tiers:\n  1:\n    - {agent: deepseek, model: deepseek-flash}\n    - {agent: claude, model: sonnet}\n"
    )
    plan = build_plan(config, item(1))
    assert [step.billing for step in plan.steps()] == ["subscription", "api"]


def test_profiles_shift_and_cap() -> None:
    cheap = build_plan(CONFIG, item(3), profile_name="cheap")
    assert cheap.start_tier == 2
    assert max(step.tier for step in cheap.steps()) <= 3
    assert len(cheap.ladders) == 2
    assert build_plan(CONFIG, item(1), profile_name="max").start_tier == 4


def test_skip_tiers_and_unknown_profile() -> None:
    config = validate_text("profiles:\n  noskip: {skip_tiers: [2, 3]}\n")
    plan = build_plan(config, item(2), profile_name="noskip")
    assert [ladder[0].tier for ladder in plan.ladders] == [4]
    assert any("skipped by profile" in reason for reason in plan.skipped)
    with pytest.raises(RoutingError, match="unknown profile"):
        build_plan(CONFIG, item(), profile_name="nope")


def test_high_risk_raises_start() -> None:
    plan = build_plan(CONFIG, item(1, risk="high"))
    assert plan.start_tier == 2
    assert any("high risk" in reason for reason in plan.reasons)


def test_unavailable_and_cooling_agents_are_skipped() -> None:
    plan = build_plan(
        CONFIG,
        item(2),
        available={"claude", "deepseek", "codex"},
        cooldowns={"codex": 2e9},
        now=1e9,
    )
    assert all(step.agent != "codex" for step in plan.steps())
    assert any("cooling down" in reason for reason in plan.skipped)
    assert any("unavailable" in reason for reason in plan.skipped)


def test_falls_back_to_lower_tier_when_top_is_empty() -> None:
    plan = build_plan(CONFIG, item(4), available={"copilot"})
    assert labels(plan) == [(1, "copilot:auto")]
    assert any("falling back" in reason for reason in plan.reasons)


def test_no_agents_is_an_error() -> None:
    with pytest.raises(RoutingError, match="no usable agent"):
        build_plan(CONFIG, item(), available=set())


def test_force_builds_single_step_plan() -> None:
    plan = build_plan(CONFIG, item(3), force=Rung("claude", "opus"))
    assert labels(plan) == [(3, "claude:opus")]
    assert plan.forced and plan.max_escalations == 0
    assert plan.steps()[0].effort == "high"
    with pytest.raises(RoutingError):
        build_plan(CONFIG, item(), force=Rung("ghost"))
    assert parse_force("codex:o3") == Rung("codex", "o3")
    assert parse_force("claude") == Rung("claude", "")
    with pytest.raises(RoutingError):
        parse_force(":x")


def test_adaptive_reorders_and_explains() -> None:
    config = validate_text(
        "tiers:\n  3:\n    - {agent: claude, model: opus}\n    - {agent: codex}\n"
    )
    stats = [
        RouteStats("refactor", 3, "claude", "opus", 9, 5, 0.4, 30),
        RouteStats("refactor", 3, "codex", "", 11, 9, 0.2, 20),
        RouteStats("debug", 3, "claude", "opus", 50, 50, 0.4, 30),
    ]
    plan = build_plan(config, item(3, kind="refactor"), route_stats=stats)
    assert labels(plan)[0] == (3, "codex:default")
    assert any(
        "success rate in refactor is 82% (n=11) vs claude:opus 56% (n=9)" in reason
        for reason in plan.reasons
    )
    plain = build_plan(config, item(3, kind="refactor"), route_stats=stats[:1])
    assert labels(plain)[0] == (3, "claude:opus")


def test_adaptive_ignores_small_samples() -> None:
    config = validate_text(
        "tiers:\n  3:\n    - {agent: claude, model: opus}\n    - {agent: codex}\n"
    )
    stats = [RouteStats("edit", 3, "codex", "", 2, 2, None, 1)]
    assert labels(build_plan(config, item(3), route_stats=stats))[0] == (3, "claude:opus")


def test_plan_round_trip() -> None:
    plan = build_plan(CONFIG, item(2))
    assert Plan.from_dict(plan.to_dict()).to_dict() == plan.to_dict()


def test_read_only_plan_reason() -> None:
    plan = build_plan(CONFIG, item(1, kind="explain", write=False))
    assert not plan.write
    assert any("read-only" in reason for reason in plan.reasons)


@settings(max_examples=300, deadline=None)
@given(
    complexity=st.integers(1, 4),
    kind=st.sampled_from(KINDS),
    risk=st.sampled_from(["low", "medium", "high"]),
    profile=st.sampled_from(["cheap", "balanced", "max"]),
    available=st.sets(
        st.sampled_from(["claude", "deepseek", "codex", "copilot", "agy"]), min_size=1
    ),
)
def test_router_invariants(
    complexity: int, kind: str, risk: str, profile: str, available: set[str]
) -> None:
    try:
        plan = build_plan(
            CONFIG, item(complexity, kind, risk), profile_name=profile, available=available
        )
    except RoutingError:
        assert not any(rung.agent in available for rungs in CONFIG.tiers.values() for rung in rungs)
        return
    steps = plan.steps()
    assert steps
    assert all(step.agent in available for step in steps)
    tiers = [ladder[0].tier for ladder in plan.ladders]
    assert tiers == sorted(tiers)
    assert len(plan.ladders) <= plan.max_escalations + 1
    for ladder in plan.ladders:
        ranks = [0 if step.billing == "subscription" else 1 for step in ladder]
        assert ranks == sorted(ranks)
        assert len({step.tier for step in ladder}) == 1
