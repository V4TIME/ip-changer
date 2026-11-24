[app]
title = Pixel Note
package.name = pixnote
package.domain = org.v4time
source.dir = .
source.include_exts = py,png,jpg,kv,atlas,ttf,txt,json
version = 1.0.0

# Kivy only. No network libraries, no analytics, no accounts.
#
# charset-normalizer is pinned on purpose: it is an indirect dependency of
# Kivy (via requests). Newer releases publish Android-specific wheels
# (cp314-...-android_24_arm64_v8a) that python-for-android's resolver picks and
# then fails to install. 3.3.2 is the pure-Python wheel, which installs
# everywhere.
requirements = python3,kivy==2.3.2,charset-normalizer==3.3.2

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
