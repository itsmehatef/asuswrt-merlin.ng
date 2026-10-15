#!/bin/bash
set -euo pipefail
umask 022
SCRIPT_DIR=$(cd -- "$(dirname -- "$0")" && pwd)
source "$SCRIPT_DIR/build-env.sh"
tm_cross_environment
TM_STAGE=$(mktemp -d "$TM_ROOT/stage.new.XXXXXX")
trap 'rm -rf "$TM_STAGE"' EXIT
mkdir -p "$TM_STAGE/usr/sbin" "$TM_STAGE/usr/bin" "$TM_STAGE/usr/lib/tm-samba" "$TM_STAGE/usr/share/samba"
for name in smbd nmbd; do install -m755 "$TM_ROOT/install/usr/sbin/$name" "$TM_STAGE/usr/sbin/$name"; done
for name in testparm smbclient pdbedit smbpasswd; do install -m755 "$TM_ROOT/install/usr/bin/$name" "$TM_STAGE/usr/bin/$name"; done
# Merlin's startup code invokes this exact path.
ln -sfn ../bin/smbpasswd "$TM_STAGE/usr/sbin/smbpasswd"
cp -a "$TM_ROOT/install/usr/lib/tm-samba/." "$TM_STAGE/usr/lib/tm-samba/"
find "$TM_ROOT/deps/usr/lib/tm-samba" -maxdepth 1 \( -type f -o -type l \) -name '*.so*' -exec cp -a -t "$TM_STAGE/usr/lib/tm-samba" '{}' +
TM_ATOMIC=$("$CC" -print-file-name=libatomic.so)
install -m755 "$(readlink -f "$TM_ATOMIC")" "$TM_STAGE/usr/lib/tm-samba/libatomic.so.1"
# The firmware omits glibc converters. Samba's default DOS charset needs IBM850,
# and its native Unicode conversion needs UTF-16; use the matching vendor libc modules.
mkdir -p "$TM_STAGE/usr/lib/gconv"
for module in IBM850 UTF-16; do
 install -m755 "$TM_SYSROOT/usr/lib/gconv/$module.so" "$TM_STAGE/usr/lib/gconv/$module.so"
done
python3 - "$TM_SYSROOT/usr/lib/gconv/gconv-modules" "$TM_STAGE/usr/lib/gconv/gconv-modules" <<'PY'
from pathlib import Path
import sys
lines = Path(sys.argv[1]).read_text().splitlines()
entries = [(line, line.split()) for line in lines if line and not line.startswith('#')]
modules = [(line, fields) for line, fields in entries
           if len(fields) >= 4 and fields[0] == 'module' and fields[3] in {'IBM850', 'UTF-16'}]
names = {name for _, fields in modules for name in fields[1:3] if name != 'INTERNAL'}
aliases = []
while True:
    found = [(line, fields) for line, fields in entries
             if len(fields) == 3 and fields[0] == 'alias' and fields[2] in names and fields[1] not in names]
    if not found:
        break
    aliases.extend(found)
    names.update(fields[1] for _, fields in found)
Path(sys.argv[2]).write_text('# Selected verbatim from the matching vendor glibc gconv-modules.\n'
                            + '\n'.join(line for line, _ in aliases + modules) + '\n')
PY
if [[ -d "$TM_ROOT/install/usr/share/samba" ]]; then cp -a "$TM_ROOT/install/usr/share/samba/." "$TM_STAGE/usr/share/samba/"; fi
# Development files do not belong in the firmware runtime.
find "$TM_STAGE/usr/lib/tm-samba" -type f \( -name '*.a' -o -name '*.la' -o -name '*.pc' \) -delete
find "$TM_STAGE/usr/lib/tm-samba" -depth -type d -empty -delete
while IFS= read -r -d '' file; do
 if file -b "$file" | grep -q 'ELF 32-bit'; then
  # Libtool can add dependency staging paths. Retain target and $ORIGIN paths only.
  old_rpath=$(patchelf --print-rpath "$file")
  new_rpath=
  IFS=: read -r -a rpaths <<< "$old_rpath"
  for path in "${rpaths[@]}"; do
   case "$path" in
    /usr/lib/tm-samba|/usr/lib/tm-samba/*|'$ORIGIN'|'$ORIGIN/'*) new_rpath+="${new_rpath:+:}$path";;
   esac
  done
  if [[ "$new_rpath" != "$old_rpath" ]]; then
   patchelf --force-rpath --set-rpath "$new_rpath" "$file"
  fi
  "$STRIP" --strip-unneeded "$file"
 fi
done < <(find "$TM_STAGE" -type f -print0)
# Do not inherit permissive build modes or mktemp's private root mode into firmware.
find "$TM_STAGE" -type d -exec chmod 0755 '{}' +
find "$TM_STAGE" -type f -exec chmod go-w '{}' +
mkdir -p "$TM_ROOT/stamps"
rm -rf "$TM_ROOT/stage.previous"
if [[ -d "$TM_ROOT/stage" ]]; then mv "$TM_ROOT/stage" "$TM_ROOT/stage.previous"; fi
mv "$TM_STAGE" "$TM_ROOT/stage"
trap - EXIT
find "$TM_ROOT/stage" -type f -exec sha256sum '{}' + > "$TM_ROOT/stage-sha256.txt"
du -sh "$TM_ROOT/stage"
