#define _GNU_SOURCE
#include <assert.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <mntent.h>
#include <sys/stat.h>
#include <unistd.h>
#include <fcntl.h>

static char mounts_file[256], config_file[256], device[128];
static int enabled = 1, samba_enabled = 1, inhibited = 0;
static const char *admin = "fixture_admin";
static int nvram_match(const char *key, const char *value)
{
	(void)value;
	return !strcmp(key, "timemachine_enable") ? enabled : samba_enabled;
}
static char *nvram_safe_get(const char *key)
{
	return (char *)(!strcmp(key, "tm_device_name") ? device : admin);
}
static FILE *fixture_setmntent(const char *path, const char *mode)
{
	return setmntent(!strcmp(path, "/proc/mounts") ? mounts_file : path, mode);
}
static FILE *fixture_fopen(const char *path, const char *mode)
{
	return fopen(!strcmp(path, "/etc/smb.conf") ? config_file : path, mode);
}
static int fixture_access(const char *path, int mode)
{
	return !strcmp(path, "/tmp/smb-timemachine.disabled") ? (inhibited ? 0 : -1) : access(path, mode);
}
#define setmntent fixture_setmntent
#define fopen fixture_fopen
#define access fixture_access
#include "smb_timemachine.h"
#undef setmntent
#undef fopen
#undef access

static void write_file(const char *path, const char *content)
{
	FILE *fp = fopen(path, "w");
	assert(fp);
	assert(fputs(content, fp) >= 0);
	assert(fclose(fp) == 0);
}

int main(void)
{
	char base[] = "/tmp/smb-tm-unit-XXXXXX", mount[PATH_MAX], backup[PATH_MAX];
	char row[8192], path[PATH_MAX], marker[PATH_MAX], sentinel[PATH_MAX];
	int fd;
	assert(mkdtemp(base));
	snprintf(mounts_file, sizeof(mounts_file), "%s/mounts", base);
	snprintf(config_file, sizeof(config_file), "%s/smb.conf", base);
	snprintf(device, sizeof(device), "smb-tm-unit-%ld", (long)getpid());
	snprintf(path, sizeof(path), "/tmp/mnt/%s", device);
	assert(mkdir("/tmp/mnt", 0755) == 0 || access("/tmp/mnt", F_OK) == 0);
	assert(mkdir(path, 0700) == 0);
	snprintf(row, sizeof(row), "/dev/test %s ext4 rw,noatime 0 0\n", path);
	write_file(mounts_file, row);
	assert(smb_tm_find_mount(device, mounts_file, mount, sizeof(mount)));
	assert(!strcmp(mount, path));
	assert(!smb_tm_find_mount("missing", mounts_file, mount, sizeof(mount)));
	assert(!smb_tm_find_mount("../elsewhere", mounts_file, mount, sizeof(mount)));
	assert(!smb_tm_find_mount("bad\nname", mounts_file, mount, sizeof(mount)));
	assert(!smb_tm_find_mount("%U", mounts_file, mount, sizeof(mount)));
	assert(!smb_tm_find_mount(device, mounts_file, mount, 3));
	snprintf(row, sizeof(row), "/dev/test %s ext4 ro,noatime 0 0\n", path);
	write_file(mounts_file, row);
	assert(!smb_tm_find_mount(device, mounts_file, mount, sizeof(mount)));
	write_file(mounts_file, "/dev/test /tmp/mnt/WD\\040Drive hfsplus rw 0 0\n");
	assert(smb_tm_find_mount("WD Drive", mounts_file, mount, sizeof(mount)));
	assert(!strcmp(mount, "/tmp/mnt/WD Drive"));
	snprintf(row, sizeof(row), "/dev/test %s ext4 rw,noatime 0 0\n", path);
	write_file(mounts_file, row);
	assert(smb_tm_paths(mount, sizeof(mount), backup, sizeof(backup)));
	assert(!smb_tm_ready(mount, sizeof(mount), backup, sizeof(backup)));
	assert(mkdir(backup, 0700) == 0);
	assert(!smb_tm_ready(mount, sizeof(mount), backup, sizeof(backup)));
	snprintf(marker, sizeof(marker), "%s/%s", backup, SMB_TM_MARKER);
	fd = open(marker, O_CREAT | O_EXCL | O_WRONLY, 0600);
	assert(fd >= 0);
	close(fd);
	assert(smb_tm_ready(mount, sizeof(mount), backup, sizeof(backup)));
	enabled = 0;
	assert(!smb_tm_ready(mount, sizeof(mount), backup, sizeof(backup)));
	assert(smb_tm_protected_mount(path));
	enabled = 1;
	samba_enabled = 0;
	assert(!smb_tm_paths(mount, sizeof(mount), backup, sizeof(backup)));
	samba_enabled = 1;
	inhibited = 1;
	assert(!smb_tm_paths(mount, sizeof(mount), backup, sizeof(backup)));
	inhibited = 0;
	admin = "bad\"name";
	assert(!smb_tm_paths(mount, sizeof(mount), backup, sizeof(backup)));
	admin = "fixture_admin";
	assert(unlink(marker) == 0);
	snprintf(sentinel, sizeof(sentinel), "%s/sentinel", base);
	write_file(sentinel, "keep existing data\n");
	assert(symlink(sentinel, marker) == 0);
	assert(!smb_tm_managed_folder(backup));
	assert(unlink(marker) == 0);
	assert(rmdir(backup) == 0);
	assert(symlink(base, backup) == 0);
	assert(!smb_tm_managed_folder(backup));
	assert(unlink(backup) == 0);
	snprintf(row, sizeof(row), "[Backups.backupdb]\n path = %s\n fruit:time machine = yes\n", backup);
	write_file(config_file, row);
	assert(smb_tm_configured(backup));
	write_file(config_file, "[ordinary]\nfruit:time machine = yes\n");
	assert(!smb_tm_configured(backup));
	snprintf(row, sizeof(row), "[Backups.backupdb]\npath = %s\nfruit:time machine = no\n", backup);
	write_file(config_file, row);
	assert(!smb_tm_configured(backup));
	write_file(config_file, "[Backups.backupdb]\npath = /wrong\nfruit:time machine = yes\n");
	assert(!smb_tm_configured(backup));
	assert(unlink(config_file) == 0);
	assert(unlink(mounts_file) == 0);
	assert(unlink(sentinel) == 0);
	assert(rmdir(path) == 0);
	assert(rmdir(base) == 0);
	puts("PASS: mount, path, namespace, marker, disable, symlink, and final-config guards");
	return 0;
}
