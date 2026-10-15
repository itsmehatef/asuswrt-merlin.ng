# Samba 4 Time Machine package for GT-AXE16000

This package cross-compiles upstream Samba 4.24.8 for the Merlin BCM4912 firmware's ARM32 EABI userspace using the existing GCC 9.2 / glibc 2.30 vendor toolchain. The router kernel is ARM64; the firmware applications and this package use ARM32.

The source archive SHA-256 is pinned in `download.py`. Its signature was verified during development against Samba's distribution key `81F5E2832BD2545A1897B713AA99442FB680B620`. The upstream signature and verification log are retained in the build evidence. Dependency archives are hash-pinned against OpenWrt or Buildroot package metadata. No target binary packages are downloaded.

Runtime dependencies built from source are GMP 6.3.0, Nettle 3.10.2, libtasn1 4.21.0, GnuTLS 3.8.13, popt 1.19, zlib 1.3.2, and attr 2.6.0. The compiler's libatomic runtime is included. libc, libm, libpthread, libdl, libgcc, and related platform runtime libraries remain supplied by the firmware.

The package also includes the vendor glibc `IBM850` and `UTF-16` conversion modules with their vendor aliases under the previously absent `/usr/lib/gconv`. These cover Samba's default DOS charset and UTF-16/UTF-8 conversion. The matching toolchain libc is byte-identical to firmware libc after stripping; that comparison is retained in build evidence. Installation refuses to replace different existing converter files.

`bash build.sh all` downloads verified sources, builds native ASN.1/error-table generators, cross-builds target dependencies and Samba, and produces `$TM_ROOT/stage`. `bash build.sh install DESTDIR` copies only runtime files. `bash build.sh clean` clears the generated Samba runtime and target objects, preserving downloads and dependency builds. A filesystem lock serializes these driver operations against the same cache; use this driver rather than running individual build steps concurrently.

All build steps share `build-env.sh`. Set `TM_ROOT` to a dedicated persistent Linux filesystem build cache; its default is `.build` beside the recipe. Logs default to `$TM_ROOT/logs`, with no required `/build` directory. For example, from this package directory:

```sh
TM_ROOT=/work/cache/samba4-tm TM_LOG_DIR=/work/logs/samba4-tm TM_JOBS=6 bash build.sh all
```

The supported overrides are:

| Variable | Default / purpose |
| --- | --- |
| `TM_ROOT` | Package-local `.build`; generated sources, objects, dependencies and staging |
| `TM_LOG_DIR` | `$TM_ROOT/logs`; configure/build/download logs |
| `TM_JOBS` | `6`; build parallelism |
| `TM_TOOLCHAIN_BIN` | `/opt/toolchains/crosstools-arm-gcc-9.2-linux-4.19-glibc-2.30-binutils-2.32/usr/bin` |
| `TM_TOOLCHAIN_LIB` | Sibling `lib` directory of `TM_TOOLCHAIN_BIN`; host libraries needed by the compiler |
| `TM_CROSS_PREFIX` | `arm-buildroot-linux-gnueabi-`; target tool executable prefix, including final hyphen |
| `TM_SYSROOT` | Selected compiler's `-print-sysroot`; explicit override applies to compiler/linker flags, QEMU probes and gconv staging |
| `TM_NATIVE_BIN` | `/usr/bin`; native compilers and parser generators |
| `TM_BUILD_TRIPLET` | `x86_64-linux-gnu`; native build host passed to dependency configure scripts |
| `TM_QEMU` | `qemu-arm-static`; target probe emulator, executable name or absolute path |

The default toolchain preserves the validated vendor layout. Overrides permit relocating that toolchain; they do not establish ABI compatibility with a different compiler/sysroot or support another router model. The sysroot's libc and conversion modules must match the firmware. Build/cache, recipe and toolchain paths should not contain whitespace because upstream compiler flags and build interfaces split them; the separate log directory may contain spaces.

The Linux build environment needs the vendor toolchain, standard C/C++ build tools, native Bison/Flex/M4, Python 3, Perl Parse::Yapp, pkg-config, qemu-user-static, patchelf, patch, GNU coreutils, flock, native GnuTLS/popt/zlib development packages, and the usual Samba configure prerequisites. This recipe does not install packages on the host or router.

