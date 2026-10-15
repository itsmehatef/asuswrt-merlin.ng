#!/usr/bin/env python3
"""Compile exact candidate/helper functions; test fixture paths as Linux UID 0."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def extract(text, name):
    start = text.index(name + '(')
    start = text.rfind('\n', 0, start) + 1
    brace = text.index('{', start)
    depth = 1
    end = brace + 1
    while depth:
        depth += (text[end] == '{') - (text[end] == '}')
        end += 1
    return text[start:end]


assert os.geteuid() == 0, 'Fixture ownership checks require UID 0'
candidate = Path(sys.argv[1])
header = Path(sys.argv[2])
function = extract(candidate.read_text(), 'smb_share_root_is_managed_backup')
managed = extract(header.read_text(), 'smb_tm_managed_folder')
assert 'nvram' not in function + managed

with tempfile.TemporaryDirectory(prefix='share-root-exclusion-') as scratch:
    root = Path(scratch)
    source = root / 'test.c'
    binary = root / 'test'
    source.write_text('''#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <limits.h>
#include <sys/stat.h>
#include <unistd.h>
#define SMB_TM_MARKER ".smb-timemachine"
''' + managed + '\n' + function + '''
int main(int argc, char **argv) {
    if (argc != 3) return 2;
    printf("%d\\n", smb_share_root_is_managed_backup(argv[1], argv[2]));
    return 0;
}
''')
    subprocess.run(['gcc', '-std=gnu99', '-Wall', '-Wextra', '-Werror',
                    str(source), '-o', str(binary)], check=True)
    a, b = root / 'disk-a', root / 'disk-b'
    backup = a / 'TimeMachine'
    (backup / 'nested' / 'child').mkdir(parents=True)
    (backup / '.smb-timemachine').write_text('managed fixture\n')
    (a / 'Files').mkdir()
    (a / 'TimeMachine-archive').mkdir()
    (b / 'TimeMachine').mkdir(parents=True)  # Existing unmarked ordinary folder.
    (a / 'root-alias').symlink_to('TimeMachine')
    (a / 'absolute-alias').symlink_to(backup)
    (a / 'child-alias').symlink_to('TimeMachine/nested/child')
    (a / 'Files' / 'nested-alias').symlink_to('../TimeMachine')
    (b / 'cross-disk-alias').symlink_to(backup)
    (b / 'cross-disk-child').symlink_to(backup / 'nested')
    (a / 'ordinary-alias').symlink_to('Files')
    (b / 'ordinary-cross-disk').symlink_to(a / 'Files')
    (a / 'missing-alias').symlink_to('missing-target')

    cases = [
        ('direct backup root', a, 'TimeMachine', 1),
        ('direct backup descendant', a, 'TimeMachine/nested/child', 1),
        ('relative root alias', a, 'root-alias', 1),
        ('absolute root alias', a, 'absolute-alias', 1),
        ('descendant alias', a, 'child-alias', 1),
        ('nested root alias', a, 'Files/nested-alias', 1),
        ('other disk alias', b, 'cross-disk-alias', 1),
        ('other disk descendant', b, 'cross-disk-child', 1),
        ('ordinary directory', a, 'Files', 0),
        ('ordinary alias', a, 'ordinary-alias', 0),
        ('ordinary other disk alias', b, 'ordinary-cross-disk', 0),
        ('similar name directory', a, 'TimeMachine-archive', 0),
        ('unmarked TimeMachine directory', b, 'TimeMachine', 0),
        ('missing directory', a, 'missing', 0),
        ('dangling alias', a, 'missing-alias', 0),
        ('bounded combined path', a, 'x' * 4096, 1),
    ]
    results = []
    for label, mount, folder, expected in cases:
        result = int(subprocess.check_output([str(binary), str(mount), folder], text=True))
        assert result == expected, (label, result, expected)
        results.append({'case': label, 'passed': True})
    print(json.dumps({'scope': 'Exact supplied generator function and managed-folder helper; native Linux path fixtures, not full ARM generator',
                      'generator_source': str(candidate), 'generator_source_sha256': hashlib.sha256(candidate.read_bytes()).hexdigest(),
                      'managed_header': str(header), 'managed_header_sha256': hashlib.sha256(header.read_bytes()).hexdigest(),
                      'passed': True, 'case_count': len(results), 'cases': results,
                      'disabled_state': 'No NVRAM or enable-state dependency; marked folders remain excluded.'}, indent=2))
