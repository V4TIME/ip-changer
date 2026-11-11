# IP Changer

**Rotate the Tor exit IP from a terminal or Termux control panel.**

IP Changer is a small, dependency-light Python tool that asks a local Tor
daemon for fresh circuits at a chosen interval, verifies the new exit IP, and
keeps a live status/log history. It ships a full-screen control panel as well as
scriptable CLI commands.

This repository is a from-scratch alternative to
[`techchipnet/ip-changer`](https://github.com/techchipnet/ip-changer). I studied
that project first; its bugs, missing features and lack of a control panel are
documented in [`docs/UPSTREAM_REVIEW.md`](docs/UPSTREAM_REVIEW.md).

**New here? Read the [step-by-step tutorial](docs/TUTORIAL.md)** — install,
panel keys, interval/logging settings, Termux and troubleshooting.

> **Important:** this rotates the **Tor exit IP**. It does not change your
> ISP-assigned address. An application only benefits if it sends its traffic
> through Tor's SOCKS port (normally `127.0.0.1:9050`), or if you use a
> system-wide Tor transparent proxy. Use it for privacy and research, not to
> evade bans, commit fraud, or break the law.

## Features

- **Control panel** — live exit IP, previous IP, countdown in seconds, latest
  log event, start/stop, rotate now, interval/jitter editor, logging and
  verification toggles, log viewer and diagnostics.
- **Real scheduler** — one long-running daemon with interval, optional jitter,
  graceful SIGTERM, and a `next_rotation` timestamp (not a systemd restart
  trick).
- **Verified rotations** — sends `SIGNAL NEWNYM`, waits for a new circuit,
  compares with the previous IP, validates the result, and checks `IsTor`.
- **Zero runtime dependencies** — Python 3 standard library only. No `curl`,
  `jq`, `nc`, `xxd`, `requests` or `PySocks`.
- **Correct Tor auth** — discovers `PROTOCOLINFO`, cookie path, and supports
  cookie, password or null authentication.
- **Safe installer** — torrc backup + validation, marked block, correct Tor
  group discovery, no root service executing a user-writable script.
- **Termux ready** — no systemd required; uses `termux-services` when available
  and otherwise gives a boot-script/manual path.
- **Structured state and logs** — atomic JSON state, rotated JSONL logs, file
  lock to prevent concurrent rotations. Logging can be switched off from the
  panel (`g`) or with `ipchanger logs --off`.
- **Diagnostics** — `ipchanger doctor` checks Python, Tor, control auth, cookie
  permissions, SOCKS, daemon state and the live exit IP.
- **Tested** — 45 unit tests for the control protocol, SOCKS5/HTTP client,
  config, logging, rotator, scheduler and CLI; CI workflow included.
- **Clean uninstall** — removes the service, files, wrapper and managed torrc
  block; `--purge` also removes config/history.

## Control panel preview

```text
+ IP Changer 1.1.0 -------------------------------------------------+
|                                                                  |
| Service       RUNNING  pid 42117                                 |
| Tor exit IP   185.220.101.42                                     |
| Tor check     verified                                           |
| Previous IP   51.15.43.8                                         |
| Last change   2026-11-11 12:34:56  (12s ago)                     |
| Next change   in 1m 48s                                          |
| Interval      2m  (120s, jitter 0%)                              |
| Options       verify on   logs on                                |
| Rotations     42 ok  /  0 failed                                 |
| Last event    2026-11-11T12:34:56  rotation  51.15.43.8 -> ...   |
|                                                                  |
+------------------------------------------------------------------+
| [s] start/stop  [r] rotate now  [v] check IP  [i] interval       |
| [g] logging  [t] verification  [l] logs  [d] doctor  [c] config  |
| Ready. Press ? for help.                                         |
+------------------------------------------------------------------+
```

## Quick start

### One command (Linux, macOS, Termux)

```bash
curl -fsSL https://raw.githubusercontent.com/V4TIME/ip-changer/main/get.sh | sudo bash -s -- --interval 5m
```

On Termux omit `sudo` (there is no root):

```bash
pkg install -y curl
curl -fsSL https://raw.githubusercontent.com/V4TIME/ip-changer/main/get.sh | bash -s -- --interval 5m
```

`get.sh` downloads the repository tarball, verifies it contains the installer,
and runs `install.sh` with the arguments you passed. Nothing is left behind in
the current directory. Pin a release by passing the variable to `bash`:
`curl -fsSL .../get.sh | sudo IPCHANGER_REF=v1.1.0 bash -s -- --interval 5m`,
or read the script first with `curl -fsSL .../get.sh | less`.

### From a clone

```bash
git clone https://github.com/V4TIME/ip-changer.git
cd ip-changer
sudo ./install.sh --interval 5m
ipchanger doctor
ipchanger panel
```

### What `install.sh` actually does

The installer is one idempotent script. Run it as root (`sudo ./install.sh`), as
a normal user (it re-executes privileged steps through `sudo`), or on Termux
(no root). It works in this order:

1. **Detects the environment** — Linux distro/package manager, macOS/Homebrew,
   or Termux; resolves the real target user from `SUDO_USER` (or `--user`), so
   files and services belong to you rather than to root.
2. **Installs prerequisites** — `tor` plus Python 3 with `apt`, `pacman`, `dnf`,
   `zypper`, `apk`, `brew` or `pkg`; checks that Python is 3.10 or newer.
3. **Configures Tor safely** — writes a marked
   `# BEGIN ip-changer managed block` to torrc with `ControlPort`, cookie
   authentication and `CookieAuthFileGroupReadable`. It backs up torrc first,
   removes an old copy of its own block on re-run, detects an existing
   `SocksPort`/`ControlPort`, and validates the result with `tor --verify-config`.
4. **Fixes cookie permissions** — discovers the real Tor group (`debian-tor`,
   `tor`, `toranon` or `_tor`) and adds the target user to it. If a re-login is
   needed, the installer says so.
5. **Installs the program** — copies the package to `/opt/ip-changer` (or a
   per-user prefix without root, or `$PREFIX/share/ip-changer` on Termux) and
   writes the `ipchanger` wrapper to `/usr/local/bin` (or `~/.local/bin`).
6. **Writes your config** — stores the interval, detected control port and SOCKS
   port in the target user's `~/.config/ipchanger/config.json`.
7. **Starts Tor and waits** — enables/restarts the distro's Tor service (or
   starts Tor in the background on Termux) and waits up to 25 seconds for the
   control port to accept connections.
