#!/usr/bin/env python3
"""One target nmbd -D lifecycle check, with broadcast sockets forced to loopback."""
import argparse
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import time

p = argparse.ArgumentParser()
p.add_argument('root', type=Path)
p.add_argument('--report', type=Path, required=True)
args = p.parse_args()
root = args.root.resolve()
report = {'scope': 'target nmbd daemon lifecycle on loopback only',
          'fixture_override': {'nbt client socket address': '127.0.0.1'},
          'passed': False,
          'limitation': 'nmbd ignores loopback for browser announcements; this checks daemon loading, binding, and stop, not LAN discovery.'}
owned = []
started = False

def live_owned():
    result = []
    for item in Path('/proc').iterdir():
        if not item.name.isdigit():
            continue
        try:
            if (item/'stat').read_text().rsplit(')', 1)[1].split()[0] == 'Z':
                continue
            if os.readlink(item/'root') != str(root):
                continue
            cmd = (item/'cmdline').read_bytes().replace(b'\0', b' ').decode(errors='replace')
            if '/usr/sbin/nmbd ' in cmd:
                result.append(int(item.name))
        except OSError:
            pass
    return result

try:
    if live_owned():
        raise RuntimeError('Fixture already has nmbd; refusing to interfere')
    checks = []
    try:
        for port in (137, 138):
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            checks.append(sock)
            sock.bind(('127.0.0.1', port))
    finally:
        for sock in checks:
            sock.close()
    started = True
    launch = subprocess.run(['chroot', str(root), '/qemu-arm-static', '/usr/sbin/nmbd',
                             '-D', '-s', '/etc/smb.conf',
                             '--option=nbt client socket address=127.0.0.1'],
                            text=True, capture_output=True, timeout=30)
    report['launch'] = {'returncode': launch.returncode,
                        'stdout': launch.stdout, 'stderr': launch.stderr}
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        owned = live_owned()
        if owned:
            break
        time.sleep(0.1)
    time.sleep(0.5)
    owned = live_owned()
    report['running_pids'] = owned
    if launch.returncode or not owned:
        raise RuntimeError('nmbd did not remain running in the loopback fixture')
    inodes = set()
    for pid in owned:
        for fd in (Path('/proc')/str(pid)/'fd').iterdir():
            try:
                link = os.readlink(fd)
                if link.startswith('socket:['):
                    inodes.add(link[8:-1])
            except OSError:
                pass
    sockets = []
    for line in Path('/proc/net/udp').read_text().splitlines()[1:]:
        fields = line.split()
        if fields[9] in inodes:
            address, port = fields[1].split(':')
            sockets.append({'address_hex': address, 'port': int(port, 16)})
    report['udp_sockets'] = sockets
    if ({entry['port'] for entry in sockets} != {137, 138} or
            any(entry['address_hex'] != '0100007F' for entry in sockets)):
        raise RuntimeError('nmbd sockets did not bind exclusively to loopback')
    for pid in owned:
        os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + 5
    while live_owned() and time.monotonic() < deadline:
        time.sleep(0.1)
    report['surviving_pids'] = live_owned()
    if report['surviving_pids']:
        raise RuntimeError('nmbd survived graceful stop')
    report['passed'] = True
except Exception as exc:
    report['error'] = type(exc).__name__ + ': ' + str(exc)
finally:
    for pid in live_owned() if started else []:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    args.report.write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(report, indent=2))
raise SystemExit(0 if report['passed'] else 1)
