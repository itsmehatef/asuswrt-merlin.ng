#!/usr/bin/env python3
"""Run real ARM write_smb_conf with test-only NVRAM/disk inputs in a chroot."""
import argparse
import configparser
import hashlib
import json
from pathlib import Path
import subprocess


def dos_attribute_checks(conf):
    """The backup override must not change the legacy ordinary-share policy."""
    default = conf['global'].get('store dos attributes')
    ordinary = [name for name in conf.sections()
                if name not in ('global', 'Backups.backupdb')]
    checks = {
        'global_dos_attributes_unchanged': default == 'no',
        'ordinary_dos_attributes_unchanged': all(
            conf[name].get('store dos attributes', default) == 'no'
            for name in ordinary),
    }
    if conf.has_section('Backups.backupdb'):
        checks['tm_dos_attributes_enabled'] = (
            conf['Backups.backupdb'].get('store dos attributes') == 'yes')
    return checks


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('root', type=Path)
    parser.add_argument('--report-dir', type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    if root == Path('/') or not (root/'test/fixture.so').is_file():
        parser.error('Expected the disposable generator root with its test-only fixture')
    args.report_dir.mkdir(parents=True, exist_ok=True)
    defaults = {
        'timemachine_enable': '1', 'enable_samba': '1', 'tm_device_name': 'sda1',
        'tm_vol_size': '102400', 'http_username': 'tmtest', 'st_samba_mode': '4',
        'computer_name': 'TMVALIDATE', 'st_samba_workgroup': 'WORKGROUP',
        'lan_ipaddr': '127.0.0.1', 'lan_netmask': '255.0.0.0', 'lan_ifname': 'lo',
        'lan_domain': 'local', 'smbd_simpler_naming': '1', 'smbd_protocol': '0',
        'smbd_master': '1', 'smbd_custom': '0', 'sw_mode': '1',
    }
    cases = [
        ('account_mode', {}, True, 'rw', False),
        ('account_alias_roots', {'test_folder_alias':'1'}, True, 'rw', False),
        ('disabled_alias_roots', {'test_folder_alias':'1', 'timemachine_enable':'0'},
                                 False, 'rw', False),
        ('timemachine_only_mode', {'st_samba_mode':'0'}, True, 'rw', False),
        ('share_name_collision', {'test_folder_collision':'1'}, True, 'rw', False),
        ('guest_mode', {'st_samba_mode':'1', 'st_samba_force_mode':'1'}, True, 'rw', False),
        ('disabled_keeps_folder_protected', {'timemachine_enable':'0'}, False, 'rw', False),
        ('guest_disabled_keeps_folder_protected', {'timemachine_enable':'0', 'st_samba_mode':'1',
                                                 'st_samba_force_mode':'1'}, False, 'rw', False),
        ('samba_disabled', {'enable_samba':'0'}, False, 'rw', False),
        ('missing_mount', {}, False, '', False),
        ('read_only_mount', {}, False, 'ro', False),
        ('unmanaged_folder', {}, False, 'rw', True),
        ('invalid_device', {'tm_device_name':'../sda1'}, False, 'rw', False),
        ('invalid_admin', {'http_username':'tmtest\"other'}, False, 'rw', False),
        ('invalid_quota', {'tm_vol_size':'-1'}, True, 'rw', False),
    ]
    results = []
    binary = root/'sbin/write_smb_conf'
    report = {'generator_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
              'fixture_sha256': hashlib.sha256((root/'test/fixture.so').read_bytes()).hexdigest(),
              'scope': 'actual target executable with test-only hardware/NVRAM responses', 'cases': results}
    marker = root/'tmp/mnt/sda1/TimeMachine/.smb-timemachine'
    # Disposable path fixtures only: no SMB access diagnostic is performed.
    disk = root/'tmp/mnt/sda1'
    (disk/'Files').mkdir(parents=True, exist_ok=True)
    (disk/'TimeMachine/nested').mkdir(parents=True, exist_ok=True)
    other_backup = root/'tmp/mnt/sdb1/TimeMachine'
    (other_backup/'nested').mkdir(parents=True, exist_ok=True)
    (other_backup/'.smb-timemachine').touch()
    alias_targets = {
        'TM-alias': 'TimeMachine',
        'TM-descendant': 'TimeMachine/nested',
        'Other-disk-TM': '/tmp/mnt/sdb1/TimeMachine/nested',
        'Ordinary-alias': 'Files',
    }
    for alias, target in alias_targets.items():
        path = disk/alias
        if path.is_symlink():
            path.unlink()
        elif path.exists():
            raise ValueError('Refusing to replace a non-symlink fixture: ' + str(path))
        path.symlink_to(target)
    for name, overrides, tm_expected, mount_mode, hide_marker in cases:
        values = {**defaults, **overrides}
        (root/'test/nvram').write_text(''.join('%s=%s\n' % item for item in values.items()))
        (root/'proc/mounts').write_text(
            ('/dev/sda1 /tmp/mnt/sda1 ext4 %s 0 0\n' % mount_mode if mount_mode else '') +
            ('/dev/sdb1 /tmp/mnt/sdb1 ext4 rw 0 0\n' if values.get('test_folder_alias') else ''))
        if hide_marker:
            marker.unlink(missing_ok=True)
        else:
            marker.touch()
        proc = subprocess.run(['chroot', str(root), '/qemu-arm-static', '-E',
                               'LD_PRELOAD=/test/fixture.so', '/sbin/write_smb_conf'],
                              capture_output=True, text=True, timeout=30)
        text = (root/'etc/smb.conf').read_text()
        (args.report_dir/(name+'.conf')).write_text(text)
        conf = configparser.ConfigParser(interpolation=None, strict=False, delimiters=('=',))
        conf.read_string(text)
        checks = {'generator_exit_zero': proc.returncode == 0,
                  'tm_presence': conf.has_section('Backups.backupdb') == tm_expected}
        checks.update(dos_attribute_checks(conf))
        if tm_expected and conf.has_section('Backups.backupdb'):
            tm = conf['Backups.backupdb']
            checks.update({
                'tm_only_managed_directory': tm.get('path') == '/tmp/mnt/sda1/TimeMachine',
                'no_guest': tm.get('guest ok') == 'no' and tm.get('guest only') == 'no',
                'admin_only': tm.get('valid users') == '"tmtest"',
                'fruit_enabled': tm.get('fruit:time machine') == 'yes',
                'private_files': tm.get('force create mode') == '0600',
                'private_directories': tm.get('force directory mode') == '0700',
                'marker_vetoed': '.smb-timemachine' in tm.get('veto files', ''),
                'smb2_minimum': conf['global'].get('server min protocol') == 'SMB2_02',
                'smb3_maximum': conf['global'].get('server max protocol') == 'SMB3',
            })
            if name == 'invalid_quota':
                checks['invalid_quota_omitted'] = 'fruit:time machine max size' not in tm
            else:
                checks['quota_correct'] = tm.get('fruit:time machine max size') == '102400M'
        if not hide_marker:
            checks['managed_folder_not_individually_shared'] = not conf.has_section('TimeMachine')
            if values['st_samba_mode'] == '1':
                checks['managed_folder_hidden_from_guest_root'] = 'TimeMachine/' in conf['sda1'].get('veto files', '')
        checks['fruit_on_ordinary_shares'] = conf['global'].get('vfs objects') == 'catia fruit streams_xattr'
        checks['metadata_uses_netatalk'] = conf['global'].get('fruit:metadata') == 'netatalk'
        checks['resource_forks_use_files'] = conf['global'].get('fruit:resource') == 'file'
        checks['local_mdns_owner'] = conf['global'].get('multicast dns register') == 'no'
        if name == 'share_name_collision':
            checks['ordinary_collision_renamed'] = conf.has_section('Backups.backupdb (files 1)')
            if conf.has_section('Backups.backupdb (files 1)'):
                checks['ordinary_collision_path_preserved'] = conf['Backups.backupdb (files 1)'].get('path') == '/tmp/mnt/sda1/Backups.backupdb'
        if values.get('test_folder_alias'):
            checks['managed_root_alias_not_shared'] = not conf.has_section('TM-alias')
            checks['managed_descendant_alias_not_shared'] = not conf.has_section('TM-descendant')
            checks['other_disk_managed_alias_not_shared'] = not conf.has_section('Other-disk-TM')
            checks['ordinary_alias_preserved'] = conf.has_section('Ordinary-alias')
            if conf.has_section('Ordinary-alias'):
                checks['ordinary_alias_path_preserved'] = conf['Ordinary-alias'].get('path') == '/tmp/mnt/sda1/Ordinary-alias'
        results.append({'name': name, 'passed': all(checks.values()), 'checks': checks,
                        'returncode': proc.returncode, 'stderr': proc.stderr})
    marker.touch()
    report['passed'] = all(case['passed'] for case in results)
    report['scenario_count'] = len(results)
    report['assertion_count'] = sum(len(case['checks']) for case in results)
    (args.report_dir/'generator-tests.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report, indent=2))
    raise SystemExit(0 if report['passed'] else 1)


if __name__ == '__main__':
    main()
