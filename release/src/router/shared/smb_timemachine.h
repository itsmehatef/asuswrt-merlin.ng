#ifndef SMB_TIMEMACHINE_H
#define SMB_TIMEMACHINE_H

#include <stdio.h>
#include <string.h>
#include <strings.h>
#include <limits.h>
#include <mntent.h>
#include <sys/stat.h>
#include <unistd.h>

#define SMB_TM_SHARE "Backups.backupdb"
#define SMB_TM_FOLDER "TimeMachine"
#define SMB_TM_MARKER ".smb-timemachine"
#define SMB_TM_INHIBIT "/tmp/smb-timemachine.disabled"

/* A configured name alone must never turn an unmounted /tmp directory into
 * a backup destination. getmntent also decodes escaped mount-point spaces. */
static inline int smb_tm_find_mount(const char *device, const char *mounts,
				   char *path, size_t size)
{
	FILE *fp;
	struct mntent *entry;
	char expected[PATH_MAX];
	int n, found = 0;

	if (!device || !*device || !strcmp(device, ".") || !strcmp(device, "..") ||
	    strpbrk(device, "/\\\r\n%") || strlen(device) > NAME_MAX)
		return 0;
	n = snprintf(expected, sizeof(expected), "/tmp/mnt/%s", device);
	if (n < 0 || (size_t)n >= sizeof(expected) || (size_t)n >= size)
		return 0;
	if (!(fp = setmntent(mounts, "r")))
		return 0;
	while ((entry = getmntent(fp)) != NULL) {
		if (!strcmp(entry->mnt_dir, expected) && hasmntopt(entry, "rw")) {
			memcpy(path, expected, (size_t)n + 1);
			found = 1;
			break;
		}
	}
	endmntent(fp);
	return found;
}

static inline int smb_tm_admin_valid(const char *name)
{
	return name && *name && !strpbrk(name, "\r\n\"\\%,");
}

static inline int smb_tm_paths(char *mount, size_t mount_size,
			      char *backup, size_t backup_size)
{
	int n;
	if (!nvram_match("timemachine_enable", "1") ||
	    !nvram_match("enable_samba", "1") || access(SMB_TM_INHIBIT, F_OK) == 0 ||
	    !smb_tm_admin_valid(nvram_safe_get("http_username")) ||
	    !smb_tm_find_mount(nvram_safe_get("tm_device_name"), "/proc/mounts", mount, mount_size))
		return 0;
	n = snprintf(backup, backup_size, "%s/%s", mount, SMB_TM_FOLDER);
	return n >= 0 && (size_t)n < backup_size;
}

static inline int smb_tm_managed_folder(const char *backup)
{
	struct stat st;
	char marker[PATH_MAX];
	int n;
	if (lstat(backup, &st) != 0 || !S_ISDIR(st.st_mode) || st.st_uid != 0)
		return 0;
	n = snprintf(marker, sizeof(marker), "%s/%s", backup, SMB_TM_MARKER);
	if (n < 0 || (size_t)n >= sizeof(marker) || lstat(marker, &st) != 0 ||
	    !S_ISREG(st.st_mode) || st.st_uid != 0)
		return 0;
	return 1;
}

static inline int smb_tm_protected_mount(const char *mount)
{
	char backup[PATH_MAX];
	int n = snprintf(backup, sizeof(backup), "%s/%s", mount, SMB_TM_FOLDER);
	return n >= 0 && (size_t)n < sizeof(backup) && smb_tm_managed_folder(backup);
}

static inline int smb_tm_ready(char *mount, size_t mount_size,
			      char *backup, size_t backup_size)
{
	return smb_tm_paths(mount, mount_size, backup, backup_size) &&
	       smb_tm_managed_folder(backup);
}

/* Custom configuration hooks may replace the generated file. Only announce
 * the backup when the final file still contains its expected share and path. */
static inline int smb_tm_configured(const char *backup)
{
	FILE *fp = fopen("/etc/smb.conf", "r");
	char line[PATH_MAX + 128], *key, *value, *end;
	int in_share = 0, path_ok = 0, fruit_ok = 0;
	if (!fp)
		return 0;
	while (fgets(line, sizeof(line), fp)) {
		key = line + strspn(line, " \t");
		end = key + strlen(key);
		while (end > key && strchr(" \t\r\n", end[-1]))
			*--end = '\0';
		if (*key == '[') {
			in_share = !strcasecmp(key, "[" SMB_TM_SHARE "]");
			continue;
		}
		if (!in_share || !(value = strchr(key, '=')))
			continue;
		*value++ = '\0';
		end = key + strlen(key);
		while (end > key && strchr(" \t", end[-1]))
			*--end = '\0';
		value += strspn(value, " \t");
		if (!strcasecmp(key, "path"))
			path_ok = !strcmp(value, backup);
		if (!strcasecmp(key, "fruit:time machine"))
			fruit_ok = !strcasecmp(value, "yes");
	}
	fclose(fp);
	return path_ok && fruit_ok;
}

/* rc implementation; the configuration writer only uses the read-only helpers. */
int prepare_timemachine_smb(void);

#endif
