#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck disable=SC1091
source "$root/deploy/dds-router/versions.env"

source_root="${DDS_ROUTER_SOURCE_ROOT:-$HOME/.local/src/project-link-dds-router-$DDS_ROUTER_VERSION}"
build_root="${DDS_ROUTER_BUILD_ROOT:-$HOME/.cache/project-link-dds-router-$DDS_ROUTER_VERSION}"
prefix="${DDS_ROUTER_PREFIX:-$HOME/$DDS_ROUTER_PREFIX_REL}"

for header in /usr/include/asio.hpp /usr/include/tinyxml2.h /usr/include/openssl/ssl.h /usr/include/yaml-cpp/yaml.h; do
  [[ -f "$header" ]] || {
    echo "Missing build dependency header: $header" >&2
    echo "Install libasio-dev libtinyxml2-dev libssl-dev libyaml-cpp-dev, then retry." >&2
    exit 3
  }
done
for command in git cmake g++ colcon; do
  command -v "$command" >/dev/null || { echo "Missing build tool: $command" >&2; exit 3; }
done

mkdir -p "$source_root/src" "$build_root"

clone_locked() {
  local name="$1" url="$2" commit="$3"
  local path="$source_root/src/$name"
  if [[ ! -d "$path/.git" ]]; then
    mkdir -p "$path"
    git -C "$path" init
    git -C "$path" remote add origin "$url"
  else
    git -C "$path" remote set-url origin "$url"
  fi
  # Fetch only the verified object. Cloning the repository's default branch is
  # both unnecessary and markedly less reliable on the Orin field network.
  git -C "$path" fetch --depth 1 --no-tags origin "$commit"
  git -C "$path" checkout --detach "$commit"
  [[ "$(git -C "$path" rev-parse HEAD)" == "$commit" ]] || {
    echo "Commit verification failed for $name" >&2
    exit 1
  }
}

clone_locked foonathan_memory_vendor "$FOONATHAN_MEMORY_VENDOR_URL" "$FOONATHAN_MEMORY_VENDOR_COMMIT"
clone_locked fastcdr "$FAST_CDR_URL" "$FAST_CDR_COMMIT"
clone_locked fastdds "$FAST_DDS_URL" "$FAST_DDS_COMMIT"
clone_locked dev-utils "$DEV_UTILS_URL" "$DEV_UTILS_COMMIT"
clone_locked ddspipe "$DDS_PIPE_URL" "$DDS_PIPE_COMMIT"
clone_locked ddsrouter "$DDS_ROUTER_URL" "$DDS_ROUTER_COMMIT"

rm -rf "$build_root/build" "$build_root/log"
mkdir -p "$build_root/build" "$build_root/log" "$prefix"
cd "$source_root"
colcon --log-base "$build_root/log" build \
  --build-base "$build_root/build" \
  --install-base "$prefix" \
  --merge-install \
  --packages-up-to ddsrouter_tool ddsrouter_yaml_validator \
  --cmake-args -DBUILD_TESTS=OFF -DCMAKE_BUILD_TYPE=Release

binary="$prefix/bin/ddsrouter"
[[ -x "$binary" ]] || binary="$prefix/ddsrouter_tool/bin/ddsrouter"
[[ -x "$binary" ]] || { echo "DDS Router binary was not installed under $prefix" >&2; exit 1; }

manifest="$prefix/project-link-source-manifest.txt"
cat >"$manifest" <<EOF
foonathan_memory_vendor $FOONATHAN_MEMORY_VENDOR_COMMIT
fastcdr $FAST_CDR_COMMIT
fastdds $FAST_DDS_COMMIT
dev-utils $DEV_UTILS_COMMIT
ddspipe $DDS_PIPE_COMMIT
ddsrouter $DDS_ROUTER_COMMIT
EOF

"$binary" --version
echo "DDS Router installed in isolated user prefix: $prefix"
