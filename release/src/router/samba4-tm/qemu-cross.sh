#!/bin/bash
set -euo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "$0")" && pwd)
source "$SCRIPT_DIR/build-env.sh"
tm_resolve_sysroot
# Waf adds the temporary test library directory to LD_LIBRARY_PATH for its
# linker probes. Retain it while adding this package's target dependencies.
exec "$TM_QEMU" -L "$TM_SYSROOT" -E "LD_LIBRARY_PATH=$TM_ROOT/deps/usr/lib/tm-samba${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" "$@"
