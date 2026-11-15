# wireproxy 1.1.3 在 Landlock 新内核上的 TCP 隧道缺陷与绕行（2026-11-16 实测）

## 结论（一句话）

随包 `wireproxy v1.1.3` 在 **支持 Landlock 网络规则的内核（ABI ≥ 4，Linux ≥ 6.7；WSL2 6.18 已复现）** 上，
所有 `[TCPClientTunnel]` 的本地监听 `bind()` 会直接 `EACCES (Permission denied)`，VPN 的 TCP 端口映射
整段不可用；老内核（Ubuntu 22.04 / 5.15 等）没有网络 ABI，不受影响。仓库内新增的
`tools/linux_bootstrap/wireproxy_nolandlock`（静态 x86-64）作为 seccomp 包装器绕开该问题，
`ctfy_agent.netbridge` 会自动优先使用它。

## 证据

- 手工复现（WSL Ubuntu-24.04，内核 6.18）：

  ```text
  ./wireproxy -c wg.conf
  2026/11/15 23:47:21 listen tcp 127.0.0.1:54123: bind: permission denied
  ```

  同端口用 Python/C `bind()` 成功；strace 显示 wireproxy 自己创建 Landlock ruleset 后调用
  `landlock_restrict_self`，随后的 `bind()` 返回 `EACCES`：

  ```text
  landlock_create_ruleset(... handled_access_net=...) = 4
  landlock_add_rule(4, LANDLOCK_RULE_NET_PORT, {allowed_access=LANDLOCK_ACCESS_NET_CONNECT_TCP, port=54123}, 0) = 0
  landlock_restrict_self(4, 0) = 0
  ...
  bind(10, {sa_family=AF_INET, sin_port=htons(54123), sin_addr=inet_addr("127.0.0.1")}, 16) = -1 EACCES
  ```

- 上游源码（`windtf/wireproxy` v1.1.3 `cmd/wireproxy/main.go` 的 `lockNetwork()`）里确有接线错误：
  `[TCPClientTunnel]` 分支加的是 `landlock.ConnectTCP(BindAddress.Port)`（应为 `BindTCP`），
  且没有为 `Target` 的端口加 `ConnectTCP`。于是它既不允许自己监听，也不允许出站连目标。
  这是上游 bug，不是我们的配置问题。

## 绕行方案

`tools/linux_bootstrap/wireproxy_nolandlock.c` 是一个 ~100 行的 C 包装器：`exec` wireproxy 前装 seccomp
过滤器，让 `landlock_create_ruleset / landlock_add_rule / landlock_restrict_self` 返回 `ENOSYS`。
go-landlock 的 `BestEffort()` 看到 `ENOSYS` 就判定内核不支持 → 不做沙箱限制，TCP 监听/连接恢复正常。
老内核本来就把 `landlock_*` 当未知系统调用（ENOSYS），包装无副作用。

- 预编译：`tools/linux_bootstrap/prebuilt/x86_64/wireproxy_nolandlock`（静态链接，`-O2 -static`，约 800 KB）。
- 自动启用：`netbridge.wireproxy_launcher()` 先找 `CTFY_WIREPROXY_LAUNCHER`，再找 wireproxy 同目录、
  `.tools/wireproxy/`、`tools/linux_bootstrap/prebuilt/{arch}/`、`PATH` 里的 `wireproxy_nolandlock`；
  只在 POSIX 上启用，Windows 直接跑原二进制。
- 关闭：`CTFY_WIREPROXY_NOLANDLOCK=0`（确认内核没有 Landlock 网络 ABI 时可关）。
- `tools/linux_bootstrap/vps_bootstrap.sh` 会 `chmod +x` 并打印提示。

## 验证结果

`runs/_tmp/vpn_bridge_test.py`（真 wireproxy 进程 + 假 WireGuard endpoint + UDP 抓包）12/12 通过：

- `build_plan` 生成 TCP/UDP/DNS 段、回填 `PersistentKeepalive`；
- `LocalBridge.start()` 拉起真 wireproxy（经包装器），本地 TCP 转发端口进入 LISTEN；
- 抓包看到 wireproxy 向 endpoint 发出的 **148 字节 WireGuard 握手发起包（type=1）**；
- 配置文件 0600、日志无明文私钥、`stop()` 后子进程退出且私钥配置删除。

注意：本地没有真实 WireGuard peer，数据面（隧道内实际 TCP 往返）无法在本机证明，需要比赛实例。
但控制面（配置生成、Target 映射、监听、握手包、清理）已全部真机验证。

## 回归

- `tests/smoke_netbridge.py` 新增包装器发现/开关/`_launch` 命令构造 4 项检查；
- `runs/_tpctf/run_all_tests.py` 增加 `netbridge` 一项（19 → 20）；
- Windows 与 WSL 全量离线套件均 **20/20 PASS**。

## 若真平台内核也 ≥ 6.7

我们的 `netbridge` 已自动绕行；若平台上没有随包包装器（例如只拷贝了 wireproxy 单文件），
表现为「隧道进程报 `bind: permission denied`」，把 `tools/linux_bootstrap/prebuilt/x86_64/wireproxy_nolandlock`
一起带上即可。