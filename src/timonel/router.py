from __future__ import annotations

import time
from collections.abc import Collection, Mapping
from dataclasses import asdict, dataclass, field
from typing import Any

from timonel.classify import Classification
from timonel.config import Config, Profile, Rung
from timonel.ledger import RouteStats

BILLING_RANK = {"subscription": 0, "api": 1}


class RoutingError(Exception):
    pass


@dataclass(frozen=True)
class Step:
    tier: int
    agent: str
    model: str
    effort: str
    billing: str
    note: str = ""

    @property
    def rung(self) -> Rung:
        return Rung(agent=self.agent, model=self.model, effort=self.effort)

    @property
    def label(self) -> str:
        return self.rung.label


@dataclass
class Plan:
    classification: Classification
    profile: str
    start_tier: int
    ladders: list[list[Step]]
    write: bool
    max_escalations: int
    review_min_tier: int
    skipped: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    forced: bool = False

    def steps(self) -> list[Step]:
        return [step for ladder in self.ladders for step in ladder]

    def to_dict(self) -> dict[str, Any]:
        return {
            "classification": self.classification.to_dict(),
            "profile": self.profile,
            "start_tier": self.start_tier,
            "ladders": [[asdict(step) for step in ladder] for ladder in self.ladders],
            "write": self.write,
            "max_escalations": self.max_escalations,
            "review_min_tier": self.review_min_tier,
            "skipped": self.skipped,
            "reasons": self.reasons,
            "forced": self.forced,
        }

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> Plan:
        return Plan(
            classification=Classification.from_dict(data["classification"]),
            profile=data["profile"],
            start_tier=data["start_tier"],
            ladders=[[Step(**step) for step in ladder] for ladder in data["ladders"]],
            write=data["write"],
            max_escalations=data["max_escalations"],
            review_min_tier=data["review_min_tier"],
            skipped=list(data.get("skipped", [])),
            reasons=list(data.get("reasons", [])),
            forced=bool(data.get("forced", False)),
        )


def clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def start_tier(classification: Classification, profile: Profile) -> tuple[int, list[str]]:
    reasons = [
        f"complexity {classification.complexity} ({classification.kind}, {classification.source})"
    ]
    tier = classification.complexity
    if classification.risk == "high" and tier < 3:
        tier += 1
        reasons.append("high risk raises the starting tier by one")
    if profile.start_offset:
        tier += profile.start_offset
        reasons.append(f"profile '{profile.name}' shifts the start by {profile.start_offset:+d}")
    return clamp(tier, profile.min_tier, profile.max_tier), reasons


def score(stats: RouteStats | None, min_samples: int) -> float | None:
    if stats is None or stats.total < min_samples:
        return None
    return (stats.successes + 1) / (stats.total + 2)


def describe(stats: RouteStats) -> str:
    return f"{stats.success_rate:.0%} (n={stats.total})"


def order_ladder(
    tier: int,
    rungs: list[Rung],
    config: Config,
    kind: str,
    stats: Mapping[tuple[int, str, str], RouteStats],
    adaptive: bool,
) -> tuple[list[Rung], list[str]]:
    def key(item: tuple[int, Rung]) -> tuple[float, ...]:
        index, rung = item
        billing = BILLING_RANK[config.agents[rung.agent].billing]
        if not adaptive:
            return (billing, index)
        found = stats.get((tier, rung.agent, rung.model))
        rate = score(found, config.adaptive.min_samples)
        cost = found.mean_cost if found and found.mean_cost is not None else float("inf")
        return (billing, -(0.5 if rate is None else rate), cost, index)

    ordered = [rung for _, rung in sorted(enumerate(rungs), key=key)]
    reasons: list[str] = []
    if adaptive and ordered:
        first = ordered[0]
        chosen = stats.get((tier, first.agent, first.model))
        rivals = [
            (rung, found)
            for rung in ordered[1:]
            if (found := stats.get((tier, rung.agent, rung.model))) is not None
            and found.total >= config.adaptive.min_samples
            and config.agents[rung.agent].billing == config.agents[first.agent].billing
        ]
        moved = ordered != rungs
        if chosen and chosen.total >= config.adaptive.min_samples and (moved or rivals):
            versus = ", ".join(f"{rung.label} {describe(found)}" for rung, found in rivals)
            suffix = f" vs {versus}" if versus else ""
            reasons.append(
                f"tier {tier}: chose {first.label} because its success rate in {kind} is "
                f"{describe(chosen)}{suffix}"
            )
    return ordered, reasons


