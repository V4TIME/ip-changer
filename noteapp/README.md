# Pixel Note

A small, offline note app for Android. Flat panels, hairline borders, one
muted accent, a bundled monospace face. No account, no network, no analytics.

## What it does

- Write a note, save it, delete it, clear everything (with confirmation).
- Notes are stored as JSON in the app's **private** storage
  (`user_data_dir/notes.json`), written atomically and `chmod 600`.
- A corrupt file is moved aside as `notes.corrupt-<timestamp>.json` instead of
  being deleted.
- Legacy note files (a bare JSON list of strings) are imported on first load.

## Get the APK

The latest build is the `pixnote-debug-apk` artifact of the `apk` workflow:
**Actions → apk → latest green run → Artifacts**. It is a debug-signed,
installable APK (no Python or Termux needed).

A green build of commit `c1c5d88`: 41.8 MB, `arm64-v8a` + `armeabi-v7a`
([run 45619201234](https://github.com/V4TIME/ip-changer/actions/runs/45619201234)).
While signed in, GitHub downloads artifacts as a zip; unzip it, copy the
`.apk` to the phone, and allow installing from the file manager.

## Build the APK

The APK is produced in CI, not on a phone. The workflow rebuilds when
`noteapp/` code, `buildozer.spec`, assets or the workflow itself change.

1. Push to `main`.
2. Open **Actions → apk → Build debug APK**.
3. Download the `pixnote-debug-apk` artifact and install the `.apk`.

Locally (needs Android SDK + NDK, ~10 GB):

```bash
cd noteapp
pip install buildozer cython
PIP_CONSTRAINT=pip-constraints.txt buildozer android debug
# output: noteapp/bin/pixnote-1.0.0-arm64-v8a_armeabi-v7a-debug.apk
```

## Permissions

The app requests none: no internet, no external storage, no location. The
`android.permissions` key is omitted from `buildozer.spec` rather than set to
an empty value, because an empty value makes buildozer pass `--permission ''`,
which python-for-android renders as
`<uses-permission android:name="android.permission." />` and the manifest
merger rejects.

## Build-time pins

`pip-constraints.txt` is exported as `PIP_CONSTRAINT` for the build. It pins
two things that otherwise break the APK build:

- `charset-normalizer==3.3.2` — newer releases ship Android wheels that
  python-for-android resolves and then refuses to install.
- `pip==25.3` — python-for-android upgrades pip inside its own build venv;
  that self-upgrade left a half-replaced tree and crashed on import.

## Tests

```bash
python -m unittest noteapp.tests.test_store -v
```

The UI smoke test needs Kivy; it runs headless with the SDL `offscreen`
driver and is described in `docs/AUDIT.md`.

## Design tokens

| Role | Hex | Use |
| --- | --- | --- |
| Background | `#0D0F12` | app canvas |
| Panel | `#15181D` | note rows, inputs |
| Border | `#2B3038` | 1 px hairlines |
| Text | `#E7E7E1` | body copy |
| Muted | `#8A9099` | labels, timestamps |
| Accent | `#C9A96A` | count, primary action |
| Danger | `#C8796F` | destructive action |

Type: DejaVu Sans Mono (bundled), 9–17 sp. No gradients, no shadows, no
rounded cards.
