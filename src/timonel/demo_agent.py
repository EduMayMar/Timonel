import json
import sys
from pathlib import Path

FIXES = {
    "fast": "def add(a, b):\n    return abs(a - b)\n",
    "strong": "def add(a, b):\n    return a + b\n",
}


def main() -> int:
    role = sys.argv[1]
    prompt = sys.stdin.read()
    if "TIMONEL_REVIEW" in prompt:
        print(json.dumps({"approve": True, "issues": []}))
        return 0
    if "read-only task" in prompt:
        print("calc.py defines add(a, b), which should return the sum of a and b.")
        return 0
    Path("calc.py").write_text(FIXES[role], encoding="utf-8")
    print(f"demo-{role}: rewrote add() in calc.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
