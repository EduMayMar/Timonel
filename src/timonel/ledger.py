from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    parent_id TEXT,
    created REAL NOT NULL,
    updated REAL NOT NULL,
    task TEXT NOT NULL,
    repo TEXT NOT NULL,
    profile TEXT NOT NULL,
    classification TEXT,
    plan TEXT,
    status TEXT NOT NULL,
    write INTEGER NOT NULL DEFAULT 0,
    branch TEXT,
    worktree TEXT,
    base_ref TEXT,
    owner_pid INTEGER,
    summary TEXT,
    diff_stat TEXT,
    error TEXT
);
CREATE TABLE IF NOT EXISTS attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(id),
    seq INTEGER NOT NULL,
    role TEXT NOT NULL,
    tier INTEGER NOT NULL,
    kind TEXT NOT NULL,
    agent TEXT NOT NULL,
    model TEXT NOT NULL,
    effort TEXT NOT NULL,
    billing TEXT NOT NULL,
    started REAL NOT NULL,
    ended REAL,
    duration_s REAL,
    exit_code INTEGER,
    outcome TEXT NOT NULL,
    cost_usd REAL,
    cost_estimated INTEGER NOT NULL DEFAULT 0,
    tokens_in INTEGER,
    tokens_out INTEGER,
    output TEXT,
    error TEXT
);
CREATE INDEX IF NOT EXISTS attempts_run ON attempts(run_id);
CREATE INDEX IF NOT EXISTS attempts_route ON attempts(kind, tier, agent);
CREATE TABLE IF NOT EXISTS cooldowns (
    agent TEXT PRIMARY KEY,
    until REAL NOT NULL,
    reason TEXT
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    ts REAL NOT NULL,
    kind TEXT NOT NULL,
    data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_run ON events(run_id, id);
"""

RUN_FIELDS = (
    "parent_id",
    "task",
    "repo",
    "profile",
    "classification",
    "plan",
    "status",
    "write",
    "branch",
    "worktree",
    "base_ref",
    "owner_pid",
    "summary",
    "diff_stat",
    "error",
)
ATTEMPT_FIELDS = (
    "ended",
    "duration_s",
    "exit_code",
    "outcome",
    "cost_usd",
    "cost_estimated",
    "tokens_in",
    "tokens_out",
    "output",
    "error",
)
MAX_OUTPUT = 200_000


def data_dir() -> Path:
    if explicit := os.environ.get("TIMONEL_DATA_DIR"):
        return Path(explicit)
    if override := os.environ.get("TIMONEL_HOME"):
        return Path(override)
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "timonel"
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "timonel"


@dataclass(frozen=True)
class RouteStats:
    kind: str
    tier: int
    agent: str
    model: str
    total: int
    successes: int
    mean_cost: float | None
    mean_duration: float | None

    @property
    def success_rate(self) -> float:
        return self.successes / self.total if self.total else 0.0


class Ledger:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or data_dir() / "ledger.sqlite"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.connection = sqlite3.connect(self.path, check_same_thread=False, timeout=30)
        self.connection.row_factory = sqlite3.Row
        with self.lock:
            self.connection.execute("PRAGMA journal_mode=WAL")
            self.connection.executescript(SCHEMA)
            self.connection.commit()

    def close(self) -> None:
        with self.lock:
            self.connection.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self.lock:
            try:
                yield self.connection
                self.connection.commit()
            except BaseException:
                self.connection.rollback()
                raise

    def create_run(self, run_id: str, **fields: Any) -> None:
        now = time.time()
        columns = {key: encode(value) for key, value in fields.items() if key in RUN_FIELDS}
        names = ", ".join(["id", "created", "updated", *columns])
        marks = ", ".join("?" * (3 + len(columns)))
        with self.transaction() as db:
            db.execute(
                f"INSERT INTO runs ({names}) VALUES ({marks})",
                (run_id, now, now, *columns.values()),
            )

    def update_run(self, run_id: str, **fields: Any) -> None:
        columns = {key: encode(value) for key, value in fields.items() if key in RUN_FIELDS}
        if not columns:
            return
        assignments = ", ".join(f"{key} = ?" for key in columns)
        with self.transaction() as db:
            db.execute(
                f"UPDATE runs SET {assignments}, updated = ? WHERE id = ?",
                (*columns.values(), time.time(), run_id),
            )

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self.lock:
            row = self.connection.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        return decode_run(row) if row else None

    def find_run(self, prefix: str) -> dict[str, Any] | None:
        with self.lock:
            rows = self.connection.execute(
                "SELECT * FROM runs WHERE id LIKE ? ORDER BY created DESC LIMIT 2",
                (prefix.replace("%", "").replace("_", "") + "%",),
            ).fetchall()
        return decode_run(rows[0]) if len(rows) == 1 else None

    def list_runs(
        self,
        limit: int = 50,
        status: str | None = None,
        search: str | None = None,
        parent: bool = True,
    ) -> list[dict[str, Any]]:
        clauses = []
        params: list[Any] = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if search:
            clauses.append("task LIKE ?")
            params.append(f"%{search}%")
        if parent:
            clauses.append("parent_id IS NULL")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.lock:
            rows = self.connection.execute(
                f"SELECT * FROM runs {where} ORDER BY created DESC LIMIT ?",
                (*params, limit),
            ).fetchall()
        return [decode_run(row) for row in rows]

    def recent_runs(
        self, repo: str, *, since: float, statuses: Sequence[str], limit: int, exclude: str
    ) -> list[dict[str, Any]]:
        marks = ", ".join("?" * len(statuses))
        with self.lock:
            rows = self.connection.execute(
                "SELECT * FROM runs WHERE repo = ? AND parent_id IS NULL AND created >= ?"
                f" AND id != ? AND status IN ({marks}) ORDER BY created DESC LIMIT ?",
                (repo, since, exclude, *statuses, limit),
            ).fetchall()
        return [decode_run(row) for row in rows]

    def child_runs(self, parent_id: str) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.connection.execute(
                "SELECT * FROM runs WHERE parent_id = ? ORDER BY created", (parent_id,)
            ).fetchall()
        return [decode_run(row) for row in rows]

    def start_attempt(
        self,
        run_id: str,
        *,
        role: str,
        tier: int,
        kind: str,
        agent: str,
        model: str,
        effort: str,
        billing: str,
    ) -> int:
        with self.transaction() as db:
            seq = db.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 FROM attempts WHERE run_id = ?", (run_id,)
            ).fetchone()[0]
            cursor = db.execute(
                "INSERT INTO attempts (run_id, seq, role, tier, kind, agent, model, effort, billing,"
                " started, outcome) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'running')",
                (run_id, seq, role, tier, kind, agent, model, effort, billing, time.time()),
            )
            return int(cursor.lastrowid or 0)

    def finish_attempt(self, attempt_id: int, **fields: Any) -> None:
        columns = {key: value for key, value in fields.items() if key in ATTEMPT_FIELDS}
        if isinstance(columns.get("output"), str):
            columns["output"] = columns["output"][-MAX_OUTPUT:]
        columns.setdefault("ended", time.time())
        assignments = ", ".join(f"{key} = ?" for key in columns)
        with self.transaction() as db:
            db.execute(
                f"UPDATE attempts SET {assignments} WHERE id = ?",
                (*columns.values(), attempt_id),
            )

    def attempts(self, run_id: str) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.connection.execute(
                "SELECT * FROM attempts WHERE run_id = ? ORDER BY seq", (run_id,)
            ).fetchall()
        return [dict(row) for row in rows]

    def all_attempts(self, since: float = 0) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.connection.execute(
                "SELECT * FROM attempts WHERE started >= ? ORDER BY started", (since,)
            ).fetchall()
        return [dict(row) for row in rows]

    def mean_reported_cost(self, agent: str, model: str) -> float | None:
        with self.lock:
            row = self.connection.execute(
                "SELECT AVG(cost_usd) FROM attempts WHERE agent = ? AND model = ?"
                " AND cost_usd IS NOT NULL AND cost_estimated = 0",
                (agent, model),
            ).fetchone()
        return float(row[0]) if row and row[0] is not None else None

    def route_stats(self, kind: str | None = None) -> list[RouteStats]:
        query = (
            "SELECT kind, tier, agent, model, COUNT(*) AS total,"
            " SUM(CASE WHEN outcome = 'succeeded' THEN 1 ELSE 0 END) AS successes,"
            " AVG(cost_usd) AS mean_cost, AVG(duration_s) AS mean_duration"
            " FROM attempts WHERE role = 'execute' AND outcome NOT IN ('running', 'rate_limited',"
            " 'cancelled', 'launch_error') {where} GROUP BY kind, tier, agent, model"
        )
        params: tuple[Any, ...] = ()
        where = ""
        if kind is not None:
            where = "AND kind = ?"
            params = (kind,)
        with self.lock:
            rows = self.connection.execute(query.format(where=where), params).fetchall()
        return [
            RouteStats(
                kind=row["kind"],
                tier=row["tier"],
                agent=row["agent"],
                model=row["model"],
                total=row["total"],
                successes=row["successes"] or 0,
                mean_cost=row["mean_cost"],
                mean_duration=row["mean_duration"],
            )
            for row in rows
        ]

    def purge(self, cutoff: float) -> int:
        with self.transaction() as db:
            ids = [
                row[0]
                for row in db.execute("SELECT id FROM runs WHERE created < ?", (cutoff,)).fetchall()
            ]
            for run_id in ids:
                db.execute("DELETE FROM attempts WHERE run_id = ?", (run_id,))
                db.execute("DELETE FROM events WHERE run_id = ?", (run_id,))
                db.execute("DELETE FROM runs WHERE id = ?", (run_id,))
            db.execute("DELETE FROM attempts WHERE run_id LIKE 'plan-%' AND started < ?", (cutoff,))
            db.execute("DELETE FROM events WHERE run_id LIKE 'plan-%' AND ts < ?", (cutoff,))
        with self.lock:
            self.connection.execute("VACUUM")
        return len(ids)

    def set_cooldown(self, agent: str, until: float, reason: str) -> None:
        with self.transaction() as db:
            db.execute(
                "INSERT INTO cooldowns (agent, until, reason) VALUES (?, ?, ?)"
                " ON CONFLICT(agent) DO UPDATE SET until = excluded.until, reason = excluded.reason",
                (agent, until, reason),
            )

    def cooldowns(self, now: float | None = None) -> dict[str, float]:
        moment = time.time() if now is None else now
        with self.lock:
            rows = self.connection.execute(
                "SELECT agent, until FROM cooldowns WHERE until > ?", (moment,)
            ).fetchall()
        return {row["agent"]: row["until"] for row in rows}

    def clear_cooldown(self, agent: str) -> None:
        with self.transaction() as db:
            db.execute("DELETE FROM cooldowns WHERE agent = ?", (agent,))

    def add_event(self, run_id: str, kind: str, data: dict[str, Any]) -> int:
        with self.transaction() as db:
            cursor = db.execute(
                "INSERT INTO events (run_id, ts, kind, data) VALUES (?, ?, ?, ?)",
                (run_id, time.time(), kind, json.dumps(data, ensure_ascii=False)),
            )
            return int(cursor.lastrowid or 0)

    def events(self, run_id: str, after: int = 0) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.connection.execute(
                "SELECT id, ts, kind, data FROM events WHERE run_id = ? AND id > ? ORDER BY id",
                (run_id, after),
            ).fetchall()
        return [
            {"id": row["id"], "ts": row["ts"], "kind": row["kind"], "data": json.loads(row["data"])}
            for row in rows
        ]


def encode(value: Any) -> Any:
    if isinstance(value, dict | list):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, Path):
        return str(value)
    return value


def decode_run(row: sqlite3.Row) -> dict[str, Any]:
    run = dict(row)
    for key in ("classification", "plan"):
        if run.get(key):
            run[key] = json.loads(run[key])
    run["write"] = bool(run["write"])
    return run


def open_ledger(path: str = "") -> Ledger:
    return Ledger(Path(path).expanduser() if path else None)
