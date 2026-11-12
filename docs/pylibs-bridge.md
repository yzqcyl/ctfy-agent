# 外挂依赖库接入（`tools/pylibs` → 进程内工具）

> 目标：让 **agent 自己的工具**（不只是 shell 子进程）能用上 pycryptodome / sympy / gmpy2 /
> z3 / pwntools。背景是 2026-11-10 那轮实测：26 题里 **CRYPTO 0/5、PWN 0/4**，收尾结论反复是
> 「本地没有库，只能纯 stdlib」。纯 stdlib 的 `aesblock`/`rsatool`/`prng`/`pwngen`/`pwnfind`
> 覆盖了一部分，但 **flag 校验器（z3）、AES-GCM、ROP 链** 这类硬缺口补不上。

## 1. 库从哪来

- 安装器：`python tools/fetch_pylibs.py [包名…]`（默认 `pycryptodome sympy gmpy2 pwntools z3-solver`）。
  装到 `tools/pylibs/`。
- `tools/pylibs/` **进 .gitignore、不进镜像**（约 312MB，且含二进制）。
- **镜像侧（2026-11-12 更新）**：v7 镜像已用 pip 内置 capstone + pwntools + pycryptodome +
  gmpy2 + sympy（`Dockerfile` L177–231）；2026-11-12 又补装 **z3-solver 5.4.1.0**
  （此前 `z3solve` 工具已上线但镜像漏装 z3 → 官方镜像里恒报缺库，本机靠外挂没暴露）。
  所以「外挂」现在的语义 = **本机 Windows 开发环境补同一批库**，线上镜像不需要 `tools/pylibs`。

## 2. 进程内接入（`src/ctfy_agent/pylibs.py`）

此前只有 `shell()` 子进程把 `tools/pylibs` 塞进 `PYTHONPATH`（`tools_local.py`），
agent 工具代码本身 `import z3` 会失败。`pylibs.py` 解决这件事：

| API | 作用 |
|---|---|
| `candidate_dirs()` | 路径优先级：`$CTFY_PYLIBS` > `/ctfy/pylibs`（将来镜像内置）> `<项目根>/tools/pylibs`；`$CTFY_PYLIBS_STRICT=1` 时只用显式目录（跨平台验证用，避免 WSL 里误 import Windows 的 `.pyd`） |
| `ensure_on_path()` | 把存在的目录**追加**到 `sys.path` 末尾（幂等；不遮 stdlib/包内模块） |
| `available(name)` / `usable(name)` | `available` 只看 finder；`usable` 真 import（原生模块加载成功才算），结果按进程缓存 |
| `import_or_none(name)` | 懒导入；失败返回 None 并记下原因（`import_error`） |
| `summary()` | 一行摘要（已加载 + 加载失败原因），`agent_main` 启动时打 `[libs] …` |
| `missing_hint(what)` / `unavailable_hint(what, module)` / `blocked_hint(what, exc)` | 缺库/加载失败/原生模块被策略拦截三种可操作提示（区分「没装」和「装了但用不了」，否则模型会误判成没库而放弃） |

Windows 下 `gmpy2.libs/*.dll` 会通过 `os.add_dll_directory` 登记（否则进程内 import gmpy2 失败）。

接入点：`agent_main.run_agent()` 启动日志、`cli.main()`、以及工具内部懒调用
（`_pylibs.import_or_none` 会先 `ensure_on_path`，所以任何入口都不会漏）。

## 3. 新增工具（`tools_domain_ext.py`）

| 工具 | 依赖 | 用途 |
|---|---|---|
| `z3solve(smt2, vars, timeout, count)` | z3-solver | 解 SMT-LIB2 约束：rev 的 flag 校验器、crypto 模方程、字节级比较（`(_ BitVec 8)`）；`count>1` 枚举多组候选，`vars` 只看关心的变量 |
| `pycrypt(op, mode, key, data, iv, nonce, aad, tag)` | pycryptodome | `aes_encrypt/aes_decrypt` × `ecb/cbc/cfb/ofb/ctr/gcm/eax`（AEAD 自动出/验 tag），`des3_encrypt/des3_decrypt`；补齐 `aesblock` 只有 ECB/CBC/CTR 的缺口 |
| `pwnrop(path, op, func, args, arg_strings, filt, limit)` | pwntools | `checksec` / `symbols`（plt+got）/ `gadgets` / `rop`（`func=system, arg_strings=/bin/sh` 直接给链 hex），不用手拼 p64 链 |

角色授权（`config/agent.json`）：`rev += [z3solve]`、`crypto += [z3solve, pycrypt]`、
`pwn += [z3solve, pwnrop]`、`misc += [z3solve]`；web/ai/coordinator 不给。
四份角色提示词都加了一段「外挂库怎么用」，协调者提示词加了「约束/校验器题派 rev/crypto（z3solve）、
pwn 走 pwnrop」的路由规则。改完 `python tools/sync_roles.py --write` 保持三源一致。

