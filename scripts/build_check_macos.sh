#!/usr/bin/env bash
# Native macOS development build. Windows mono/DLL checks are separate.
set -euo pipefail
repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
tooling_dir="${VKD_TOOLING_DIR:-$HOME/.local/share/vkd-risk/tooling}"
export QT_DIR="${QT_DIR:-$HOME/Qt/6.11.0/macos}"
export PATH="$tooling_dir/bin:$QT_DIR/bin:$PATH"
cmake -S "$repo_dir" -B "$repo_dir/build-macos" -G Ninja \
  -DCMAKE_PREFIX_PATH="$QT_DIR" -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_CXX_STANDARD=20 -DCMAKE_EXPORT_COMPILE_COMMANDS=ON \
  -DVKD_MONOLITH=OFF -DVKD_BUILD_TESTS=ON
cmake --build "$repo_dir/build-macos"
ctest --test-dir "$repo_dir/build-macos" --output-on-failure
echo 'macOS build OK. If CTest reports no tests, only compilation was checked.'
