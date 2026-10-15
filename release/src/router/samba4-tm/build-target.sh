#!/bin/bash
set -euo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "$0")" && pwd)
source "$SCRIPT_DIR/build-env.sh"
tm_cross_environment
# Vendor host utilities can embed obsolete build-machine paths. Keep native
# generators first; all cross tools have explicit absolute paths.
export PATH="$TM_ROOT/host/bin:$TM_NATIVE_BIN:/bin:$TM_TOOLCHAIN_BIN:$PATH"
mkdir -p "$TM_ROOT/host/bin"
for tool in bison flex m4; do
 test -x "$TM_NATIVE_BIN/$tool"
 ln -sfn "$TM_NATIVE_BIN/$tool" "$TM_ROOT/host/bin/$tool"
done
export BISON="$TM_NATIVE_BIN/bison" FLEX="$TM_NATIVE_BIN/flex" M4="$TM_NATIVE_BIN/m4"
export BISON_PKGDATADIR=$("$BISON" --print-datadir)
export PKG_CONFIG=pkg-config PKG_CONFIG_PATH=
export CFLAGS="-Os -fPIC -std=gnu17 -ffunction-sections -fdata-sections -D_FILE_OFFSET_BITS=64 -D_LARGEFILE64_SOURCE --sysroot=$TM_SYSROOT"
export CXXFLAGS="$CFLAGS"
export CPPFLAGS="-I$TM_ROOT/deps/usr/include"
export LDFLAGS="--sysroot=$TM_SYSROOT -L$TM_ROOT/deps/usr/lib/tm-samba -Wl,-rpath,/usr/lib/tm-samba -Wl,-rpath-link,$TM_ROOT/deps/usr/lib/tm-samba -Wl,--gc-sections -latomic"
export PKG_CONFIG_LIBDIR="$TM_ROOT/deps/usr/lib/tm-samba/pkgconfig"
export PKG_CONFIG_SYSROOT_DIR="$TM_ROOT/deps"
export ASN1_COMPILE="$TM_ROOT/host/bin/asn1_compile" COMPILE_ET="$TM_ROOT/host/bin/compile_et"
export PYTHONHASHSEED=1 WAF_NO_PREFORK=1
cd "$TM_ROOT/src/samba-4.24.8"
tm_prepare_waf_cache target
# Cross answers describe this compiler/sysroot, so re-probe when the driver
# invalidates the target fingerprint rather than reusing another toolchain's answers.
rm -f -- "$TM_ROOT/cross-answers.txt"
./configure --out=bin --cross-compile \
 --cross-execute="bash $SCRIPT_DIR/qemu-cross.sh" \
 --cross-answers="$TM_ROOT/cross-answers.txt" \
 --hostcc=gcc --prefix=/usr --enable-fhs --sysconfdir=/etc --localstatedir=/var \
 --libdir=/usr/lib/tm-samba --libexecdir=/usr/lib/tm-samba/libexec \
 --with-privatelibdir=/usr/lib/tm-samba/private --with-modulesdir=/usr/lib/tm-samba/modules \
 --with-configdir=/etc --with-privatedir=/etc/samba --with-statedir=/var/lib/samba \
 --with-cachedir=/var/cache/samba --with-lockdir=/var/lock/samba --with-piddir=/var/run/samba \
 --with-logfilebase=/var/log/samba \
 --disable-python --without-ad-dc --without-ads --without-ldap --without-winbind \
 --without-json --without-ldb-lmdb --without-libarchive --without-acl-support --without-quotas \
 --disable-avahi --disable-cups --disable-iprint --disable-cephfs --disable-glusterfs \
 --disable-spotlight --disable-fault-handling --without-pam --without-systemd --without-utmp \
 --without-gpgme --without-regedit --without-libunwind --without-gettext --without-lttng \
 --without-automount --without-dmapi --without-fam --without-kernel-keyring \
 --without-profiling-data \
 '--with-static-modules=vfs_default,auth_builtin,auth_sam,auth_unix,pdb_smbpasswd,pdb_tdbsam,!DEFAULT,!FORCED' \
 '--with-shared-modules=vfs_fruit,vfs_catia,vfs_streams_xattr,!DEFAULT,!FORCED' \
 --bundled-libraries=talloc,tevent,tevent-util,texpect,tdb,ldb,tdr,cmocka,replace,com_err,!asn1_compile,!compile_et \
 --private-libraries=talloc,tevent,tevent-util,texpect,tdb,ldb,tdr,cmocka,replace,com_err
# Recreate only the disposable install tree. Removed modules must not survive
# an incremental build; preserve Waf objects, dependencies and downloaded sources.
rm -rf -- "$TM_ROOT/install"
mkdir -p "$TM_ROOT/install"
./buildtools/bin/waf install --out=bin --destdir="$TM_ROOT/install" -j"$TM_JOBS"
