# Audit — 2026-11-25

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
| 14 | **High** | Rotation only worked through `SIGNAL NEWNYM`. Orbot never exposes its control port to other apps, so on Android the documented workflow could not rotate at all. | Added `rotation_mode=socks`: a fresh SOCKS5 username/password per attempt, which Tor isolates into a new circuit. `auto` probes control and falls back. Verified live against real Tor with the control port closed. |
| 15 | **Medium** | `doctor` reported an unreachable control port as `FAIL` even when SOCKS rotation was configured, implying the tool was broken when it was not. | Control-port failure is now `WARN` when the mode allows SOCKS, with the Orbot explanation; the mode itself is an explicit row. |

## Verification performed

| Check | Result |
| --- | --- |
| `python -m unittest discover -s tests` | 64 passed |
| `python -m unittest noteapp.tests.test_store` | 14 passed |
| `python -m compileall ipchanger noteapp` | clean |
| Panel under a pty at 100×30, 80×24, 60×18, 44×16, 30×10 | exit 0, no traceback |
| Kivy headless smoke (SDL `offscreen`) | build, save, reject empty, delete, persist → `SMOKE OK` |
| Rendered frames at 420×800 and 360×740 | reviewed; layout and contrast verified |
| `.github/workflows/apk.yml` on GitHub Actions | **success** — `pixnote-debug-apk`, 41.8 MB ([run 45619201234](https://github.com/V4TIME/ip-changer/actions/runs/45619201234)) |
| `tests` workflow on the same commit | 3.10 / 3.11 / 3.13 all green |
| Live Tor 0.4.9.16, `rotation_mode=control` | two rotations changed the exit IP: `203.55.81.1 → 192.42.116.56 → 192.42.116.143` |
| Live Tor 0.4.9.16, `rotation_mode=socks`, control port deliberately closed | two rotations changed the exit IP: `192.42.116.143 → 23.191.200.81 → 203.55.81.2`, `mode: "socks"` |
| Live Tor 0.4.9.16, `rotation_mode=auto` | fell back to `socks` with the control port closed, then used `control` after it was restored |
| `doctor` with SOCKS mode and a closed control port | control port `WARN` with the Orbot explanation; SOCKS and exit IP `OK` |

### What the APK build had to fix

The first CI runs exposed six real problems. Each was diagnosed from the build
log, fixed, and re-run until the artifact existed:

| # | Symptom | Cause | Fix |
| --- | --- | --- | --- |
| 1 | Action failed before our command ran | Third-party `buildozer-action` broke in its own setup | Build with Buildozer directly on the runner |
| 2 | `EOFError` asking `[y/n]` | `warn_on_root = 1` prompts on stdin, and CI has no TTY | `warn_on_root = 0` |
| 3 | `pip` import error inside the build venv | Container image moved to Python 3.14, where p4a's bootstrap pip broke | Ubuntu 22.04 runner + `setup-python` 3.11 |
| 4 | `charset_normalizer-…-android_24_arm64_v8a.whl is not a supported wheel` | PyPI now publishes Android wheels; p4a resolves them but installs without `--platform` | Pin `charset-normalizer==3.3.2` (pure wheel) via `PIP_CONSTRAINT` |
| 5 | `ImportError: cannot import name 'capture_logging'` | p4a upgrades pip inside its own venv; the self-upgrade left a mixed tree (intermittent) | Pin `pip==25.3` so the upgrade is a no-op |
| 6 | `Android Gradle plugin requires Java 17 … currently using Java 11` | The runner's default `JAVA_HOME` is JDK 11 | Export `JAVA_HOME` pointing at JDK 17 |

A seventh bug was ours, not the toolchain's: `android.permissions =` (empty)
made Buildozer pass `--permission ''`, which python-for-android rendered as
`<uses-permission android:name="android.permission." />`; the AGP manifest
merger rejects that name. The key is now omitted, so the app requests no
permissions at all.

### Not verified here

- **No physical-device or emulator run.** The APK installs and starts only
  once someone runs it on Android; CI proves it compiles, signs with the debug
  key, and packages.
- Only a **debug** APK is configured. Release signing keys are not set up.
- The APK is ~42 MB because Kivy ships both `arm64-v8a` and `armeabi-v7a`.
  Dropping the 32-bit ABI would roughly halve it, at the cost of very old
  devices.

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
   they send traffic to the SOCKS proxy. The panel says this on screen.
3. **SOCKS rotation is per-identity, not global.** A fresh username/password
   gets a fresh circuit, but a client that keeps using one credential pair
   keeps one exit. Orbot in VPN mode routes apps itself, so its own
   new-identity control is what moves that traffic. The tool proves SOCKS-only
   rotation works; it cannot reconfigure other apps.
4. **No exit selection.** Tor chooses the exit; there is no country picker, by
   design of the network.
5. **The APK is CI-built and debug-signed.** It is proven to compile and
   package, but it has not been run on a device, and a debug key is not
   suitable for store distribution.
6. **Pinned build-time versions age.** `pip-constraints.txt` pins
   `charset-normalizer` and `pip` to work around python-for-android's
   resolver and its pip self-upgrade. Re-check both when Buildozer or
   python-for-android is upgraded.
