"""Turn a verbose Buildozer log into the part a human can act on.

Buildozer prints every environment variable when a command fails, and Cython
emits thousands of "performance hint" lines, which together bury the actual
error. This keeps the window around the failing Gradle/buildozer step plus the
error lines and the tail. Used by `.github/workflows/apk.yml` to publish a
readable diagnostic comment. Pure standard library.

Usage: python3 scripts/apk_diagnostics.py noteapp/build.log > diag.txt
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
ENV_DUMP = re.compile(r"^(#\s{2,}[A-Za-z_][A-Za-z0-9_]* = |export [A-Za-z_][A-Za-z0-9_]*=)")
NOISE = re.compile(
    r"(performance hint:"
    r"|Requirement already satisfied:"
    r"|^\s*━"
    r"|^\s*Downloading \S+ \("
    r"|^\s*\[?\?25[lh]\s*$)"
)
INTERESTING = re.compile(
    r"(Traceback \(most recent|Error|ERROR|error:|FAILED|No such file|not found"
    r"|Exception|Command failed|not a valid|Could not|Caused by)"
)
FAILURE = re.compile(
    r"(FAILURE: Build failed"
    r"|What went wrong"
    r"|BUILD FAILED in"
    r"|Execution failed for task"
    r"|A problem occurred"
    r"|> Task .*FAILED)"
)

MAX_CHARS = 55_000
WINDOW = 70


def clean(raw_lines: list[str]) -> list[str]:
    lines: list[str] = []
    for raw in raw_lines:
        line = ANSI.sub("", raw).rstrip()
        if ENV_DUMP.match(line):
            continue
        if NOISE.search(line):
            continue
        lines.append(line)
    return lines


def summarise(text: str, max_chars: int = MAX_CHARS) -> str:
    lines = clean(text.splitlines())
    body = [line for line in lines if line.strip()]

    out: list[str] = []
    hits = [index for index, line in enumerate(body) if FAILURE.search(line)]
    if hits:
        centre = hits[-1]
        start = max(0, centre - WINDOW)
        out += ["== failing step ==", *body[start : centre + WINDOW], ""]

    errors = [line for line in body if INTERESTING.search(line)]
    out += ["== error lines ==", *errors[-40:], "", "== tail ==", *body[-50:]]

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
