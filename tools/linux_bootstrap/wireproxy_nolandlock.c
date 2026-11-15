/* wireproxy_nolandlock.c —— 用 seccomp 让 wireproxy 的 Landlock 自沙箱退化为 no-op。
 *
 * 背景：wireproxy v1.1.3 的 lockNetwork() 对 [TCPClientTunnel] 只加了
 * ConnectTCP(BindAddress.Port)（应为 BindTCP），且漏了 ConnectTCP(Target.Port)。
 * 在支持 Landlock 网络规则的核（ABI>=4，Linux>=6.7；WSL2 6.18 实测）上，
 * wireproxy 自己的 TCP 监听 bind() 直接 EACCES（"bind: permission denied"），
 * 隧道完全不可用。老内核（Landlock 无网络 ABI）不受影响。
 *
 * 做法：exec wireproxy 前装一个 seccomp 过滤器，让 landlock_* 三个系统调用返回
 * ENOSYS。go-landlock 的 BestEffort() 见 ENOSYS 即判定内核不支持 → 不做沙箱限制，
 * TCP 监听/连接恢复正常（任务本来也不需要 wireproxy 的文件沙箱）。
 *
 * 用法：wireproxy_nolandlock /path/to/wireproxy -c wireproxy.conf -s
 */
#define _GNU_SOURCE
#include <errno.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include <sys/prctl.h>
#include <sys/syscall.h>
#include <linux/filter.h>
#include <linux/seccomp.h>

#ifndef SYS_landlock_create_ruleset
#define SYS_landlock_create_ruleset 444
#endif
#ifndef SYS_landlock_add_rule
#define SYS_landlock_add_rule 445
#endif
#ifndef SYS_landlock_restrict_self
#define SYS_landlock_restrict_self 446
#endif

int main(int argc, char **argv) {
    if (argc < 2) {
        fprintf(stderr, "usage: %s <program> [args...]\n", argv[0]);
        return 2;
    }

    /* A=syscall nr；等于目标号时跳 0 步落到 RET ERRNO，否则跳 1 步跳过 RET。 */
    struct sock_filter filter[] = {
        BPF_STMT(BPF_LD | BPF_W | BPF_ABS, offsetof(struct seccomp_data, nr)),
        BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, SYS_landlock_create_ruleset, 0, 1),
        BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ERRNO | (ENOSYS & SECCOMP_RET_DATA)),
        BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, SYS_landlock_add_rule, 0, 1),
        BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ERRNO | (ENOSYS & SECCOMP_RET_DATA)),
        BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, SYS_landlock_restrict_self, 0, 1),
        BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ERRNO | (ENOSYS & SECCOMP_RET_DATA)),
        BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ALLOW),
    };
    struct sock_fprog prog = {
        .len = (unsigned short)(sizeof(filter) / sizeof(filter[0])),
        .filter = filter,
    };

    if (prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0) {
        perror("prctl(PR_SET_NO_NEW_PRIVS)");
        return 4;
    }
    if (syscall(SYS_seccomp, SECCOMP_SET_MODE_FILTER, 0, &prog) != 0) {
        perror("seccomp");
        return 5;
    }

    execv(argv[1], &argv[1]);
    perror("execv");
    return 127;
}