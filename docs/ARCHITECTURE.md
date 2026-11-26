# Architecture

How the components communicate, which module owns each task, and what happens
during a rotation.

## High-level flow

```
                 ipchanger panel / CLI
                          |
          +---------------+----------------+
          |                                |
   config + state                    daemon.py
   (JSON, 0600)                     start/stop/pid
          |                                |
          |                          scheduler.py
          |                       interval + jitter
          |                                |
          +----------> rotator.py <--------+
                          |
             +------------+-------------+
             |                          |
      control.py                  torhttp.py
   Tor control port 9051       SOCKS5 -> HTTPS check
   AUTHENTICATE                check.torproject.org
   SIGNAL NEWNYM                     |
             |                       exit IP
             +---------> state.py <---+
                       JSONL log, state
```

## Modules

| Module | Responsibility |
|--------|----------------|
| `ipchanger/config.py` | Defaults, paths, JSON config, validation, duration parsing, atomic writes. |
| `ipchanger/control.py` | Tor control protocol: `PROTOCOLINFO`, cookie/password/null auth, `SIGNAL NEWNYM`, `GETINFO version`. |
| `ipchanger/torhttp.py` | Minimal SOCKS5 client and HTTPS GET, IP parsing, Tor check, fallback endpoints. |
| `ipchanger/rotator.py` | One rotation: baseline, control `SIGNAL NEWNYM` or a fresh SOCKS identity, wait for a new exit, verify, persist, log. |
| `ipchanger/scheduler.py` | Long-running loop, interval, optional jitter, stop event, next-rotation timestamp. |
| `ipchanger/state.py` | `State` dataclass, atomic state file, rotated JSONL log, PID helpers, `flock` lock. |
| `ipchanger/daemon.py` | Detach `ipchanger run`, write/read PID, graceful stop, restart. |
| `ipchanger/tui.py` | Curses control panel and plain-text fallback. |
| `ipchanger/theme.py` | Palette, glyphs and layout metrics (no curses import). |
| `ipchanger/doctor.py` | Environment checks used by `ipchanger doctor` and the panel. |
| `ipchanger/cli.py` | Argument parsing and all commands. |
| `install.sh` / `uninstall.sh` | Packages, torrc, user/group, wrapper, service, Termux service. |
| `scripts/configure_tor.py` | Backs up torrc and manages only an explicit marked block. |

## A rotation, step by step

1. **Load state.** `StateStore` reads `state.json` (last IP, previous IP,
   counters, last NEWNYM time). If it is missing or corrupt, a fresh state is
   used rather than crashing.
2. **Baseline.** If no last IP is known, the current exit is fetched first.
   This also proves that the SOCKS port works before anything changes.
3. **Respect Tor's rate limit.** NEWNYM is accepted at most once every 10
   seconds. If the last signal was recent, the rotator waits the remainder.
   This is why configuration rejects intervals below 10 seconds.
4. **Signal.** `TorController` connects to `127.0.0.1:9051`, reads
   `PROTOCOLINFO 1`, authenticates, and sends `SIGNAL NEWNYM`. Tor marks its
   current circuits dirty; new connections will use new circuits.
5. **Wait for a new circuit.** The rotator repeatedly asks a check endpoint
   through SOCKS. It stops when the exit IP differs from the old one, or when
   `change_timeout` expires. Tor may legitimately reuse the same exit; that is
   reported as a warning, not hidden.
6. **Verify.** The check endpoint is `check.torproject.org/api/ip` by default,
   which returns `{"IsTor": true, "IP": "..."}`. If it explicitly says
   `IsTor: false`, the rotation fails. Fallback endpoints can only provide an
   IP, so they are marked "Tor status not confirmed" unless
   `require_tor=true`.
7. **Persist and log.** State is written atomically (`0600`). The event is
   appended to a JSONL log, with size-based rotation. A `flock` prevents a
   manual `ipchanger rotate` from racing the daemon.
8. **Schedule the next one.** The scheduler writes `next_rotation` as a Unix
   timestamp so the panel/`status` can show a live countdown.

## Rotation modes

Step 4 above is the `control` strategy. `Config.rotation_mode` selects it:

| Mode | What the rotator does | Needs |
|------|-----------------------|-------|
| `control` | `SIGNAL NEWNYM`, then wait for a new exit | A reachable control port |
| `socks` | For each attempt, generate a fresh SOCKS5 username/password, fetch the exit IP through it, retry until the IP changes | Only the SOCKS port |
| `auto` | Probe the control port; use `control` if it answers, otherwise `socks` | Either |

