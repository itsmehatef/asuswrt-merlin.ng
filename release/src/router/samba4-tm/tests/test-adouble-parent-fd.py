#!/usr/bin/env python3
"""Test exact patched function control flow and the recipe's source-hash gate.

Samba allocation/VFS APIs are mocks. The mock explicitly models parent_pathref's
parent-owned atname and rejects a second free after its owner has freed it.
Full ARM protocol/security checks remain required.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def function(text):
    start = text.index('static NTSTATUS adouble_open_rsrc_fsp(')
    end = text.index('\nNTSTATUS adouble_open_from_base_fsp(', start)
    return text[start:end]


STUBS = r'''
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <fcntl.h>
#include <sys/types.h>
#ifndef O_PATH
#define O_PATH 0x200000
#endif
typedef int NTSTATUS;
#define NT_STATUS_OK 0
#define NT_STATUS_NO_MEMORY 1
#define NT_STATUS_IO_DEVICE_ERROR 2
#define NT_STATUS_IS_OK(x) ((x) == 0)
#define ADOUBLE_RSRC 1
#define discard_const_p(type, p) ((type *)(p))
#define talloc_tos() NULL
struct connection { void *vfs_handles; };
struct files_struct;
struct smb_filename { char *base_name; struct files_struct *fsp; };
struct files_struct { struct connection *conn; struct smb_filename *name; };
struct adouble { int unused; };
struct vfs_open_how { int flags; mode_t mode; };
static struct smb_filename ad_name, parent_name, at_name;
static struct files_struct internal, parent_handle;
static struct adouble header;
static char full[512], parent[512];
static int fault, parent_calls, fd_calls, freed, parent_freed, at_freed;
static int init_calls, write_calls, expected_flags;
static mode_t expected_mode;
#define VERIFY(x) do { if (!(x)) { fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #x); exit(1); } } while (0)
static void mock_free(void **p) {
    if (*p == &parent_name) {
        VERIFY(!parent_freed);
        parent_freed++;
        /* vfswrap_parent_pathname allocates atname under parent. */
        if (!at_freed) at_freed++;
    }
    if (*p == &at_name) {
        VERIFY(!at_freed);
        at_freed++;
    }
    *p = NULL;
}
#define TALLOC_FREE(p) mock_free((void **)&(p))
static int adouble_path(void *ctx, const struct smb_filename *base, struct smb_filename **out) {
    const char *last = strrchr(base->base_name, '/');
    (void)ctx;
    if (fault == 1) return -1;
    if (last) snprintf(full, sizeof(full), "%.*s/._%s", (int)(last-base->base_name), base->base_name, last+1);
    else snprintf(full, sizeof(full), "._%s", base->base_name);
    ad_name.base_name = full; *out = &ad_name; return 0;
}
static NTSTATUS create_internal_fsp(struct connection *conn, struct smb_filename *name, struct files_struct **out) {
    if (fault == 2) return 12;
    internal.conn = conn; internal.name = name; *out = &internal; return 0;
}
static void file_free(void *request, struct files_struct *fsp) {
    (void)request; VERIFY(fsp == &internal); freed++;
}
static NTSTATUS parent_pathref(void *ctx, struct files_struct *dir, struct smb_filename *name,
                              struct smb_filename **out_parent, struct smb_filename **out_at) {
    const char *last = strrchr(name->base_name, '/');
    (void)ctx; parent_calls++;
    if (fault == 3) return 13;
    if (last) snprintf(parent, sizeof(parent), "%.*s", (int)(last-name->base_name), name->base_name);
    else snprintf(parent, sizeof(parent), ".");
    parent_name.base_name = parent; parent_name.fsp = &parent_handle;
    parent_handle.conn = dir->conn; parent_handle.name = &parent_name;
    at_name.base_name = (char *)(last ? last+1 : name->base_name);
    *out_parent = &parent_name; *out_at = &at_name; return 0;
}
static NTSTATUS fd_openat(const struct files_struct *dir, struct smb_filename *name,
                         struct files_struct *fsp, struct vfs_open_how *how) {
    char resolved[512];
    fd_calls++;
    /* Model the exact problematic split: nested internal names enter client veto lookup. */
    if (strchr(name->base_name, '/')) return 14;
    VERIFY(!parent_freed && !at_freed);
    VERIFY(how->flags == expected_flags && how->mode == expected_mode);
    if (strcmp(dir->name->base_name, ".") == 0) snprintf(resolved, sizeof(resolved), "%s", name->base_name);
    else snprintf(resolved, sizeof(resolved), "%s/%s", dir->name->base_name, name->base_name);
    VERIFY(strcmp(resolved, fsp->name->base_name) == 0);
    VERIFY(strncmp(name->base_name, "._", 2) == 0);
    return fault == 4 ? 15 : 0;
}
static struct adouble *ad_init(void *ctx, int type) {
    (void)ctx; VERIFY(type == ADOUBLE_RSRC); init_calls++;
    return fault == 5 ? NULL : &header;
}
static int ad_fset(void *handle, struct adouble *ad, struct files_struct *fsp) {
    (void)handle; VERIFY(ad == &header && fsp == &internal); write_calls++;
    return fault == 6 ? -1 : 0;
}
'''

MAIN = r'''
int main(int argc, char **argv) {
    struct connection conn = {0};
    struct smb_filename dot = {.base_name = "."};
    struct files_struct dir = {.conn = &conn, .name = &dot};
    struct smb_filename base;
    struct files_struct *out = NULL;
    int flags, result, expected;
    VERIFY(argc == 5);
    base = (struct smb_filename){.base_name = argv[1]};
    flags = atoi(argv[2]); fault = atoi(argv[3]); expected = atoi(argv[4]);
    expected_flags = flags & ~O_PATH;
    if (expected_flags & (O_CREAT|O_TRUNC|O_WRONLY)) {
        expected_flags &= ~O_WRONLY; expected_flags |= O_RDWR;
    }
    expected_mode = 0751;
    result = adouble_open_rsrc_fsp(&dir, &base, flags, expected_mode, &out);
    VERIFY(result == expected);
    VERIFY(parent_calls == (fault < 1 || fault >= 3));
    VERIFY(fd_calls == (fault == 0 || fault >= 4));
    VERIFY(parent_freed == fd_calls && at_freed == fd_calls);
    VERIFY(freed == (fault >= 3));
    VERIFY((out == &internal) == (fault == 0));
    VERIFY(init_calls == ((fault == 0 || fault >= 5) && (flags & (O_CREAT|O_TRUNC)) != 0));
    VERIFY(write_calls == (init_calls && fault != 5));
    return 0;
}
'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('pristine_source', type=Path)
    parser.add_argument('recipe', type=Path)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    recipe = args.recipe.resolve()
    original = args.pristine_source.read_bytes()
    assert hashlib.sha256(original).hexdigest() == '8c6d4b945ebd04a8015f8eb6a8feb93d827131d2793e58d5f8e71e091061627d'
    driver = (recipe/'build.sh').read_text()
    gate = driver[driver.index('# Apply the runtime fix'):driver.index('bash "$SCRIPT_DIR/build-deps.sh"')]
    results = []
    def check(name, passed):
        results.append({'name': name, 'passed': bool(passed)})
        assert passed, name
    source3 = args.pristine_source.parent.parent
    parent_source = source3/'modules/vfs_default.c'
    parent_text = parent_source.read_text()
    start = parent_text.index('static NTSTATUS vfswrap_parent_pathname(')
    end = parent_text.index('\n/*', start)
    parent_function = parent_text[start:end]
    check('pinned_parent_api_owns_atname',
          'name = synthetic_smb_fname(\n\t\tparent,' in parent_function and
          'Freeing the parent is thus sufficient to free both.' in parent_text)
    active_modules = ['vfs_fruit.c', 'vfs_catia.c', 'vfs_streams_xattr.c']
    check('active_modules_use_default_parent_api', all(
          '.parent_pathname_fn' not in (source3/'modules'/name).read_text()
          for name in active_modules))
    with tempfile.TemporaryDirectory(prefix='adouble-parent-fd-') as scratch:
        root = Path(scratch)
        src = root/'src/samba-4.24.8/source3/lib/adouble.c'
        src.parent.mkdir(parents=True)
        src.write_bytes(original)
        env = {**os.environ, 'TM_ROOT': str(root), 'SCRIPT_DIR': str(recipe)}
        # macOS has shasum, while the production Linux recipe uses sha256sum.
        if not any((Path(p)/'sha256sum').exists() for p in env['PATH'].split(os.pathsep)):
            bindir = root/'host-test-bin'; bindir.mkdir()
            shim = bindir/'sha256sum'
            shim.write_text('#!/bin/sh\nexec shasum -a 256 "$@"\n'); shim.chmod(0o755)
            env['PATH'] = str(bindir)+os.pathsep+env['PATH']
        first = subprocess.run(['bash', '-eu', '-o', 'pipefail', '-c', gate], env=env, capture_output=True, text=True)
        check('exact_recipe_applies_without_fuzz', first.returncode == 0 and digest(src) == '1db270c156a2d0c075ef8485199ed51ca7453f0aac578a4b9ffada0361f81a05')
        patched = src.read_text()
        mtime = src.stat().st_mtime_ns
        second = subprocess.run(['bash', '-eu', '-o', 'pipefail', '-c', gate], env=env, capture_output=True, text=True)
        check('already_patched_is_idempotent', second.returncode == 0 and src.stat().st_mtime_ns == mtime)
        src.write_text(patched+'\n/* unknown local drift */\n')
        drift = digest(src)
        rejected = subprocess.run(['bash', '-eu', '-o', 'pipefail', '-c', gate], env=env, capture_output=True, text=True)
        check('unknown_source_refused_without_modification', rejected.returncode != 0 and digest(src) == drift)
        flawed = patched.replace(
            '\t/* ad_atname is a talloc child of ad_parent; free both together. */\n\tTALLOC_FREE(ad_parent);\n',
            '\tTALLOC_FREE(ad_parent);\n\tTALLOC_FREE(ad_atname);\n')
        assert hashlib.sha256(flawed.encode()).hexdigest() == '979b560f6cead55cfdfa1055283525d5695a051c3739ac9687051b233df65789'
        src.write_text(flawed)
        rejected = subprocess.run(['bash', '-eu', '-o', 'pipefail', '-c', gate], env=env, capture_output=True, text=True)
        check('flawed_interim_source_refused_without_modification',
              rejected.returncode != 0 and src.read_text() == flawed)
        cases = [
            ('root_read', 'file', 0, 0, 0),
            ('nested_read', 'dir/file', 0, 0, 0),
            ('deep_create', 'dir/sub/file', os.O_CREAT|os.O_WRONLY, 0, 0),
            ('unicode_parent', 'تهران/雪/file', os.O_CREAT|os.O_RDWR, 0, 0),
            ('truncate_header', 'dir/file', os.O_TRUNC|os.O_WRONLY, 0, 0),
            ('write_only_becomes_read_write', 'dir/file', os.O_WRONLY, 0, 0),
            ('pathref_flag_removed', 'dir/file', 0x200000, 0, 0),
            ('path_allocation_failure', 'dir/file', os.O_CREAT, 1, 1),
            ('internal_fsp_failure', 'dir/file', os.O_CREAT, 2, 12),
            ('parent_failure_propagated', 'dir/file', os.O_CREAT, 3, 13),
            ('open_failure_cleans_parent', 'dir/file', os.O_CREAT, 4, 15),
            ('header_allocation_failure', 'dir/file', os.O_CREAT, 5, 1),
            ('header_write_failure', 'dir/file', os.O_CREAT, 6, 2),
        ]
        for label, body in [('patched', patched), ('original', original.decode()), ('flawed', flawed)]:
            c = root/(label+'.c'); binary = root/label
            c.write_text(STUBS+function(body)+MAIN)
            command = ['cc', '-std=c99', '-Wall', '-Wextra', '-Werror']
            if label == 'original': command += ['-Wno-unused-function']
            subprocess.run(command+[str(c), '-o', str(binary)], check=True)
            if label == 'patched':
                for name, path, flags, fault, expected in cases:
                    result = subprocess.run([str(binary), path, str(flags), str(fault), str(expected)], capture_output=True, text=True)
                    check('exact_function_'+name, result.returncode == 0)
            elif label == 'original':
                result = subprocess.run([str(binary), 'dir/file', '0', '0', '0'], capture_output=True, text=True)
                check('pristine_nested_negative_control_fails', result.returncode != 0 and 'result == expected' in result.stderr)
            else:
                result = subprocess.run([str(binary), 'dir/file', '0', '0', '0'], capture_output=True, text=True)
                check('flawed_interim_double_free_negative_control_fails',
                      result.returncode != 0 and '!at_freed' in result.stderr)
    report = {'passed': all(r['passed'] for r in results), 'check_count': len(results), 'checks': results,
              'scope': 'Exact recipe source-hash/apply gate and exact adouble_open_rsrc_fsp compiled natively with mocked Samba VFS/allocation APIs, including parent-owned atname lifetime; not real talloc, a full ARM server or filesystem-security test',
              'parent_api_source_sha256': digest(parent_source),
              'parent_api_source_reference': 'source3/modules/vfs_default.c:1417-1424,1460-1466',
              'active_parent_api_modules_sha256': {name: digest(source3/'modules'/name) for name in active_modules},
              'pristine_source_sha256': hashlib.sha256(original).hexdigest(),
              'patched_source_sha256': hashlib.sha256(patched.encode()).hexdigest(),
              'patch_sha256': digest(recipe/'appledouble-parent-fd.patch'),
              'build_driver_sha256': digest(recipe/'build.sh')}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