The standalone file server includes smbd, nmbd, smbpasswd, pdbedit, testparm, and smbclient. Shared VFS modules are fruit, catia, and streams_xattr. LDAP, Active Directory controller/member features, winbind, printer integration, Python runtime modules, Spotlight, Ceph, Gluster, PAM, systemd integration, and quotas are disabled. SMB1 server support remains compiled for compatibility with the existing GUI's optional legacy setting; generated Time Machine configurations use SMB2/SMB3.

Libraries, VFS modules, and RPC subprocess helpers are isolated under `/usr/lib/tm-samba`. The configuration defaults to `/etc/smb.conf`, private password data to `/etc/samba`, and writable state/cache/lock/pid locations under `/var`. `/usr/sbin/smbpasswd` is retained as the path expected by Merlin's startup code.

Staging removes build-machine library search paths while retaining target private paths and relative `$ORIGIN` paths. Runtime directories use mode 0755, and regular files are not writable by group or others. Installation replaces legacy executable symlinks without following their destinations.

Configure runtime probes execute ARM binaries through qemu against the selected sysroot and record answers in `$TM_ROOT/cross-answers.txt`. A target rebuild clears those answers before configure so another toolchain cannot inherit old probe results. These are local build probes, not a test of the physical router or its USB filesystem. See `VALIDATION.md` for the precise scope of prior firmware and hardware evidence.

`host-generators.patch` is the OpenWrt cross-compilation patch that lets an embedded Heimdal build use native `asn1_compile` and `compile_et` executables. It changes build-time tool selection only.

`appledouble-parent-fd.patch` fixes internal resource-fork sidecar access in nested directories. Samba's multi-component open otherwise applies the client-facing `._` veto to its own AppleDouble file. The fix resolves the parent through `parent_pathref` and opens the final sidecar basename relative to that directory descriptor, retaining `O_NOFOLLOW` and normal client veto checks. The patch does not disable `fruit:veto_appledouble` or change metadata/resource storage formats.

The driver verifies the exact original or patched `source3/lib/adouble.c` hash and applies this patch without fuzz. Unexpected source contents stop the build. The shared environment helper, relevant recipes, pinned-source manifest, patches, selected compiler tools/sysroot paths and runtime modules, and completed dependency/native-generator fingerprints participate in cache invalidation. The target also fingerprints QEMU. Log location and job count do not invalidate build caches. When changing other contents of a toolchain/sysroot in place, use a fresh `TM_ROOT` rather than assuming that these selected-file fingerprints cover its entire contents.

A changed source or recipe fingerprint reruns configure/build/install and restaging using the existing `bin` cache. Separate tool identity stamps clear the corresponding `bin` or `bin-host` objects when tools change, including a compiler replaced at the same path; Waf alone does not hash compiler contents. The first use of this recipe also clears any Waf cache without those identity stamps. Downloads and the other Waf output tree are preserved. Dependency input changes separately invalidate dependency output/stamps.

After configure succeeds, the recipe recreates only `$TM_ROOT/install` before Waf install, so modules removed from the current build cannot survive from an earlier install. This install cleanup preserves target objects, downloads, dependency and native-generator caches. Use the normal `bash build.sh all` entry point, and preserve previous image and stage evidence before doing so.

The configurable-path and fresh-install changes in this contribution have lightweight mocked regression coverage in `tests/test_build_recipe.py`; they have not been used to rebuild a complete firmware image. The earlier tm2 artifact's runtime tests do not validate these revised build scripts. Run `python3 tests/test_build_recipe.py` for those local checks; a fresh build and extracted-image tests remain required for a new artifact.

The target uses Samba's standard `bin` output directory. Native build-time generators use `bin-host`, avoiding changes to upstream generated-header paths.

Parser generation uses Bison, Flex and M4 from `TM_NATIVE_BIN` (default `/usr/bin`); the vendor toolchain's non-relocatable host utilities are excluded from selection. The recipe recreates these native-tool shims and records this selection in its target fingerprint.

Primary references:

- https://www.samba.org/samba/history/samba-4.24.8.html
- https://download.samba.org/pub/samba/stable/samba-4.24.8.tar.gz
- https://raw.githubusercontent.com/openwrt/packages/master/net/samba4/Makefile
- https://raw.githubusercontent.com/openwrt/packages/master/net/samba4/patches/009-samba-4-11-fix-host-tools-checks.patch.patch
- https://www.gnutls.org/news.html
- https://raw.githubusercontent.com/buildroot/buildroot/master/package/gnutls/gnutls.hash
- https://raw.githubusercontent.com/buildroot/buildroot/master/package/libtasn1/libtasn1.hash
