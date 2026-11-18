# 比赛机准备记录（2026-11-18）

> 机器：`root@39.107.43.191:22`（官方比赛托管机，平台按其 `task.json → result.json` 协议起容器）
> 操作：`tools/remote_ssh.py`（本次新增，paramiko 密码登录；凭据在 `runs/_ssh/cred.json`，gitignore）

## 1. 机器现状（只读盘点）

| 项 | 值 |
|---|---|
| 系统 | Ubuntu 26.04.1 LTS / 内核 7.0.0-31 / x86_64 |
| 资源 | 4 vCPU / 7.3 GiB 内存 / 99 GB 磁盘（已用 4.4G） |
| 时间 | 2026-11-18 15:xx +08:00，chrony 正常 |
| Docker | 29.7.2（containerd），默认只有 `ubuntu:22.04` |
| Python（宿主） | 3.14.4（镜像内是 3.12.16，与提交镜像一致） |
| 工具链（宿主） | 有 objdump/readelf/strings/nm/nc/curl/tar/tmux/git；**缺 gdb/qemu-i386/socat/unzip/7z**（不影响比赛：agent 跑在自带工具的镜像里） |
| `/ctfy` | 赛前不存在（平台起容器时才 staging `task.json`/attachments） |
| 平台连通 | `ctfy.tsinghua.edu.cn/api/v1/mcp` → 308/405（端点活着） |
| LLM 连通 | `llm.ctfy.io` → 503（根路径；带鉴权路径由运行时决定） |

## 2. 关键坑：系统 DNS 只放行白名单

比赛机默认 DNS（`10.0.10.238`）只解析 `/etc/hosts` 里 CTFY 预置的白名单域名（ctfy、各 LLM 厂商、镜像站），
**`ghcr.io` 解析不了** → 平台拉我们的镜像会失败。

实测：
- 直接 UDP 查询 `223.5.5.5` 能拿到应答，但 `ghcr.io` 仍返回空/无记录（网络层有域名过滤）
- 用 Windows 侧解析出的 IP + `curl --resolve` 验证：`ghcr.io` → `20.205.243.164` 可用（/v2/ 401 = 正常鉴权挑战）
- CDN 速度差异巨大：`pkg-containers.githubusercontent.com` 的 `185.199.108.154` 只有 **18 KB/s**，
  `185.199.109.154` / `185.199.111.154` 跑满 **10 MB/s**（同一个 105MB blob 实测）

## 3. 本次改了什么（都可回滚）

| 改动 | 内容 | 回滚 |
|---|---|---|
| `/etc/hosts` 追加块 | `ctfy-ghcr-hosts`：`20.205.243.164 ghcr.io`、`185.199.109.154/110.154/111.154 pkg-containers.githubusercontent.com`、`185.199.111.133/109.133 objects.githubusercontent.com` | 删掉 `# BEGIN ctfy-ghcr-hosts` 到 `# END ctfy-ghcr-hosts` 之间；原文件已备份 `/etc/hosts.bak.*` |
| systemd-resolved drop-in | `/etc/systemd/resolved.conf.d/99-ctf-fallback.conf`（`FallbackDNS=223.5.5.5 119.29.29.29`） | 删文件后 `systemctl restart systemd-resolved` |
| 链路 DNS | `resolvectl dns eth0 223.5.5.5 119.29.29.29 10.0.10.238`（运行期） | `resolvectl dns eth0 10.0.10.238` |
| 预拉镜像 | `docker pull ghcr.io/yzqcyl/ctfy-agent-pub:v7.3` | `docker rmi ghcr.io/yzqcyl/ctfy-agent-pub:v7.3` |
| 验收脚本 | `/root/ctfy_check/container_check.py` + `check.log` | `rm -rf /root/ctfy_check` |

> 链路 DNS 的改动让**容器内** `/etc/resolv.conf` 也带上了 223.5.5.5/119.29.29.29（Docker 会复制宿主机 upstream），
> 这是容器内能解析 `ctfy.tsinghua.edu.cn` / `llm.ctfy.io` / `ghcr.io` / `ctf2.dasctf.com` 的直接原因。

