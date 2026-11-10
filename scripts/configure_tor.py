#!/usr/bin/env python3
"""Safely add IP Changer's control-port settings to a torrc file.

The installer runs this helper because editing torrc with grep/sed is where
many similar tools break: they append duplicate directives, miss an existing
ControlPort on another port, or leave Tor unable to start.

Output is intentionally machine-readable, one ``KEY=value`` per line, so the
shell installer can read the detected ports.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

BEGIN = "# BEGIN ip-changer managed block"
END = "# END ip-changer managed block"
CONTROL_RE = re.compile(r"^\s*ControlPort\s+([^\s#]+)", re.I)
SOCKS_RE = re.compile(r"^\s*SocksPort\s+([^\s#]+)", re.I)


def uncommented_value(lines: list[str], pattern: re.Pattern[str]) -> str | None:
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = pattern.match(line)
        if match:
            return match.group(1)
    return None


def remove_managed_block(lines: list[str]) -> list[str]:
    result: list[str] = []
    inside = False
    for line in lines:
        if line.strip() == BEGIN:
            inside = True
            continue
        if line.strip() == END:
            inside = False
            continue
        if not inside:
            result.append(line)
    return result


def parse_port(value: str | None, default: int) -> int:
    if not value:
        return default
    if value.startswith("unix:"):
        return default
    if value == "0":
        return default
    if ":" in value:
        value = value.rsplit(":", 1)[1]
    try:
        return int(value)
    except ValueError:
        return default


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("torrc", type=Path)
    parser.add_argument("--control-port", type=int, default=9051)
    parser.add_argument("--remove", action="store_true", help="remove the managed block")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    path = args.torrc
    if not path.exists():
        print(f"ERROR=torrc not found: {path}", file=sys.stderr)
        return 1
    try:
        original_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"ERROR=cannot read {path}: {exc}", file=sys.stderr)
        return 1

    original_lines = original_text.splitlines()
    lines = remove_managed_block(original_lines)
    control_value = uncommented_value(lines, CONTROL_RE)
    socks_value = uncommented_value(lines, SOCKS_RE)
    control_port = parse_port(control_value, args.control_port)
    socks_port = parse_port(socks_value, 9050)

    if args.remove:
        candidate = "\n".join(lines).rstrip() + "\n"
        changed = candidate != original_text.rstrip() + "\n"
        print(f"CONTROL_PORT={control_port}")
        print(f"SOCKS_PORT={socks_port}")
        print(f"CHANGED={1 if changed else 0}")
        if args.dry_run or not changed:
            return 0
        backup = path.with_name(f"{path.name}.ipchanger.bak.{time.strftime('%Y%m%d%H%M%S')}")
        try:
            shutil.copy2(path, backup)
            path.write_text(candidate, encoding="utf-8")
        except OSError as exc:
            print(f"ERROR=cannot write {path}: {exc}", file=sys.stderr)
            return 1
        print(f"BACKUP={backup}")
        print("OK=1")
        return 0

    additions: list[str] = []
    if control_value is None:
        additions.append(f"ControlPort {control_port}")
    elif control_value == "0":
        additions.append(f"ControlPort {control_port}")
    if socks_value == "0":
        additions.append(f"SocksPort {socks_port}")
    additions.extend(
        [
            "CookieAuthentication 1",
            "CookieAuthFileGroupReadable 1",
        ]
    )

    # Avoid appending a block that is byte-for-byte what is already there.
    prefix = list(lines)
    if prefix and prefix[-1].strip() != "":
        prefix.append("")
    candidate_lines = prefix + [BEGIN, *additions, END]
    candidate = "\n".join(candidate_lines).rstrip() + "\n"
    changed = candidate != original_text.rstrip() + "\n"
    print(f"CONTROL_PORT={control_port}")
    print(f"SOCKS_PORT={socks_port}")
    print(f"CHANGED={1 if changed else 0}")

    if args.dry_run:
        print("--- planned torrc additions ---")
        print("\n".join(["", BEGIN, *additions, END]))
        return 0

    if not changed:
        return 0

    backup = path.with_name(f"{path.name}.ipchanger.bak.{time.strftime('%Y%m%d%H%M%S')}")
    try:
        shutil.copy2(path, backup)
        path.write_text(candidate, encoding="utf-8")
    except OSError as exc:
        print(f"ERROR=cannot write {path}: {exc}", file=sys.stderr)
        return 1
    print(f"BACKUP={backup}")

    tor = shutil.which("tor")
    if tor:
        result = subprocess.run(
            [tor, "--verify-config", "-f", str(path)],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        if result.returncode != 0:
            try:
                shutil.copy2(backup, path)
            except OSError:
                pass
            print("ERROR=tor rejected the new torrc; original restored", file=sys.stderr)
            print((result.stderr or result.stdout).strip(), file=sys.stderr)
            return 1
    print("OK=1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
