# Prototype validation and limits

The saved `tm2` candidate has local validation; earlier `tm1` hardware observations are separate. No new runtime or hardware tests were performed for this preview.

The recorded candidate targets GT-AXE16000 on Merlin 3006.102.9, base commit `56e5fe7c3f4862c31726e69f2bc1c5ec55d1ebac`, with Samba 4.24.8. Its source export contains 18 files; “three modified + one added” describes only `tm1` → `tm2`.

Recorded image: **75,481,908 bytes**, SHA-256 `d450a9edbe1ced75a116cb19c28caa2392be21e1e1a559a4d163f8ba32bf9ab7`.

The contribution branch targets the newer `3006.102-wifi6` base and includes publication-preparation changes to portability, installation, and model selection. **The results below do not validate those changes or a firmware image built from that branch.** A fresh build and runtime validation are required.

| Evidence | Result | Scope |
| --- | --- | --- |
| Firmware assembly and payload integrity | Build exit 0; 36 embedded payload hashes matched | Integrity and model metadata, not proof of boot |
| Extracted ARM configuration generator | 15 scenarios / 214 assertions passed | Synthetic mount/NVRAM fixtures |
| Extracted ARM SMB server | Six restart cycles / 321 assertions passed | Linux/QEMU, disposable loopback shares; 51 assertions per account-mode cycle and 56 per guest-mode cycle |
| Packaged-file identity | 182 staged regular-file hashes matched the final image | Links the rebuilt Samba package to the tested candidate |
| Supported loader graph | 150 object contexts / zero errors | Executables, VFS modules, RPC helpers, and converters; not a claim that every raw whole-image finding is absent |
| Raw dependency assessment | Eight added findings classified; zero unresolved | Two unused private libraries, retained in the raw report |
| NetBIOS and existing OpenSSL | Lifecycle passed; five OpenSSL checks passed | Local extracted-image tests |
| Native configuration regression | Five cases / 19 assertions; three negative controls | Exact emitted configuration with native fixtures |
| Native AppleDouble regression | 21 checks passed | Exact source/apply guards and mocked allocation/VFS behavior; separate from actual ARM protocol results |

The 321 assertions include repetitions across six cycles. Coverage includes authentication, ordinary sharing and backup isolation, AAPL/FULLSYNC, reported size, Unicode, offsets above 4 GiB, metadata/resource forks, rename/delete/replacement, sidecar vetoes, symlink boundaries, encrypted reads, durable reconnect, and RPC cleanup. Test processes exited.

The Samba patch fixes internal nested AppleDouble access while retaining client sidecar vetoes. A development ownership error was caught by ARM testing and corrected before the recorded image was assembled; the evidence preserves that failure and added negative controls. Native mocks alone did not establish runtime correctness.

Earlier `tm1` hardware evidence is separate:

- `tm1` booted; core services returned, USB mounted read/write, and macOS discovered the destination.
- The observed Samba/Tuxera HFS+ setup failed rename/delete during empty extended-attribute enumeration. The backup-share DOS metadata setting restored those operations for newly created items.
- A temporary 1 GiB APFS sparsebundle passed creation, a 1 MiB write/flush, detach/reopen, and hash comparison. A real backup entered copying. Neither observation verifies a complete backup/restore or final `tm2` hardware behavior.

The development Mac was separately identified as macOS 27.2, build 26B5112a. This is not general compatibility evidence. Final `tm2` hardware boot, physical USB behavior, and complete backup/restore remain unverified.

Before production claims, validate a clean build in the supported environment and the final image's hardware, reboot/reconnect, physical filesystem, complete backup/restore, and ordinary sharing. Additional models or client/storage combinations need their own coverage.

Review the Merlin integration and generic Samba patch separately. The latter needs an upstream-suitable nested-resource regression test retaining the sidecar veto, ownership correction, and path-boundary checks.


## Contribution preparation

The six existing upstream files touched by the saved patch are byte-identical between the tested `56e5fe7c3f4862c31726e69f2bc1c5ec55d1ebac` base and `3006.102-wifi6` at `2df5b849fdf7af5e9f887703390cc5924c67def9`. The patch applied cleanly and all 18 resulting source-file hashes matched the saved export before preparation edits. This does not validate the newer firmware base.

The package-selection Makefile block passed four isolated GNU Make checks: GT-AXE16000 with Samba 4 selects this package; another Samba 4 model retains the previous package; both models retain legacy Samba when Samba 4 is disabled. This checks selection logic only.

The curated tests were checked again during contribution preparation: the native DOS-attribute fixture passed five cases / 19 assertions plus three negative controls; the AppleDouble source/hash-gate and ownership-mock suite passed 21 checks. Eight curated Python test/helper files passed syntax validation. These checks do not execute the ARM server or validate the revised build recipe end to end.

Reproduction instructions and test limitations are in [tests/README.md](tests/README.md).

The revised build scripts passed 12 mocked regression tests covering configured paths, compiler/sysroot propagation, logs and dependency caching, fresh install contents, failure preservation, and selective Waf cache invalidation after tool changes. Seven shell scripts passed `bash -n`. Mock tools do not establish cross-compilation or runtime correctness.