def build_plan(
    config: Config,
    classification: Classification,
    *,
    profile_name: str = "balanced",
    available: Collection[str] | None = None,
    cooldowns: Mapping[str, float] | None = None,
    route_stats: list[RouteStats] | None = None,
    force: Rung | None = None,
    now: float | None = None,
) -> Plan:
    if profile_name not in config.profiles:
        raise RoutingError(
            f"unknown profile {profile_name!r}; choose {', '.join(sorted(config.profiles))}"
        )
    profile = config.profiles[profile_name]
    moment = time.time() if now is None else now
    usable = set(config.agents) if available is None else set(available)
    cooling = {agent: until for agent, until in (cooldowns or {}).items() if until > moment}
    max_escalations = (
        profile.max_escalations
        if profile.max_escalations is not None
        else config.execution.max_escalations
    )
    if force is not None:
        if force.agent not in config.agents:
            raise RoutingError(f"unknown agent {force.agent!r}")
        tier = clamp(classification.complexity, 1, 4)
        effort = force.effort or next(
            (
                rung.effort
                for rungs in config.tiers.values()
                for rung in rungs
                if rung.agent == force.agent and rung.model == force.model
            ),
            "",
        )
        step = Step(
            tier, force.agent, force.model, effort, config.agents[force.agent].billing, "forced"
        )
        return Plan(
            classification=classification,
            profile=profile.name,
            start_tier=tier,
            ladders=[[step]],
            write=classification.needs_write,
            max_escalations=0,
            review_min_tier=config.execution.review_min_tier,
            reasons=[f"forced to {step.label}"],
            forced=True,
        )
    first, reasons = start_tier(classification, profile)
    stats = {
        (item.tier, item.agent, item.model): item
        for item in route_stats or []
        if item.kind == classification.kind
    }
    skipped: list[str] = []
    ladders: list[list[Step]] = []
    for tier in list(range(first, profile.max_tier + 1)) + list(range(first - 1, 0, -1)):
        if tier in profile.skip_tiers:
            skipped.append(f"tier {tier}: skipped by profile '{profile.name}'")
            continue
        if tier < first and ladders:
            break
        candidates = []
        for rung in config.tiers.get(tier, ()):
            if rung.agent not in usable:
                skipped.append(
                    f"tier {tier}: {rung.label} unavailable (not installed or not authenticated)"
                )
            elif rung.agent in cooling:
                skipped.append(f"tier {tier}: {rung.label} cooling down after a usage limit")
            else:
                candidates.append(rung)
        if not candidates:
            continue
        if tier < first:
            reasons.append(f"no usable agent at tier {first} or above; falling back to tier {tier}")
        ordered, adaptive_reasons = order_ladder(
            tier, candidates, config, classification.kind, stats, config.adaptive.enabled
        )
        reasons.extend(adaptive_reasons)
        ladders.append(
            [
                Step(tier, rung.agent, rung.model, rung.effort, config.agents[rung.agent].billing)
                for rung in ordered
            ]
        )
    if not ladders:
        raise RoutingError("no usable agent for this task; run 'timonel doctor'")
    ladders = ladders[: max_escalations + 1]
    head = ladders[0][0]
    reasons.append(
        f"start at tier {ladders[0][0].tier} with {head.label} ({head.billing}; subscriptions first)"
    )
    if classification.needs_write:
        reasons.append("needs write access: runs in an isolated git worktree")
    else:
        reasons.append("read-only task: runs in place with read-only permissions")
    return Plan(
        classification=classification,
        profile=profile.name,
        start_tier=ladders[0][0].tier,
        ladders=ladders,
        write=classification.needs_write,
        max_escalations=max_escalations,
        review_min_tier=config.execution.review_min_tier,
        skipped=skipped,
        reasons=reasons,
    )


def parse_force(value: str) -> Rung:
    agent, _, model = value.partition(":")
    if not agent:
        raise RoutingError("use --force agent or agent:model")
    return Rung(agent=agent.strip(), model=model.strip())
