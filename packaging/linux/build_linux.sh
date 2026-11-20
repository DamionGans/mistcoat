#!/usr/bin/env bash
# Build Mistcoat for Linux: .deb, .rpm, AppImage and Flatpak bundle.
#
#   ./packaging/linux/build_linux.sh
#
# Needs Python 3.11+, curl, and for the Flatpak: flatpak with the Flathub remote
# and a current flatpak-builder. Distro packages of flatpak-builder older than
# 1.4 cannot build for the 25.08 runtime; the Flathub build works everywhere:
#   flatpak install --user flathub org.flatpak.Builder
#   FLATPAK_BUILDER="flatpak run org.flatpak.Builder" ./packaging/linux/build_linux.sh
# Set SKIP_FLATPAK=1 to leave that one out. Outputs land in
# dist/:
#   mistcoat_<ver>_<amd64|arm64>.deb
#   mistcoat-<ver>-1.<x86_64|aarch64>.rpm
#   Mistcoat-<ver>-linux-<arch>.AppImage
#   Mistcoat-<ver>-linux-<arch>.flatpak
#
# Build on the oldest distro you want to support (CI uses Ubuntu 22.04): the
# binaries only run on systems whose glibc is at least as new.
set -euo pipefail
cd "$(dirname "$0")/../.."

APP_ID=app.mistcoat.Mistcoat
NFPM_VERSION=2.48.0
APPIMAGETOOL_VERSION=1.9.1
APPIMAGE_RUNTIME_VERSION=20251108

ARCH=$(uname -m)                       # x86_64 | aarch64
case "$ARCH" in
  x86_64)  NFPM_ARCH=amd64; NFPM_TARBALL=x86_64 ;;
  aarch64) NFPM_ARCH=arm64; NFPM_TARBALL=arm64 ;;
  *) echo "Unsupported architecture: $ARCH" >&2; exit 1 ;;
esac

PY=${PYTHON:-python3}
if [ ! -d .venv-linux ]; then "$PY" -m venv .venv-linux; fi
source .venv-linux/bin/activate
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -r requirements.txt pyinstaller

VERSION=$(python -c "import mistcoat; print(mistcoat.__version__)")
python tools/make_icons.py
rm -rf build/mistcoat dist/Mistcoat dist/linux
python -m PyInstaller packaging/mistcoat.spec --noconfirm --distpath dist --workpath build

# 1. Prove the raw build works before wrapping it.
dist/Mistcoat/Mistcoat --selftest dist/selftest.json
cat dist/selftest.json

# 2. Shared files: launcher entry, AppStream metadata, icons at every size.
SHARE=dist/linux/share
install -Dm644 packaging/linux/$APP_ID.desktop "$SHARE/applications/$APP_ID.desktop"
install -Dm644 packaging/linux/$APP_ID.metainfo.xml "$SHARE/metainfo/$APP_ID.metainfo.xml"
python - "$SHARE" "$APP_ID" <<'PY'
import sys
from pathlib import Path
from PIL import Image
share, app_id = Path(sys.argv[1]), sys.argv[2]
master = Image.open("assets/icon.png")
for px in (16, 24, 32, 48, 64, 128, 256, 512):
    out = share / "icons" / "hicolor" / f"{px}x{px}" / "apps" / f"{app_id}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    master.resize((px, px), Image.LANCZOS).save(out)
PY

TOOLS=build/linux-tools
mkdir -p "$TOOLS"
fetch() {  # url dest
  [ -f "$2" ] || curl -fsSL --retry 3 -o "$2" "$1"
}

# 3. .deb and .rpm (nfpm reads packaging/linux/nfpm.yaml).
ROOT=dist/linux/root
mkdir -p "$ROOT/opt" "$ROOT/usr"
cp -a dist/Mistcoat "$ROOT/opt/mistcoat"
cp -a "$SHARE" "$ROOT/usr/share"
NFPM_TGZ="nfpm_${NFPM_VERSION}_Linux_${NFPM_TARBALL}.tar.gz"
NFPM_URL="https://github.com/goreleaser/nfpm/releases/download/v${NFPM_VERSION}"
if [ ! -x "$TOOLS/nfpm" ]; then
  fetch "$NFPM_URL/$NFPM_TGZ" "$TOOLS/$NFPM_TGZ"
  fetch "$NFPM_URL/checksums.txt" "$TOOLS/nfpm-checksums.txt"
  (cd "$TOOLS" && grep " $NFPM_TGZ\$" nfpm-checksums.txt | sha256sum -c -)
  tar -xzf "$TOOLS/$NFPM_TGZ" -C "$TOOLS" nfpm
fi
export VERSION NFPM_ARCH
"$TOOLS/nfpm" package --config packaging/linux/nfpm.yaml --packager deb --target dist/
"$TOOLS/nfpm" package --config packaging/linux/nfpm.yaml --packager rpm --target dist/

# 4. AppImage.
APPDIR=dist/linux/Mistcoat.AppDir
mkdir -p "$APPDIR/usr/lib"
cp -a dist/Mistcoat "$APPDIR/usr/lib/mistcoat"
cp -a "$SHARE" "$APPDIR/usr/share"
install -m755 packaging/linux/AppRun "$APPDIR/AppRun"
cp "$SHARE/applications/$APP_ID.desktop" "$APPDIR/"
cp "$SHARE/icons/hicolor/256x256/apps/$APP_ID.png" "$APPDIR/$APP_ID.png"
ln -sf "$APP_ID.png" "$APPDIR/.DirIcon"
fetch "https://github.com/AppImage/appimagetool/releases/download/${APPIMAGETOOL_VERSION}/appimagetool-${ARCH}.AppImage" \
      "$TOOLS/appimagetool-${ARCH}.AppImage"
fetch "https://github.com/AppImage/type2-runtime/releases/download/${APPIMAGE_RUNTIME_VERSION}/runtime-${ARCH}" \
      "$TOOLS/runtime-${ARCH}"
chmod +x "$TOOLS/appimagetool-${ARCH}.AppImage"
APPIMAGE="dist/Mistcoat-${VERSION}-linux-${ARCH}.AppImage"
# Extract-and-run so the build works without FUSE (containers, CI).
APPIMAGE_EXTRACT_AND_RUN=1 ARCH="$ARCH" "$TOOLS/appimagetool-${ARCH}.AppImage" \
  --no-appstream --runtime-file "$TOOLS/runtime-${ARCH}" "$APPDIR" "$APPIMAGE"
"$APPIMAGE" --appimage-extract-and-run --selftest dist/selftest-appimage.json
grep -q '"ok": true' dist/selftest-appimage.json

# 5. Flatpak bundle.
if [ "${SKIP_FLATPAK:-0}" != "1" ]; then
  SRC=dist/linux/flatpak-src
  mkdir -p "$SRC"
  cp -a dist/Mistcoat "$SRC/mistcoat"
  cp -a "$SHARE" "$SRC/share"
  ${FLATPAK_BUILDER:-flatpak-builder} --user --force-clean --install-deps-from=flathub \
    --repo=build/flatpak-repo build/flatpak-build packaging/linux/$APP_ID.yml
  FLATPAK="dist/Mistcoat-${VERSION}-linux-${ARCH}.flatpak"
  flatpak build-bundle build/flatpak-repo "$FLATPAK" "$APP_ID" \
    --runtime-repo=https://dl.flathub.org/repo/flathub.flatpakrepo
  echo "Built $FLATPAK"
fi

ls -la dist/*.deb dist/*.rpm dist/*.AppImage dist/*.flatpak 2>/dev/null || true
