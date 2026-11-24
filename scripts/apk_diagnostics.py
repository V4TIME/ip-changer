"""Turn a verbose Buildozer log into the part a human can act on.

Buildozer prints every environment variable when a command fails, which buries
the actual traceback. This keeps the error lines plus the tail of the real
output, and is used by `.github/workflows/apk.yml` to publish a readable
diagnostic comment. Pure standard library so it runs anywhere.

Usage: python3 scripts/apk_diagnostics.py noteapp/build.log > diag.txt
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
ENV_DUMP = re.compile(r"^#\s{2,}[A-Za-z_][A-Za-z0-9_]* = ")
INTERESTING = re.compile(
    r"(Traceback \(most recent|Error|ERROR|error:|FAILED|No such file|not found|Exception|Command failed)"
)

MAX_CHARS = 55_000


def clean(raw_lines: list[str]) -> list[str]:
    lines: list[str] = []
    for raw in raw_lines:
        line = ANSI.sub("", raw).rstrip()
        if ENV_DUMP.match(line):
            continue
        lines.append(line)
    return lines


def summarise(text: str, max_chars: int = MAX_CHARS) -> str:
    lines = clean(text.splitlines())
    errors = [line for line in lines if INTERESTING.search(line)]
    body = [line for line in lines if line.strip()]
    out = ["== error lines ==", *errors[-80:], "", "== tail ==", *body[-120:]]
    result = "\n".join(out)
    if len(result) > max_chars:
        result = result[:max_chars] + "\n... truncated"
    return result


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    path = Path(argv[1])
    if not path.exists():
        print(f"no log at {path}", file=sys.stderr)
        return 1
    print(summarise(path.read_text(errors="replace")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
