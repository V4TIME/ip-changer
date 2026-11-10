# Review: `techchipnet/ip-changer`

This document records what I found while studying the upstream project before
building this alternative. The review is based on commit `6376a6a`
("Video Demo included") cloned on 2026-11-10.

Upstream files reviewed:

- `README.md`
- `ip-changer.sh` (installer + foreground loop)
- `setup.sh` (installer + systemd service)
- `change_tor_ip.sh` (the command systemd restarts)
- `change-tor-ip.service`

## What upstream does

The project is a small Bash wrapper around Tor's control port:

1. Install Tor and append `ControlPort 9051`,
   `CookieAuthentication 1`, and `CookieAuthFileGroupReadable 1` to torrc.
2. Read `/var/run/tor/control.authcookie` as hex with `xxd`.
3. Pipe `AUTHENTICATE <cookie>`, `SIGNAL NEWNYM`, and `QUIT` into
   `nc 127.0.0.1 9051`.
4. Ask `https://check.torproject.org/api/ip` through the Tor SOCKS port and
   print/log the returned `.IP`.
5. In the service variant, restart the one-shot script every
   `RestartSec=<interval>` seconds with `Restart=always`.

It has no control panel, no daemon state, no verification logic beyond printing
a number, and no tests.

## Bugs and dangerous behaviour

Severity is my assessment: **Critical** can lead to root code execution or a
broken Tor install; **High** breaks the advertised feature; **Medium** produces
misleading results or poor operation; **Low** is documentation/quality.