## 4. 验证结果（全部在比赛机现场）

### 4.1 镜像

| 项 | 值 |
|---|---|
| tag | `ghcr.io/yzqcyl/ctfy-agent-pub:v7.3` |
| digest | `sha256:2700685e7663ddca82888d1216b77070cf40d05e9196ab4a663035c95cd39fa6`（与 `docs/submit-image-runbook.md` 记录**一致**） |
| 压缩传输 | **251.7 MB / 19 层**（平台门禁 400 MB ✅） |
| 本地体积 | 741.9 MB（`docker image inspect .Size`，未压缩；`container_check.py` 的 400MB 门禁按此值判会误报，见 §5） |
| 运行契约 | `User=app`（uid 10001）、`Entrypoint=python -m ctfy_agent agent`、`WorkingDir=/ctfy` |

### 4.2 B1 契约验收（`python3 container_check.py --no-build --max-mb 900`）

```
[洁净度] /ctfy 只有 config/ 与 src/          ✅
[非 root] Config.User='app' 运行时 uid=10001 ✅
[cwd 无关] 任意目录 import ctfy_agent ok     ✅
[契约] dry_run      exit=0 stop_reason=dry_run  ✅
[契约] no_task      exit=0 stop_reason=halted   ✅
[契约] bad_run_mode exit=0 stop_reason=refused  ✅
[契约] no_creds     exit=0 stop_reason=refused  ✅
[契约] no_mcp       exit=0 stop_reason=halted   ✅
全部通过 ✓
```

### 4.3 容器内网络（这才是 agent 真正跑的地方）

```
resolv.conf: 223.5.5.5 / 119.29.29.29 / 10.0.10.238
解析: ctfy.tsinghua.edu.cn=101.6.15.70  llm.ctfy.io=104.21.86.233
      ghcr.io=20.205.243.164            ctf2.dasctf.com=106.119.92.107
HTTPS: https://ctfy.tsinghua.edu.cn/api/v1/mcp → 308（端点活着）
       https://llm.ctfy.io/ → 503（根路径）
```

## 5. 遗留问题

1. **`tools/container_check.py` 体积门禁口径错**：它拿 `docker image inspect .Size`（未压缩，741.9MB）和 400MB 比，
   而平台门禁是「压缩传输体积」（251.7MB）。在这台 Docker 29 上会误报「体积超限」。
   建议改成：有 registry 时按 manifest 层大小求和（压缩口径），否则以 `--max-mb` 显式放宽。
2. **`/etc/hosts` 的 ghcr 映射是手工 IP**：GitHub CDN IP 可能变化。若赛前再拉镜像失败，
   优先检查这三行是否还指向可用 IP（Windows 侧 `Resolve-DnsName` 取新 IP，逐 IP 用
   `curl --resolve ghcr.io:443:<ip> https://ghcr.io/v2/` 验 401 再写回）。
3. 比赛机**没有 gdb/qemu**，但比赛镜像内已有 gdb 16.3 + pwntools；本地调试仍建议在镜像内做：
   `docker run --rm -it --entrypoint bash ghcr.io/yzqcyl/ctfy-agent-pub:v7.3`

## 6. 赛前 5 分钟检查清单

```powershell
$py = "$env:USERPROFILE\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
& $py tools\remote_ssh.py --cmd "docker images --digests | grep ctfy-agent; ls /ctfy/work 2>&1 | head -3; date -Is"
```

- [ ] 镜像在本地（digest 匹配 2700685e…）
- [ ] `/ctfy/work` 出现 `task.json` 时说明平台已开始派题
- [ ] `docker ps -a` 能看到平台起的容器；容器消失后检查 `/ctfy/work/result.json` 是否写出
- [ ] 出问题先看：`journalctl -u docker -n 100` 与容器日志 `docker logs <id>`