# Linux 运行时与 pwn/rev 工具链（2026-11-16）

## 为什么必须用 Linux 运行时

agent 的 shell 工具是 `subprocess.Popen(command, shell=True)`（见 `src/ctfy_agent/tools_local.py`）：
**Python 进程在哪个 OS，命令就在哪个 OS 执行**。

- Windows GUI 模式下，rev/pwn 需要的 `gcc/gdb/checksec/pwntools 原生工具/patchelf/ROPgadget`
  在 Windows 上缺失或语义不同 → 工具失败被算进"未解出"，**rev/pwn 的正确率数据不可信**；
- 正式比赛 VPS 是 **Ubuntu 4C8G（Linux）**，真实运行环境本来就是 Linux。

结论：**rev/pwn 的评测与彩排必须在 Linux 运行时里跑**；Windows 只适合 web/misc 纯网络类。
Windows 数字与 Linux 数字不可直接比较；"环境/工具缺失"类失败应单独标记，不能当模型能力证据。

## 本机 Linux 运行时：WSL Ubuntu 24.04

```bash
# Windows 侧一条命令进入 Linux 原生运行时（自动设 PYTHONPATH/PATH/LD_LIBRARY_PATH）
wsl.exe -d Ubuntu-24.04 -- bash /mnt/e/ctfy_project/tools/linux_bootstrap/wsl_agent.sh \
    python3 runs/_tpctf/rehearsal.py
```

`wsl_agent.sh`（tracked）做的事：
- `PYTHONPATH=/root/tpctf-toolkit/pylibs:<repo>/src`（持久工具箱，不是 /tmp）；
- `PATH` 前置工具箱 `bin/`（`checksec`/`ROPgadget`/`ropper`/`z3` 命令）；
- 可选 `LD_LIBRARY_PATH` 指向 `sysroot`（存在时）；
- `cd $REPO` 后 `exec "$@"`（路径 `/mnt/e` 与 `E:\` 互通）。

## 工具链清单（2026-11-16 补齐并实测）

| 类别 | 工具 | 状态 |
|---|---|---|
| 编译 | gcc 13.3 / g++ 13.3 / build-essential | ✅ apt 安装 |
| 调试 | gdb 15.1（`info address main` 实测可用） | ✅ apt 安装 |
| 二进制 | objdump / readelf / strings / file / patchelf 0.18 | ✅（patchelf 新增） |
| 网络靶 | socat 1.8 / nc | ✅（socat 新增，用于把真 binary/checker 包成 TCP 靶） |
| Python | pwntools / pyelftools / capstone / unicorn / cryptography / freetype-py | ✅ 工具箱已有 |
| 新增 | **z3-solver 5.4.1**（SMT 实测 sat）、**ROPgadget 7.7**（CLI 实测出 gadget）、**ropper 1.13.13** + filebytes | ✅ pip 装进持久工具箱 |
| 便捷命令 | `checksec`（pwntools ELF.checksec 的 CLI 包装） | ✅ 实测 clean-room：Full RELRO / Canary / NX / PIE / FORTIFY |

验证记录：
- `gdb -batch -ex "info address main" challenge` → `Symbol "main" is a function at 0x10f9b8`；
- `checksec challenge` → 与题目 README 声明一致（ASan 全保护）；
- `ROPgadget --binary challenge --only "pop|ret"` → 正常出 gadget；
- `from z3 import *; 3x==42` → `sat, x=14`；
- WSL 全量回归 **23/23 PASS**（持久工具箱路径）。

## 离线 bundle（VPS 无网也能用）

`tools/linux_bootstrap/bundle` 从 38 → **41 个 wheel**，新增：

- `z3_solver-5.4.1.0-py3-none-manylinux_2_27_x86_64.whl`（37 MB，crypto 约束求解）；
- `ropper-1.13.13-py3-none-any.whl` + `filebytes-0.10.2-py3-none-any.whl`（gadget 搜索）。

`provision.py` 的 `default_manifest()` 已补 `z3-solver/ropper`，`filebytes` 因为 PyPI 只有
sdist、`pypi_wheel()` 只收 wheel，改为**本地预构建 wheel 条目**（`kind=wheel` + 固定 sha256）。
`manifest.resolved.json` 已重新生成；实测离线安装：

```bash
python3 tools/linux_bootstrap/provision.py --install tools/linux_bootstrap/bundle \
    --toolkit /root/_bundlecheck --verify      # PASS
# import: pwn/z3/ropper/capstone/elftools/freetype 全 OK；z3 求解 sat
```

## 还没做的（后续）

1. **web 靶**需要 php/node/nginx（Kali WSL 或 apt 均可；当前 WSL 未装），留待 cve_stage 本地靶阶段；
2. Windows GUI 的 **WSL shell 后端**（`shell_backend=wsl` + 路径映射）还没接：现阶段的办法是
   **直接在 WSL 里跑 agent**（CLI/supervisor），GUI 只做看板；
3. Kali：需要预装工具全集可 `wsl --install -d kali-linux`（Kali WSL，无 VM 开销），但不是必需；
   正式 VPS 是 Ubuntu，Ubuntu WSL 与生产更同构。

## 下一步

在 WSL 里用真 LLM 跑 1 道 rev + 1 道 pwn（每题 ≤350k token）对比 Windows 旧数据，确认
"环境修正后 rev/pwn 数据可信"，再进入 L2 比赛环境模拟（平台模拟器 + 本地靶）。
