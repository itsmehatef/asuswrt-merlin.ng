#!/bin/bash
set -euo pipefail
TM_ROOT=${TM_ROOT:-/build/tm-samba}
TM_JOBS=${TM_JOBS:-6}
SCRIPT_DIR=$(cd -- "$(dirname -- "$0")" && pwd)
TC=/opt/toolchains/crosstools-arm-gcc-9.2-linux-4.19-glibc-2.30-binutils-2.32/usr/bin
# Vendor host utilities such as bison embed obsolete build-machine paths.
# Native generators must use the container tools; cross tools have explicit names.
export PATH="$TM_ROOT/host/bin:/usr/bin:/bin:$TC:$PATH"
for tool in bison flex m4; do
 test -x "/usr/bin/$tool"
 ln -sfn "/usr/bin/$tool" "$TM_ROOT/host/bin/$tool"
done
export BISON=/usr/bin/bison FLEX=/usr/bin/flex M4=/usr/bin/m4
export BISON_PKGDATADIR=$(/usr/bin/bison --print-datadir)
export LD_LIBRARY_PATH="${TC%/bin}/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export CC=arm-buildroot-linux-gnueabi-gcc CXX=arm-buildroot-linux-gnueabi-g++
export AR=arm-buildroot-linux-gnueabi-ar RANLIB=arm-buildroot-linux-gnueabi-ranlib
export LD=arm-buildroot-linux-gnueabi-ld NM=arm-buildroot-linux-gnueabi-nm
export AS=arm-buildroot-linux-gnueabi-as CPP=arm-buildroot-linux-gnueabi-cpp
export STRIP=arm-buildroot-linux-gnueabi-strip PKG_CONFIG=pkg-config PKG_CONFIG_PATH=
export CFLAGS='-Os -fPIC -std=gnu17 -ffunction-sections -fdata-sections -D_FILE_OFFSET_BITS=64 -D_LARGEFILE64_SOURCE'
export CXXFLAGS="$CFLAGS"
export CPPFLAGS="-I$TM_ROOT/deps/usr/include"
export LDFLAGS="-L$TM_ROOT/deps/usr/lib/tm-samba -Wl,-rpath,/usr/lib/tm-samba -Wl,-rpath-link,$TM_ROOT/deps/usr/lib/tm-samba -Wl,--gc-sections -latomic"
export PKG_CONFIG_LIBDIR="$TM_ROOT/deps/usr/lib/tm-samba/pkgconfig"
export PKG_CONFIG_SYSROOT_DIR="$TM_ROOT/deps"
export ASN1_COMPILE="$TM_ROOT/host/bin/asn1_compile" COMPILE_ET="$TM_ROOT/host/bin/compile_et"
export PYTHONHASHSEED=1 WAF_NO_PREFORK=1
export TM_SYSROOT=$($CC -print-sysroot)
cd "$TM_ROOT/src/samba-4.24.8"
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
./buildtools/bin/waf install --out=bin --destdir="$TM_ROOT/install" -j"$TM_JOBS"
