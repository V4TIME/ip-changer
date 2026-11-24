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

## Build the APK

The APK is produced in CI, not on a phone.

1. Push to `main`.
2. Open **Actions → apk → Build debug APK**.
3. Download the `pixnote-debug-apk` artifact and install the `.apk`.

Locally (needs Android SDK + NDK, ~10 GB):

```bash
cd noteapp
pip install buildozer cython
buildozer android debug
# output: noteapp/bin/pixnote-1.0.0-arm64-v8a_armeabi-v7a-debug.apk
```

## Permissions

`android.permissions` is empty on purpose. The app needs no internet, no
external storage and no location access.

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
