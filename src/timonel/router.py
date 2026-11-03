from __future__ import annotations

import fnmatch
import time
from collections.abc import Collection, Mapping
from dataclasses import asdict, dataclass, field, replace
from pathlib import PurePosixPath
from typing import Any

from timonel.classify import Classification, mentioned_files
from timonel.config import Config, Profile, Rule, Rung
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
    verify: list[list[str]] | None = None
    rules: list[str] = field(default_factory=list)

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
            "verify": self.verify,
            "rules": self.rules,
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
            verify=data.get("verify"),
            rules=list(data.get("rules", [])),
        )


def clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


LANGUAGE_EXTENSIONS = {
    "python": (".py",),
    "javascript": (".js", ".jsx", ".mjs", ".cjs"),
    "typescript": (".ts", ".tsx"),
    "go": (".go",),
    "rust": (".rs",),
    "java": (".java",),
    "kotlin": (".kt", ".kts"),
    "csharp": (".cs",),
    "c": (".c", ".h"),
    "cpp": (".cpp", ".cc", ".hpp", ".cxx"),
    "ruby": (".rb",),
    "php": (".php",),
    "swift": (".swift",),
    "shell": (".sh", ".bash", ".ps1"),
    "html": (".html", ".htm"),
    "css": (".css", ".scss"),
    "markdown": (".md",),
    "yaml": (".yaml", ".yml"),
    "sql": (".sql",),
}


def task_languages(files: list[str], repo_languages: Collection[str] = ()) -> set[str]:
    found = set(repo_languages)
    for name in files:
        suffix = PurePosixPath(name.replace("\\", "/")).suffix.lower()
        found.update(
            language for language, suffixes in LANGUAGE_EXTENSIONS.items() if suffix in suffixes
        )
    return found


def rule_matches(
    rule: Rule, task: str, classification: Classification, languages: set[str]
) -> bool:
    when = rule.when
    lowered = task.lower()
    files = mentioned_files(task)
    checks = [
        not when.kinds or classification.kind in when.kinds,
        not when.languages or bool(languages & {item.lower() for item in when.languages}),
        not when.paths
        or any(fnmatch.fnmatch(name, pattern) for name in files for pattern in when.paths),
        not when.keywords or any(word.lower() in lowered for word in when.keywords),
        when.min_chars is None or len(task) >= when.min_chars,
        when.max_chars is None or len(task) <= when.max_chars,
        when.min_complexity is None or classification.complexity >= when.min_complexity,
        when.max_complexity is None or classification.complexity <= when.max_complexity,
    ]
    return all(checks)


def denied_model(agent: str, model: str, patterns: tuple[str, ...]) -> bool:
    return any(
        fnmatch.fnmatch(model, pattern) or fnmatch.fnmatch(f"{agent}:{model}", pattern)
        for pattern in patterns
        if model
    )


