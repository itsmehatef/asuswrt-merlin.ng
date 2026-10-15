#!/bin/bash
set -euo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "$0")" && pwd)
source "$SCRIPT_DIR/build-env.sh"
mkdir -p "$TM_ROOT"
if [[ ${TM_BUILD_LOCKED:-0} != 1 ]]; then
 exec 9>"$TM_ROOT/build.lock"
 flock 9
 export TM_BUILD_LOCKED=1
fi
case "${1:-all}" in
 clean)
  # Only this package's generated files are removed; verified downloads and dependencies survive.
  rm -rf "$TM_ROOT/stage" "$TM_ROOT/install" "$TM_ROOT/src/samba-4.24.8/bin"
  rm -f "$TM_ROOT/stamps/samba-target"
  exit 0;;
 install)
  test $# -eq 2 && test -n "$2"
  test -x "$TM_ROOT/stage/usr/sbin/smbd"
  mkdir -p "$2"
  for relative in usr/lib/gconv/IBM850.so usr/lib/gconv/UTF-16.so usr/lib/gconv/gconv-modules; do
   if [[ -e "$2/$relative" || -L "$2/$relative" ]]; then
    if [[ -L "$2/$relative" ]] || ! cmp -s "$TM_ROOT/stage/$relative" "$2/$relative"; then
     echo "Refusing to replace an existing, different converter: $2/$relative" >&2
     exit 1
    fi
   fi
  done
  cp -a --remove-destination "$TM_ROOT/stage/." "$2/"
  exit 0;;
 all) ;;
 *) echo 'usage: build.sh {all|install DESTDIR|clean}' >&2; exit 2;;
esac
tm_cross_environment
mkdir -p "$TM_ROOT/downloads" "$TM_ROOT/src" "$TM_ROOT/stamps" "$TM_LOG_DIR"
python3 "$SCRIPT_DIR/download.py" > "$TM_LOG_DIR/download.log" 2>&1
for arc in "$TM_ROOT"/downloads/*.tar.gz "$TM_ROOT"/downloads/*.tar.xz; do
 [[ -f "$arc" ]] || continue
 dir=$(basename "$arc" | sed -E 's/\.tar\.(gz|xz)$//')
 [[ -d "$TM_ROOT/src/$dir" ]] || tar -xf "$arc" -C "$TM_ROOT/src"
done
if ! grep -q 'def check_system_heimdal_binary' "$TM_ROOT/src/samba-4.24.8/wscript_configure_embedded_heimdal"; then
 (cd "$TM_ROOT/src/samba-4.24.8" && patch -p1 < "$SCRIPT_DIR/host-generators.patch")
fi
# Apply the runtime fix only to the exact pinned source, or accept its exact
# already-patched result. Refuse source drift instead of guessing patch context.
resource_source="$TM_ROOT/src/samba-4.24.8/source3/lib/adouble.c"
resource_patched=1db270c156a2d0c075ef8485199ed51ca7453f0aac578a4b9ffada0361f81a05
case "$(sha256sum "$resource_source" | cut -d' ' -f1)" in
 8c6d4b945ebd04a8015f8eb6a8feb93d827131d2793e58d5f8e71e091061627d)
  (cd "$TM_ROOT/src/samba-4.24.8" && patch --batch --fuzz=0 --forward -p1 < "$SCRIPT_DIR/appledouble-parent-fd.patch");;
 "$resource_patched") ;;
 *) echo 'Refusing unrecognized Samba AppleDouble source' >&2; exit 1;;
esac
test "$(sha256sum "$resource_source" | cut -d' ' -f1)" = "$resource_patched"
bash "$SCRIPT_DIR/build-deps.sh" > "$TM_LOG_DIR/deps-driver.log" 2>&1
host_hash=$(tm_fingerprint host "$SCRIPT_DIR/build-env.sh" "$SCRIPT_DIR/build-host.sh" "$SCRIPT_DIR/download.py" "$SCRIPT_DIR/host-generators.patch")
if [[ ! -f "$TM_ROOT/stamps/samba-host" ]] || [[ $(cat "$TM_ROOT/stamps/samba-host") != "$host_hash" ]]; then
 bash "$SCRIPT_DIR/build-host.sh" > "$TM_LOG_DIR/samba-host.log" 2>&1
 printf '%s\n' "$host_hash" > "$TM_ROOT/stamps/samba-host"
fi
target_hash=$(tm_fingerprint target "$SCRIPT_DIR/build-env.sh" "$SCRIPT_DIR/build.sh" "$SCRIPT_DIR/build-target.sh" "$SCRIPT_DIR/qemu-cross.sh" "$SCRIPT_DIR/stage.sh" "$SCRIPT_DIR/download.py" "$SCRIPT_DIR/build-deps.sh" "$SCRIPT_DIR/build-host.sh" "$SCRIPT_DIR"/*.patch "$TM_ROOT/stamps/samba-host" "$TM_ROOT/stamps/deps-config")
if [[ ! -f "$TM_ROOT/stamps/samba-target" ]] || [[ $(cat "$TM_ROOT/stamps/samba-target") != "$target_hash" ]]; then
 target_log="$TM_LOG_DIR/samba-target-$(date -u +%Y%m%dT%H%M%SZ)-$$.log"
 ln -sfn "$(basename "$target_log")" "$TM_LOG_DIR/samba-target.log"
 bash "$SCRIPT_DIR/build-target.sh" > "$target_log" 2>&1
 bash "$SCRIPT_DIR/stage.sh" > "$TM_LOG_DIR/stage.log" 2>&1
 printf '%s\n' "$target_hash" > "$TM_ROOT/stamps/samba-target"
fi
