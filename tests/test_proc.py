import os
import sys
import threading
import time
from pathlib import Path

import pytest

from timonel.proc import (
    LaunchError,
    kill_tree,
    npm_shim_script,
    pid_alive,
    resolve_launch,
    run_process,
    run_quick,
)

PY = getattr(sys, "_base_executable", sys.executable)
ENV = {**os.environ, "PYTHONUTF8": "1"}


def test_captures_stdout_stderr_and_exit_code(tmp_path: Path) -> None:
    result = run_process(
        [PY, "-c", "import sys; print('out'); print('err', file=sys.stderr); sys.exit(3)"],
        cwd=tmp_path,
        env=ENV,
    )
    assert result.exit_code == 3
    assert "out" in result.output
    assert "err" in result.output
    assert not result.timed_out


def test_feeds_stdin_and_streams_lines(tmp_path: Path) -> None:
    seen: list[str] = []
    result = run_process(
        [PY, "-c", "import sys; data = sys.stdin.read(); print(data.upper()); print('ñ✓')"],
        cwd=tmp_path,
        env=ENV,
        stdin="héllo",
        on_line=seen.append,
    )
    assert "HÉLLO" in result.output
    assert "ñ✓" in result.output
    assert any("HÉLLO" in line for line in seen)


def test_total_timeout_kills_process(tmp_path: Path) -> None:
    started = time.monotonic()
    result = run_process(
        [PY, "-c", "import time\nwhile True:\n    print('tick', flush=True)\n    time.sleep(0.1)"],
        cwd=tmp_path,
        env=ENV,
        total_timeout_s=1,
        idle_timeout_s=30,
    )
    assert result.timed_out
    assert time.monotonic() - started < 10


def test_idle_timeout(tmp_path: Path) -> None:
    result = run_process(
        [PY, "-c", "import time; print('start', flush=True); time.sleep(60)"],
        cwd=tmp_path,
        env=ENV,
        total_timeout_s=60,
        idle_timeout_s=1,
    )
    assert result.idle_timed_out
    assert "start" in result.output


def test_cancel_kills_whole_tree(tmp_path: Path) -> None:
    marker = tmp_path / "grandchild.pid"
    script = (
        "import subprocess, sys, time\n"
        f"child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        f"open(r'{marker}', 'w').write(str(child.pid))\n"
        "print('ready', flush=True)\n"
        "time.sleep(60)\n"
    )
    cancel = threading.Event()

    def on_line(line: str) -> None:
        if "ready" in line:
            cancel.set()

    result = run_process([PY, "-c", script], cwd=tmp_path, env=ENV, cancel=cancel, on_line=on_line)
    assert result.cancelled
    grandchild = int(marker.read_text())
    deadline = time.monotonic() + 5
    while pid_alive(grandchild) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not pid_alive(grandchild)


@pytest.mark.skipif(os.name == "nt", reason="process reaping differs on Windows")
def test_keyboard_interrupt_kills_child(tmp_path: Path) -> None:
    started: list[int] = []

    def on_start(pid: int) -> None:
        started.append(pid)

    def boom(line: str) -> None:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        run_process(
            [PY, "-c", "import time; print('x', flush=True); time.sleep(60)"],
            cwd=tmp_path,
            env=ENV,
            on_line=boom,
            on_start=on_start,
        )
    deadline = time.monotonic() + 5
    while pid_alive(started[0]) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not pid_alive(started[0])


def test_missing_command() -> None:
    with pytest.raises(LaunchError, match="not found"):
        resolve_launch(["definitely-not-a-real-tool-xyz"])


def test_pid_alive_edges() -> None:
    assert pid_alive(os.getpid())
    assert not pid_alive(0)
    kill_tree(2**22 + 12345)


@pytest.mark.skipif(os.name == "nt", reason="posix pty")
def test_pty_gives_child_a_terminal(tmp_path: Path) -> None:
    result = run_process(
        [PY, "-c", "import sys; print('tty' if sys.stdout.isatty() else 'pipe')"],
        cwd=tmp_path,
        env=ENV,
        use_pty=True,
    )
    assert result.output.strip() == "tty"
    piped = run_quick(
        [PY, "-c", "import sys; print('tty' if sys.stdout.isatty() else 'pipe')"], tmp_path
    )
    assert piped.output.strip() == "pipe"


