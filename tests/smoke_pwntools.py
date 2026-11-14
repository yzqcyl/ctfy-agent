"""PWN 工具链自检：agent 的 shell 里 `from pwn import *` 必须真的能用。

为什么要有这条（2026-11-10 真实踩坑）：`tools/pylibs` 里 pwntools 装好了，但 psutil 是
一次被中断的 `pip install --target` 留下的半截包（缺 `_common.py`），一 import 就
「partially initialized module 'psutil'」→ PWN 题一上来就废，而 `--list` 只看目录名看不出来。

本机没装 pylibs 时直接跳过（退出 0），不阻塞 CI。
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

FAILS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"[{'ok' if cond else 'FAIL'}] {name}" + (f"  {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def main() -> int:
    pylibs = ROOT / "tools" / "pylibs"
    if not (pylibs / "pwn").is_dir():
        print("[skip] tools/pylibs/pwn 不在（本机没装 pwntools），跳过")
        return 0

    from ctfy_agent.state import Store
    from ctfy_agent.tools_local import LocalTools

    tmp = ROOT / "runs" / "_t_pwntools_smoke"
    shutil.rmtree(tmp, ignore_errors=True)
    try:
        store = Store(tmp / "store").load()
        store.challenges.clear()
        local = LocalTools(store, "pwn-smoke", cache={})
        out = local.shell(
            'python -c "from pwn import *; context.log_level=\'error\'; '
            'print(\'pwn ok\', p64(0x41424344).hex(), u64(b\'ABCDEFGH\'))"')
        check("shell 里能 import pwn 并打包/解包", "pwn ok" in out, out[:300])
        out2 = local.shell(
            'python -c "from pwn import *; context.log_level=\'error\'; '
            'print(\'tube ok\', remote, ELF, ROP)"')
        check("remote/ELF/ROP 都能导入", "tube ok" in out2, out2[:300])
        out3 = local.shell(
            'python -c "import psutil; print(\'psutil ok\', psutil.__version__)"')
        check("psutil 完整可导入（半截安装会在这里露馅）", "psutil ok" in out3, out3[:300])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if FAILS:
        print(f"smoke_pwntools 失败 {len(FAILS)} 项：" + "、".join(FAILS))
        print("修复：python tools/fetch_pylibs.py psutil pwntools")
        return 1
    print("smoke_pwntools 全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())