def start_tier(
    classification: Classification, profile: Profile, raise_on_risk: bool = True
) -> tuple[int, list[str]]:
    reasons = [
        f"complexity {classification.complexity} ({classification.kind}, {classification.source})"
    ]
    tier = classification.complexity
    if raise_on_risk and classification.risk == "high" and tier < 3:
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
        billing = (
            BILLING_RANK[config.agents[rung.agent].billing]
            if config.routing.prefer_subscription
            else 0
        )
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
    profile_name: str | None = None,
    available: Collection[str] | None = None,
    cooldowns: Mapping[str, float] | None = None,
    route_stats: list[RouteStats] | None = None,
    force: Rung | None = None,
    now: float | None = None,
    task: str = "",
    repo_languages: Collection[str] = (),
) -> Plan:
    chosen_profile = profile_name or config.default_profile
    languages = task_languages(mentioned_files(task), repo_languages)
    matched = [
        rule for rule in config.routing.rules if rule_matches(rule, task, classification, languages)
    ]
    deny = set(config.routing.deny_agents)
    only: set[str] | None = None
    min_tier, max_tier = 1, 4
    verify: list[list[str]] | None = None
    reasons_from_rules: list[str] = []
    for rule in matched:
        then = rule.then
        label = rule.name or "rule"
        reasons_from_rules.append(f"rule '{label}' matched")
        if then.profile:
            chosen_profile = then.profile
        if then.force and force is None:
            force = parse_force(then.force)
        if then.min_tier:
            min_tier = max(min_tier, then.min_tier)
        if then.max_tier:
            max_tier = min(max_tier, then.max_tier)
        deny.update(then.deny_agents)
        if then.only_agents:
            only = set(then.only_agents) if only is None else only & set(then.only_agents)
        if then.verify is not None:
            verify = [list(command) for command in then.verify]
    if chosen_profile not in config.profiles:
        raise RoutingError(
            f"unknown profile {chosen_profile!r}; choose {', '.join(sorted(config.profiles))}"
        )
    base_profile = config.profiles[chosen_profile]
    profile = replace(
        base_profile,
        min_tier=max(base_profile.min_tier, min_tier),
        max_tier=max(max(base_profile.min_tier, min_tier), min(base_profile.max_tier, max_tier)),
    )
    write = classification.needs_write and classification.kind in config.execution.write_kinds
    moment = time.time() if now is None else now
    usable = {
        name
        for name in (set(config.agents) if available is None else set(available))
        if name in config.agents
        and config.agents[name].enabled
        and config.agents[name].kind == "agent"
        and name not in deny
        and (only is None or name in only)
        and (write or config.agents[name].supports_read_only)
    }
    cooling = {agent: until for agent, until in (cooldowns or {}).items() if until > moment}
    max_escalations = (
        profile.max_escalations
        if profile.max_escalations is not None
        else config.escalation.max_escalations
    )
    review_min_tier = config.review.min_tier if config.review.enabled else 5
    if force is not None:
        agent = config.agents.get(force.agent)
        if agent is None or agent.kind != "agent":
            raise RoutingError(f"unknown agent {force.agent!r}")
        if not write and not agent.supports_read_only:
            raise RoutingError(f"{force.agent} cannot run read-only tasks safely")
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
        step = Step(tier, force.agent, force.model, effort, agent.billing, "forced")
        return Plan(
            classification=classification,
            profile=profile.name,
            start_tier=tier,
            ladders=[[step]],
            write=write,
            max_escalations=0,
            review_min_tier=review_min_tier,
            reasons=[*reasons_from_rules, f"forced to {step.label}"],
            forced=True,
            verify=verify,
            rules=[rule.name for rule in matched],
        )
    first, reasons = start_tier(
        classification, profile, config.classifier.heuristics.high_risk_raises_tier
    )
    reasons = [*reasons_from_rules, *reasons]
    stats = {
        (item.tier, item.agent, item.model): item
        for item in route_stats or []
        if item.kind == classification.kind
    }
    skipped: list[str] = []
    ladders: list[list[Step]] = []
    for tier in list(range(first, profile.max_tier + 1)) + list(
        range(first - 1, profile.min_tier - 1, -1)
    ):
        if tier in profile.skip_tiers:
            skipped.append(f"tier {tier}: skipped by profile '{profile.name}'")
            continue
        if tier < first and ladders:
            break
        candidates = []
        for rung in config.tiers.get(tier, ()):
            if rung.agent not in usable:
                skipped.append(
                    f"tier {tier}: {rung.label} unavailable (disabled, denied, not installed or not authenticated)"
                )
            elif denied_model(rung.agent, rung.model, config.routing.deny_models):
                skipped.append(f"tier {tier}: {rung.label} model denied by routing.deny_models")
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
    order_note = "subscriptions first" if config.routing.prefer_subscription else "configured order"
    reasons.append(
        f"start at tier {ladders[0][0].tier} with {head.label} ({head.billing}; {order_note})"
    )
    if write:
        reasons.append("needs write access: runs in an isolated git worktree")
    else:
        reasons.append("read-only task: runs in place with read-only permissions")
    return Plan(
        classification=classification,
        profile=profile.name,
        start_tier=ladders[0][0].tier,
        ladders=ladders,
        write=write,
        max_escalations=max_escalations,
        review_min_tier=review_min_tier,
        skipped=skipped,
        reasons=reasons,
        verify=verify,
        rules=[rule.name for rule in matched],
    )


def parse_force(value: str) -> Rung:
    agent, _, model = value.partition(":")
    if not agent:
        raise RoutingError("use --force agent or agent:model")
    return Rung(agent=agent.strip(), model=model.strip())
