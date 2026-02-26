[app]
title = ArtCrawler
package.name = artcrawler
package.domain = org.art

# App entry point
source.dir = .
source.main = main.py

version = 1.0.0
requirements = python3,kivy
orientation = portrait
fullscreen = 0

# Permissions (Android 16 ignores old external storage permissions)
android.permissions = INTERNET,FOREGROUND_SERVICE
android.enable_foreground_service = 1

# Service definition (single-line format avoids parser bugs)
services = artcrawler:artcrawler.service:main

# Use the GitHub Actions SDK/NDK instead of Buildozer auto-download
use_android_sdk = True
android.sdk_path = /home/runner/android-sdk
android.ndk_path = /home/runner/android-sdk/ndk-bundle

# Android 16 requires targetSdkVersion >= 34
android.api = 34
android.minapi = 23
android.sdk = 34
android.build_tools_version = 35.0.0

# Accept licenses automatically
android.accept_sdk_license = True

# Architectures
android.archs = arm64-v8a,armeabi-v7a

# Build options
log_level = 2
android.keep_python_bytecode = True
android.skip_update = True

# Use modern python-for-android
p4a.branch = master