8. **Registers the background runner** — on systemd, renders
   `ipchanger.service` as the invoking user and starts it; on Termux, creates
   `termux-services` entries or a `~/.termux/boot/start-ipchanger` fallback;
   elsewhere it tells you to use `ipchanger start`.
9. **Runs `ipchanger doctor --quick`** and prints the panel/status/rotate
   commands. Nothing is hidden: use `--dry-run` to see the plan, `--no-service`
   to skip the service, and `--interval` to avoid the prompt.

If it says you were added to the Tor group, log out and back in (or run
`newgrp debian-tor`) once so your shell can read the cookie. The systemd service
gets the group immediately.

### Did the upstream repo install the same way?

**Partly, but the details are very different.** `techchipnet/ip-changer` does
perform the same four broad steps — install Tor, edit torrc, add the user to a
Tor group, and register a background job — but it ships two drifting installer
scripts (`ip-changer.sh` for a foreground loop and `setup.sh` for systemd) and
takes several unsafe shortcuts:

| Install step | `techchipnet/ip-changer` | This project |
|--------------|--------------------------|--------------|
| Package install | `tor curl jq xxd`; `nc` is required but never installed, README lists unused `fq` | `tor` + Python 3 only; no shell tools needed |
| torrc changes | Blind `grep`/append, no backup, no validation; misses indented/other-port directives | Managed block, backup, port detection, `tor --verify-config` |
| Tor group | Hard-codes `debian-tor`/`tor`; under `sudo` often adds `root` | Discovers the real group and adds `SUDO_USER`/`--user` |
| Install location | Copies a script into `/home/$USER`, then `sed`s `HOME` | Fixed `/opt/ip-changer` (or user/Termux prefix) + wrapper |
| Service identity | Root systemd unit executing a user-writable script | Runs as the invoking user, with hardening |
| Timing | Abuses `RestartSec` on a `Restart=always` one-shot unit | One long-running scheduler process |
| Re-running | `sed -i` dirties the clone and is not idempotent | Safe to re-run; renders the unit outside the repo |
| Platforms | Six Linux distros, systemd only | Debian/Ubuntu/Kali/Parrot, Arch, Fedora, openSUSE, Alpine, macOS, Termux |
| Uninstall | Removes the unit and script; leaves torrc lines and group membership | Removes files/service/managed torrc block; `--purge` clears state |
| Verification | None; installer can "succeed" while rotation is broken | Waits for the control port and runs `doctor` at the end |

