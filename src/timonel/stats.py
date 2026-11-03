from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from timonel.config import Config
from timonel.ledger import Ledger

DONE = frozenset({"succeeded", "applied"})


@dataclass(frozen=True)
class Group:
    key: str
    attempts: int
    successes: int
    cost_usd: float
    cost_known: int
    mean_duration_s: float | None

    @property
    def success_rate(self) -> float:
        return self.successes / self.attempts if self.attempts else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "attempts": self.attempts,
            "successes": self.successes,
            "success_rate": self.success_rate,
            "cost_usd": self.cost_usd,
            "cost_known": self.cost_known,
            "mean_duration_s": self.mean_duration_s,
        }


@dataclass(frozen=True)
class Savings:
    runs: int
    actual_usd: float
    baseline_usd: float | None
    baseline_per_run_usd: float | None
    baseline_source: str

    @property
    def saved_usd(self) -> float | None:
        return None if self.baseline_usd is None else self.baseline_usd - self.actual_usd

    def to_dict(self) -> dict[str, Any]:
        return {
            "runs": self.runs,
            "actual_usd": self.actual_usd,
            "baseline_usd": self.baseline_usd,
            "baseline_per_run_usd": self.baseline_per_run_usd,
            "baseline_source": self.baseline_source,
            "saved_usd": self.saved_usd,
        }


def group_attempts(rows: list[dict[str, Any]], field: str) -> list[Group]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row["role"] == "execute" and row["outcome"] not in ("running", "cancelled"):
            buckets[str(row[field])].append(row)
    groups = []
    for key, items in sorted(buckets.items()):
        durations = [item["duration_s"] for item in items if item["duration_s"] is not None]
        costs = [item["cost_usd"] for item in items if item["cost_usd"] is not None]
        groups.append(
            Group(
                key=key,
                attempts=len(items),
                successes=sum(1 for item in items if item["outcome"] == "succeeded"),
                cost_usd=sum(costs),
                cost_known=len(costs),
                mean_duration_s=sum(durations) / len(durations) if durations else None,
            )
        )
    return groups


def top_tier_cost(config: Config, rows: list[dict[str, Any]]) -> tuple[float | None, str]:
    top = max((tier for tier, rungs in config.tiers.items() if rungs), default=None)
    if top is None:
        return None, "no tiers"
    observed = [
        row["cost_usd"]
        for row in rows
        if row["tier"] == top
        and row["role"] == "execute"
        and row["cost_usd"] is not None
        and not row["cost_estimated"]
    ]
    if observed:
        return sum(observed) / len(
            observed
        ), f"mean reported cost of {len(observed)} tier-{top} attempts"
    for rung in config.tiers[top]:
        price = config.agents[rung.agent].cost_per_attempt_usd
        if price is not None:
            return price, f"cost_per_attempt_usd of {rung.agent}"
    return None, f"no cost data for tier {top} yet"


def savings(config: Config, ledger: Ledger, since: float = 0) -> Savings:
    rows = ledger.all_attempts()
    runs = [
        run
        for run in ledger.list_runs(limit=100000)
        if run["status"] in DONE and run["created"] >= since
    ]
    run_ids = {run["id"] for run in runs}
    children = {child["id"]: run["id"] for run in runs for child in ledger.child_runs(run["id"])}
    actual = sum(
        row["cost_usd"] or 0.0
        for row in rows
        if row["run_id"] in run_ids or row["run_id"] in children
    )
    per_run, source = top_tier_cost(config, rows)
    baseline = per_run * len(runs) if per_run is not None else None
    return Savings(len(runs), actual, baseline, per_run, source)


def spend_since(ledger: Ledger, since: float) -> float:
    return sum(row["cost_usd"] or 0.0 for row in ledger.all_attempts(since))


def start_of_day(now: float | None = None) -> float:
    moment = time.localtime(time.time() if now is None else now)
    return time.mktime((moment.tm_year, moment.tm_mon, moment.tm_mday, 0, 0, 0, 0, 0, -1))


def summary(config: Config, ledger: Ledger) -> dict[str, Any]:
    rows = ledger.all_attempts()
    return {
        "by_agent": [group.to_dict() for group in group_attempts(rows, "agent")],
        "by_tier": [group.to_dict() for group in group_attempts(rows, "tier")],
        "by_kind": [group.to_dict() for group in group_attempts(rows, "kind")],
        "savings": savings(config, ledger).to_dict(),
        "spend_today_usd": spend_since(ledger, start_of_day()),
        "runs": len(ledger.list_runs(limit=100000)),
    }
