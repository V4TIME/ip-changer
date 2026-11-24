[app]
title = Pixel Note
package.name = pixnote
package.domain = org.v4time
source.dir = .
source.include_exts = py,png,jpg,kv,atlas,ttf,txt,json
version = 1.0.0

# Kivy only. No network libraries, no analytics, no accounts.
requirements = python3,kivy

orientation = portrait
fullscreen = 0

# Private app storage only: no external-storage or internet permission needed.
android.permissions =
android.api = 34
android.minapi = 24
android.ndk_api = 24
android.archs = arm64-v8a,armeabi-v7a
android.accept_sdk_license = True
android.allow_backup = False
android.release_artifact = pixnote

[buildozer]
log_level = 2
# CI runs inside a container as root; the interactive warning cannot be
# answered without a TTY, so it is disabled explicitly.
warn_on_root = 0