So: the upstream installer does roughly the same *job*, but not the same *way*,
and its shortcuts are the reason this project has its own installer. The
line-by-line evidence is in
[`docs/UPSTREAM_REVIEW.md`](docs/UPSTREAM_REVIEW.md).

### Termux

```bash
pkg install -y git python tor
git clone https://github.com/V4TIME/ip-changer.git
cd ip-changer
./install.sh --interval 5m
ipchanger doctor
ipchanger panel
```

No root is needed. If `termux-services` is available, the installer creates
`tor` and `ipchanger` services. Otherwise it starts Tor in the background and
writes `~/.termux/boot/start-ipchanger` (requires the Termux:Boot app) so you can
keep it running after a reboot. You can always run it manually:

```bash
ipchanger start
ipchanger status
```

### Run from a clone without installing

```bash
python3 -m ipchanger --help
python3 -m ipchanger doctor
python3 -m ipchanger panel
```

Tor must already be running with a control port; the installer is the easy path
for that.

## Everyday usage

```bash
ipchanger                     # open the control panel
ipchanger panel               # same thing, explicit
ipchanger doctor              # check Tor, cookie, SOCKS, daemon and exit IP
ipchanger start               # start automatic rotation in the background
ipchanger status              # service, exit IP, last change, countdown
ipchanger status --json       # machine-readable status
ipchanger rotate              # NEWNYM once, wait, verify, log
ipchanger rotate --no-verify  # just signal NEWNYM
ipchanger ip                  # current Tor exit IP
ipchanger logs --follow       # follow structured rotation events
ipchanger logs --off          # stop writing new log entries
ipchanger logs --on           # enable logging again
ipchanger stop                # stop the background daemon
```

Useful configuration changes:

```bash
ipchanger config list
ipchanger config get interval
ipchanger config set interval 5m
ipchanger config set jitter 20             # add 0-20% random delay
ipchanger config set log_enabled false     # same as: ipchanger logs --off
ipchanger config set require_tor true      # fail if IsTor cannot be confirmed
ipchanger config set check_urls '["https://check.torproject.org/api/ip"]'
ipchanger config reset
```

After `config set`, restart a running daemon to apply it:

```bash
ipchanger restart
```

The control panel applies interval/jitter changes immediately by restarting the
daemon for you.

### Command reference

| Command | What it does |
|---------|--------------|
| `ipchanger [panel]` | Open the interactive control panel. |
| `ipchanger run` | Run the scheduler in the foreground (used by systemd/Termux services). `--once` rotates once and exits. |
| `ipchanger start` / `stop` / `restart` | Manage the detached daemon. `--interval` and `--jitter` override for that run. |
| `ipchanger status [--json]` | Show daemon state, exit IP, last/next change and counters. |
| `ipchanger rotate [--json] [--no-verify] [--no-wait]` | Rotate once and verify. |
| `ipchanger ip [--json]` | Fetch the current exit IP through Tor. |
| `ipchanger config list/get/set/reset` | Inspect or edit JSON configuration. |
| `ipchanger logs [--lines N] [--follow] [--json]` | Read or follow the JSONL log. |
| `ipchanger logs --on` / `--off` / `--status` | Enable, disable or inspect rotation logging. |
| `ipchanger doctor [--quick]` | Diagnose the environment. |
| `ipchanger --config PATH ...` | Use a custom config file. |