Tor isolates circuits by SOCKS credentials when `IsolateSOCKSAuth` is on,
which is the default. A previously unseen username/password pair therefore
gets its own circuit and, almost always, a different exit. This is the only
rotation path available on Android with Orbot, because Orbot never exposes
its control socket to other apps.

Consequences worth knowing:

- The SOCKS identity is ephemeral. Rotation proves that a fresh identity
  produces a fresh exit; it does not reconfigure other apps. A client only
  benefits when it sends traffic through that SOCKS proxy.
- `socks` mode does not touch `last_newnym`; the 10-second NEWNYM rate limit
  does not apply. It still retries within `change_timeout`, with a cap of
  `SOCKS_ATTEMPTS` (8) identities.
- If the exit stays the same after every identity, the result is a successful
  rotation with a warning that the proxy may not isolate by credentials,
  rather than a silent "nothing happened".
- A client that keeps using one credential pair keeps one circuit. To get a
  new exit, it must present a new pair (or reconnect after the circuit is
  discarded).


## Tor control protocol in one page

Connect to `ControlPort` (default `9051`) and send CRLF-terminated commands:

```
PROTOCOLINFO 1
250-PROTOCOLINFO 1
250-AUTH METHODS=COOKIE,SAFECOOKIE COOKIEFILE="/run/tor/control.authcookie"
250-VERSION Tor="0.4.8.10"
250 OK

AUTHENTICATE 6f6e696f6e...       # hex-encoded cookie bytes
250 OK

SIGNAL NEWNYM
250 OK

QUIT
250 closing connection
```

`ipchanger` discovers the cookie path instead of hard-coding
`/var/run/tor/control.authcookie`. If cookie permissions are a problem it
offers a clear fix (add the user to the Tor group and log in again), or you can
use `auth_method=password` with a hashed control password.

## SOCKS5 and HTTPS in one page

The verification request is not sent directly to the internet. `torhttp.py`
opens a TCP connection to `127.0.0.1:9050`, performs the SOCKS5 handshake, asks
Tor to CONNECT to the check host by **domain name** (so DNS is resolved by the
exit node), wraps the socket with `ssl`, sends a normal HTTP/1.1 GET, and reads
the response. It handles `Content-Length`, chunked encoding, gzip/deflate, and
redirects. No `requests`, `PySocks`, `curl`, or `jq` is required.

## Paths

| Purpose | Default |
|---------|---------|
| Config | `~/.config/ipchanger/config.json` |
| State | `~/.local/state/ipchanger/state.json` |
| JSONL log | `~/.local/state/ipchanger/ipchanger.log` |
| Daemon stdout/stderr | `~/.local/state/ipchanger/daemon.log` |
| PID | `~/.local/state/ipchanger/ipchanger.pid` |
| Lock | `~/.local/state/ipchanger/ipchanger.lock` |

`XDG_CONFIG_HOME`, `XDG_STATE_HOME`, `IPCHANGER_CONFIG`,
`IPCHANGER_CONFIG_DIR`, `IPCHANGER_STATE_DIR`, `IPCHANGER_LOG` are respected.

## The control panel

`ipchanger panel` runs a curses loop that polls input every 200 ms and only
redraws when a value changes or a second ticks, so it stays quiet on battery.
It reads state, not the network. Expensive operations (rotate now, verify,
doctor) run in a worker thread and report back through a queue. The panel can
start/stop the daemon; changing interval or jitter saves the config and
restarts the daemon so the new value takes effect immediately. Press `?` inside
the panel for the key list.

The visual layer is split in two: `ipchanger/theme.py` holds palette indices,
glyphs and layout metrics (no `curses` import, so it is testable), and
`ipchanger/tui.py` turns state into layout. The panel collapses to a compact
four-line view below 44×16 cells, and falls back to `render_text_dashboard`
when stdout is not a TTY.

## The Android note app

`noteapp/` is a separate Kivy application. `noteapp/store.py` is pure standard
library and is covered by `noteapp/tests/test_store.py`; `noteapp/main.py` is
the UI. The APK is built by `.github/workflows/apk.yml`.

## Testing without Tor

The tests use a fake control-port server, a fake SOCKS5 proxy and a local HTTP
server. This means `python3 -m unittest discover -s tests -v` works on a
machine without Tor installed and without touching the real network. Run
`ipchanger doctor` for a live check when Tor is available.
