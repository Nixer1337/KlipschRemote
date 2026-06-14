#!/usr/bin/env bash
#
# Builds the native KlipschRemote bundle with `flet build linux` and wraps it in
# a portable AppImage that runs on essentially any modern x86-64 Linux distro.
#
# This is the Linux counterpart of build_app.ps1. Like that script it compiles a
# real Flutter app whose executable IS the program (not the shared flet binary),
# so the window/taskbar icon and app identity are baked into the bundle. The raw
# Flutter bundle only runs where the system glibc/GTK are new enough; the AppImage
# step makes it self-contained and distro-independent (single executable file,
# no install, no root).
#
# Prerequisites (one-time, on the BUILD machine - must be Linux; `flet build
# linux` cannot cross-compile from Windows/macOS):
#   - Python 3.10+ and `pip install flet`
#   - Flutter's Linux toolchain deps:
#       sudo apt-get install -y clang cmake ninja-build pkg-config \
#         libgtk-3-dev liblzma-dev libstdc++-12-dev
#   - For the AppImage step: file, wget (appimagetool is auto-downloaded)
#   (`flet build` downloads and manages its own pinned Flutter SDK under ~/flutter.)
#
# Usage (from this folder):
#   ./build_app.sh
# Output:
#   dist_app/KlipschRemote/                      (the raw Flutter bundle)
#   dist_installer/KlipschRemote-x86_64.AppImage (portable, ship this)
#
# From Windows you can run it via Docker (see README / build_app_linux.bat):
#   docker run --rm -v "%cd%":/src -w /src ghcr.io/cirruslabs/flutter:stable \
#     bash -lc "apt-get update && apt-get install -y python3-pip clang cmake \
#       ninja-build pkg-config libgtk-3-dev liblzma-dev file wget && \
#       pip install flet --break-system-packages && ./build_app.sh"

set -euo pipefail

# Resolve the script's own directory so the build works regardless of CWD.
ScriptDir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ScriptDir"

# flet build's progress UI (rich) writes Unicode glyphs; force UTF-8 so a legacy
# locale can't trip stdout encoding (mirrors the Windows script).
export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8

ARTIFACT="KlipschRemote"

# --- Stage a clean flet-build project (source stays single-sourced) -----------
Stage="$ScriptDir/.build_app"
Src="$Stage/src"
rm -rf "$Stage"
mkdir -p "$Src"

echo "==> Staging sources..."
cp "$ScriptDir/packaging/pyproject.toml" "$Stage/"
cp "$ScriptDir/packaging/main.py"        "$Src/"
cp -r "$ScriptDir/klipsch_remote" "$Src/"
cp -r "$ScriptDir/klipsch_ble"    "$Src/"
# Don't ship stale bytecode.
find "$Src" -type d -name "__pycache__" -prune -exec rm -rf {} +

# flet generates the native window/taskbar icon from <app>/assets/icon.png.
Assets="$Src/assets"
mkdir -p "$Assets"
cp "$ScriptDir/klipsch_remote/assets/icon.png" "$Assets/icon.png"

# --- Build --------------------------------------------------------------------
echo "==> Running flet build linux (first run compiles Flutter, a few minutes)..."
pushd "$Stage" >/dev/null
flet build linux \
    --artifact "$ARTIFACT" \
    --product "Klipsch Remote" \
    --org "com.unofficial" \
    --company "Unofficial" \
    --copyright "Unofficial. Not affiliated with or endorsed by Klipsch." \
    --no-rich-output \
    --yes
popd >/dev/null

# NOTE: no manual runner patch/recompile here (unlike the Windows build). Re-running
# `flutter build linux` outside flet's managed environment re-triggers the
# serious_python_linux CMake install hook, which mis-resolves its copy root and
# dies on system files (e.g. "file COPY cannot find ///etc/ufw/after6.rules").
# The only thing the Windows build recompiles for is hiding the window during the
# ~2s Python boot - on Linux we do that from AppRun via FLET_HIDE_WINDOW_ON_START
# instead (see below), so a single flet build is all we need.

# --- Locate the bundle flet produced ------------------------------------------
# `flet build linux` copies the finished Flutter bundle to <project>/build/linux.
Bundle="$Stage/build/linux"
if [ ! -d "$Bundle" ]; then
    echo "ERROR: build reported success but $Bundle is missing." >&2
    exit 1
fi

