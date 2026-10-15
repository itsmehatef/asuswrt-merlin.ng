#!/usr/bin/env python3
"""Create a disposable target-root copy and test exact ARM Samba on loopback."""
import argparse
import configparser
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import secrets
import shutil
import signal
import socket
import stat
import subprocess
import sys
import time


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--source-root', type=Path, required=True)
    p.add_argument('--stage', type=Path)
    p.add_argument('--work-dir', type=Path, required=True)
    p.add_argument('--config-dir', type=Path, required=True)
    p.add_argument('--client-python', default=sys.executable)
    p.add_argument('--nss-probe', type=Path)
    p.add_argument('--iconv-probe', type=Path)
    p.add_argument('--expected-libc-sha256',
                   default='d64469d372c7b0f5953b3b6cceb3b9fdf53eb48520c3e46c58b66126039eea73')
    p.add_argument('--port', type=int, default=1445)
    p.add_argument('--cycles', type=int, default=3)
    p.add_argument('--variant-label', default='generated firmware configuration')
    p.add_argument('--prepare-only', action='store_true',
                   help='prepare and record a fixture without launching Samba')
    args = p.parse_args()
    if not args.prepare_only and (args.nss_probe is None or args.iconv_probe is None):
        p.error('--nss-probe and --iconv-probe are required for runtime tests')
    if os.getuid() != 0:
        p.error('Run as container UID0; only the disposable fixture is modified.')
    if args.work_dir.exists():
        p.error('Use a fresh work directory, preserving previous test evidence.')
    args.work_dir.mkdir(parents=True)
    work = args.work_dir.resolve()
    root = work/'root'
    subprocess.run(['cp', '-a', '--reflink=auto', str(args.source_root), str(root)], check=True)
    if args.stage:
        # Baseline smbd/nmbd point at one multicall executable. Replacing the
        # directory entry is essential; following those links aliases the new
        # binaries and silently overwrites them with each other.
        subprocess.run(['cp', '-a', '--remove-destination', str(args.stage) + '/.', str(root)], check=True)
    def rp(name):
        """Resolve all firmware symlinks in the fixture's target namespace."""
        virtual = '/' + name.lstrip('/')
        for _ in range(64):
            virtual = os.path.normpath(virtual)
            parts = PurePosixPath(virtual).parts[1:]
            walked = '/'
            for index, part in enumerate(parts):
                candidate = root / walked.lstrip('/') / part
                if candidate.is_symlink():
                    link = os.readlink(candidate)
                    if not link.startswith('/'):
                        link = str(PurePosixPath(walked) / link)
                    virtual = str(PurePosixPath(link).joinpath(*parts[index+1:]))
                    break
                walked = str(PurePosixPath(walked) / part)
            else:
                return root / virtual.lstrip('/')
        raise RuntimeError('Firmware symlink cycle: ' + name)
    shutil.copy2('/usr/bin/qemu-arm-static', rp('qemu-arm-static'))
    for name in ['tmp/etc/samba/private', 'var/lock/samba', 'var/run/samba', 'var/log',
                 'tmp/mnt/sda1/TimeMachine', 'tmp/mnt/sda1/Files', 'test', 'proc', 'dev']:
        rp(name).mkdir(parents=True, exist_ok=True)
    for name, minor in [('null',3), ('random',8), ('urandom',9), ('zero',5)]:
        node = rp('dev/'+name)
        if not node.exists():
            os.mknod(str(node), stat.S_IFCHR | 0o666, os.makedev(1,minor))
    # Match create_passwd/create_custom_passwd: configured administrator is
    # the first UID0 entry, NAS is100, first non-admin USB account is501.
    rp('etc/passwd').write_text('tmtest:x:0:0:Test administrator:/root:/bin/sh\n'
                                 'nas:x:100:100:nas:/dev/null:/dev/null\n'
                                 'otheruser:x:501:501:Other test user:/dev/null:/dev/null\n'
                                 'nobody:x:65534:65534:nobody:/tmp:/bin/false\n')
    rp('etc/group').write_text('root:x:0:\nnas:x:100:\notheruser:x:501:\nnobody:x:65534:\n')
    rp('etc/nsswitch.conf').write_text('passwd: files\ngroup: files\nshadow: files\nhosts: files\n')
    rp('etc/hosts').write_text('127.0.0.1 localhost TMVALIDATE\n')
    rp('etc/resolv.conf').parent.mkdir(parents=True, exist_ok=True)
    rp('etc/resolv.conf').write_text('')
    rp('proc/mounts').write_text('/dev/sda1 /tmp/mnt/sda1 ext4 rw 0 0\n')
    rp('tmp/mnt/sda1/TimeMachine/.smb-timemachine').touch()
    for directory in ['tmp/mnt/sda1/TimeMachine', 'etc/samba/private']:
        os.chown(rp(directory), 0, 0)
        os.chmod(rp(directory), 0o700)
    # The disposable ordinary share permits both USB accounts to write; the
    # dedicated Time Machine directory retains its private 0700 permissions.
    for directory in ['tmp/mnt/sda1', 'tmp/mnt/sda1/Files']:
        os.chmod(rp(directory), 0o777)
    # Bounded parent-directory traversal controls for the AppleDouble fix.
    # These paths are confined to this new disposable fixture.
    rp('tmp/mnt/sda1/TimeMachine/tm-resource-safe-target').mkdir()
    rp('tmp/mnt/sda1/TimeMachine/tm-resource-safe-alias').symlink_to('tm-resource-safe-target')
    rp('tmp/mnt/sda1/TimeMachine/tm-resource-outside').symlink_to('../Files')
    canary_data = b'outside-share canary remains unchanged'
    rp('tmp/mnt/sda1/Files/tm-resource-outside-canary.bin').write_bytes(canary_data)
    # Exercise UTF-8 credential import and NTLM's UTF-16 conversion, including
    # a non-BMP character, without using or recording real user credentials.
    secret = 'TM-test-É-تهران-雪-💾-' + secrets.token_urlsafe(24)
    password_file = work/'test-password'
    password_file.write_text(secret+'\n')
    password_file.chmod(0o600)
    authfile = rp('test/client.auth')
    authfile.write_text('username = tmtest\npassword = '+secret+'\ndomain = WORKGROUP\n')
    authfile.chmod(0o600)
    prefix = ['chroot', str(root), '/qemu-arm-static']
    report = {'source_root':str(args.source_root), 'overlay_stage':str(args.stage) if args.stage else None,
              'configuration_directory': str(args.config_dir), 'configuration_variant': args.variant_label,
              'scope':'exact target ARM Samba with generated fixture configuration in local chroot',
              'fixture_overrides':{'interfaces':'127.0.0.1', 'bind interfaces only':'yes',
                                   'smb ports':str(args.port), 'log level':'3'},
              'runs':[], 'passed':False}
    report['test_password_character_classes'] = ['ASCII', 'Latin accented', 'Persian', 'CJK', 'non-BMP emoji']
    report['resource_parent_fixture'] = {
        'within_share_alias': 'TimeMachine/tm-resource-safe-alias -> tm-resource-safe-target',
        'outside_share_alias': 'TimeMachine/tm-resource-outside -> ../Files',
        'outside_canary_sha256': hashlib.sha256(canary_data).hexdigest(),
    }
    for rel in ['usr/sbin/smbd','usr/sbin/nmbd','usr/bin/smbpasswd','usr/bin/smbclient']:
        path = rp(rel)
        if path.is_file():
            report.setdefault('target_sha256',{})['/'+rel]=hashlib.sha256(path.read_bytes()).hexdigest()
    def save():
        (work/'runtime-report.json').write_text(json.dumps(report,indent=2)+'\n')
    if args.prepare_only:
        report['fixture_prepared'] = True
        report['scope'] = 'fixture preparation only; no Samba tests executed'
        save()
        print(json.dumps(report, indent=2))
        return
    def target(argv, **kwargs):
        return subprocess.run(prefix+argv, text=True, capture_output=True, timeout=60, **kwargs)
    def processes():
        found=[]
        for item in Path('/proc').iterdir():
            if not item.name.isdigit(): continue
            try:
                state=(item/'stat').read_text().rsplit(')',1)[1].split()[0]
                if state == 'Z':
                    continue  # exited; cannot hold ports or Samba state locks
                if os.readlink(item/'root') != str(root): continue
                comm=(item/'comm').read_text().strip()
                cmd=(item/'cmdline').read_bytes().replace(b'\0',b' ').decode(errors='replace')
                found.append({'pid':int(item.name),'comm':comm,'cmdline':cmd,'state':state})
            except (OSError,PermissionError): pass
        return found
    def stop_fixture():
        before=processes()
        # Production first stops smbd/nmbd, then the verified detached RPC
        # process group. QEMU changes /proc/PID/exe, so this harness uses the
        # test-root boundary plus command name in place of that native check.
        names={'smbd','nmbd'}
        for proc in before:
            if proc['comm'] in names or any('/'+name+' ' in proc['cmdline'] for name in names):
                try: os.kill(proc['pid'],signal.SIGTERM)
                except ProcessLookupError: pass
        pidfile=rp('var/run/samba/samba-dcerpcd.pid')
        try:
            rpcpid=int(pidfile.read_text().strip())
            process=next((item for item in processes() if item['pid']==rpcpid),None)
            if process and ('samba-dcerpcd' in process['comm'] or 'samba-dcerpcd' in process['cmdline']):
                if os.getpgid(rpcpid)==rpcpid:
                    os.killpg(rpcpid,signal.SIGTERM)
        except (ValueError,OSError):
            pass
        deadline=time.monotonic()+8
        while processes() and time.monotonic()<deadline:
            time.sleep(0.1)
        after=processes()
        for proc in after:
            try: os.kill(proc['pid'],signal.SIGKILL)
            except ProcessLookupError: pass
        return before,after
    server=None
    report['shutdown_fixture_override']='QEMU executable identity differs; target-root+command+group-leader checks replace native /proc/PID/exe path check. Exact C control flow is tested separately.'
    try:
        libc_hash = hashlib.sha256(rp('lib/libc.so.6').read_bytes()).hexdigest()
        report['baseline_libc_preserved'] = {'expected_sha256': args.expected_libc_sha256,
                                            'actual_sha256': libc_hash,
                                            'passed': libc_hash == args.expected_libc_sha256}
        if libc_hash != args.expected_libc_sha256:
            raise RuntimeError('The packaged libc differs from the verified baseline')
        gconv_dir = rp('usr/lib/gconv')
        gconv_files = sorted(item.name for item in gconv_dir.iterdir()) if gconv_dir.is_dir() else []
        report['gconv_package'] = {'files': gconv_files,
                                  'passed': gconv_files == ['IBM850.so', 'UTF-16.so', 'gconv-modules']}
        if not report['gconv_package']['passed']:
            raise RuntimeError('Unexpected packaged gconv module inventory')
        report['gconv_package']['sha256'] = {
            name: hashlib.sha256((gconv_dir/name).read_bytes()).hexdigest() for name in gconv_files
        }
        shutil.copy2(args.nss_probe, rp('test/probe-nss'))
        nss = target(['/test/probe-nss'])
        (work/'nss-preflight.log').write_text(nss.stdout+nss.stderr)
        report['nss_preflight'] = {'returncode': nss.returncode,
                                  'fixture_probe_sha256': hashlib.sha256(args.nss_probe.read_bytes()).hexdigest()}
        if nss.returncode:
            raise RuntimeError('Target NSS or fixture credential preflight failed')
        shutil.copy2(args.iconv_probe, rp('test/probe-iconv'))
        conversion = target(['/test/probe-iconv'])
        (work/'iconv-preflight.log').write_text(conversion.stdout+conversion.stderr)
        report['iconv_preflight'] = {'returncode': conversion.returncode,
                                    'fixture_probe_sha256': hashlib.sha256(args.iconv_probe.read_bytes()).hexdigest()}
        if conversion.returncode:
            raise RuntimeError('Target packaged charset conversion preflight failed')
        rpc_path = '/usr/lib/tm-samba/libexec/samba/samba-dcerpcd'
        rpc_entry = root / rpc_path.lstrip('/')
        rpc_regular_elf = rpc_entry.is_file() and not rpc_entry.is_symlink()
        if rpc_regular_elf:
            with rpc_entry.open('rb') as f:
                rpc_regular_elf = f.read(4) == b'\x7fELF'
        report['rpc_helper'] = {'path': rpc_path, 'regular_elf': rpc_regular_elf}
        if not rpc_regular_elf:
            raise RuntimeError('Expected production RPC helper is not a regular ELF file')
        targets=['/usr/sbin/smbd','/usr/sbin/nmbd','/usr/bin/smbpasswd','/usr/bin/testparm',
                 '/usr/bin/smbclient','/usr/bin/pdbedit']
        # Audit real loader entry points recursively. Private DSOs can legally
        # inherit their executable's DT_RPATH; treating each as an independent
        # process creates false missing-library findings without that ancestry.
        for directory in ['usr/lib/tm-samba/modules', 'usr/lib/tm-samba/libexec', 'usr/lib/gconv']:
            for item in rp(directory).rglob('*'):
                if item.is_file() and not item.is_symlink():
                    with item.open('rb') as f:
                        if f.read(4)==b'\x7fELF': targets.append('/'+str(item.relative_to(root)))
        elf_audit=subprocess.run(['python3',str(Path(__file__).with_name('audit_elf.py')),
                                 str(root),*targets,'--report',str(work/'samba-elf.json')],
                                text=True,capture_output=True,timeout=120)
        (work/'samba-elf-summary.log').write_text(elf_audit.stdout+elf_audit.stderr)
        if elf_audit.returncode: raise RuntimeError('Target Samba dependency audit failed')
        version=target(['/usr/sbin/smbd','--version'])
        report['version']={'returncode':version.returncode,'stdout':version.stdout,'stderr':version.stderr}
        if version.returncode: raise RuntimeError('Target smbd version check failed')
        nmbd_version=target(['/usr/sbin/nmbd','--version'])
        report['nmbd_version']={'returncode':nmbd_version.returncode,
                               'stdout':nmbd_version.stdout,'stderr':nmbd_version.stderr}
        if nmbd_version.returncode: raise RuntimeError('Target nmbd loader/version check failed')
        report['generated_config_validation'] = []
        for config_path in sorted(args.config_dir.glob('*.conf')):
            fixture_config = '/test/generated-' + config_path.name
            shutil.copy2(config_path, rp(fixture_config))
            validation = target(['/usr/bin/testparm', '-s', fixture_config])
            output = validation.stdout + validation.stderr
            (work/('generated-'+config_path.stem+'-testparm.log')).write_text(output)
            valid = validation.returncode == 0 and 'Unknown parameter' not in output and 'Ignoring unknown parameter' not in output
            report['generated_config_validation'].append({'config': config_path.name,
                                                         'returncode': validation.returncode,
                                                         'passed': valid})
            if not valid:
                raise RuntimeError('Target testparm rejected generated configuration: ' + config_path.name)
        with socket.socket() as sock:
            if sock.connect_ex(('127.0.0.1',args.port)) == 0:
                raise RuntimeError('Test port is already in use; refusing to interfere')
        for mode in ['account_mode','guest_mode']:
            cfg=configparser.ConfigParser(interpolation=None,strict=False,delimiters=('=',))
            cfg.read(args.config_dir/(mode+'.conf'))
            for key,value in report['fixture_overrides'].items(): cfg['global'][key]=value
            with rp('etc/smb.conf').open('w') as f: cfg.write(f)
            shutil.copy2(rp('etc/smb.conf'), work/(mode+'-runtime.conf'))
            validate=target(['/usr/bin/testparm','-s','/etc/smb.conf'])
            (work/(mode+'-testparm.log')).write_text(validate.stdout+validate.stderr)
            if validate.returncode or 'Unknown parameter' in validate.stderr or 'Ignoring unknown parameter' in validate.stderr:
                raise RuntimeError('Target testparm rejected '+mode+' or found unsupported options')
            for username in ['tmtest','otheruser']:
                result=target(['/usr/sbin/smbpasswd','-c','/etc/smb.conf','-s','-a',username],input=secret+'\n'+secret+'\n')
                (work/(mode+'-'+username+'-import.log')).write_text(result.stdout+result.stderr)
                if result.returncode: raise RuntimeError('Target account import failed for '+username)
            for cycle in range(args.cycles):
                label=mode+'-cycle'+str(cycle+1)
                daemonized = cycle == args.cycles-1
                entry={'mode':mode,'cycle':cycle+1,
                       'launch_mode':'production -D' if daemonized else 'foreground --no-process-group'}
                report['runs'].append(entry)
                with (work/(label+'-server.log')).open('w') as log:
                    if daemonized:
                        launch=target(['/usr/sbin/smbd','-D','-s','/etc/smb.conf'])
                        log.write(launch.stdout+launch.stderr)
                        log.flush()
                        entry['daemon_launch_exitcode']=launch.returncode
                        if launch.returncode: raise RuntimeError('Production daemon launch failed: '+label)
                    else:
                        server=subprocess.Popen(prefix+['/usr/sbin/smbd','--foreground','--no-process-group',
                                                         '--debug-stdout','--configfile=/etc/smb.conf'],
                                                stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
                    deadline=time.monotonic()+30
                    listening=False
                    while time.monotonic()<deadline and (daemonized or server.poll() is None):
                        with socket.socket() as sock:
                            if sock.connect_ex(('127.0.0.1',args.port)) == 0:
                                listening=True; break
                        time.sleep(0.1)
                    if not listening: raise RuntimeError('Target smbd did not listen: '+label)
                    listing=target(['/usr/bin/smbclient','-L','127.0.0.1','-p',str(args.port),
                                    '-A','/test/client.auth','-m','SMB3','--option=client min protocol=SMB2'])
                    (work/(label+'-rpc-list.log')).write_text(listing.stdout+listing.stderr)
                    entry['rpc_enumeration']={'returncode':listing.returncode,'backup_share_listed':'Backups.backupdb' in listing.stdout}
                    if listing.returncode or 'Backups.backupdb' not in listing.stdout:
                        raise RuntimeError('RPC share enumeration failed: '+label)
                    ordinary='Files' if mode=='account_mode' else 'sda1'
                    result=subprocess.run([args.client_python,str(Path(__file__).with_name('probe_smb.py')),
                                           '--port',str(args.port),'--share','Backups.backupdb','--ordinary-share',ordinary,
                                           '--config-file',str(work/(mode+'-runtime.conf')),
                                           '--password-file',str(password_file),'--report',str(work/(label+'-protocol.json'))],
                                          text=True,capture_output=True,timeout=180)
                    (work/(label+'-client.log')).write_text(result.stdout+result.stderr)
                    entry['protocol_returncode']=result.returncode
                    before,after=stop_fixture()
                    entry['processes_before_shutdown']=before
                    entry['processes_after_graceful_shutdown']=after
                    if server:
                        server.wait(timeout=10)
                        server=None
                    if rp('var/log/samba.log').exists():
                        shutil.copy2(rp('var/log/samba.log'),work/(label+'-samba.log'))
                    save()
                    if result.returncode: raise RuntimeError('SMB protocol validation failed: '+label)
                    if after: raise RuntimeError('Samba processes survived fixture shutdown: '+label)
                    # Production clears this volatile PID directory after stop.
                    shutil.rmtree(rp('var/run/samba'))
                    rp('var/run/samba').mkdir()
        report['passed']=True
    except Exception as exc:
        report['error']=type(exc).__name__+': '+str(exc)
        raise
    finally:
        report['cleanup_processes']=stop_fixture()[1]
        if server:
            try: server.wait(timeout=10)
            except subprocess.TimeoutExpired: pass
        save()
    print(json.dumps(report,indent=2))


if __name__=='__main__':
    main()
