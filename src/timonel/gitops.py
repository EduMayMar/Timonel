from __future__ import annotations

import hashlib
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from timonel.ledger import data_dir

IDENTITY = [
    "-c",
    "user.name=Timonel",
    "-c",
    "user.email=timonel@localhost",
    "-c",
    "commit.gpgsign=false",
]


class GitError(Exception):
    pass


@dataclass(frozen=True)
class DiffSummary:
    files: list[str]
    insertions: int
    deletions: int
    text: str

    @property
    def size(self) -> int:
        return self.insertions + self.deletions


def git(repo: Path, *args: str, check: bool = True, timeout: float = 120) -> str:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"}
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=repo,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise GitError(f"git {' '.join(args)}: {error}") from error
    if check and completed.returncode != 0:
        raise GitError(
            f"git {' '.join(args)}: {completed.stderr.strip() or completed.stdout.strip()}"
        )
    return completed.stdout


def repo_root(path: Path) -> Path:
    try:
        top = git(path, "rev-parse", "--show-toplevel").strip()
    except GitError as error:
        raise GitError(f"{path} is not inside a git repository") from error
    return Path(top).resolve()


def head(repo: Path) -> str:
    return git(repo, "rev-parse", "HEAD").strip()


def is_clean(repo: Path) -> bool:
    return not git(repo, "status", "--porcelain", "--untracked-files=no").strip()


def worktrees_root(repo: Path) -> Path:
    digest = hashlib.sha256(str(repo).encode("utf-8")).hexdigest()[:10]
    return data_dir() / "worktrees" / digest


def branch_name(run_id: str) -> str:
    return f"timonel/{run_id}"


def create_worktree(repo: Path, run_id: str, base: str | None = None) -> tuple[Path, str, str]:
    base_ref = base or head(repo)
    target = worktrees_root(repo) / run_id
    target.parent.mkdir(parents=True, exist_ok=True)
    branch = branch_name(run_id)
    git(repo, "worktree", "add", "-b", branch, str(target), base_ref)
    return target, branch, base_ref


def commit_all(worktree: Path, message: str) -> bool:
    git(worktree, "add", "-A")
    if not git(worktree, "status", "--porcelain").strip():
        return False
    git(worktree, *IDENTITY, "commit", "--no-verify", "-q", "-m", message)
    return True


def changed_entries(repo: Path, base: str, ref: str) -> list[tuple[str, str]]:
    raw = git(repo, "diff", "--raw", "-z", "--no-renames", f"{base}..{ref}")
    parts = [part for part in raw.split("\0") if part]
    entries = []
    for meta, path in zip(parts[::2], parts[1::2], strict=False):
        new_mode = meta.split()[1] if len(meta.split()) > 1 else ""
        entries.append((new_mode, path))
    return entries


def unsafe_paths(repo: Path, base: str, ref: str) -> list[str]:
    problems = []
    for mode, path in changed_entries(repo, base, ref):
        pure = PurePosixPath(path)
        if (
            pure.is_absolute()
            or ".." in pure.parts
            or (pure.parts and pure.parts[0].lower() == ".git")
        ):
            problems.append(f"{path}: path escapes the repository")
            continue
        if mode == "120000":
            target = git(repo, "cat-file", "-p", f"{ref}:{path}").strip()
            resolved = PurePosixPath(os.path.normpath(str(pure.parent / target)))
            if (
                target.startswith(("/", "\\"))
                or ":" in target
                or (resolved.parts and resolved.parts[0] == "..")
            ):
                problems.append(f"{path}: symlink points outside the repository ({target})")
    return problems


def diff_summary(repo: Path, base: str, ref: str) -> DiffSummary:
    numstat = git(repo, "diff", "--numstat", f"{base}..{ref}")
    files: list[str] = []
    insertions = deletions = 0
    for line in numstat.splitlines():
        added, removed, name = ([*line.split("\t", 2), "", "", ""])[:3]
        files.append(name)
        insertions += int(added) if added.isdigit() else 0
        deletions += int(removed) if removed.isdigit() else 0
    text = git(repo, "diff", f"{base}..{ref}")
    return DiffSummary(files=files, insertions=insertions, deletions=deletions, text=text)


def merge_branch(repo: Path, branch: str, message: str) -> None:
    if not is_clean(repo):
        raise GitError("the repository has uncommitted changes; commit or stash them first")
    try:
        git(repo, *IDENTITY, "merge", "--no-ff", "--no-edit", "-m", message, branch)
    except GitError:
        git(repo, "merge", "--abort", check=False)
        raise


def remove_worktree(repo: Path, worktree: Path | None, branch: str | None) -> None:
    if worktree is not None:
        git(repo, "worktree", "remove", "--force", str(worktree), check=False)
    git(repo, "worktree", "prune", check=False)
    if branch:
        git(repo, "branch", "-D", branch, check=False)


def merge_into_worktree(worktree: Path, branch: str, message: str) -> bool:
    try:
        git(worktree, *IDENTITY, "merge", "--no-ff", "--no-edit", "-m", message, branch)
    except GitError:
        git(worktree, "merge", "--abort", check=False)
        return False
    return True