def test_pty_rejects_stdin_prompt(tmp_path: Path) -> None:
    with pytest.raises(LaunchError):
        run_process([PY, "-c", "pass"], cwd=tmp_path, env=ENV, stdin="x", use_pty=True)


def write_npm_shim(folder: Path, name: str) -> Path:
    script = folder / "node_modules" / "pkg" / "cli.js"
    script.parent.mkdir(parents=True)
    script.write_text("", encoding="utf-8")
    shim = folder / name
    shim.write_text(
        "@ECHO off\r\nGOTO start\r\n:start\r\nSETLOCAL\r\n"
        'endLocal & goto #_undefined_# 2>NUL || title %COMSPEC% & "%_prog%"  '
        '"%dp0%\\node_modules\\pkg\\cli.js" %*\r\n',
        encoding="utf-8",
    )
    shim.chmod(0o755)
    return script


def test_npm_shim_is_resolved_to_node_script(tmp_path: Path) -> None:
    script = write_npm_shim(tmp_path, "tool.cmd")
    assert npm_shim_script(tmp_path / "tool.cmd") == script.resolve()
    node = tmp_path / ("node.exe" if os.name == "nt" else "node")
    node.write_text("", encoding="utf-8")
    node.chmod(0o755)
    launch = resolve_launch(["tool.cmd", "a b", 'q"x'], path=str(tmp_path), windows=True)
    assert launch[1:] == [str(script.resolve()), "a b", 'q"x']


def test_plain_batch_shim_refuses_metacharacters(tmp_path: Path) -> None:
    shim = tmp_path / "tool.cmd"
    shim.write_text("@echo off\r\nsomething.exe %*\r\n", encoding="utf-8")
    shim.chmod(0o755)
    assert resolve_launch(["tool.cmd", "safe words"], path=str(tmp_path), windows=True)[1:] == [
        "safe words"
    ]
    for evil in ('a" & calc', "100%", "a|b", "line\nbreak"):
        with pytest.raises(LaunchError, match="metacharacters"):
            resolve_launch(["tool.cmd", evil], path=str(tmp_path), windows=True)
    assert npm_shim_script(tmp_path / "missing.cmd") is None


def test_kill_is_fast(tmp_path: Path) -> None:
    started = time.monotonic()
    result = run_process(
        [PY, "-c", "import time; time.sleep(60)"],
        cwd=tmp_path,
        env=ENV,
        total_timeout_s=0.5,
        idle_timeout_s=30,
    )
    assert result.timed_out
    assert time.monotonic() - started < 2.5


def test_orphaned_background_process_does_not_block_completion(tmp_path: Path) -> None:
    script = (
        "import subprocess, sys\n"
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "print('result ready', flush=True)\n"
    )
    started = time.monotonic()
    result = run_process([PY, "-c", script], cwd=tmp_path, env=ENV, idle_timeout_s=20)
    assert result.exit_code == 0
    assert not result.idle_timed_out
    assert "result ready" in result.output
    assert time.monotonic() - started < 10


def test_last_line_without_newline_is_kept(tmp_path: Path) -> None:
    result = run_process(
        [PY, "-c", "import sys; sys.stdout.write('a\\nno newline')"], cwd=tmp_path, env=ENV
    )
    assert result.output.endswith("no newline")


def test_capture_keeps_the_tail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import timonel.proc as proc

    monkeypatch.setattr(proc, "MAX_CAPTURE", 50)
    result = run_process(
        [PY, "-c", "print('\\n'.join(f'line {i}' for i in range(100)))"], cwd=tmp_path, env=ENV
    )
    assert "line 99" in result.output
    assert "line 0\n" not in result.output
    assert len(result.output) <= 60


def test_which_ignores_current_directory_on_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import timonel.proc as proc

    planted = tmp_path / "evil.cmd"
    planted.write_text("@echo off\r\n", encoding="utf-8")
    planted.chmod(0o755)
    elsewhere = tmp_path / "bin"
    elsewhere.mkdir()
    monkeypatch.setattr(proc.shutil, "which", lambda command, path=None: str(planted))
    assert proc.which("evil", path=str(elsewhere), windows=True) is None
    monkeypatch.setattr(
        proc.shutil, "which", lambda command, path=None: str(elsewhere / "good.exe")
    )
    assert proc.which("good", path=str(elsewhere), windows=True) == str(elsewhere / "good.exe")
    assert proc.which(str(planted), path=str(elsewhere), windows=True) == str(
        elsewhere / "good.exe"
    )
