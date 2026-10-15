#!/usr/bin/env python3
"""Audit target ELF dependency closure without resolving outside a staged root.

Pass the exact extracted firmware root and all Samba executables/modules. This
checks library presence and ELF architecture. Runtime loader checks complement
it by validating symbol versions, initialization, and dynamically loaded NSS.
"""
import argparse
import json
import os
from pathlib import Path, PurePosixPath
import re
import struct
import subprocess


def main():
    p = argparse.ArgumentParser()
    p.add_argument('root', type=Path)
    p.add_argument('paths', nargs='*')
    p.add_argument('--all', action='store_true', help='inspect every executable/shared ELF in the root')
    p.add_argument('--report', required=True)
    p.add_argument('--library-path', default='', help='explicit LD_LIBRARY_PATH for this audit; normally empty')
    args = p.parse_args()
    root = args.root.resolve()
    search = [x for x in args.library_path.split(':') if x]
    report = {'root': str(root), 'requested_paths': args.paths, 'objects': [], 'errors': []}
    visited = {}
    if args.all:
        for candidate in root.rglob('*'):
            if candidate.is_symlink() or not candidate.is_file():
                continue
            with candidate.open('rb') as f:
                header = f.read(20)
            if len(header) == 20 and header[:4] == b'\x7fELF':
                endianness = '<' if header[5] == 1 else '>'
                if struct.unpack_from(endianness+'H', header, 16)[0] in (2, 3):
                    args.paths.append('/' + str(candidate.relative_to(root)))

    def normalize(path):
        return '/' + os.path.normpath('/' + path.lstrip('/')).lstrip('/')

    def resolve(path):
        """Resolve absolute and relative symlinks in the target namespace."""
        path = normalize(path)
        for _ in range(64):
            parts = PurePosixPath(path).parts[1:]
            walked = '/'
            changed = False
            for index, part in enumerate(parts):
                candidate = root / walked.lstrip('/') / part
                if candidate.is_symlink():
                    target = os.readlink(candidate)
                    if not target.startswith('/'):
                        target = str(PurePosixPath(walked) / target)
                    path = normalize(str(PurePosixPath(target).joinpath(*parts[index+1:])))
                    changed = True
                    break
                walked = str(PurePosixPath(walked) / part)
            if not changed:
                return path if (root / path.lstrip('/')).is_file() else None
        return None

    def inspect(path, expected_machine=None, inherited_paths=()):
        resolved = resolve(path)
        if resolved is None:
            report['errors'].append({'path': path, 'error': 'missing file or symlink cycle'})
            return
        visit_key = (resolved, tuple(inherited_paths))
        if visit_key in visited:
            if expected_machine and visited[visit_key] != expected_machine:
                report['errors'].append({'path': path, 'error': 'architecture mismatch',
                                         'expected': expected_machine, 'actual': visited[visit_key]})
            return
        physical = root / resolved.lstrip('/')
        output = subprocess.run(['readelf', '-hld', str(physical)],
                                text=True, capture_output=True)
        if output.returncode:
            report['errors'].append({'path': path, 'error': 'readelf failed', 'details': output.stderr})
            return
        text = output.stdout
        machine_match = re.search(r'^\s*Machine:\s+(.+)$', text, re.M)
        machine = machine_match.group(1).strip() if machine_match else None
        visited[visit_key] = machine
        elfclass_match = re.search(r'^\s*Class:\s+(\S+)', text, re.M)
        elfclass = elfclass_match.group(1) if elfclass_match else None
        if expected_machine and machine != expected_machine:
            report['errors'].append({'path': path, 'error': 'architecture mismatch',
                                     'expected': expected_machine, 'actual': machine})
        entry = {'path': path, 'resolved': resolved, 'machine': machine, 'class': elfclass,
                 'bytes': physical.stat().st_size, 'dependencies': []}
        report['objects'].append(entry)
        needed = re.findall(r'\(NEEDED\).*?\[([^\]]+)\]', text)
        interp = re.search(r'Requesting program interpreter:\s*([^\]]+)\]', text)
        if interp:
            entry['interpreter'] = interp.group(1)
            inspect(interp.group(1), machine)
        origin = str(PurePosixPath(resolved).parent)
        runpath_values = re.findall(r'\(RUNPATH\).*?\[([^\]]*)\]', text)
        rpath_values = re.findall(r'\(RPATH\).*?\[([^\]]*)\]', text)
        def expand(values):
            return [part.replace('${ORIGIN}', origin).replace('$ORIGIN', origin)
                    for item in values for part in item.split(':') if part]
        runpaths = expand(runpath_values)
        # glibc ignores DT_RPATH when the same object has DT_RUNPATH. Only
        # RPATH continues through the loader ancestry; RUNPATH is direct-only.
        rpaths = [] if runpath_values else expand(rpath_values)
        entry['rpath'] = rpaths
        entry['runpath'] = runpaths
        for location in rpaths + runpaths:
            if location == '/build' or location.startswith('/build/'):
                report['errors'].append({'path': path, 'error': 'build directory in target library path',
                                         'library_path': location})
        inherited_for_children = list(dict.fromkeys(rpaths + list(inherited_paths)))
        paths = inherited_for_children + search + runpaths + ['/lib', '/usr/lib']
        for lib in needed:
            found = None
            candidates = [lib] if '/' in lib else [str(PurePosixPath(d)/lib) for d in paths]
            for candidate in candidates:
                found = resolve(candidate)
                if found:
                    break
            entry['dependencies'].append({'soname': lib, 'resolved': found})
            if not found:
                report['errors'].append({'path': path, 'error': 'missing dependency', 'soname': lib})
            else:
                inspect(found, machine, inherited_for_children)

    for item in args.paths:
        inspect(item)
    report['passed'] = not report['errors']
    Path(args.report).write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'passed': report['passed'], 'objects': len(report['objects']),
                      'errors': report['errors']}, indent=2))
    raise SystemExit(0 if report['passed'] else 1)


if __name__ == '__main__':
    main()
