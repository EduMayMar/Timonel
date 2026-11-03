import json
import os
import re
import sys
import time
from pathlib import Path

DIRECTIVE = re.compile(r"\[\[(\w+)(?: (\w+))?:([^\]]*)\]\]")


def directives(prompt: str, name: str, kind: str) -> list[str]:
    found = []
    for verb, who, value in DIRECTIVE.findall(prompt):
        if verb == kind and who in ("", name, "all"):
            found.append(value)
    return found


def log_call(name: str, argv: list[str], prompt: str) -> None:
    state = os.environ.get("FAKE_STATE_DIR")
    if not state:
        return
    record = {
        "name": name,
        "argv": argv,
        "cwd": os.getcwd(),
        "prompt": prompt,
        "env": {
            k: v
            for k, v in os.environ.items()
            if k.startswith(("ANTHROPIC_", "DEEPSEEK", "FAKE_SECRET"))
        },
        "isatty": sys.stdout.isatty(),
    }
    with Path(state, "calls.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")


def extract_prompt(argv: list[str]) -> str:
    if "--output-format" in argv or "exec" in argv or "-" in argv:
        return sys.stdin.read()
    for flag in ("-p", "--prompt"):
        if flag in argv:
            index = argv.index(flag)
            if index + 1 < len(argv):
                return argv[index + 1]
    if not sys.stdin.isatty():
        return sys.stdin.read()
    return ""


def writing(argv: list[str]) -> bool:
    joined = " ".join(argv)
    return any(
        marker in joined
        for marker in ("acceptEdits", "workspace-write", "accept-edits", "shell(git push)")
    )


def mode_for(name: str, prompt: str) -> str:
    chosen = directives(prompt, name, "mode")
    if chosen:
        return chosen[-1]
    return os.environ.get(f"FAKE_MODE_{name.upper()}", os.environ.get("FAKE_MODE", "success"))


def answer_for(name: str, prompt: str) -> str:
    if "TIMONEL_CLASSIFY" in prompt:
        return os.environ.get(
            "FAKE_CLASSIFICATION",
            json.dumps(
                {
                    "complexity": 2,
                    "kind": "edit",
                    "risk": "low",
                    "needs_write": True,
                    "estimated_files": 1,
                }
            ),
        )
    if "TIMONEL_REVIEW" in prompt:
        return os.environ.get("FAKE_REVIEW", json.dumps({"approve": True, "issues": []}))
    if "TIMONEL_PLAN" in prompt:
        return os.environ.get("FAKE_PLAN", json.dumps({"subtasks": []}))
    said = directives(prompt, name, "say")
    return said[-1] if said else os.environ.get("FAKE_SAY", f"{name} done")


def apply_edits(name: str, prompt: str) -> None:
    extra = [os.environ["FAKE_EDIT"]] if os.environ.get("FAKE_EDIT") else []
    for edit in directives(prompt, name, "edit") + extra:
        path, _, content = edit.partition("=")
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def apply_links(name: str, prompt: str) -> None:
    for link in directives(prompt, name, "link"):
        path, _, target = link.partition("=")
        os.symlink(target, path)


def emit(argv: list[str], text: str, error: bool = False, cost: float = 0.0123) -> None:
    if "--output-format" in argv and argv[argv.index("--output-format") + 1] == "json":
        payload = {
            "type": "result",
            "subtype": "error_during_execution" if error else "success",
            "is_error": error,
            "result": text,
            "total_cost_usd": cost,
            "usage": {"input_tokens": 100, "output_tokens": 20},
        }
        print(json.dumps(payload))
    elif "--json" in argv:
        print(json.dumps({"type": "thread.started", "thread_id": "t1"}))
        print(
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {"id": "i1", "type": "agent_message", "text": text},
                }
            )
        )
        kind = "turn.failed" if error else "turn.completed"
        print(json.dumps({"type": kind, "usage": {"input_tokens": 50, "output_tokens": 10}}))
    else:
        print(text)


def main() -> int:
    name = sys.argv[1]
    argv = sys.argv[2:]
    if argv[:1] == ["--version"]:
        print(f"{name} 9.9.9 (fake)")
        return 0
    if argv[:2] in (["auth", "status"], ["login", "status"]):
        if os.environ.get(f"FAKE_UNAUTH_{name.upper()}"):
            print("Not logged in")
            return 1
        print(json.dumps({"loggedIn": True}) if name == "claude" else "Logged in")
        return 0
    if argv[:1] == ["models"]:
        print("fake-pro\nfake-flash")
        return 0
    prompt = extract_prompt(argv)
    log_call(name, argv, prompt)
    mode = mode_for(name, prompt)
    if mode == "rate_limit":
        print("Error: 429 Too Many Requests - rate limit exceeded", file=sys.stderr)
        return 1
    if mode == "hang":
        time.sleep(3600)
        return 0
    if mode == "silent_hang":
        print("thinking...", flush=True)
        time.sleep(3600)
        return 0
    if mode == "garbage":
        print("\x00\x01 ~~~ not json at all ~~~ \x1b[31mred\x1b[0m")
        return 0
    if mode == "partial":
        print('{"type": "result", "result": "half')
        return 1
    if mode == "invalid_json":
        print('{"result": ')
        return 0
    if mode == "crash":
        print("Traceback: boom", file=sys.stderr)
        return 2
    if mode == "need_tty" and not sys.stdout.isatty():
        time.sleep(3600)
        return 0
    if writing(argv):
        apply_edits(name, prompt)
        apply_links(name, prompt)
    emit(argv, answer_for(name, prompt), error=(mode == "error"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
