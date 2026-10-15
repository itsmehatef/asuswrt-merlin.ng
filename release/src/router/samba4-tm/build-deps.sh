#!/bin/bash
set -euo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "$0")" && pwd)
source "$SCRIPT_DIR/build-env.sh"
tm_cross_environment
export PKG_CONFIG=pkg-config PKG_CONFIG_PATH=
export CFLAGS="-Os -fPIC -ffunction-sections -fdata-sections -D_FILE_OFFSET_BITS=64 --sysroot=$TM_SYSROOT"
export CXXFLAGS="$CFLAGS"
export CPPFLAGS="-I$TM_ROOT/deps/usr/include"
export LDFLAGS="--sysroot=$TM_SYSROOT -L$TM_ROOT/deps/usr/lib/tm-samba -Wl,-rpath,/usr/lib/tm-samba -Wl,-rpath-link,$TM_ROOT/deps/usr/lib/tm-samba -Wl,--gc-sections"
export PKG_CONFIG_LIBDIR="$TM_ROOT/deps/usr/lib/tm-samba/pkgconfig"
export PKG_CONFIG_SYSROOT_DIR="$TM_ROOT/deps"
mkdir -p "$TM_ROOT/deps" "$TM_ROOT/stamps" "$TM_ROOT/build" "$TM_LOG_DIR"
dep_hash=$(tm_fingerprint dependencies "$SCRIPT_DIR/build-env.sh" "$SCRIPT_DIR/build-deps.sh" "$SCRIPT_DIR/download.py")
if [[ -f "$TM_ROOT/stamps/deps-config" ]] && [[ $(cat "$TM_ROOT/stamps/deps-config") != "$dep_hash" ]]; then
 rm -rf "$TM_ROOT/deps" "$TM_ROOT/build"
 rm -f "$TM_ROOT/stamps/"{gmp-6.3.0,nettle-3.10.2,libtasn1-4.21.0,popt-1.19,attr-2.6.0,zlib-1.3.2,gnutls-3.8.13}
 # zlib uses an in-source build, unlike the autotools dependencies.
 rm -rf "$TM_ROOT/src/zlib-1.3.2"
 tar -xf "$TM_ROOT/downloads/zlib-1.3.2.tar.xz" -C "$TM_ROOT/src"
 mkdir -p "$TM_ROOT/deps" "$TM_ROOT/build"
fi
printf '%s\n' "$dep_hash" > "$TM_ROOT/stamps/deps-config"
common=(--host="${TM_CROSS_PREFIX%-}" --build="$TM_BUILD_TRIPLET" --prefix=/usr --libdir=/usr/lib/tm-samba --disable-static --enable-shared)
build_one() {
 local name=$1; shift
 if [[ -f "$TM_ROOT/stamps/$name" ]]; then return; fi
 mkdir -p "$TM_ROOT/build/$name"
 (
  cd "$TM_ROOT/build/$name"
  "$TM_ROOT/src/$name/configure" "${common[@]}" "$@"
  make -j"$TM_JOBS"
  make DESTDIR="$TM_ROOT/deps" install
 ) > "$TM_LOG_DIR/$name.log" 2>&1
 touch "$TM_ROOT/stamps/$name"
 printf '%s completed %s\n' "$name" "$(date -u +%FT%TZ)"
}
build_one gmp-6.3.0 --disable-cxx
build_one nettle-3.10.2 --disable-documentation --disable-openssl
build_one libtasn1-4.21.0 --disable-doc --disable-gtk-doc --disable-gtk-doc-html
build_one popt-1.19 --disable-nls
build_one attr-2.6.0 --disable-nls
if [[ ! -f "$TM_ROOT/stamps/zlib-1.3.2" ]]; then
 (
  cd "$TM_ROOT/src/zlib-1.3.2"
  ./configure --prefix=/usr --libdir=/usr/lib/tm-samba
  make -j"$TM_JOBS"
  make DESTDIR="$TM_ROOT/deps" install
 ) > "$TM_LOG_DIR/zlib-1.3.2.log" 2>&1
 touch "$TM_ROOT/stamps/zlib-1.3.2"
fi
build_one gnutls-3.8.13 --disable-doc --disable-tests --disable-tools --disable-cxx --disable-nls --disable-guile --disable-dane --disable-openssl-compatibility --disable-libdane --without-p11-kit --without-tpm --without-tpm2 --without-idn --without-brotli --without-zstd --with-included-unistring --with-default-trust-store-file=/etc/ssl/certs/ca-certificates.crt
