#!/bin/bash
set -euo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "$0")" && pwd)
source "$SCRIPT_DIR/build-env.sh"
export PATH="$TM_NATIVE_BIN:/bin:$PATH"
cd "$TM_ROOT/src/samba-4.24.8"
export PYTHONHASHSEED=1 WAF_NO_PREFORK=1
export CC=gcc CXX=g++ CFLAGS=-Os CXXFLAGS=-Os
export AR=ar RANLIB=ranlib LD=ld NM=nm AS=as STRIP=strip CPP=cpp PKG_CONFIG=pkg-config
unset CPPFLAGS LDFLAGS LD_LIBRARY_PATH PKG_CONFIG_PATH PKG_CONFIG_SYSROOT_DIR PKG_CONFIG_LIBDIR
tm_prepare_waf_cache host
./configure --out=bin-host --prefix="$TM_ROOT/host" --disable-python --without-ad-dc --without-ads --without-ldap --without-winbind --without-json --without-ldb-lmdb --without-libarchive --without-acl-support --without-quotas --disable-avahi --disable-cups --disable-iprint --disable-cephfs --disable-glusterfs --disable-spotlight --disable-fault-handling --without-pam --without-systemd --without-utmp --without-gpgme --without-regedit --without-libunwind --without-gettext --without-lttng --without-automount --without-dmapi --without-fam --disable-rpath --disable-rpath-install --disable-rpath-private-install '--with-static-modules=!DEFAULT,!FORCED' '--with-shared-modules=!DEFAULT,!FORCED' --builtin-libraries=replace --nonshared-binary=asn1_compile,compile_et
./buildtools/bin/waf build --out=bin-host --targets=asn1_compile,compile_et -j"$TM_JOBS"
mkdir -p "$TM_ROOT/host/bin"
cp bin-host/asn1_compile bin-host/compile_et "$TM_ROOT/host/bin/"