# The bundle's executable is the lone top-level executable file (beside lib/ and
# data/). Detect it rather than assuming it matches --artifact.
ExePath="$(find "$Bundle" -maxdepth 1 -type f -executable | head -n1)"
if [ -z "$ExePath" ]; then
    echo "ERROR: no executable found at the root of $Bundle." >&2
    exit 1
fi
ExeName="$(basename "$ExePath")"
echo "==> Bundle: $Bundle (exe: $ExeName)"

DistApp="$ScriptDir/dist_app/$ARTIFACT"
rm -rf "$DistApp"
mkdir -p "$DistApp"
cp -r "$Bundle/." "$DistApp/"
echo "==> Raw bundle: $DistApp"

# --- Package the AppImage (portable across distros) ---------------------------
echo "==> Building AppImage..."
AppDir="$Stage/AppDir"
rm -rf "$AppDir"
mkdir -p "$AppDir/usr/bin"
cp -r "$Bundle/." "$AppDir/usr/bin/"

# Desktop entry + icon (appimagetool requires both at the AppDir root, and the
# Icon= name must match the .png filename).
cp "$ScriptDir/klipsch_remote/assets/icon.png" "$AppDir/$ARTIFACT.png"
cat > "$AppDir/$ARTIFACT.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Klipsch Remote
Comment=Unofficial desktop remote for Klipsch powered speakers
Exec=$ExeName
Icon=$ARTIFACT
Categories=AudioVideo;Audio;
Terminal=false
EOF

# AppRun launches the bundled executable with its own lib/ on the library path so
# the Flutter engine and plugins resolve against the shipped copies, not the host.
# FLET_HIDE_WINDOW_ON_START keeps the window hidden during the ~2s Python boot;
# the app reveals it (centred, correctly sized) once ready - no empty-window flash.
cat > "$AppDir/AppRun" <<EOF
#!/bin/sh
HERE="\$(dirname "\$(readlink -f "\$0")")"
export LD_LIBRARY_PATH="\$HERE/usr/bin/lib:\${LD_LIBRARY_PATH:-}"
export FLET_HIDE_WINDOW_ON_START=true
exec "\$HERE/usr/bin/$ExeName" "\$@"
EOF
chmod +x "$AppDir/AppRun"

# appimagetool: use $APPIMAGETOOL if provided, else cache a download under .build_app.
AppImageTool="${APPIMAGETOOL:-}"
if [ -z "$AppImageTool" ]; then
    AppImageTool="$Stage/appimagetool-x86_64.AppImage"
    if [ ! -x "$AppImageTool" ]; then
        echo "==> Fetching appimagetool..."
        wget -q -O "$AppImageTool" \
            "https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-x86_64.AppImage"
        chmod +x "$AppImageTool"
    fi
fi

# Embed the STATIC type-2 runtime (libfuse3 linked in) instead of appimagetool's
# default runtime, which dlopen()s libfuse.so.2 at launch - a host dependency that
# Ubuntu 22.04+/Fedora 36+/etc no longer ship by default, so a plain double-click
# there fails with "Cannot mount AppImage". The static runtime needs nothing on the
# host, which is the whole point of "runs on any distro".
Runtime="${APPIMAGE_RUNTIME:-}"
if [ -z "$Runtime" ]; then
    Runtime="$Stage/runtime-x86_64"
    if [ ! -f "$Runtime" ]; then
        echo "==> Fetching static type-2 runtime..."
        wget -q -O "$Runtime" \
            "https://github.com/AppImage/type2-runtime/releases/download/continuous/runtime-x86_64"
    fi
fi

DistInstaller="$ScriptDir/dist_installer"
mkdir -p "$DistInstaller"
Out="$DistInstaller/$ARTIFACT-x86_64.AppImage"
rm -f "$Out"

# APPIMAGE_EXTRACT_AND_RUN lets appimagetool (itself an AppImage) run on CI/Docker
# where FUSE is unavailable; ARCH is required when building from a bare AppDir.
# --runtime-file swaps in the static runtime fetched above.
APPIMAGE_EXTRACT_AND_RUN=1 ARCH=x86_64 "$AppImageTool" --runtime-file "$Runtime" "$AppDir" "$Out"

mb=$(du -sm "$Out" | cut -f1)
echo ""
echo "==> Done. $Out (~${mb} MB)"
echo "    Portable AppImage (static runtime, no host FUSE needed) - chmod +x and run."
echo "    Raw Flutter bundle also in: $DistApp"
