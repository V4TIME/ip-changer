# IP Changer tutorial

This is the complete walkthrough: install, open the panel, change the rotation
interval, control logging, and run it in Termux or as a service.

> **What it changes:** the Tor **exit IP**. Your ISP-assigned address does not
> change. Applications only benefit when they send traffic through Tor's SOCKS
> proxy (`127.0.0.1:9050`). Use it for privacy/research, not to break the law.

## 1. Install everything with one command

### Linux and macOS

```bash
curl -fsSL https://raw.githubusercontent.com/V4TIME/ip-changer/main/get.sh | sudo bash -s -- --interval 5m
```

### Termux (no root)

```bash
pkg install -y curl
curl -fsSL https://raw.githubusercontent.com/V4TIME/ip-changer/main/get.sh | bash -s -- --interval 5m
```

The bootstrap downloads the repository and runs `install.sh`, which:

1. installs Tor and Python 3.10+,
2. backs up torrc and adds a managed control-port/cookie block,
3. validates torrc with `tor --verify-config`,
4. adds your user to the correct Tor group,
5. installs the program and the `ipchanger` command,
6. starts Tor and waits for the control port,
7. creates a systemd service (or Termux services/boot script),
8. runs `ipchanger doctor`.

Prefer a clone? Same result:

```bash
git clone https://github.com/V4TIME/ip-changer.git
cd ip-changer
sudo ./install.sh --interval 5m
```

Useful installer flags:

| Flag | Meaning |
|------|---------|
| `--interval 5m` | Set the first rotation interval. |
| `--user NAME` | Install for a specific account when run as root. |
| `--no-service` | Install the CLI/panel only; do not start a daemon. |
| `--dry-run` | Show the plan without changing anything. |

If the installer added you to a Tor group, log out and back in once (or run
`newgrp debian-tor`) so your shell can read the cookie.

## 2. First run

```bash
ipchanger doctor      # checks Tor, auth, SOCKS and the live exit IP
ipchanger panel       # open the control panel
```

`doctor` should end with `[ OK ]` lines. If it reports a failure, fix that line
before starting the daemon.

## 3. The control panel

```text
+ IP Changer 1.1.0 -------------------------------------------------+
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
+------------------------------------------------------------------+
| [s] start/stop  [r] rotate now  [v] check IP  [i] interval ...   |
| [g] logging  [t] verification  [l] logs  [d] doctor  [c] config |
| Ready. Press ? for help.                                         |
+------------------------------------------------------------------+
```

| Key | Action |
|-----|--------|
| `s` | Start or stop the background daemon. |
| `r` | Rotate now: `SIGNAL NEWNYM`, wait, verify, log. |
| `v` | Fetch the current exit IP through Tor. |
| `i` | Change the interval. Accepts `90`, `5m`, `1h30m`, `00:05:00`. |
| `j` | Change jitter (0–100% random extra delay). |
| `g` | **Turn rotation logging on or off.** |
| `t` | Turn exit-IP verification on or off. |
| `l` | Browse the rotation log. |
| `d` | Run diagnostics. |
| `c` | Show the active configuration. |
| `?` / `h` | Help. |
| `q` | Leave the panel; the daemon keeps running. |

The panel refreshes every second and shows a live countdown, the seconds until
the next change, the previous/new IP, success/failure counts and the latest log
event.

## 4. Change the interval (seconds)

From the panel press `i`, type the new value and press Enter. The daemon is
restarted automatically if it was running.

From the shell:

```bash
ipchanger config set interval 30      # seconds
ipchanger config set interval 5m      # minutes
ipchanger config set interval 1h30m   # hours + minutes
ipchanger restart                     # apply to a running daemon
```

The minimum is **10 seconds** because Tor rate-limits `SIGNAL NEWNYM`.

## 5. Turn logging on or off

```bash
ipchanger logs --off      # stop writing new rotation events
ipchanger logs --on       # enable logging again
ipchanger logs --status   # show the current setting and log path
ipchanger logs            # read existing entries
ipchanger logs --follow   # follow new entries live
```