### Control panel keys

| Key | Action |
|-----|--------|
| `s` | Start/stop the background daemon. |
| `r` | Rotate now (NEWNYM + wait + verify) in a worker thread. |
| `v` | Fetch the current exit IP. |
| `i` | Change interval (`90`, `5m`, `1h30m`; minimum 10s). |
| `j` | Change jitter (0-100%). |
| `g` | Turn rotation logging on/off. |
| `t` | Turn exit-IP verification on/off. |
| `l` | Browse the rotation log. |
| `d` | Run diagnostics. |
| `c` | Show the active configuration. |
| `?` / `h` | Help. |
| `q` | Leave the panel; the daemon keeps running. |

## How it works

```text
panel/CLI -> scheduler -> rotator -> Tor control port (SIGNAL NEWNYM)
                               \--> SOCKS5 -> HTTPS check -> exit IP
                               \--> state.json + JSONL log
```

1. Read the last IP from state (or establish a baseline through Tor).
2. Respect Tor's 10-second NEWNYM rate limit.
3. Authenticate to `ControlPort 9051` (cookie, password or null).
4. Send `SIGNAL NEWNYM`.
5. Poll a check endpoint through `127.0.0.1:9050` until the exit IP changes or
   the timeout expires.
6. Verify `IsTor`, store the new IP/previous IP/counters, append a JSONL event.
7. Sleep for the configured interval (plus optional jitter), then repeat.