| # | Severity | Where | Problem | Effect / fix |
|---|----------|-------|---------|--------------|
| 1 | Critical | `change-tor-ip.service` (whole file), `setup.sh:99-109` | The service has no `User=`, so it runs as root. `ExecStart` points to a script in the invoking user's home (`/home/$USER/change_tor_ip.sh`). The user can modify or replace that file after installation. | Any compromise of that user becomes root code execution on the next restart. The replacement runs as the target user with a fixed, read-only install directory and `NoNewPrivileges=true`. |
| 2 | High | `change_tor_ip.sh:11` | `>> HOME/tor_ip_log.txt` is missing `$`. `HOME` is a literal filename, not the home directory. | The log is written to `/HOME/tor_ip_log.txt` (relative to the service's working directory) or the write fails. Fixed by using a state directory chosen by the application. |
| 3 | High | `ip-changer.sh:104`, `README.md:29` | The scripts pipe into `nc`, but `nc`/netcat is never installed and is not listed as a prerequisite. The README instead lists `fq`, which is not used. | On a minimal install the control command fails immediately. The replacement implements the control protocol in Python and needs no `nc`, `xxd`, `jq`, or `curl`. |
| 4 | High | `ip-changer.sh:112-120` | The interval is read with no validation. Empty uses 10, but `abc`, `0`, `-1`, or `1` are accepted. | `sleep abc` exits, making the loop spin and hammer the control port; intervals below 10 s are silently ignored by Tor because NEWNYM is rate-limited. The replacement rejects anything below 10 s. |
| 5 | High | `ip-changer.sh:106` | No `--fail`, `--max-time`, or retry on the verification request. It parses `.IP` with `jq` even when the response is HTML or an error. | The log can contain `null`, a stale IP, or the script hangs on a dead network. The replacement has timeouts, retries/fallback endpoints, and validates the IP. |
| 6 | High | `change_tor_ip.sh:4-10` | No error handling and no wait for a new circuit. It sends NEWNYM and immediately asks check.torproject.org. | The check can see the old circuit or fail, and the script still exits successfully. The replacement waits and compares against the previous IP, with a timeout. |
| 7 | High | `ip-changer.sh:82-98`, `setup.sh:75-91` | torrc is edited with `grep`/append and never backed up or validated. `grep "^ControlPort 9051"` misses leading whitespace, comments, and an existing ControlPort on another port. | Duplicate or conflicting directives can prevent Tor from starting. The replacement removes/re-adds a marked block, backs up torrc, detects existing ports, and runs `tor --verify-config`. |
| 8 | High | `setup.sh:93-110` | Setting the rotation interval by changing `RestartSec` on a `Restart=always` service is a misuse of systemd's restart delay. | `RestartSec` is not a scheduler; it also interacts with `StartLimitBurst`/`StartLimitIntervalSec`. At short intervals the unit can enter the failed state and stop rotating. The replacement has a real scheduler in one long-running process. |
| 9 | Medium | `change-tor-ip.service:3-9` | `After=network.target` only. No dependency on Tor, no readiness check, no `User=`, no `Group=`, no hardening. | On boot the first run often fails before Tor's control port exists. The replacement retries on failure and its installer waits for the control port; the service runs as the invoking user. |
| 10 | Medium | `ip-changer.sh:24-27`, `70-75` | The foreground script requires root but then calls `sudo` again. Under `sudo`, `$USER` is commonly `root`, so the code adds `root` to the Tor group rather than the person who invoked it. | The intended user never gets cookie access. The replacement uses `SUDO_USER`/`--user` and adds the correct account. |
| 11 | Medium | `ip-changer.sh:43-52`, `setup.sh:36-57` | Fedora is configured with `TOR_GROUP="tor"`, but Fedora's Tor user/group is normally `toranon`. | The cookie is not group-readable by the user, so authentication fails. The replacement discovers an existing group (`debian-tor`, `tor`, `toranon`, `_tor`). |
| 12 | Medium | `ip-changer.sh:102`, `change_tor_ip.sh:4` | The cookie path is hard-coded to `/var/run/tor/control.authcookie`. | It is wrong on installations using a different `CookieAuthFile`, a Tor Browser bundle, or some containers. The replacement discovers the path from `PROTOCOLINFO` and allows an override. |
| 13 | Medium | `ip-changer.sh:117-120` | `set -e` is used, but a failed check exits the whole loop; there is no retry, backoff, lock, or status. | A temporary network failure kills the tool. Running two copies can race on the control port. The replacement records failures, keeps scheduling, and takes a file lock around rotations. |
| 14 | Medium | `setup.sh:97-104` | `sed -i` mutates the service file inside the cloned repository and substitutes `$USER` into paths. Re-running is not idempotent and `sed` delimiters break on unusual paths. | The working tree is dirty after install and a second run can produce different units. The replacement renders a template to `/etc/systemd/system` without modifying the repository. |
| 15 | Medium | `README.md:71-99` | The README says the service changes the IP at the chosen interval, but `RestartSec` is edited only in the unit and the script itself is a one-shot. There is no verification that the new IP differs or that `IsTor` is true. | Users can believe rotation happened when the same exit was reused or the SOCKS request was not using Tor. The replacement checks `IsTor` and reports unchanged exits. |
| 16 | Low | `README.md:29` | Prerequisites list `fq`, but the code uses `xxd`, `nc`, `curl`, `jq` and `systemctl`. | Installation fails on a clean system. The replacement's only runtime dependency is Python 3 (plus Tor). |
| 17 | Low | `README.md:101-111` | Uninstall removes only the unit and `/home/$USER/change_tor_ip.sh`; it leaves the appended torrc lines and the user in the Tor group. | Reinstalling can create duplicate torrc blocks. The replacement removes its marked block and offers `--purge`. |
| 18 | Low | Repository root | No `LICENSE` file (README claims MIT), no CI, no tests, no changelog. | Hard to audit or contribute. The replacement ships all of these. |
| 19 | Low | `ip-changer.sh` / `setup.sh` | Two copies of the installer logic have already drifted (the service path, group handling, and prompts differ). | Fixes must be made twice. The replacement keeps one installer and one Python implementation. |
| 20 | Low | `ip-changer.sh:9-21`, `setup.sh:9-21` | The banner is duplicated and has `set -e` after the banner. Cosmetic, but it makes the scripts harder to read. | Not harmful; cleaned up in the replacement. |

## What upstream is missing

The biggest gap is not a line-level bug: the tool does not actually route any
application through Tor. It only sends NEWNYM and prints the exit IP. A user who
does not configure a browser or shell to use SOCKS `127.0.0.1:9050` will keep
their normal IP. This is why the replacement says explicitly: **it rotates the
Tor exit IP; it does not change your ISP-assigned IP.**

Missing operational features:

1. **Control panel** — no status screen, no start/stop/restart, no rotate-now,
   no interval editor, no log viewer, no diagnostics. This project adds a
   curses panel that works in a normal terminal and Termux.
2. **A real scheduler** — one long-running process instead of relying on
   systemd restart behaviour.
3. **State** — last/previous IP, last success/error, next rotation, counters,
   and durable state across restarts.
4. **Structured logs** — rotated JSONL logs instead of a single append file
   (which upstream cannot even locate because of bug #2).
5. **Verification** — timeouts, fallback endpoints, IP validation, comparison
   with the previous exit, and an explicit `IsTor` check.
6. **Authentication discovery** — `PROTOCOLINFO`, cookie path discovery,
   password and null auth, clear permission errors.
7. **Configuration validation** — minimum 10 s interval, port ranges, check
   URLs, jitter, timeouts.
8. **Termux support** — no systemd, so upstream's service does not work there.
   The replacement detects Termux, uses `termux-services` when present, and
   otherwise provides a boot-script path/manual commands.
9. **Portability** — Debian, Ubuntu, Kali, Parrot, Arch, Fedora, openSUSE,
   Alpine, macOS (CLI) and Termux; correct group discovery.
10. **Safety** — no root service executing a user-writable script, no torrc
    damage, no `nc` dependency, no unvalidated sleep, no silent duplicate
    rotations.
11. **Diagnostics** — `ipchanger doctor` checks Python, Tor, control auth,
    cookie readability, SOCKS, systemd/daemon state and the live exit IP.
12. **Tests and CI** — protocol, SOCKS5, HTTP, config, scheduler, rotator and
    CLI tests run without a live Tor daemon.
13. **Uninstall** — removes the service, files, wrapper and managed torrc block;
    `--purge` removes config and history.

## Design decision: why not just patch upstream?

The upstream scripts are easy to read and are a useful first demonstration of
`SIGNAL NEWNYM`. Patching them would still leave a root service running a
user-writable shell script, a shell dependency on `nc`/`xxd`/`jq`, and no
testable state machine. This repository keeps the same core idea but moves the
protocol, scheduler, verification, and UI into a small Python package with
tests, so the next bug fix lands in one place.

## Reproduce the upstream findings

```bash
git clone https://github.com/techchipnet/ip-changer.git /tmp/upstream
cd /tmp/upstream
nl -ba change_tor_ip.sh
nl -ba change-tor-ip.service
nl -ba setup.sh | sed -n '90,115p'
nl -ba ip-changer.sh | sed -n '100,120p'
```
