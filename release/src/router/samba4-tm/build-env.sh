#!/bin/bash
# Shared by the driver, standalone build steps, staging and Waf's QEMU wrapper.
TM_SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
tm_abspath() {
 python3 -c 'import os, sys; print(os.path.realpath(sys.argv[1]))' "$1"
}
TM_ROOT=$(tm_abspath "${TM_ROOT:-$TM_SCRIPT_DIR/.build}")
TM_LOG_DIR=$(tm_abspath "${TM_LOG_DIR:-$TM_ROOT/logs}")
export TM_ROOT TM_LOG_DIR
export TM_JOBS=${TM_JOBS:-6}
TM_TOOLCHAIN_BIN=$(tm_abspath "${TM_TOOLCHAIN_BIN:-/opt/toolchains/crosstools-arm-gcc-9.2-linux-4.19-glibc-2.30-binutils-2.32/usr/bin}")
TM_TOOLCHAIN_LIB=$(tm_abspath "${TM_TOOLCHAIN_LIB:-${TM_TOOLCHAIN_BIN%/*}/lib}")
export TM_TOOLCHAIN_BIN TM_TOOLCHAIN_LIB
export TM_CROSS_PREFIX=${TM_CROSS_PREFIX:-arm-buildroot-linux-gnueabi-}
TM_NATIVE_BIN=$(tm_abspath "${TM_NATIVE_BIN:-/usr/bin}")
export TM_NATIVE_BIN
export TM_BUILD_TRIPLET=${TM_BUILD_TRIPLET:-x86_64-linux-gnu}
export TM_QEMU=${TM_QEMU:-qemu-arm-static}
if [[ "$TM_ROOT" == / ]]; then
 echo 'TM_ROOT must be a dedicated build cache, not /' >&2
 return 1
fi

tm_resolve_sysroot() {
 if [[ -z ${TM_SYSROOT:-} ]]; then
  TM_SYSROOT=$(LD_LIBRARY_PATH="$TM_TOOLCHAIN_LIB${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
   "$TM_TOOLCHAIN_BIN/${TM_CROSS_PREFIX}gcc" -print-sysroot)
 fi
 test -n "$TM_SYSROOT"
 TM_SYSROOT=$(tm_abspath "$TM_SYSROOT")
 export TM_SYSROOT
}

tm_cross_environment() {
 export PATH="$TM_TOOLCHAIN_BIN:$PATH"
 export LD_LIBRARY_PATH="$TM_TOOLCHAIN_LIB${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
 export CC="$TM_TOOLCHAIN_BIN/${TM_CROSS_PREFIX}gcc" CXX="$TM_TOOLCHAIN_BIN/${TM_CROSS_PREFIX}g++"
 export AR="$TM_TOOLCHAIN_BIN/${TM_CROSS_PREFIX}ar" RANLIB="$TM_TOOLCHAIN_BIN/${TM_CROSS_PREFIX}ranlib"
 export LD="$TM_TOOLCHAIN_BIN/${TM_CROSS_PREFIX}ld" NM="$TM_TOOLCHAIN_BIN/${TM_CROSS_PREFIX}nm"
 export AS="$TM_TOOLCHAIN_BIN/${TM_CROSS_PREFIX}as" CPP="$TM_TOOLCHAIN_BIN/${TM_CROSS_PREFIX}cpp"
 export STRIP="$TM_TOOLCHAIN_BIN/${TM_CROSS_PREFIX}strip"
 tm_resolve_sysroot
}

tm_fingerprint() {
 local role=$1 tool emulator
 shift
 {
  printf '%s\0' "$role" "$TM_ROOT" "$TM_NATIVE_BIN" "$TM_BUILD_TRIPLET"
  # Native generators and parsers must not silently reuse a different host tool.
  for tool in gcc g++ bison flex m4; do
   if [[ -f "$TM_NATIVE_BIN/$tool" ]]; then sha256sum "$TM_NATIVE_BIN/$tool" || return; fi
  done
  if [[ "$role" != host ]]; then
   printf '%s\0' "$TM_TOOLCHAIN_BIN" "$TM_TOOLCHAIN_LIB" "$TM_CROSS_PREFIX" "$TM_SYSROOT" "$TM_QEMU"
   sha256sum "$CC" "$CXX" "$AR" "$RANLIB" "$LD" "$STRIP" || return
   # Track the ABI/runtime source used for QEMU probes and gconv staging too.
   for tool in "$TM_SYSROOT/lib/libc.so.6" "$TM_SYSROOT/usr/lib/gconv/IBM850.so" "$TM_SYSROOT/usr/lib/gconv/UTF-16.so" "$TM_SYSROOT/usr/lib/gconv/gconv-modules"; do
    if [[ -f "$tool" ]]; then sha256sum "$tool" || return; fi
   done
  fi
  if [[ "$role" == target ]]; then
   emulator=$(command -v "$TM_QEMU") || return
   sha256sum "$emulator" || return
  fi
  if [[ $# -gt 0 ]]; then sha256sum "$@" || return; fi
 } | sha256sum | cut -d' ' -f1
}

tm_prepare_waf_cache() {
 local role=$1 output identity stamp
 case "$role" in
  host) output=bin-host;;
  target) output=bin;;
  *) echo "Unknown Waf cache role: $role" >&2; return 1;;
 esac
 identity=$(tm_fingerprint "$role")
 stamp="$TM_ROOT/stamps/waf-$role-tools"
 # Waf does not hash compiler executable contents. A changed compiler at the
 # same path must not reuse old objects, even after configure succeeds again.
 if [[ ! -f "$stamp" ]] || [[ $(cat "$stamp") != "$identity" ]]; then
  rm -rf -- "$TM_ROOT/src/samba-4.24.8/$output"
 fi
 mkdir -p "$TM_ROOT/stamps"
 printf '%s\n' "$identity" > "$stamp"
}
