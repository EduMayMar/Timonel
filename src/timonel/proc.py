from __future__ import annotations

import contextlib
import os
import queue
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any, Protocol

IS_WINDOWS = os.name == "nt"
CMD_UNSAFE = re.compile(r'[\r\n"%!^&|<>()]')
NPM_SHIM_TARGET = re.compile(r'"%(?:~?dp0%?)\\?([^"%]+\.(?:js|cjs|mjs))"', re.IGNORECASE)
MAX_CAPTURE = 4_000_000
ORPHAN_GRACE_S = 2.0

OnLine = Callable[[str], None]


class LaunchError(Exception):
    pass


@dataclass(frozen=True)
class ProcResult:
    exit_code: int | None
    output: str
    duration_s: float
    timed_out: bool = False
    idle_timed_out: bool = False
    cancelled: bool = False
    pid: int | None = None


class Handle(Protocol):
    pid: int

    def poll(self) -> int | None: ...


def folder_key(path: str | Path) -> str:
    return os.path.normcase(str(Path(path).absolute()))


def path_entries(path: str | None) -> set[str]:
    raw = path if path is not None else os.environ.get("PATH", "")
    return {folder_key(entry) for entry in raw.split(os.pathsep) if entry}


def which(command: str, path: str | None = None, windows: bool = IS_WINDOWS) -> str | None:
    found = shutil.which(command, path=path)
    explicit = Path(command).is_absolute() or os.sep in command or "/" in command
    if found is None or not windows or explicit:
        return found
    return found if folder_key(Path(found).absolute().parent) in path_entries(path) else None


def npm_shim_script(shim: Path) -> Path | None:
    try:
        text = shim.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    match = NPM_SHIM_TARGET.search(text)
    if not match:
        return None
    script = (shim.parent / match.group(1).replace("\\", os.sep)).resolve()
    return script if script.is_file() else None


def resolve_launch(
    argv: Sequence[str], path: str | None = None, windows: bool = IS_WINDOWS
) -> list[str]:
    found = which(argv[0], path)
    if found is None:
        raise LaunchError(f"{argv[0]!r} not found on PATH")
    executable = Path(found)
    rest = list(argv[1:])
    if not windows or executable.suffix.lower() not in (".cmd", ".bat"):
        return [str(executable), *rest]
    script = npm_shim_script(executable)
    node = which("node", path)
    if script is not None and node is not None:
        return [node, str(script), *rest]
    unsafe = [arg for arg in rest if CMD_UNSAFE.search(arg)]
    if unsafe:
        raise LaunchError(
            f"{executable.name} is a batch shim; refusing to pass arguments with cmd.exe "
            "metacharacters. Set prompt_via: stdin for this agent or install a native binary."
        )
    return [str(executable), *rest]


def kill_tree(pid: int, running: Callable[[], bool] | None = None) -> None:
    if IS_WINDOWS:
        subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(pid)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return
    try:
        os.killpg(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    deadline = time.monotonic() + 3
    while running is not None and running() and time.monotonic() < deadline:
        time.sleep(0.02)
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pid, signal.SIGKILL)


def pump_fd(fd: int, lines: queue.Queue[str | None]) -> None:
    buffer = b""
    while True:
        try:
            chunk = os.read(fd, 65536)
        except OSError:
            break
        if not chunk:
            break
        buffer += chunk
        *complete, buffer = buffer.split(b"\n")
        for line in complete:
            lines.put(line.decode("utf-8", errors="replace") + "\n")
    if buffer:
        lines.put(buffer.decode("utf-8", errors="replace"))
    lines.put(None)


def feed_stdin(stream: IO[bytes] | None, data: str | None) -> None:
    if stream is None:
        return
    with contextlib.suppress(OSError, ValueError):
        if data is not None:
            stream.write(data.encode("utf-8"))
        stream.close()


