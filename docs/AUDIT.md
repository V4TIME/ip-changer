# Audit — 2026-11-26

Scope: the terminal control panel and CLI (`ipchanger/`). Severity is about
user impact, not code style.

## Findings and fixes

| # | Severity | Finding | Fix |
| --- | --- | --- | --- |
| 1 | **Critical** | `ipchanger/tui.py` did not import: `ImportError: cannot import name 'Visual' from 'ipchanger.theme'`. Every `ipchanger` / `ipchanger panel` invocation crashed on start. | `Visual` and `_build_visual` now live in `tui.py`; `theme.py` exports tokens only. |
| 2 | **Critical** | The "blocky" rewrite had deleted the panel's actual controls (interval, jitter, logging, verification, log pager, doctor, config, help, non-TTY fallback). | Restored all controls; the blocky status bar is now an addition, not a replacement. |
| 3 | **High** | Rotation only worked through `SIGNAL NEWNYM`. Orbot never exposes its control port to other apps, so rotation could not work on Android. | Added `rotation_mode=socks`: a fresh SOCKS5 username/password per attempt, which Tor isolates into a new circuit. `auto` probes control and falls back. Verified live against real Tor with the control port closed. |
| 4 | **Medium** | `doctor` reported an unreachable control port as `FAIL` even when SOCKS rotation was configured, implying the tool was broken when it was not. | Control-port failure is now `WARN` when the mode allows SOCKS, with the Orbot explanation; the mode itself is an explicit row. |

## Verification performed

| Check | Result |
| --- | --- |
| `python -m unittest discover -s tests` | 64 passed |
| `python -m compileall ipchanger scripts` | clean |
| Panel under a pty at 100×30, 80×24, 60×18, 44×16, 30×10 | exit 0, no traceback |
| `tests` workflow on GitHub Actions | 3.10 / 3.11 / 3.13 all green |
| Live Tor 0.4.9.16, `rotation_mode=control` | two rotations changed the exit IP: `203.55.81.1 → 192.42.116.56 → 192.42.116.143` |
| Live Tor 0.4.9.16, `rotation_mode=socks`, control port deliberately closed | two rotations changed the exit IP: `192.42.116.143 → 23.191.200.81 → 203.55.81.2`, `mode: "socks"` |
| Live Tor 0.4.9.16, `rotation_mode=auto` | fell back to `socks` with the control port closed, then used `control` after it was restored |
| `doctor` with SOCKS mode and a closed control port | control port `WARN` with the Orbot explanation; SOCKS and exit IP `OK` |

### Not verified here

- **Orbot itself was not available here.** SOCKS rotation was proven against a
  local Tor 0.4.9.16 with its control port closed. Orbot exposes the same Tor
  SOCKS listener, but the end-to-end phone test is still owed.
- **No Android app is shipped.** This repository is the terminal/Termux tool.
  It is not a VPN and does not route other apps' traffic.

## Design system

Palette (all text pairs ≥ 5.5:1, WCAG AA for body text):

| Role | Hex | Contrast on background |
| --- | --- | --- |
| Background | `#0D0F12` | — |
| Panel | `#15181D` | — |
| Border | `#2B3038` | — |
| Text | `#E7E7E1` | 15.46:1 |
| Muted | `#8A9099` | 5.97:1 |
| Accent | `#C9A96A` | 8.56:1 |
| Danger | `#C8796F` | 5.87:1 |

Rules applied:

- One accent, used only for the primary action.
- State is carried by word + shape, never by colour alone.
- Square corners, 1 px hairlines, no shadows, gradients or glow.
- Terminal panel: filled status bar, 14-column label grid, two columns above
  88 cells, one below, bottom-anchored live log, 200 ms input poll with a
  1 Hz redraw gate.

Rejected as slop: purple/indigo gradients, neon on black, glass effects,
three-card feature grids, dim-only secondary text, ASCII box art.

## Remaining risks

1. **Tor mode is not a VPN.** It rotates the exit node; apps only benefit when
   they send traffic to the SOCKS proxy. The panel says this on screen.
2. **SOCKS rotation is per-identity, not global.** A fresh username/password
   gets a fresh circuit, but a client that keeps using one credential pair
   keeps one exit. Orbot in VPN mode routes apps itself, so its own
   new-identity control is what moves that traffic. The tool proves SOCKS-only
   rotation works; it cannot reconfigure other apps.
3. **No exit selection.** Tor chooses the exit; there is no country picker, by
   design of the network.
