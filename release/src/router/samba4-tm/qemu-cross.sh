#!/bin/bash
set -euo pipefail
TM_ROOT=${TM_ROOT:-/build/tm-samba}
TM_SYSROOT=${TM_SYSROOT:-/home/docker/am-toolchains/brcm-arm-hnd/crosstools-arm-gcc-9.2-linux-4.19-glibc-2.30-binutils-2.32/arm-buildroot-linux-gnueabi/sysroot}
# Waf adds the temporary test library directory to LD_LIBRARY_PATH for its
# linker probes. Retain it while adding this package's target dependencies.
exec qemu-arm-static -L "$TM_SYSROOT" -E "LD_LIBRARY_PATH=$TM_ROOT/deps/usr/lib/tm-samba${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" "$@"