def popen_kwargs() -> dict[str, Any]:
    if IS_WINDOWS:
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        return {"creationflags": flags | getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    return {"start_new_session": True}


def spawn_pipe(
    argv: list[str], cwd: Path, env: Mapping[str, str], stdin: str | None
) -> tuple[subprocess.Popen[bytes], queue.Queue[str | None]]:
    child = subprocess.Popen(
        argv,
        cwd=cwd,
        env=dict(env),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        **popen_kwargs(),
    )
    lines: queue.Queue[str | None] = queue.Queue()
    if child.stdout is None:
        raise LaunchError("could not capture output")
    threading.Thread(target=pump_fd, args=(child.stdout.fileno(), lines), daemon=True).start()
    threading.Thread(target=feed_stdin, args=(child.stdin, stdin), daemon=True).start()
    return child, lines


def spawn_posix_pty(
    argv: list[str], cwd: Path, env: Mapping[str, str]
) -> tuple[subprocess.Popen[bytes], queue.Queue[str | None]]:
    import pty

    master, slave = pty.openpty()
    try:
        child = subprocess.Popen(
            argv,
            cwd=cwd,
            env={**env, "TERM": env.get("TERM", "xterm-256color")},
            stdin=slave,
            stdout=slave,
            stderr=slave,
            start_new_session=True,
        )
    finally:
        os.close(slave)
    lines: queue.Queue[str | None] = queue.Queue()

    def pump_and_close() -> None:
        pump_fd(master, lines)
        with contextlib.suppress(OSError):
            os.close(master)

    threading.Thread(target=pump_and_close, daemon=True).start()
    return child, lines


class WinPtyHandle:
    def __init__(self, process: Any) -> None:
        self.process = process
        self.pid = int(process.pid)

    def poll(self) -> int | None:
        if self.process.isalive():
            return None
        status = self.process.exitstatus
        return int(status) if status is not None else None


def spawn_windows_pty(
    argv: list[str], cwd: Path, env: Mapping[str, str]
) -> tuple[WinPtyHandle, queue.Queue[str | None]]:
    try:
        from winpty import PtyProcess
    except ImportError as error:
        raise LaunchError("this agent needs a pseudo-terminal: pip install pywinpty") from error
    process = PtyProcess.spawn(
        subprocess.list2cmdline(argv), cwd=str(cwd), env=dict(env), dimensions=(50, 200)
    )
    lines: queue.Queue[str | None] = queue.Queue()

    def pump() -> None:
        buffer = ""
        while True:
            try:
                chunk = process.read(65536)
            except EOFError:
                break
            except OSError:
                break
            if not chunk:
                if not process.isalive():
                    break
                time.sleep(0.05)
                continue
            buffer += chunk
            *complete, buffer = buffer.split("\n")
            for line in complete:
                lines.put(line + "\n")
        if buffer:
            lines.put(buffer)
        lines.put(None)

    threading.Thread(target=pump, daemon=True).start()
    return WinPtyHandle(process), lines


def spawn(
    argv: list[str], cwd: Path, env: Mapping[str, str], stdin: str | None, use_pty: bool
) -> tuple[Handle, queue.Queue[str | None]]:
    if use_pty and stdin is not None:
        raise LaunchError("pseudo-terminal agents must receive the prompt as an argument")
    if use_pty and IS_WINDOWS:
        return spawn_windows_pty(argv, cwd, env)
    if use_pty and sys.platform != "win32":
        return spawn_posix_pty(argv, cwd, env)
    return spawn_pipe(argv, cwd, env, stdin)


def wait_exit(handle: Handle, grace_s: float) -> int | None:
    deadline = time.monotonic() + grace_s
    while (code := handle.poll()) is None and time.monotonic() < deadline:
        time.sleep(0.02)
    return code


def remember(captured: list[str], line: str, size: int) -> int:
    captured.append(line)
    size += len(line)
    while size > MAX_CAPTURE and len(captured) > 1:
        size -= len(captured.pop(0))
    return size


def pump_until_done(
    handle: Handle,
    lines: queue.Queue[str | None],
    captured: list[str],
    started: float,
    total_timeout_s: float,
    idle_timeout_s: float,
    cancel: threading.Event | None,
    on_line: OnLine | None,
) -> str:
    size = 0
    last_activity = started
    exited_at: float | None = None
    while True:
        try:
            line = lines.get(timeout=0.1)
        except queue.Empty:
            line = ""
        now = time.monotonic()
        if line is None:
            return "finished"
        if line:
            last_activity = now
            size = remember(captured, line, size)
            if on_line is not None:
                on_line(line)
        elif exited_at is None and handle.poll() is not None:
            exited_at = now
        elif exited_at is not None and now - max(exited_at, last_activity) > ORPHAN_GRACE_S:
            return "finished"
        if cancel is not None and cancel.is_set():
            return "cancelled"
        if now - started > total_timeout_s:
            return "timed_out"
        if now - last_activity > idle_timeout_s:
            return "idle_timed_out"


def run_process(
    argv: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
    stdin: str | None = None,
    use_pty: bool = False,
    total_timeout_s: float = 1800,
    idle_timeout_s: float = 300,
    cancel: threading.Event | None = None,
    on_line: OnLine | None = None,
    on_start: Callable[[int], None] | None = None,
) -> ProcResult:
    launch = resolve_launch(argv, env.get("PATH"))
    started = time.monotonic()
    handle, lines = spawn(launch, cwd, env, stdin, use_pty)
    captured: list[str] = []

    def running() -> bool:
        return handle.poll() is None

    try:
        if on_start is not None:
            on_start(handle.pid)
        ending = pump_until_done(
            handle, lines, captured, started, total_timeout_s, idle_timeout_s, cancel, on_line
        )
    except BaseException:
        kill_tree(handle.pid, running)
        raise
    if ending != "finished":
        kill_tree(handle.pid, running)
    exit_code = wait_exit(handle, 5 if ending == "finished" else 3)
    if exit_code is None:
        kill_tree(handle.pid, running)
        exit_code = wait_exit(handle, 2)
    while ending == "finished" and not lines.empty():
        rest = lines.get_nowait()
        if rest:
            captured.append(rest)
    if ending == "finished" and not IS_WINDOWS:
        kill_tree(handle.pid, running)
    return ProcResult(
        exit_code=exit_code,
        output="".join(captured),
        duration_s=time.monotonic() - started,
        timed_out=ending == "timed_out",
        idle_timed_out=ending == "idle_timed_out",
        cancelled=ending == "cancelled",
        pid=handle.pid,
    )


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if IS_WINDOWS:
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        process = kernel32.OpenProcess(0x1000, False, pid)
        if not process:
            return False
        code = ctypes.c_ulong()
        kernel32.GetExitCodeProcess(process, ctypes.byref(code))
        kernel32.CloseHandle(process)
        return code.value == 259
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def run_quick(
    argv: Sequence[str],
    cwd: Path | None = None,
    timeout_s: float = 20,
    env: Mapping[str, str] | None = None,
) -> ProcResult:
    return run_process(
        argv,
        cwd=cwd or Path.cwd(),
        env=env if env is not None else dict(os.environ),
        total_timeout_s=timeout_s,
        idle_timeout_s=timeout_s,
    )
