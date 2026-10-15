# Samba Time Machine regression tests

These tests cover the configuration generator, managed backup folders, DOS
metadata, nested AppleDouble resource forks, and the packaged ARM Samba runtime.
They do not flash firmware or connect to a router. Keep generated reports and
fixture files outside the source checkout.

## Native tests

Run from the repository root with Python 3.9 or later and a native C compiler:

```sh
TM_ROUTER="$PWD/release/src/router"
TM_TESTS="$TM_ROUTER/samba4-tm/tests"
TM_TEST_OUT=$(mktemp -d "${TMPDIR:-/tmp}/merlin-smb-tests.XXXXXX")
export PYTHONDONTWRITEBYTECODE=1

python3 "$TM_TESTS/test-tm-dos-attributes.py" \
  "$TM_ROUTER/libdisk/write_smb_conf.c" "$TM_TESTS/test_generator.py" \
  --report "$TM_TEST_OUT/dos-attributes.json"
```

This compiles the exact global DOS-attribute output block and complete Time
Machine stanza, using synthetic ordinary shares. It checks that only the backup
share enables DOS attributes and that three incorrect configurations are rejected.
It does not execute the full generator or reproduce a physical filesystem driver.

The AppleDouble test requires a pristine Samba 4.24.8 source tree. Reuse the
archive already downloaded by the package build, verify its pin, and extract it
into the temporary test directory:

```sh
TM_SAMBA_ARCHIVE=/absolute/path/to/package-cache/downloads/samba-4.24.8.tar.gz
python3 - "$TM_SAMBA_ARCHIVE" <<'PY'
import hashlib, pathlib, sys
assert hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest() == \
    '9b6611ab9ae718f47ef0d54008403d7b60a3c6c13e59b2aba769b085caea1d75'
PY
tar -xf "$TM_SAMBA_ARCHIVE" -C "$TM_TEST_OUT"
python3 "$TM_TESTS/test-adouble-parent-fd.py" \
  "$TM_TEST_OUT/samba-4.24.8/source3/lib/adouble.c" \
  "$TM_ROUTER/samba4-tm" --report "$TM_TEST_OUT/appledouble.json"
```

This verifies the recipe's exact source/hash gate, idempotence, rejection of
source drift, and the patched function's control flow. Its allocation mocks
model the basename as a child of the parent path object. A negative control
rejects the earlier double-free implementation. These are mocked APIs, not real
talloc or a complete Samba path-resolution test; the ARM suite below remains
necessary.

The remaining native filesystem tests require **Linux UID 0 inside a disposable
container**, because production ownership checks require root-owned directories.
The C test creates and removes its own unique directory under `/tmp/mnt`:

```sh
python3 "$TM_TESTS/test-share-root-exclusion.py" \
  "$TM_ROUTER/libdisk/write_smb_conf.c" \
  "$TM_ROUTER/shared/smb_timemachine.h" > "$TM_TEST_OUT/share-roots.json"
cc -std=gnu99 -Wall -Wextra -I "$TM_ROUTER/shared" \
  "$TM_TESTS/test-smb-timemachine.c" -o "$TM_TEST_OUT/test-smb-timemachine"
"$TM_TEST_OUT/test-smb-timemachine"
```

These cover managed-folder ownership, writable mounts, invalid names, stop
inhibition, final configuration checks, and omission of share-root aliases into
managed backup directories. Ordinary aliases remain valid.

## Extracted ARM-image tests

Use Linux with Python 3.9+, GNU coreutils, binutils (`readelf`), and
`/usr/bin/qemu-arm-static`. The runner requires UID 0, permission to use chroot,
create fixture device nodes, and change fixture process credentials. ARM
`binfmt_misc` execution must also work for Samba's spawned RPC helpers; explicit
QEMU execution of the first daemon alone is insufficient. Use an isolated
container without host/router USB mounts or published ports. The filesystem
holding the fixtures must support extended attributes and sparse files.

Inputs:

- A freshly extracted GT-AXE16000 firmware root containing the patched
  `write_smb_conf`, Samba executables, libraries, modules, and gconv files.
  Firmware extraction and FIT/payload verification are separate steps.
- The matching ARM32 GCC 9.2/glibc 2.30 vendor cross compiler. Compile the fixture
  against this checkout's `libdisk` headers; do not substitute a host-built
  preload library or host Samba server.
- The protocol client dependencies pinned in `client-requirements.txt`, installed
  in an isolated Python environment. They are test dependencies only.

From the repository root, in the disposable Linux environment:

