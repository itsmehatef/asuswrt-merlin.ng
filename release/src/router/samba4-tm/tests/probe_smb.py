#!/usr/bin/env python3
"""Exercise the target Samba over loopback. Requires smbprotocol in an isolated venv.

This is a protocol test against a disposable local test share, not a Time Machine
backup test. Test files stay within that disposable share; mutation cases also
verify removal of their own fresh files and directories.
"""
import argparse
import configparser
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import struct
import sys
import time
import uuid

from smbprotocol.connection import Capabilities, Connection
from smbprotocol.create_contexts import (
    SMB2CreateContextRequest, SMB2CreateDurableHandleRequestV2,
    SMB2CreateDurableHandleReconnectV2, SMB2CreateDurableHandleResponseV2,
    SMB2CreateRequestLeaseV2, SMB2CreateResponseLeaseV2,
)
from smbprotocol.exceptions import SMBResponseException
from smbprotocol.file_info import FileFsFullSizeInformation
from smbprotocol.open import (
    Open, CreateDisposition, CreateOptions, FileAttributes,
    FilePipePrinterAccessMask, ImpersonationLevel, RequestedOplockLevel, ShareAccess,
    SMB2QueryInfoRequest, SMB2QueryInfoResponse,
    SMB2SetInfoRequest, SMB2SetInfoResponse,
)
from smbprotocol.session import Session
from smbprotocol.tree import TreeConnect


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--host', default='127.0.0.1')
    p.add_argument('--port', type=int, default=1445)
    p.add_argument('--share', default='Backups.backupdb')
    p.add_argument('--ordinary-share', default='Files')
    p.add_argument('--username', default='tmtest')
    p.add_argument('--other-user', default='otheruser')
    p.add_argument('--password-file', required=True)
    p.add_argument('--report', required=True)
    p.add_argument('--config-file', required=True,
                   help='Exact generated runtime configuration used by this server')
    p.add_argument('--expected-quota-mib', type=int, default=102400)
    args = p.parse_args()
    if not ipaddress.ip_address(args.host).is_loopback:
        p.error('This validation script only permits loopback destinations.')
    password = Path(args.password_file).read_text().rstrip('\r\n')
    report = {'scope': 'local qemu target Samba protocol tests', 'host': args.host,
              'port': args.port, 'tests': [], 'limitations': [
                  'Not a full macOS Time Machine backup or restore test.',
                  'Does not exercise router hardware, NAND capacity, or the Tuxera HFS+ driver.'
              ]}
    connections = []

    def save():
        Path(args.report).write_text(json.dumps(report, indent=2) + '\n')

    def check(name, value, **details):
        report['tests'].append({'name': name, 'passed': bool(value), **details})
        save()
        if not value:
            raise AssertionError(name)

    def connect(username=args.username, secret=password, guid=None, signing=True,
                encryption=False, share=None):
        c = Connection(guid or uuid.uuid4(), args.host, args.port, require_signing=signing)
        # smbprotocol's default high-level client does not request leases; this
        # low-level probe explicitly exercises the lease/durable protocol.
        c.client_capabilities |= Capabilities.SMB2_GLOBAL_CAP_LEASING
        connections.append(c)
        c.connect(timeout=30)
        s = Session(c, username=username, password=secret,
                    require_encryption=encryption, auth_protocol='ntlm')
        s.connect()
        t = TreeConnect(s, '\\\\%s\\%s' % (args.host, share or args.share))
        # Intentional guest controls have no session signing key. Authenticated
        # backup connections retain the client's negotiation verification.
        t.connect(require_secure_negotiate=signing)
        return c, s, t

    def open_file(t, name, contexts=None, lease=False, disposition=None):
        f = Open(t, name)
        reply = f.create(
            ImpersonationLevel.Impersonation,
            FilePipePrinterAccessMask.GENERIC_READ | FilePipePrinterAccessMask.GENERIC_WRITE,
            FileAttributes.FILE_ATTRIBUTE_NORMAL,
            ShareAccess.FILE_SHARE_READ | ShareAccess.FILE_SHARE_WRITE | ShareAccess.FILE_SHARE_DELETE,
            disposition if disposition is not None else CreateDisposition.FILE_OVERWRITE_IF,
            CreateOptions.FILE_NON_DIRECTORY_FILE,
            create_contexts=contexts,
            oplock_level=(RequestedOplockLevel.SMB2_OPLOCK_LEVEL_LEASE if lease
                          else RequestedOplockLevel.SMB2_OPLOCK_LEVEL_NONE),
        )
        return f, reply or []

    def read_all(f, size):
        data = bytearray()
        while len(data) < size:
            part = f.read(len(data), min(size-len(data), 65536))
            if not part:
                break
            data.extend(part)
        return bytes(data)

    def write_all(f, data):
        offset = 0
        while offset < len(data):
            count = f.write(data[offset:offset + 65536], offset=offset, write_through=True)
            if count <= 0:
                raise RuntimeError('SMB write made no progress')
            offset += count
        f.flush()

    def mutation_handle(tree, name, directory=False):
        # Match the DELETE-access CREATE that failed on the physical router.
        # Merely creating/reading a file never exercises open_streams_for_delete.
        desired_access = 0x00110080  # SYNCHRONIZE | DELETE | FILE_READ_ATTRIBUTES
        report['last_mutation_operation'] = {'operation': 'DELETE-access CREATE',
                                      'path': name, 'desired_access': hex(desired_access)}
        f = Open(tree, name)
        f.create(ImpersonationLevel.Impersonation, desired_access,
                 FileAttributes.FILE_ATTRIBUTE_DIRECTORY if directory else FileAttributes.FILE_ATTRIBUTE_NORMAL,
                 ShareAccess.FILE_SHARE_READ | ShareAccess.FILE_SHARE_WRITE | ShareAccess.FILE_SHARE_DELETE,
                 CreateDisposition.FILE_OPEN,
                 CreateOptions.FILE_DIRECTORY_FILE if directory else CreateOptions.FILE_NON_DIRECTORY_FILE)
        return f

    def set_info(tree, f, info_class, value):
        request = SMB2SetInfoRequest()
        request['info_type'] = 1  # SMB2_0_INFO_FILE
        request['file_info_class'] = info_class
        request['file_id'] = f.file_id
        request['buffer'] = value
        connection = tree.session.connection
        sent = connection.send(request, tree.session.session_id, tree.tree_connect_id)
        response = connection.receive(sent, timeout=30)
        parsed = SMB2SetInfoResponse()
        parsed.unpack(response['data'].get_value())

    def rename(tree, old, new, directory=False, replace=False):
        f = mutation_handle(tree, old, directory=directory)
        try:
            report['last_mutation_operation'] = {'operation': 'FileRenameInformation',
                                          'source': old, 'destination': new,
                                          'replace_if_exists': replace}
            encoded = new.encode('utf-16-le')
            # MS-FSCC FileRenameInformation: replace byte, seven reserved bytes,
            # zero RootDirectory, name byte length, and UTF-16 relative name.
            value = struct.pack('<B7xQI', int(replace), 0, len(encoded)) + encoded
            set_info(tree, f, 10, value)
        finally:
            f.close()

    def remove(tree, name, directory=False):
        f = mutation_handle(tree, name, directory=directory)
        try:
            report['last_mutation_operation'] = {'operation': 'FileDispositionInformation',
                                          'path': name, 'directory': directory}
            set_info(tree, f, 13, b'\x01')  # DeletePending; deletion is verified after CLOSE.
        finally:
            f.close()

    def mkdir(tree, name):
        f = Open(tree, name)
        f.create(ImpersonationLevel.Impersonation,
                 FilePipePrinterAccessMask.FILE_READ_ATTRIBUTES,
                 FileAttributes.FILE_ATTRIBUTE_DIRECTORY,
                 ShareAccess.FILE_SHARE_READ | ShareAccess.FILE_SHARE_WRITE | ShareAccess.FILE_SHARE_DELETE,
                 CreateDisposition.FILE_CREATE, CreateOptions.FILE_DIRECTORY_FILE)
        f.close()

    def absent(tree, name, directory=False):
        f = Open(tree, name)
        try:
            f.create(ImpersonationLevel.Impersonation,
                     FilePipePrinterAccessMask.FILE_READ_ATTRIBUTES,
                     FileAttributes.FILE_ATTRIBUTE_DIRECTORY if directory else FileAttributes.FILE_ATTRIBUTE_NORMAL,
                     ShareAccess.FILE_SHARE_READ | ShareAccess.FILE_SHARE_WRITE | ShareAccess.FILE_SHARE_DELETE,
                     CreateDisposition.FILE_OPEN,
                     CreateOptions.FILE_DIRECTORY_FILE if directory else CreateOptions.FILE_NON_DIRECTORY_FILE)
        except SMBResponseException as exc:
            if exc.status in (0xC0000034, 0xC000003A):  # NAME_NOT_FOUND / PATH_NOT_FOUND only.
                return True
            raise
        f.close()
        return False

    def read_named(tree, name, size):
        f, _ = open_file(tree, name, disposition=CreateDisposition.FILE_OPEN)
        try:
            return read_all(f, size)
        finally:
            f.close()

    def require_open_rejected(label, tree, name, disposition, expected_status):
        status = None
        try:
            f, _ = open_file(tree, name, disposition=disposition)
            f.close()
        except SMBResponseException as exc:
            status = exc.status
        check(label, status == expected_status, requested_path=name,
              status=hex(status) if status is not None else None,
              expected_status=hex(expected_status))

    try:
        config_path = Path(args.config_file)
        config = configparser.ConfigParser(interpolation=None, strict=False, delimiters=('=',))
        config.read(config_path)
        report['configuration'] = {'path': str(config_path),
                                   'sha256': hashlib.sha256(config_path.read_bytes()).hexdigest()}
        ordinary_dos = config.get(args.ordinary_share, 'store dos attributes',
                                  fallback=config.get('global', 'store dos attributes'))
        veto_appledouble = config.get(args.share, 'fruit:veto_appledouble',
            fallback=config.get('global', 'fruit:veto_appledouble', fallback='yes'))
        check('tm2_generated_metadata_configuration',
              config.get('global', 'store dos attributes') == 'no' and
              config.get(args.share, 'store dos attributes') == 'yes' and
              ordinary_dos == 'no' and
              config.get('global', 'fruit:metadata') == 'netatalk' and
              config.get('global', 'fruit:resource') == 'file' and
              veto_appledouble == 'yes',
              global_store_dos_attributes=config.get('global', 'store dos attributes'),
              backup_store_dos_attributes=config.get(args.share, 'store dos attributes'),
              ordinary_store_dos_attributes=ordinary_dos,
              effective_fruit_veto_appledouble=veto_appledouble)
        for label, username, secret in [
            ('wrong_password_rejected', args.username, password + '-incorrect'),
            ('unknown_user_cannot_access_timemachine', 'tm-validation-no-such-user', password),
            ('other_valid_user_cannot_access_timemachine', args.other_user, password),
        ]:
            rejected = False
            status = None
            try:
                bad, _, _ = connect(username, secret, signing=False)
                bad.disconnect()
            except SMBResponseException as exc:
                status = hex(exc.status)
                # Only an authentication or access rejection counts as success.
                rejected = exc.status in (0xC000006D, 0xC0000022, 0xC0000064,
                                          0xC000006A, 0xC000015B)
            check(label, rejected, status=status)

        client_guid = uuid.uuid4()
        c, s, t = connect(guid=client_guid, share=args.ordinary_share or args.share)
        check('authenticated_smb3', c.dialect >= 0x0300, dialect=hex(c.dialect))
        aapl = SMB2CreateContextRequest()
        aapl['buffer_name'] = b'AAPL'
        # command=SERVER_QUERY, request bitmap=server/volume/model, client caps=7.
        aapl['buffer_data'] = struct.pack('<IIQQ', 1, 0, 7, 7)
        # macOS negotiates AAPL on its first tree connection; ordinary shares
        # must retain fruit too, or a first visit there can break Time Machine.
        if args.ordinary_share:
            first_tree = t
            first_root = Open(first_tree, '')
            first_replies = first_root.create(
                ImpersonationLevel.Impersonation,
                FilePipePrinterAccessMask.FILE_READ_ATTRIBUTES,
                FileAttributes.FILE_ATTRIBUTE_DIRECTORY,
                ShareAccess.FILE_SHARE_READ | ShareAccess.FILE_SHARE_WRITE | ShareAccess.FILE_SHARE_DELETE,
                CreateDisposition.FILE_OPEN, CreateOptions.FILE_DIRECTORY_FILE,
                create_contexts=[aapl],
            ) or []
            first_aapl = next((x for x in first_replies if isinstance(x, bytes)), b'')
            check('ordinary_share_first_aapl', len(first_aapl) >= 32, bytes=len(first_aapl))
            first_root.close()
            t = TreeConnect(s, '\\\\%s\\%s' % (args.host, args.share))
            t.connect()
        root = Open(t, '')
        replies = root.create(
            ImpersonationLevel.Impersonation,
            FilePipePrinterAccessMask.FILE_READ_ATTRIBUTES,
            FileAttributes.FILE_ATTRIBUTE_DIRECTORY,
            ShareAccess.FILE_SHARE_READ | ShareAccess.FILE_SHARE_WRITE | ShareAccess.FILE_SHARE_DELETE,
            CreateDisposition.FILE_OPEN, CreateOptions.FILE_DIRECTORY_FILE,
            create_contexts=[aapl],
        ) or []
        aapl_reply = next((x for x in replies if isinstance(x, bytes)), b'')
        check('aapl_response_present', len(aapl_reply) >= 32, bytes=len(aapl_reply))
        command, reserved, bitmap, server_caps, volume_caps = struct.unpack_from('<IIQQQ', aapl_reply)
        check('aapl_fullsync', command == 1 and bitmap & 2 and volume_caps & 4,
              server_caps=hex(server_caps), volume_caps=hex(volume_caps), reply_bitmap=hex(bitmap))
        query = SMB2QueryInfoRequest()
        query['info_type'] = FileFsFullSizeInformation.INFO_TYPE
        query['file_info_class'] = FileFsFullSizeInformation.INFO_CLASS
        query['output_buffer_length'] = 32
        query['file_id'] = root.file_id
        request = c.send(query, s.session_id, t.tree_connect_id)
        response = c.receive(request, timeout=30)
        query_response = SMB2QueryInfoResponse()
        query_response.unpack(response['data'].get_value())
        capacity = FileFsFullSizeInformation()
        capacity.unpack(query_response['buffer'].get_value())
        unit_bytes = capacity['sectors_per_unit'].get_value() * capacity['bytes_per_sector'].get_value()
        total_bytes = capacity['total_allocation_units'].get_value() * unit_bytes
        caller_free = capacity['caller_available_units'].get_value() * unit_bytes
        actual_free = capacity['actual_available_units'].get_value() * unit_bytes
        expected_bytes = args.expected_quota_mib * 1024 * 1024
        check('timemachine_quota_advertised', unit_bytes > 0 and
              abs(total_bytes - expected_bytes) < unit_bytes and
              0 <= caller_free <= total_bytes and 0 <= actual_free <= total_bytes,
              total_bytes=total_bytes, expected_bytes=expected_bytes,
              caller_free_bytes=caller_free, actual_free_bytes=actual_free,
              allocation_unit_bytes=unit_bytes)
        root.close()

        payload = hashlib.sha256(b'tm-local-validation').digest() * 4096
        f, _ = open_file(t, 'tm-validation.bin')
        write_all(f, payload)
        check('file_write_flush_read', read_all(f, len(payload)) == payload,
              bytes=len(payload), sha256=hashlib.sha256(payload).hexdigest())
        f.close()
        unicode_name = 'tm-Unicode-ÉCOLE-تهران-雪-💾.txt'
        unicode_data = 'Unicode filename content: تهران 雪 💾'.encode('utf-8')
        uf, _ = open_file(t, unicode_name)
        write_all(uf, unicode_data)
        uf.close()
        uf, _ = open_file(t, 'TM-unicode-école-تهران-雪-💾.TXT',
                          disposition=CreateDisposition.FILE_OPEN)
        check('unicode_filename_and_casefold_roundtrip', read_all(uf, len(unicode_data)) == unicode_data,
              filename=unicode_name, bytes=len(unicode_data))
        uf.close()
        # The router userspace is32-bit, while sparsebundle bands and other
        # files may use64-bit offsets. This consumes only a small sparse extent.
        large, _ = open_file(t, 'tm-large-offset.bin')
        large_offset = (1 << 32) + 123
        large_data = b'64-bit-offset-validation' * 32
        count = large.write(large_data, offset=large_offset, write_through=True)
        large.flush()
        large.close()
        large, _ = open_file(t, 'tm-large-offset.bin', disposition=CreateDisposition.FILE_OPEN)
        check('file_offset_above_4gib', count == len(large_data) and
              large.end_of_file == large_offset + len(large_data) and
              large.read(large_offset, len(large_data)) == large_data,
              offset=large_offset, bytes=len(large_data), reported_file_size=large.end_of_file)
        large.close()
        stream_cases = [
            ('validation', b'local alternate stream data'),
            # FinderInfo starts at16. An all-zero FinderInfo asks macOS/Samba
            # to delete this stream, so retain meaningful file/creator codes.
            # Netatalk reconstructs the canonical AD_DATE_START backup-time
            # sentinel (0x80000000); the complete 60-byte response is checked.
            ('AFP_AfpInfo', b'AFP\0' + struct.pack('>I', 0x00000100) + b'\0' * 4 +
                            struct.pack('>I', 0x80000000) +
                            b'TEXTttxt' + b'\0' * 36),
            ('AFP_Resource', b'Local resource fork\0' * 4096),
        ]
        for stream_name, data in stream_cases:
            stream, _ = open_file(t, 'tm-validation.bin:%s:$DATA' % stream_name)
            write_all(stream, data)
            stream.close()
            stream, _ = open_file(t, 'tm-validation.bin:%s:$DATA' % stream_name,
                                  disposition=CreateDisposition.FILE_OPEN)
            actual = read_all(stream, len(data))
            check('stream_roundtrip_' + stream_name, actual == data,
                  bytes=len(data), sha256=hashlib.sha256(data).hexdigest(),
                  readback_sha256=hashlib.sha256(actual).hexdigest())
            stream.close()

        # Exercise mutation operations after closing handles. Each cycle uses a
        # fresh directory so prior DOS xattrs or leftover names cannot mask a
        # create/close/reopen regression. All paths are within the disposable
        # backup share; no existing backup is inspected or modified.
        mutation_dir = 'tm-mutations-' + uuid.uuid4().hex
        mkdir(t, mutation_dir)
        plain = mutation_dir + '\\plain.tmp'
        renamed = mutation_dir + '\\plain-renamed.bin'
        f, _ = open_file(t, plain)
        write_all(f, b'closed plain file')
        f.close()
        f = mutation_handle(t, plain)
        f.close()
        check('delete_access_reopen_plain_file', True, desired_access='0x110080')
        rename(t, plain, renamed)
        check('plain_file_rename_readback', absent(t, plain) and
              read_named(t, renamed, 17) == b'closed plain file')

        replacement = mutation_dir + '\\replacement.tmp'
        f, _ = open_file(t, replacement)
        write_all(f, b'atomic replacement content')
        f.close()
        old_target, _ = open_file(t, renamed, disposition=CreateDisposition.FILE_OPEN)
        try:
            # Samba's SMB FileRenameInformation path explicitly rejects an
            # existing target open for data access, even with FILE_SHARE_DELETE.
            # This is not POSIX rename-over-an-open-inode semantics. Verify the
            # refusal changes neither file, then close the target and exercise
            # the replacement rename that backup metadata commits actually use.
            rejected = False
            replacement_status = None
            try:
                rename(t, replacement, renamed, replace=True)
            except SMBResponseException as exc:
                replacement_status = hex(exc.status)
                rejected = exc.status == 0xC0000022  # STATUS_ACCESS_DENIED
            check('replace_open_target_rejected_without_changes', rejected and
                  read_named(t, replacement, 26) == b'atomic replacement content' and
                  read_named(t, renamed, 17) == b'closed plain file' and
                  read_all(old_target, 17) == b'closed plain file',
                  status=replacement_status)
        finally:
            old_target.close()
        rename(t, replacement, renamed, replace=True)
        check('closed_target_replace_readback', absent(t, replacement) and
              read_named(t, renamed, 26) == b'atomic replacement content')
        remove(t, renamed)
        check('plain_file_delete_on_close', absent(t, renamed))

        empty = mutation_dir + '\\empty'
        empty_new = mutation_dir + '\\empty-renamed'
        mkdir(t, empty)
        f = mutation_handle(t, empty, directory=True)
        f.close()
        check('delete_access_reopen_empty_directory', True, desired_access='0x110080')
        rename(t, empty, empty_new, directory=True)
        check('empty_directory_rename', absent(t, empty, directory=True) and
              not absent(t, empty_new, directory=True))
        remove(t, empty_new, directory=True)
        check('empty_directory_remove', absent(t, empty_new, directory=True))

        parent = mutation_dir + '\\populated'
        parent_new = mutation_dir + '\\populated-renamed'
        mkdir(t, parent)
        f, _ = open_file(t, parent + '\\child.bin')
        write_all(f, b'nested child content')
        f.close()
        rename(t, parent, parent_new, directory=True)
        check('populated_directory_rename', absent(t, parent, directory=True) and
              read_named(t, parent_new + '\\child.bin', 20) == b'nested child content')
        remove(t, parent_new + '\\child.bin')
        check('nested_file_delete', absent(t, parent_new + '\\child.bin'))
        remove(t, parent_new, directory=True)
        check('populated_directory_remove_after_child', absent(t, parent_new, directory=True))

        # Two directory levels and a UTF-8 parent exercise the checked-parent
        # AppleDouble open. The original root-level fork probe cannot detect
        # the internal sidecar being rejected by the client-facing ._ veto.
        metadata_parent = mutation_dir + '\\metadata-تهران-雪-💾'
        mkdir(t, metadata_parent)
        metadata = metadata_parent + '\\metadata.bin'
        metadata_new = metadata_parent + '\\metadata-renamed.bin'
        f, _ = open_file(t, metadata)
        write_all(f, b'file with Apple metadata')
        f.close()
        for stream_name, data in stream_cases:
            stream, _ = open_file(t, metadata + ':%s:$DATA' % stream_name)
            write_all(stream, data)
            stream.close()
            check('nested_stream_reopen_' + stream_name,
                  read_named(t, metadata + ':%s:$DATA' % stream_name, len(data)) == data,
                  bytes=len(data))
        require_open_rejected('existing_appledouble_client_open_vetoed', t,
            metadata_parent + '\\._metadata.bin', CreateDisposition.FILE_OPEN, 0xC0000034)
        require_open_rejected('new_appledouble_client_create_vetoed', t,
            metadata_parent + '\\._client-created.bin', CreateDisposition.FILE_CREATE, 0xC0000034)
        require_open_rejected('managed_marker_client_open_vetoed', t,
            '.smb-timemachine', CreateDisposition.FILE_OPEN, 0xC0000034)
        rename(t, metadata, metadata_new)
        check('metadata_file_rename_readback', absent(t, metadata) and
              read_named(t, metadata_new, 24) == b'file with Apple metadata')
        for stream_name, data in stream_cases:
            check('renamed_stream_roundtrip_' + stream_name,
                  read_named(t, metadata_new + ':%s:$DATA' % stream_name, len(data)) == data,
                  bytes=len(data))
        remove(t, metadata_new)
        check('metadata_file_delete_on_close', absent(t, metadata_new))
        # A leftover AppleDouble companion would make this directory nonempty.
        remove(t, metadata_parent, directory=True)
        check('metadata_parent_cleanup', absent(t, metadata_parent, directory=True))
        remove(t, mutation_dir, directory=True)
        check('mutation_directory_cleanup', absent(t, mutation_dir, directory=True))

        # Fixture symlinks are created by run_runtime.py, never by SMB clients.
        # The patch must retain ordinary within-share directory traversal and
        # the existing boundary against following a directory outside a share.
        alias_case = 'case-' + uuid.uuid4().hex
        alias_dir = 'tm-resource-safe-alias\\' + alias_case
        canonical_dir = 'tm-resource-safe-target\\' + alias_case
        mkdir(t, alias_dir)
        alias_basename = 'resource.bin'
        alias_path = alias_dir + '\\' + alias_basename
        canonical_path = canonical_dir + '\\' + alias_basename
        f, _ = open_file(t, alias_path)
        write_all(f, b'within-share parent symlink')
        f.close()
        alias_resource = b'checked parent resource data' * 64
        f, _ = open_file(t, alias_path + ':AFP_Resource:$DATA')
        write_all(f, alias_resource)
        f.close()
        check('within_share_symlink_resource_roundtrip',
              read_named(t, alias_path + ':AFP_Resource:$DATA', len(alias_resource)) == alias_resource and
              read_named(t, canonical_path + ':AFP_Resource:$DATA', len(alias_resource)) == alias_resource)
        require_open_rejected('symlink_appledouble_client_open_vetoed', t,
            alias_dir + '\\._' + alias_basename, CreateDisposition.FILE_OPEN, 0xC0000034)
        remove(t, alias_path)
        # Directory removal also catches a hidden orphan AppleDouble sidecar.
        remove(t, alias_dir, directory=True)
        check('within_share_symlink_resource_cleanup', absent(t, alias_path) and
              absent(t, canonical_path) and absent(t, alias_dir, directory=True) and
              absent(t, canonical_dir, directory=True))
        # filename.c maps an outside-share link with an unparsed child path to
        # OBJECT_PATH_NOT_FOUND. Other errors do not satisfy these controls.
        canary_name = 'tm-resource-outside-canary.bin'
        outside_path = 'tm-resource-outside\\' + canary_name
        require_open_rejected('outside_share_symlink_file_rejected', t, outside_path,
                              CreateDisposition.FILE_OPEN, 0xC000003A)
        require_open_rejected('outside_share_symlink_resource_rejected', t,
                              outside_path + ':AFP_Resource:$DATA',
                              CreateDisposition.FILE_OPEN_IF, 0xC000003A)
        _, _, canary_tree = connect(share=args.ordinary_share)
        legitimate_canary = ('Files\\' if args.ordinary_share == 'sda1' else '') + canary_name
        canary_data = b'outside-share canary remains unchanged'
        check('outside_share_symlink_canary_intact',
              read_named(canary_tree, legitimate_canary, len(canary_data)) == canary_data)

        create_guid = uuid.uuid4()
        lease_key = uuid.uuid4().bytes
        lease = SMB2CreateRequestLeaseV2()
        lease['lease_key'] = lease_key
        lease['lease_state'] = 7  # read, handle, write caching
        lease['parent_lease_key'] = b'\0' * 16
        lease['epoch'] = b'\0' * 2
        durable = SMB2CreateDurableHandleRequestV2()
        durable['timeout'] = 60000
        durable['create_guid'] = create_guid
        held, replies = open_file(t, 'tm-durable.bin', [durable, lease], lease=True)
        durable_replies = [x for x in replies if isinstance(x, SMB2CreateDurableHandleResponseV2)]
        lease_replies = [x for x in replies if isinstance(x, SMB2CreateResponseLeaseV2)]
        check('durable_handle_granted', bool(durable_replies),
              oplock_level=hex(held.oplock_level))
        check('lease_v2_granted', bool(lease_replies))
        lease_reply = lease_replies[0]
        write_all(held, b'persist across transport disconnect')
        file_id = held.file_id
        c.disconnect(close=False)
        time.sleep(1)
        c2, _, t2 = connect(guid=client_guid)
        reconnect = SMB2CreateDurableHandleReconnectV2()
        reconnect['file_id'] = file_id
        reconnect['create_guid'] = create_guid
        lease2 = SMB2CreateRequestLeaseV2()
        lease2['lease_key'] = lease_key
        lease2['lease_state'] = lease_reply['lease_state'].get_value()
        lease2['parent_lease_key'] = b'\0' * 16
        lease2['epoch'] = struct.pack('<H', lease_reply['epoch'].get_value())
        resumed, _ = open_file(t2, 'tm-durable.bin', [reconnect, lease2], lease=True,
                              disposition=CreateDisposition.FILE_OPEN)
        durable_data = b'persist across transport disconnect'
        check('durable_disconnect_reconnect_read', read_all(resumed, len(durable_data)) == durable_data)
        resumed.close()

        encrypted, session3, tree3 = connect(encryption=True)
        ef, _ = open_file(tree3, 'tm-validation.bin', disposition=CreateDisposition.FILE_OPEN)
        check('encrypted_smb3_read', read_all(ef, len(payload)) == payload,
              dialect=hex(encrypted.dialect), encryption_enabled=bool(session3.encrypt_data))
        ef.close()
        if args.ordinary_share:
            ordinary, _, ot = connect(share=args.ordinary_share)
            if args.ordinary_share == 'sda1':
                for variant, path in [
                    ('exact', 'TimeMachine\\tm-validation.bin'),
                    ('lowercase', 'timemachine\\tm-validation.bin'),
                    ('uppercase', 'TIMEMACHINE\\tm-validation.bin'),
                    ('parent_component', 'Files\\..\\TimeMachine\\tm-validation.bin'),
                ]:
                    blocked = False
                    block_status = None
                    try:
                        escaped, _ = open_file(ot, path, disposition=CreateDisposition.FILE_OPEN)
                        escaped.close()
                    except SMBResponseException as exc:
                        block_status = hex(exc.status)
                        blocked = exc.status in (0xC0000022, 0xC0000033, 0xC0000034,
                                                 0xC000003A, 0xC000003B)
                    check('ordinary_disk_share_hides_timemachine_'+variant, blocked,
                          status=block_status, requested_path=path)
            of, _ = open_file(ot, 'ordinary-validation.bin')
            write_all(of, b'ordinary share remains writable')
            check('ordinary_share_write_read', read_all(of, 31) == b'ordinary share remains writable')
            of.close()
            other, _, other_tree = connect(username=args.other_user, share=args.ordinary_share)
            other_file, _ = open_file(other_tree, 'other-user-validation.bin')
            other_data = b'ordinary USB account remains usable'
            write_all(other_file, other_data)
            check('ordinary_other_account_write_read', read_all(other_file, len(other_data)) == other_data)
            other_file.close()
            if args.ordinary_share == 'sda1':
                guest, guest_session, guest_tree = connect(
                    username='tm-validation-no-such-user', signing=False,
                    share=args.ordinary_share)
                guest_file, _ = open_file(guest_tree, 'guest-validation.bin')
                guest_data = b'ordinary guest sharing remains usable'
                write_all(guest_file, guest_data)
                check('ordinary_guest_write_read', read_all(guest_file, len(guest_data)) == guest_data)
                guest_file.close()
        report['passed'] = True
    except Exception as exc:
        report['passed'] = False
        report['error'] = '%s: %s' % (type(exc).__name__, exc)
        raise
    finally:
        for c in connections:
            try:
                c.disconnect(timeout=5)
            except Exception:
                pass
        save()
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