## 4. Windows 本机的限制（Smart App Control）与能力矩阵

本机 `HKLM\SYSTEM\CurrentControlSet\Control\CI\Policy\VerifiedAndReputablePolicyState = 1`
（Smart App Control 强制模式）会拦截未签名的原生 `.pyd`/`.dll`，事件日志
`Microsoft-Windows-CodeIntegrity/Operational` 记为 `WinError 4551 / 0x11c7`。
实测能力（2026-11-12，本机）：

| 能力 | Windows 本机 | Linux（WSL venv / 镜像） |
|---|---|---|
| `z3solve` | ❌ `libz3.dll` 被拦 | ✅ 全部通过（sat/unsat/多解/BitVec） |
| `pycrypt` AES-CBC/CFB/OFB | ✅ | ✅（CBC 与纯 stdlib `aesblock` 逐字节一致） |
| `pycrypt` AES-CTR/GCM/EAX、3DES、ECB | ❌ 对应 `_raw_*.pyd` 被拦 | ✅（GCM 出/验 tag、错 tag 被拒） |
| `pwnrop` checksec/symbols/ROP/gadget | ✅ | ✅ |
| 纯 stdlib 工具（aesblock/rsatool/prng/pwngen/pwnfind/elfpeek） | ✅ | ✅ |

被拦时工具不会静默降级成错答案：`pycrypt` 会提示「原生模块被系统策略拦截（Windows Smart App Control…Linux 镜像无此限制）」，
`z3solve` 提示「已安装但加载失败」。**正式镜像是 Linux，不受影响**；本地若要用 z3/GCM，
可在 WSL 里跑（见 §5）或关闭 Smart App Control（系统设置 → 隐私和安全性 → Windows 安全中心 → 应用和浏览器控制，
关闭后需重启且不可逆回评估模式，属系统级改动，需自行决定）。

## 5. 验证

- **Windows／本机**：`python tests/smoke_libs.py` → 30 项通过 + 4 项「环境跳过」（z3/GCM/CTR/3DES 被 SAC 拦，
  会额外断言「提示可操作」，确保不是静默失败）。
- **Linux／WSL**（真功能验证，20/20）：先把 Linux 版库装进 venv（脚本 `runs/_mon/wsl_setup_libs.sh`：
  apt gcc + python3.12-venv + pip 装 z3-solver/pycryptodome/pwntools），再跑

  ```powershell
  wsl -d Ubuntu-24.04 -u root -- env PYTHONPATH=/mnt/e/ctfy_project/src `
      CTFY_PYLIBS=/root/ctfy-libs/lib/python3.12/site-packages CTFY_PYLIBS_STRICT=1 `
      PYTHONIOENCODING=utf-8 /root/ctfy-libs/bin/python -u /mnt/e/ctfy_project/tests/_libs_linux_probe.py
  ```

  覆盖 z3solve（sat/unsat/多解/BitVec/坏输入）、pycrypt（CBC+CFB+OFB+CTR+GCM+3DES、GCM 错 tag 拒绝、
  与 `aesblock` 逐字节一致）、pwnrop（gcc 编 `-no-pie` 夹具 → checksec/symbols/system('/bin/sh') 链/gadget）。
- **域工具接线**：`python runs/_verify_domain_tools.py`。
- **全量**：`python runs/_mon/run_all_tests.py`。

## 6. 镜像内置现状（2026-11-12）

| 库 | 镜像状态 | 体积（解包） |
|---|---|---|
| capstone 5.0.3 | ✅ v5 起内置（`--require-hashes` 双钉死） | ~40MB（含 wheel 内 libcapstone） |
| pwntools 4.15.0 + 传递依赖 | ✅ v7 内置 | ~40MB（pwnlib）+ unicorn 57MB 等 |
| pycryptodome 4.4.0 | ✅ v7 内置（sha256 锁） | ~8MB |
| gmpy2 2.3.2 | ✅ v7 内置（sha256 锁） | ~2MB |
| sympy 1.14.0 | ✅ v7 内置 | ~66MB |
| z3-solver 5.4.1.0 | ✅ **2026-11-12 补装**（sha256 锁 `e680ce08…`） | ~21MB |

镜像体积口径见 `docs/submit-image-runbook.md`（v7.2 实测 197.7MB，门禁 400MB；补 z3 后约 +21MB）。
`tools/pylibs`（312MB）**不进镜像**，只作本机开发补库。重建镜像时 `Dockerfile` 段的
`ext-libs selftest` 会真 import + 求解（z3 断言 x>3∧x<5 → 4），漏装/装坏会构建失败。
许可证复核与镜像内容合规仍按 `docs/submit-image-runbook.md` 走。