```sh
TM_ROUTER="$PWD/release/src/router"
TM_TESTS="$TM_ROUTER/samba4-tm/tests"
TM_TEST_OUT=$(mktemp -d /tmp/merlin-smb-arm-tests.XXXXXX)
TM_IMAGE_ROOT=/absolute/path/to/verified-extracted-root
TM_CC=/absolute/path/to/vendor/usr/bin/arm-buildroot-linux-gnueabi-gcc
export PYTHONDONTWRITEBYTECODE=1

# If the vendor compiler requires its own host libraries, set its runtime path
# for these compiler commands, using the matching toolchain installation.
export LD_LIBRARY_PATH="$(dirname "$(dirname "$TM_CC")")/lib"
"$TM_CC" -shared -fPIC -Os -D_FILE_OFFSET_BITS=64 -D_LARGEFILE64_SOURCE \
  -I "$TM_ROUTER/libdisk" "$TM_TESTS/generator_fixture.c" \
  -o "$TM_TEST_OUT/generator-fixture.so"
"$TM_CC" -Os "$TM_TESTS/probe_nss.c" -o "$TM_TEST_OUT/probe-nss"
"$TM_CC" -Os "$TM_TESTS/probe_iconv.c" -o "$TM_TEST_OUT/probe-iconv"
unset LD_LIBRARY_PATH

python3 -m venv "$TM_TEST_OUT/client"
"$TM_TEST_OUT/client/bin/python" -m pip install -r "$TM_TESTS/client-requirements.txt"

# Prepare a disposable root without starting a server. Only this copy is edited.
python3 "$TM_TESTS/run_runtime.py" \
  --source-root "$TM_IMAGE_ROOT" --work-dir "$TM_TEST_OUT/generator-fixture" \
  --config-dir "$TM_TEST_OUT/configs" --prepare-only
cp "$TM_TEST_OUT/generator-fixture.so" \
  "$TM_TEST_OUT/generator-fixture/root/test/fixture.so"
python3 "$TM_TESTS/test_generator.py" "$TM_TEST_OUT/generator-fixture/root" \
  --report-dir "$TM_TEST_OUT/configs"

# Use a second fresh copy for the actual server, without an overlay stage.
python3 "$TM_TESTS/run_runtime.py" \
  --source-root "$TM_IMAGE_ROOT" --work-dir "$TM_TEST_OUT/runtime" \
  --config-dir "$TM_TEST_OUT/configs" \
  --client-python "$TM_TEST_OUT/client/bin/python" \
  --nss-probe "$TM_TEST_OUT/probe-nss" --iconv-probe "$TM_TEST_OUT/probe-iconv" \
  --port 1445 --cycles 3
python3 "$TM_TESTS/probe_nmbd.py" "$TM_TEST_OUT/runtime/root" \
  --report "$TM_TEST_OUT/runtime/nmbd-lifecycle.json"
```

The fixture needs `disk_initial.h` and `disk_io_tools.h` in
`release/src/router/libdisk`; include those files when using a sparse checkout.
The runtime defaults to the verified baseline libc hash for this firmware line.
For a different vendor baseline, first verify its libc independently, then pass
that hash using `--expected-libc-sha256`. Do not replace the expected value merely
to suppress a mismatch.

`test_generator.py` executes the actual ARM generator with test-only NVRAM,
mount, disk, account and custom-hook inputs. Its 15 cases include account/guest
modes, disabled and missing mounts, invalid selections, name collisions, quota
parsing, DOS-attribute isolation, and managed-folder aliases. The preload fixture
is never installed in firmware. Reports include generator and fixture hashes.

`run_runtime.py` first checks target NSS, charset converters, supported ELF
dependency paths and every generated configuration with target `testparm`.
It then runs three start/stop cycles in each account/guest mode, including real
`smbd -D` launches and RPC share enumeration. `probe_smb.py` tests authentication,
AAPL/FULLSYNC, ordinary-share compatibility, Unicode, sparse offsets over 4 GiB,
flushes, alternate streams, metadata/resource forks, nested rename/delete,
replacement behavior, sidecar/marker vetoes, directory symlinks, durable reconnect
and SMB3 encryption. Protocol clients accept only loopback destinations.

The runner refuses an occupied SMB test port and existing work directories.
Its cleanup targets processes belonging to its fixture root. QEMU changes
`/proc/PID/exe`, so the shutdown harness uses fixture-root, command and process
group checks in place of the firmware's native executable-identity check. The
separate nmbd probe requires free loopback UDP ports 137/138 and checks its own
daemon/socket lifecycle, not LAN discovery.

Reports and logs remain in the temporary output directory. Runtime setup also
generates disposable test passwords and account databases; never commit or
publish the fixture root, `test-password`, or `client.auth`. No existing user
credentials are read by these tests.

## Limits

These commands do not boot the router kernel or exercise USB hardware, the
Tuxera HFS+ driver, NAND flashing, Wi-Fi, or a complete macOS Time Machine backup
and restore. The sparse-offset probe writes a small extent beyond 4 GiB; it is
not a large sustained-transfer benchmark. The ELF audit covers supported runtime
entry points and their dependencies; it is not a claim that every unrelated
firmware object passes a standalone loader audit.

Keep native, staged-overlay, and exact extracted-image results distinct. This
README describes reproducible checks and makes no assertion that a new checkout
has already passed the ARM suite.
