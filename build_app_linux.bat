@echo off
REM Builds the Linux AppImage from Windows using Docker Desktop.
REM
REM `flet build linux` only runs on Linux, so this spins up a Flutter container,
REM installs the toolchain + flet, and runs build_app.sh inside it against this
REM repo (mounted read-write). Output lands in dist_installer\ and dist_app\ just
REM like a native Linux build.
REM
REM Requires Docker Desktop running. First run is slow (downloads the image and
REM compiles Flutter); reruns are faster.

docker run --rm -v "%cd%":/src -w /src ghcr.io/cirruslabs/flutter:stable bash -lc ^
  "apt-get update && apt-get install -y python3-pip clang cmake ninja-build pkg-config libgtk-3-dev liblzma-dev libstdc++-12-dev file wget && pip install flet --break-system-packages && ./build_app.sh"
