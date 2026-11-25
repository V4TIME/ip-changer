[app]
title = Pixel Note
package.name = pixnote
package.domain = org.v4time
source.dir = .
source.include_exts = py,png,jpg,kv,atlas,ttf,txt,json
# Keep build-time files out of the APK.
source.exclude_patterns = tests,README.md,pip-constraints.txt
version = 1.0.0

# Kivy only. No network libraries, no analytics, no accounts.
# charset-normalizer (an indirect Kivy dependency) is pinned in
# pip-constraints.txt, not here: buildozer strips version specifiers before
# python-for-android resolves the dependency graph.
requirements = python3,kivy==2.3.2

orientation = portrait
fullscreen = 0

# No permissions are requested: notes live in private app storage, and the app
# never touches the network. The key must be *absent*, not empty: an empty
# value makes buildozer pass `--permission ''`, and python-for-android turns
# that into `<uses-permission android:name="android.permission." />`, which the
# manifest merger rejects.
android.api = 34
android.minapi = 24
android.ndk_api = 24
android.archs = arm64-v8a,armeabi-v7a
android.accept_sdk_license = True
android.allow_backup = False
android.release_artifact = pixnote

[buildozer]
log_level = 2
# CI runs without a TTY, where the interactive root warning cannot be answered.
warn_on_root = 0
