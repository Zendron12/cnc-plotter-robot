#!/usr/bin/env bash
# Build and install AutoTrace from source (Debian/Ubuntu devcontainer).
# AutoTrace is not in Ubuntu apt; this script matches upstream CI dependencies.
set -euo pipefail

PREFIX="${AUTOTRACE_PREFIX:-/usr/local}"
BUILD_DIR="${AUTOTRACE_BUILD_DIR:-/tmp/autotrace-build}"
REPO="${AUTOTRACE_REPO:-https://github.com/autotrace/autotrace.git}"

if command -v autotrace >/dev/null 2>&1; then
  echo "autotrace already installed: $(command -v autotrace)"
  autotrace --version 2>/dev/null || autotrace --help 2>&1 | head -1
  exit 0
fi

echo "Installing AutoTrace build dependencies..."
sudo apt-get update
sudo apt-get install -y \
  gcc g++ make pkg-config autoconf automake libtool intltool autopoint \
  libgraphicsmagick1-dev libpng-dev libexiv2-dev libtiff-dev libjpeg-dev \
  libxml2-dev libbz2-dev libfreetype6-dev libpstoedit-dev

rm -rf "${BUILD_DIR}"
git clone --depth 1 "${REPO}" "${BUILD_DIR}"
cd "${BUILD_DIR}"
./autogen.sh
./configure --prefix="${PREFIX}"
make -j"$(nproc)"
sudo make install
sudo ldconfig 2>/dev/null || true

echo "AutoTrace installed:"
autotrace --help 2>&1 | head -5