Read [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the module map, the raw
control-protocol exchange, the SOCKS5 handshake, and all data paths.

### Paths

| Purpose | Default |
|---------|---------|
| Config | `~/.config/ipchanger/config.json` |
| State | `~/.local/state/ipchanger/state.json` |
| Log | `~/.local/state/ipchanger/ipchanger.log` |
| Daemon output | `~/.local/state/ipchanger/daemon.log` |
| PID / lock | `~/.local/state/ipchanger/ipchanger.pid` / `.lock` |

## What I found in the upstream project

The full line-by-line review is in
[`docs/UPSTREAM_REVIEW.md`](docs/UPSTREAM_REVIEW.md). The highest-impact
findings:

| Severity | Upstream issue | This project |
|----------|----------------|--------------|
| Critical | The systemd unit runs as root and executes a script from a user-writable home directory. | Runs as the invoking user from a fixed install directory, with hardening. |
| High | `change_tor_ip.sh` logs to `HOME/tor_ip_log.txt` (missing `$`). | State/log paths are explicit and private. |
| High | Requires `nc` but never installs it; README lists unused `fq`; omits `xxd`/`jq`/`nc`. | No shell utilities; the control protocol and SOCKS5/HTTP are implemented in Python. |
| High | Accepts `abc`, `0`, negative or 1-second intervals; `sleep` fails or spins; Tor ignores NEWNYM under 10s. | Duration parser and hard minimum of 10 seconds. |
| High | No timeouts/retry/`--fail`; can log `null` or hang. | Timeouts, retries/fallback endpoints, IP validation, `IsTor` check. |
| High | Sends NEWNYM and immediately checks, so it can report the old IP. | Waits for the exit to change, with a timeout and an honest warning if Tor reuses an exit. |
| High | `grep`/append torrc without backup or validation; misses existing/indented directives. | Marked block, backup, port discovery, `tor --verify-config`. |
| High | Uses systemd `RestartSec` as the rotation interval. | A real scheduler in one long-running process. |
| Medium | Adds `root` to the Tor group under `sudo`; Fedora group is normally `toranon`. | Uses `SUDO_USER`/`--user` and discovers the real group. |
| Medium | No control panel, state, status, logs, diagnostics or Termux support. | All included. |

**What upstream is missing most:** it never routes applications through Tor. It
only rotates the daemon's circuits and prints a number. If an app is not using
SOCKS `127.0.0.1:9050`, its visible IP will not change. This project makes that
limitation explicit and verifies the exit through Tor.

## Comparison

| Capability | `techchipnet/ip-changer` | This project |
|------------|--------------------------|--------------|
| Tor circuit rotation | Yes (shell + `nc`) | Yes (implemented control protocol) |
| Control panel | No | Full curses panel + text fallback |
| Interval validation | No | Minimum 10s, duration parser |
| Wait for new circuit | No | Yes, with timeout |
| `IsTor` verification | No | Yes (fails on explicit non-Tor) |
| State/countdown | No | JSON state + live countdown |
| Structured logs | Broken path | Rotated JSONL + viewer |
| Cookie discovery | Hard-coded path | `PROTOCOLINFO` + override |
| Password/null auth | No | Yes |
| Termux | No | Yes |
| Root service executing user script | Yes | No; runs as user |
| torrc safety | Append only | Backup + managed block + verify |
| Diagnostics | No | `ipchanger doctor` |
| Tests/CI | No | 45 tests + GitHub Actions |
| Uninstall | Partial | Service/files/torrc + `--purge` |

## Termux notes

- No systemd. Use `ipchanger start/stop/status` or `termux-services`.
- If you use Termux:Boot, the installer writes
  `~/.termux/boot/start-ipchanger`; the Termux:Boot app must be installed and
  opened once.
- Battery optimisation can kill background processes. Run
  `termux-wake-lock` (the boot script does this) and exempt Termux from battery
  optimisation.
- Tor can be slow to bootstrap on mobile networks. Run `ipchanger doctor` and
  check the Tor log if the control port never opens.

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `cannot connect to the Tor control port` | Start Tor (`sudo systemctl start tor`, or `tor &` in Termux) and confirm `ControlPort 9051` is in torrc. Run `ipchanger doctor`. |
| `cannot read Tor cookie` / authentication failed | Add your user to the Tor group (`debian-tor`, `tor` or `toranon`) and log in again, or set `auth_method=password` with a Tor control password. |
| `SOCKS port` connection refused | Tor is not running, or `SocksPort` is disabled. Check torrc and `ipchanger doctor`. |
| Exit IP does not change | Tor may reuse an exit; wait for the next interval. Some networks force a single exit. Check the warning in `ipchanger logs`. |
| Websites block the IP | Many sites block Tor exits. Try another rotation or accept that Tor is not suitable for that site. |
| Interval change did nothing | `ipchanger restart` after `config set`; the panel restarts automatically. |
| No new log entries | Logging may be off: `ipchanger logs --status`, then `ipchanger logs --on`. |
| Service keeps restarting | `systemctl status ipchanger.service` and `journalctl -u ipchanger.service -n 100`; then `ipchanger doctor`. |
| `PYTHONPATH`/module not found | Use the installed wrapper `ipchanger`, or run `python3 -m ipchanger` from the repository root. |

## Development

```bash
git clone https://github.com/V4TIME/ip-changer.git
cd ip-changer
python3 -m unittest discover -s tests -v
python3 -m compileall -q ipchanger scripts
bash -n install.sh uninstall.sh
```

The tests do not need Tor or network access: they use a fake control server, a
fake SOCKS5 proxy and a local HTTP server. The CI workflow runs them on Python
3.10, 3.11 and 3.13.

## Documentation

- [`docs/TUTORIAL.md`](docs/TUTORIAL.md) — install, panel tour, interval,
  logging on/off, Termux and troubleshooting.
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — module map and data flow.
- [`docs/UPSTREAM_REVIEW.md`](docs/UPSTREAM_REVIEW.md) — line-referenced review
  of the original project.

## Uninstall

```bash
sudo ./uninstall.sh            # keep config/state
sudo ./uninstall.sh --purge    # also remove config, state and logs
```

The Tor package, the Tor group and torrc backups are left in place. Only the
marked `# BEGIN ip-changer managed block` is removed from torrc.

## License

MIT. See [`LICENSE`](LICENSE).

## Credits

The original idea and demo: [TechChip / techchipnet](https://github.com/techchipnet/ip-changer).
This alternative keeps the core NEWNYM idea, adds a control panel and a tested
implementation, and documents the original's pitfalls.
