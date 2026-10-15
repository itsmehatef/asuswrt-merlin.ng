/* Test-only LD_PRELOAD fixture for the actual ARM write_smb_conf executable.
 * Compile against the repository's disk_initial.h. Never package in firmware.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "disk_initial.h"

struct value { char key[80]; char value[256]; };
static struct value values[100];
static int loaded, count;

char *nvram_get(const char *name)
{
    int i;
    if (!loaded) {
        FILE *f = fopen("/test/nvram", "r");
        char line[400];
        loaded = 1;
        while (f && count < 100 && fgets(line, sizeof(line), f)) {
            char *equal = strchr(line, '=');
            if (!equal) continue;
            *equal++ = 0;
            equal[strcspn(equal, "\r\n")] = 0;
            snprintf(values[count].key, sizeof(values[count].key), "%s", line);
            snprintf(values[count].value, sizeof(values[count].value), "%s", equal);
            ++count;
        }
        if (f) fclose(f);
    }
    for (i=0; i<count; ++i)
        if (!strcmp(name, values[i].key)) return values[i].value;
    return NULL;
}
int nvram_get_int(const char *name) { char *s=nvram_get(name); return s ? atoi(s) : 0; }
char *nvram_default_get(const char *name) { return nvram_get(name); }
char *nvram_pf_get(const char *prefix, const char *name)
{
    char key[160]; snprintf(key, sizeof(key), "%s%s", prefix, name); return nvram_get(key);
}
int nvram_pf_get_int(const char *prefix, const char *name)
{
    char *s=nvram_pf_get(prefix,name); return s ? atoi(s) : 0;
}
char *get_lan_hostname(void) { return "TMVALIDATE"; }
char *get_productid(void) { return "GT-AXE16000"; }
char *getifaddr(const char *name, int family, int flags) { return NULL; }
void append_custom_config(const char *name, FILE *fp) { }
void use_custom_config(const char *name, const char *path) { }
void run_postconf(const char *name, const char *path) { }

disk_info_t *read_disk_data(void)
{
    static disk_info_t disk;
    static partition_info_t partition;
    disk.tag="Validation disk"; disk.vendor="LOCAL"; disk.model="Disposable";
    disk.device="sda"; disk.major=8; disk.minor=0; disk.port="1";
    disk.partition_number=1; disk.mounted_number=1; disk.size_in_kilobytes=1024*1024;
    disk.partitions=&partition; disk.next=NULL;
    partition.device="sda1"; partition.label="Fixture"; partition.partition_order=1;
    partition.mount_point="/tmp/mnt/sda1"; partition.file_system="ext4";
    partition.permission="rw"; partition.size_in_kilobytes=1024*1024;
    partition.disk=&disk; partition.next=NULL;
    return &disk;
}
void free_disk_data(disk_info_t **disk) { *disk=NULL; }
int get_account_list(int *num, char ***list)
{
    *num=2; *list=calloc(2,sizeof(char *));
    (*list)[0]=strdup("tmtest"); (*list)[1]=strdup("otheruser"); return 0;
}
int get_folder_list(const char *mount, int *num, char ***list)
{
    int collision=nvram_get_int("test_folder_collision");
    int alias=nvram_get_int("test_folder_alias");
    int next=2;
    *num=2+!!collision+(alias ? 4 : 0); *list=calloc(*num,sizeof(char *));
    (*list)[0]=strdup("Files"); (*list)[1]=strdup("TimeMachine");
    if (collision) (*list)[next++]=strdup("Backups.backupdb");
    if (alias) {
        (*list)[next++]=strdup("TM-alias");
        (*list)[next++]=strdup("TM-descendant");
        (*list)[next++]=strdup("Other-disk-TM");
        (*list)[next++]=strdup("Ordinary-alias");
    }
    return 0;
}
int get_permission(const char *user, const char *mount, const char *folder,
                   const char *protocol, int is_group) { return 3; }
void free_2_dimension_list(int *num, char ***list)
{
    int i; if (*list) { for(i=0;i<*num;i++) free((*list)[i]); free(*list); }
    *list=NULL; *num=0;
}