In the panel, press `g`. The `Options` row changes to `logs off`, and the daemon
restarts so the setting applies immediately. Existing log entries are kept;
disabling logging only stops new writes.

Equivalent config command:

```bash
ipchanger config set log_enabled false
ipchanger restart
```

## 6. Other essential options

| Option | Default | Why you may change it |
|--------|---------|-----------------------|
| `interval` | `120` | How often to request a new Tor circuit. Minimum 10s. |
| `jitter` | `0` | Adds 0–100% random delay so rotations are not perfectly regular. |
| `verify` | `true` | Check the new exit IP through Tor after each rotation. |
| `wait_for_change` | `true` | Wait until the exit actually changes (or time out). |
| `require_tor` | `false` | Fail if `IsTor` cannot be confirmed. |
| `change_timeout` | `45` | Seconds to wait for a new exit before warning. |
| `check_urls` | Tor Project + fallbacks | Exit-IP check endpoints. |
| `log_enabled` | `true` | Write rotation/daemon events to the JSONL log. |
| `log_max_bytes` | `1048576` | Rotate the log after this size (`0` disables rotation). |
| `log_backups` | `3` | Number of rotated log files to keep. |
| `control_port` | `9051` | Tor control port. |
| `socks_port` | `9050` | Tor SOCKS port used for verification. |
| `auth_method` | `auto` | `auto`, `cookie`, `password` or `null`. |

Examples:

```bash
ipchanger config set jitter 20
ipchanger config set require_tor true
ipchanger config set change_timeout 60
ipchanger config set check_urls '["https://check.torproject.org/api/ip"]'
ipchanger config list
ipchanger config reset
ipchanger restart
```

## 7. Run it as a service

On systemd installations the installer creates `ipchanger.service` and starts it:

```bash
systemctl status ipchanger.service
journalctl -u ipchanger.service -n 100
sudo systemctl restart ipchanger.service
```

On Termux:

```bash
ipchanger start
ipchanger status
ipchanger stop
```

If `termux-services` is installed, the installer creates `tor` and `ipchanger`
services. Otherwise it starts Tor in the background and writes
`~/.termux/boot/start-ipchanger` for the Termux:Boot app. Keep Termux alive with
`termux-wake-lock` and disable battery optimisation for Termux.

## 8. Logs and state

| Purpose | Default path |
|---------|--------------|
| Config | `~/.config/ipchanger/config.json` |
| State | `~/.local/state/ipchanger/state.json` |
| Rotation log | `~/.local/state/ipchanger/ipchanger.log` |
| Daemon output | `~/.local/state/ipchanger/daemon.log` |
| PID / lock | `~/.local/state/ipchanger/ipchanger.pid` / `.lock` |

Logs are JSONL (one JSON object per line), private (`0600`) and rotated by size.

## 9. Troubleshooting

| Symptom | Fix |
|---------|-----|
| `cannot connect to the Tor control port` | Start Tor (`sudo systemctl start tor`, or `tor &` in Termux) and run `ipchanger doctor`. |
| Cookie/authentication error | Add your user to the Tor group and log in again, or use `auth_method=password`. |
| `SOCKS port` connection refused | Tor is not running or `SocksPort` is disabled. |
| Exit IP unchanged | Tor may reuse an exit; wait for the next interval or rotate again. |
| Panel says `terminal is too small` | Resize to at least 50×14; 80×24 is comfortable. |
| Logging seems off | Run `ipchanger logs --status`; turn it on with `ipchanger logs --on`. |
| Service keeps restarting | `systemctl status ipchanger.service` then `ipchanger doctor`. |

## 10. Uninstall

```bash
sudo ./uninstall.sh            # keep config/state
sudo ./uninstall.sh --purge    # also remove config, state and logs
```

Only the managed torrc block is removed. Tor itself, the Tor group and torrc
backups are left alone.
