# Audit — 2026-11-24

Scope: the terminal control panel (`ipchanger/`) and the Android note app
(`noteapp/`). Severity is about user impact, not code style.

## Findings and fixes

| # | Severity | Finding | Fix |
| --- | --- | --- | --- |
| 1 | **Critical** | `ipchanger/tui.py` did not import: `ImportError: cannot import name 'Visual' from 'ipchanger.theme'`. Every `ipchanger` / `ipchanger panel` invocation crashed on start. | `Visual` and `_build_visual` now live in `tui.py`; `theme.py` exports tokens only. |
| 2 | **Critical** | The "blocky" rewrite had deleted the panel's actual controls (interval, jitter, logging, verification, log pager, doctor, config, help, non-TTY fallback). | Restored all controls; the blocky status bar is now an addition, not a replacement. |
| 3 | **Critical** | Note app wrote to a relative `notes.json`. On Android the working directory is not writable and not stable, so notes were lost. | Uses `App.user_data_dir` (`NoteStore`), the app-private directory. |
| 4 | **Critical** | The scaffold saved with `open(..., "a")` + `json.dump`, producing invalid JSON on the second save. | Atomic write (temp file + `os.replace`), schema `{version, notes[]}`, `chmod 600`. |
| 5 | **High** | Note app had no delete, no clear-all, and no scrolling; a long list overflowed the screen. | `ScrollView` + `GridLayout`, per-note delete, clear-all with confirmation. |
| 6 | **High** | Empty/whitespace notes were saved; there was no length cap. | `clean_text()` rejects empty input and enforces 10 000 chars, with a visible error. |
| 7 | **High** | `WRITE_EXTERNAL_STORAGE` was requested although private storage is enough. | Permission list is empty; the app has no network or storage permission. |
| 8 | **High** | A corrupt `notes.json` made the app look empty and overwrote user data. | Corrupt file is moved to `notes.corrupt-<timestamp>.json`; legacy string-list files are imported. |
| 9 | **Medium** | Header text was clipped by the panel border at the top. | Header height 58 dp, padding 14/10, explicit type sizes. |
| 10 | **Medium** | Delete button contrast was ~3.3:1 (light text on salmon). | Dark text on `#C8796F` → **5.87:1**. |
| 11 | **Medium** | Widget order depended on `add_widget(..., index=0)` behaving as a visual insert. | Widgets are added in display order. |
| 12 | **Low** | No divider between the list header and the first card. | 1 px hairline. |
| 13 | **Low** | Earlier audit notes claimed a bundled custom font and private storage that did not exist. | The font is now actually bundled (`noteapp/assets/DejaVuSansMono.ttf`) and storage is actually private. |

## Verification performed

| Check | Result |
| --- | --- |
| `python -m unittest discover -s tests` | 56 passed |
| `python -m unittest noteapp.tests.test_store` | 14 passed |
| `python -m compileall ipchanger noteapp` | clean |
| Panel under a pty at 100×30, 80×24, 60×18, 44×16, 30×10 | exit 0, no traceback |
| Kivy headless smoke (SDL `offscreen`) | build, save, reject empty, delete, persist → `SMOKE OK` |
| Rendered frames at 420×800 and 360×740 | reviewed; layout and contrast verified |

### Not verified here

- **The APK was not compiled on this machine.** Buildozer needs the Android
  SDK/NDK and root-installed packages. `.github/workflows/apk.yml` builds it in
  CI; the first green run is the proof.
- No physical-device test.
- Only a **debug** APK is configured. Release signing keys are not set up.

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

- One accent, used only for the primary action and the note count.
- State is carried by word + shape, never by colour alone.
- Square corners, 1 px hairlines, no shadows, gradients or glow.
- Bundled monospace face at a 9/10/12/14/17 sp scale.
- Terminal panel: filled status bar, 14-column label grid, two columns above
  88 cells, one below, bottom-anchored live log, 200 ms input poll with a
  1 Hz redraw gate.

Rejected as slop: purple/indigo gradients, neon on black, glass effects,
three-card feature grids, dim-only secondary text, ASCII box art.

## Remaining risks

1. **Notes are plaintext at rest.** Private app storage plus
   `android.allow_backup = False` limits exposure, but a rooted device can read
   them. Encryption is the next security feature, not a bug.
2. **Tor mode is not a VPN.** It rotates the exit node; apps only benefit when
   they send traffic to the SOCKS proxy. The panel now says this on screen.
3. **No exit selection.** Tor chooses the exit; there is no country picker, by
   design of the network.
4. **CI-only APK build.** Until the workflow runs once, treat the Android
   artifact as unproven.
