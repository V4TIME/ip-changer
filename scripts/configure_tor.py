#!/usr/bin/env python3
"""Add or remove IP Changer's managed control-port block in a torrc file.

Output is machine-readable, one ``KEY=value`` per line.
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
BOOL_RE = {
    "CookieAuthentication": re.compile(r"^\s*CookieAuthentication\s+([^\s#]+)", re.I),
    "CookieAuthFileGroupReadable": re.compile(
        r"^\s*CookieAuthFileGroupReadable\s+([^\s#]+)", re.I
    ),
}


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


def boolean_addition(lines: list[str], name: str) -> str | None:
    """Return a directive when *name* is absent or not already enabled."""

    value = uncommented_value(lines, BOOL_RE[name])
    if value is None:
        return f"{name} 1"
    if value.strip().lower() in {"1", "yes", "true", "on"}:
        return None
    return f"{name} 1"


def backup_path(path: Path) -> Path:
    stamp = time.strftime("%Y%m%d%H%M%S")
    candidate = path.with_name(f"{path.name}.ipchanger.bak.{stamp}")
    counter = 1
    while candidate.exists():
        candidate = path.with_name(f"{path.name}.ipchanger.bak.{stamp}.{counter}")
        counter += 1
    return candidate


def tcp_port(value: str | None) -> int | None:
    if not value or value == "0" or value.startswith("unix:"):
        return None
    candidate = value.rsplit(":", 1)[1] if ":" in value else value
    try:
        port = int(candidate)
    except ValueError:
        return None
    return port if 1 <= port <= 65535 else None


def is_tcp_endpoint(value: str | None) -> bool:
    return tcp_port(value) is not None


def parse_port(value: str | None, default: int) -> int:
    return tcp_port(value) or default


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
        backup = backup_path(path)
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
    if not is_tcp_endpoint(control_value):
        additions.append(f"ControlPort {control_port}")
    if socks_value is not None and not is_tcp_endpoint(socks_value):
        additions.append(f"SocksPort {socks_port}")
    for name in ("CookieAuthentication", "CookieAuthFileGroupReadable"):
        directive = boolean_addition(lines, name)
        if directive:
            additions.append(directive)

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

    backup = backup_path(path)
    try:
        shutil.copy2(path, backup)
        path.write_text(candidate, encoding="utf-8")
    except OSError as exc:
        print(f"ERROR=cannot write {path}: {exc}", file=sys.stderr)
        return 1
    print(f"BACKUP={backup}")

    tor = shutil.which("tor")
    if tor:
        try:
            result = subprocess.run(
                [tor, "--verify-config", "-f", str(path)],
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            result = None
            print(f"ERROR=could not validate torrc: {exc}", file=sys.stderr)
        if result is None or result.returncode != 0:
            try:
                shutil.copy2(backup, path)
            except OSError:
                pass
            if result is not None:
                print("ERROR=tor rejected the new torrc; original restored", file=sys.stderr)
                print((result.stderr or result.stdout).strip(), file=sys.stderr)
            else:
                print("ERROR=original torrc restored", file=sys.stderr)
            return 1
    print("OK=1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
