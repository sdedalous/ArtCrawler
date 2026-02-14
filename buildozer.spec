[app]
title = ArtCrawler
package.name = artcrawler
package.domain = org.art
source.dir = ArtCrawler
source.main = main.py
version = 1.0.0
requirements = python3,kivy
orientation = portrait
fullscreen = 0
android.permissions = INTERNET,WRITE_EXTERNAL_STORAGE,READ_EXTERNAL_STORAGE,FOREGROUND_SERVICE
android.enable_foreground_service = 1
services = artcrawler

# Prevent Buildozer from downloading its own SDK/NDK
use_android_sdk = True

# Force Buildozer to use the external SDK installed by GitHub Actions
android.sdk_path = /home/runner/android-sdk
android.ndk_path = /home/runner/android-sdk/ndk-bundle

# Pin versions to avoid Buildozer auto-installing broken ones
android.api = 33
android.minapi = 21
android.build_tools_version = 35.0.0

# Accept licenses automatically
android.accept_sdk_license = True

# Architectures
android.archs = arm64-v8a,armeabi-v7a

# Reduce build noise
log_level = 2

# Keep Python bytecode
android.keep_python_bytecode = True

# Disable internal downloads
android.skip_update = True

# Avoid old toolchain
p4a.branch = master

# Service entry point
service.artcrawler = artcrawler.service:main
