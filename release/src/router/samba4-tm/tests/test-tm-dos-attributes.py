#!/usr/bin/env python3
"""Execute exact generator output blocks natively; full ARM suite runs separately."""
import argparse
import configparser
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import tempfile


def parse(text):
    conf = configparser.ConfigParser(interpolation=None, delimiters=('=',))
    conf.read_string(text)
    return conf


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('source', type=Path)
    parser.add_argument('generator_tests', type=Path)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    text = args.source.read_text()
    spec = importlib.util.spec_from_file_location('generator_tests', args.generator_tests)
    tests = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tests)
    global_block = re.search(
        r'#if defined\(RTCONFIG_SAMBA36X\) \|\| defined\(RTCONFIG_SAMBA4\)\n'
        r'\s*fprintf\(fp, "store dos attributes = no\\n"\);\n'
        r'#else\n\s*fprintf\(fp, "store dos attributes = yes\\n"\);\n#endif',
        text).group(0)
    start = text.index('if (tm_ready && tm_globals_ready) {', text.index('confpage:'))
    end = text.index('{', start) + 1
    depth = 1
    while depth:
        depth += (text[end] == '{') - (text[end] == '}')
        end += 1
    tm_block = text[start:end]
    source = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define RTCONFIG_SAMBA4 1
#define SMB_TM_SHARE "Backups.backupdb"
#define SMB_TM_MARKER ".smb-timemachine"
static const char *quota;
static const char *nvram_safe_get(const char *key) {
    return strcmp(key, "tm_vol_size") == 0 ? quota : "tmtest";
}
int main(int argc, char **argv) {
    if (argc != 4) return 2;
    FILE *fp = stdout;
    int tm_ready = atoi(argv[1]), tm_globals_ready = atoi(argv[2]);
    const char *tm_backup = "/tmp/mnt/sda1/TimeMachine";
    quota = argv[3];
    fprintf(fp, "[global]\n");
''' + global_block + r'''
    /* These ordinary stanzas are fixtures; the two production blocks are exact. */
    fprintf(fp, "[Files]\npath = /fixture/Files\n");
    fprintf(fp, "[Backups.backupdb (files 1)]\npath = /fixture/collision\n");
''' + tm_block + '\nreturn 0;\n}\n'
    cases = [
        ('ready', '1', '1', '102400', True),
        ('disabled_or_unavailable', '0', '1', '102400', False),
        ('globals_unavailable', '1', '0', '102400', False),
        ('both_unavailable', '0', '0', '102400', False),
        ('invalid_quota', '1', '1', '-1', True),
    ]
    results = []
    enabled_text = None
    with tempfile.TemporaryDirectory(prefix='tm-dos-attributes-') as scratch:
        cfile, binary = Path(scratch)/'test.c', Path(scratch)/'test'
        cfile.write_text(source)
        subprocess.run(['cc', '-std=c99', '-Wall', '-Wextra', '-Werror',
                        str(cfile), '-o', str(binary)], check=True)
        for name, ready, globals_ready, quota, expected in cases:
            output = subprocess.check_output([str(binary), ready, globals_ready, quota], text=True)
            conf = parse(output)
            checks = tests.dos_attribute_checks(conf)
            checks['tm_presence'] = conf.has_section('Backups.backupdb') == expected
            if expected:
                actual = conf['Backups.backupdb'].get('fruit:time machine max size')
                checks['quota_unchanged'] = actual == (None if quota == '-1' else quota+'M')
            if name == 'ready':
                enabled_text = output
            results.append({'name': name, 'checks': checks, 'passed': all(checks.values())})
    mutations = {
        'legacy_missing_tm_override': enabled_text.replace('store dos attributes = yes\n', ''),
        'global_policy_changed': enabled_text.replace('store dos attributes = no\n', 'store dos attributes = yes\n', 1),
        'ordinary_override_leaked': enabled_text.replace('[Files]\n', '[Files]\nstore dos attributes = yes\n', 1),
    }
    negatives = {name: not all(tests.dos_attribute_checks(parse(value)).values())
                 for name, value in mutations.items()}
    report = {
        'scope': 'Exact production global DOS-attribute block and complete gated Time Machine stanza compiled natively; synthetic ordinary stanzas; not the full target executable or physical filesystem',
        'source_sha256': hashlib.sha256(args.source.read_bytes()).hexdigest(),
        'generator_tests_sha256': hashlib.sha256(args.generator_tests.read_bytes()).hexdigest(),
        'cases': results, 'positive_case_count': len(results),
        'assertion_count': sum(len(case['checks']) for case in results),
        'negative_controls_detected': negatives,
        'passed': all(case['passed'] for case in results) and all(negatives.values()),
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report, indent=2))
    raise SystemExit(0 if report['passed'] else 1)


if __name__ == '__main__':
    main()
