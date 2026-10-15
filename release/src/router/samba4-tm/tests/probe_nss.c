/* Baseline target-libc/NSS fixture preflight, not a Samba protocol test. */
#include <errno.h>
#include <grp.h>
#include <pwd.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>

static void check(int value, const char *name)
{
    if (!value) {
        fprintf(stderr, "FAIL %s (errno=%d)\n", name, errno);
        exit(1);
    }
    printf("PASS %s\n", name);
    fflush(stdout);
}

int main(void)
{
    struct passwd *pw;
    struct group *gr;
    pid_t child;
    int status;
    pw = getpwnam("tmtest");
    check(pw && pw->pw_uid == 0 && pw->pw_gid == 0, "administrator NSS lookup");
    pw = getpwnam("otheruser");
    check(pw && pw->pw_uid == 501 && pw->pw_gid == 501, "USB account NSS lookup");
    check(getpwnam("tm-validation-no-such-user") == NULL, "unknown NSS user absent");
    gr = getgrnam("root");
    check(gr && gr->gr_gid == 0, "root NSS group lookup");
    child = fork();
    check(child >= 0, "target fork");
    if (child == 0) {
        check(initgroups("otheruser", 501) == 0, "USB account group initialization");
        check(setgid(501) == 0 && setuid(501) == 0, "USB account credential switch");
        check(access("/tmp/mnt/sda1/Files", W_OK | X_OK) == 0,
              "ordinary folder writable by USB account");
        errno = 0;
        check(access("/tmp/mnt/sda1/TimeMachine", R_OK | X_OK) != 0 && errno == EACCES,
              "private backup folder denies USB account at filesystem level");
        _exit(0);
    }
    check(waitpid(child, &status, 0) == child && WIFEXITED(status) && WEXITSTATUS(status) == 0,
          "target credential child completed");
    return 0;
}
