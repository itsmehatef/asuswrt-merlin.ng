#!/usr/bin/env python3
"""Exercise the build recipe with temporary fake tools; no downloads or compilation."""
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import textwrap
import unittest


PACKAGE = Path(__file__).resolve().parents[1]
DEPENDENCIES = (
    "gmp-6.3.0", "nettle-3.10.2", "libtasn1-4.21.0", "popt-1.19",
    "attr-2.6.0", "zlib-1.3.2", "gnutls-3.8.13",
)


class BuildRecipeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="samba-recipe-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / "cache"
        self.tc = self.base / "toolchain" / "bin"
        self.native = self.base / "native"
        self.sysroot = self.base / "vendor-sysroot"
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("TM_")}
        self.env.update(
            TM_ROOT=str(self.root), TM_TOOLCHAIN_BIN=str(self.tc),
            TM_TOOLCHAIN_LIB=str(self.base / "host-libraries"),
            TM_NATIVE_BIN=str(self.native), TM_CROSS_PREFIX="mock-arm-",
            TM_BUILD_TRIPLET="mock-host-linux-gnu", TM_JOBS="2",
            TM_QEMU=str(self.base / "qemu"),
            FIXTURE_SYSROOT=str(self.sysroot),
            RECORD=str(self.base / "record.jsonl"),
        )
        for directory in (self.root, self.tc, self.native, self.sysroot):
            directory.mkdir(parents=True)
        self.recording_tool = """
            import json, os, sys
            from pathlib import Path
            with open(os.environ['RECORD'], 'a') as out:
                out.write(json.dumps({'tool': Path(sys.argv[0]).name,
                                      'args': sys.argv[1:], 'env': dict(os.environ)}) + '\\n')
        """
        self.python_tool(self.base / "qemu", self.recording_tool)
        for name in ("gcc", "g++", "ar", "ranlib", "ld", "nm", "as", "cpp", "strip"):
            self.python_tool(self.tc / ("mock-arm-" + name), """
                import os, sys
                if '-print-sysroot' in sys.argv:
                    print(os.environ['FIXTURE_SYSROOT'])
            """)
        for name in ("gcc", "g++", "bison", "flex", "m4"):
            self.python_tool(self.native / name, """
                import sys
                if '--print-datadir' in sys.argv:
                    print('/mock/native/bison-data')
            """)

    def python_tool(self, path, body):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("#!" + sys.executable + "\n" + textwrap.dedent(body))
        path.chmod(0o755)

    def run_shell(self, script, *args, env=None, expected=0):
        result = subprocess.run(
            ["bash", str(script), *args], cwd=self.base, env=env or self.env,
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return result

    def helper(self, body, env=None, expected=0):
        script = self.base / "helper-test.sh"
        script.write_text("set -euo pipefail\nsource " + shlex.quote(str(PACKAGE / "build-env.sh")) + "\n" + body)
        return self.run_shell(script, env=env, expected=expected)

    def records(self):
        path = Path(self.env["RECORD"])
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def target_fixture(self):
        # An existing cache with known, unchanged tool identity is incremental.
        self.helper("tm_cross_environment\ntm_prepare_waf_cache target\n")
        source = self.root / "src" / "samba-4.24.8"
        self.python_tool(source / "configure", self.recording_tool + """
            assert not (Path(os.environ['TM_ROOT']) / 'cross-answers.txt').exists()
            sys.exit(int(os.environ.get('CONFIGURE_EXIT', '0')))
        """)
        self.python_tool(source / "buildtools" / "bin" / "waf", self.recording_tool + """
            dest = Path(next(a.split('=', 1)[1] for a in sys.argv if a.startswith('--destdir=')))
            assert dest.is_dir() and not list(dest.iterdir()), 'install tree was not emptied'
            output = dest / 'usr/lib/tm-samba/modules/vfs/current.so'
            output.parent.mkdir(parents=True)
            output.write_text('new module')
        """)
        stale = self.root / "install" / "usr/lib/tm-samba/modules/vfs/removed.so"
        stale.parent.mkdir(parents=True)
        stale.write_text("old module")
        (self.root / "cross-answers.txt").write_text("old compiler answers")
        caches = [source / "bin/cached.o", self.root / "deps/cached.so",
                  self.root / "downloads/cached.tar.gz", self.root / "host/cached-tool"]
        for path in caches:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("preserve me")
        return stale, caches

    def test_shared_environment_normalizes_root_and_defaults_logs(self):
        env = dict(self.env, TM_ROOT="relative/cache")
        result = self.helper('printf "%s\\n" "$TM_ROOT" "$TM_LOG_DIR" "$TM_TOOLCHAIN_LIB"\n', env)
        self.assertEqual(result.stdout.splitlines(), [str(self.base / "relative/cache"),
                         str(self.base / "relative/cache/logs"), self.env["TM_TOOLCHAIN_LIB"]])

    def test_root_directory_is_rejected(self):
        result = self.helper("echo should-not-run\n", dict(self.env, TM_ROOT="/"), expected=1)
        self.assertIn("dedicated build cache", result.stderr)
        self.assertNotIn("should-not-run", result.stdout)

    def test_qemu_uses_selected_compiler_sysroot_and_preserves_probe_libraries(self):
        env = dict(self.env, LD_LIBRARY_PATH="/waf/probe/lib")
        self.run_shell(PACKAGE / "qemu-cross.sh", "/probe/program", "an argument", env=env)
        record = self.records()[0]
        self.assertEqual(record["args"], ["-L", str(self.sysroot), "-E",
                         f"LD_LIBRARY_PATH={self.root}/deps/usr/lib/tm-samba:/waf/probe/lib",
                         "/probe/program", "an argument"])

    def test_qemu_honors_explicit_sysroot(self):
        override = self.base / "other-sysroot"
        self.run_shell(PACKAGE / "qemu-cross.sh", "/probe", env=dict(self.env, TM_SYSROOT=str(override)))
        self.assertEqual(self.records()[0]["args"][:2], ["-L", str(override)])

    def test_target_install_removes_old_modules_and_preserves_build_caches(self):
        stale, caches = self.target_fixture()
        explicit = self.sysroot
        self.run_shell(PACKAGE / "build-target.sh", env=dict(self.env, TM_SYSROOT=str(explicit)))
        self.assertFalse(stale.exists())
        self.assertTrue(stale.with_name("current.so").is_file())
        for path in caches:
            self.assertEqual(path.read_text(), "preserve me")
        configure, waf = self.records()
        self.assertEqual(configure["env"]["CC"], str(self.tc / "mock-arm-gcc"))
        self.assertIn("--sysroot=" + str(explicit), configure["env"]["CFLAGS"])
        self.assertIn("--sysroot=" + str(explicit), configure["env"]["LDFLAGS"])
        self.assertEqual(configure["env"]["BISON"], str(self.native / "bison"))
        self.assertIn("--with-shared-modules=vfs_fruit,vfs_catia,vfs_streams_xattr,!DEFAULT,!FORCED", configure["args"])
        self.assertEqual(waf["args"], ["install", "--out=bin", "--destdir=" + str(self.root / "install"), "-j2"])

    def test_configure_failure_preserves_previous_install_and_caches(self):
        stale, caches = self.target_fixture()
        self.run_shell(PACKAGE / "build-target.sh", env=dict(self.env, CONFIGURE_EXIT="23"), expected=23)
        self.assertEqual(stale.read_text(), "old module")
        self.assertEqual(len(self.records()), 1)  # Waf install was never reached.
        for path in caches:
            self.assertEqual(path.read_text(), "preserve me")

    def test_fingerprints_track_build_inputs_but_ignore_log_path_and_jobs(self):
        input_file = self.base / "dependency-stamp"
        input_file.write_text("dependency-v1")
        command = "tm_cross_environment\ntm_fingerprint target " + shlex.quote(str(input_file)) + "\n"
        fingerprint = lambda env=None: self.helper(command, env).stdout.strip()
        initial = fingerprint()
        self.assertEqual(initial, fingerprint(dict(self.env, TM_JOBS="12", TM_LOG_DIR=str(self.base / "new logs"))))
        self.assertNotEqual(initial, fingerprint(dict(self.env, TM_SYSROOT=str(self.base / "other-sysroot"))))
        input_file.write_text("dependency-v2")
        changed_input = fingerprint()
        self.assertNotEqual(initial, changed_input)
        with (self.tc / "mock-arm-gcc").open("a") as tool:
            tool.write("\n# changed compiler\n")
        changed_compiler = fingerprint()
        self.assertNotEqual(changed_input, changed_compiler)
        with (self.base / "qemu").open("a") as tool:
            tool.write("\n# changed emulator\n")
        self.assertNotEqual(changed_compiler, fingerprint())

    def test_changed_compiler_discards_only_target_waf_objects(self):
        _, caches = self.target_fixture()
        native = self.root / "src/samba-4.24.8/bin-host/native.o"
        native.parent.mkdir(parents=True)
        native.write_text("native object")
        with (self.tc / "mock-arm-gcc").open("a") as tool:
            tool.write("\n# replacement at the same path\n")
        self.run_shell(PACKAGE / "build-target.sh")
        self.assertFalse(caches[0].exists())
        self.assertEqual(native.read_text(), "native object")
        for path in caches[1:]:
            self.assertEqual(path.read_text(), "preserve me")

    def test_changed_native_compiler_discards_only_native_waf_objects(self):
        command = "tm_prepare_waf_cache host\n"
        self.helper(command)
        native = self.root / "src/samba-4.24.8/bin-host/native.o"
        target = self.root / "src/samba-4.24.8/bin/target.o"
        for path in (native, target):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("object")
        self.helper(command)
        self.assertTrue(native.exists())
        with (self.native / "gcc").open("a") as tool:
            tool.write("\n# replacement at the same path\n")
        self.helper(command)
        self.assertFalse(native.exists())
        self.assertEqual(target.read_text(), "object")

    def test_missing_cross_tool_fails_before_mutating_cached_outputs(self):
        stale, caches = self.target_fixture()
        (self.tc / "mock-arm-strip").unlink()
        self.run_shell(PACKAGE / "build-target.sh", expected=1)
        self.assertEqual(stale.read_text(), "old module")
        self.assertEqual(self.records(), [])
        for path in caches:
            self.assertEqual(path.read_text(), "preserve me")

    def test_driver_download_log_uses_configured_directory(self):
        # Stop at the real download boundary, without network or source patching.
        self.python_tool(self.tc / "python3", """
            import os, sys
            if len(sys.argv) > 1 and sys.argv[1].endswith('/download.py'):
                print('mock download boundary')
                sys.exit(42)
            os.execv(sys.executable, [sys.executable] + sys.argv[1:])
        """)
        logs = self.base / "custom logs"
        env = dict(self.env, TM_LOG_DIR=str(logs), TM_BUILD_LOCKED="1")
        self.run_shell(PACKAGE / "build.sh", "all", env=env, expected=42)
        self.assertEqual((logs / "download.log").read_text().strip(), "mock download boundary")
        self.assertFalse((self.root / "logs").exists())

    def test_dependency_logs_and_configure_use_overrides_and_cache_second_run(self):
        for name in DEPENDENCIES:
            self.python_tool(self.root / "src" / name / "configure", self.recording_tool)
        self.python_tool(self.tc / "make", self.recording_tool)
        logs = self.base / "dependency logs"
        env = dict(self.env, TM_LOG_DIR=str(logs))
        self.run_shell(PACKAGE / "build-deps.sh", env=env)
        records = self.records()
        self.assertEqual(len(records), 21)  # Seven configure/build/install sequences.
        for name in DEPENDENCIES:
            self.assertTrue((logs / (name + ".log")).is_file())
            self.assertTrue((self.root / "stamps" / name).is_file())
        for configure in records[::3]:
            self.assertEqual(configure["env"]["CC"], str(self.tc / "mock-arm-gcc"))
            self.assertIn("--sysroot=" + str(self.sysroot), configure["env"]["CFLAGS"])
            self.assertIn("--sysroot=" + str(self.sysroot), configure["env"]["LDFLAGS"])
            if "--host=mock-arm" not in configure["args"]:  # zlib has its own configure interface.
                self.assertEqual(configure["args"], ["--prefix=/usr", "--libdir=/usr/lib/tm-samba"])
            else:
                self.assertIn("--build=mock-host-linux-gnu", configure["args"])
        self.run_shell(PACKAGE / "build-deps.sh", env=env)
        self.assertEqual(self.records(), records)


if __name__ == "__main__":
    unittest.main(verbosity=2)
