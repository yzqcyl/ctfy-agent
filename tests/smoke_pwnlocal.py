#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""smoke_pwnlocal.py — 本地运行附件工具（pwnlocal）回归。

覆盖：
  A. 工具已注册到 pwn/rev 工具面（specs + config）
  B. 参数校验：缺文件 / 非法 hex / Windows 上明确拒绝
  C. Linux 容器内真跑：/bin/echo 喂 stdin、gdb=true 抓寄存器
运行：python tests/smoke_pwnlocal.py（Windows 上自动跳过 C 的实跑）
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

FAILS: list[str] = []


def check(name: str, cond: bool, detail: object = "") -> None:
    print(f"[{'ok' if cond else 'FAIL'}] {name}" + (f"  {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


_TMP_RUN = ROOT / "tests" / "_tmp_pwnlocal"


class _Store:
    run_dir = _TMP_RUN

    def note(self, *a, **k):
        return None

    def append_transcript(self, *a, **k):
        return None


def _tools():
    from ctfy_agent.tools_local import LocalTools  # noqa: PLC0415
    return LocalTools(_Store(), "CHAL-PWNLOCAL", allow_shell=True)


def case_spec() -> None:
    from ctfy_agent.tools_local import LocalTools  # noqa: PLC0415

    spec = next((s for s in _tools().specs() if s["function"]["name"] == "pwnlocal"), None)
    check("specs 里有 pwnlocal", spec is not None)
    props = sorted((spec or {}).get("function", {}).get("parameters", {}).get("properties", {}))
    check("pwnlocal 参数齐全", props == ["argv", "gdb", "gdb_cmds", "path", "stdin_hex",
                                         "stdin_text", "timeout"], str(props))
    cfg = json.loads((ROOT / "config/agent.json").read_text(encoding="utf-8-sig"))
    roles = cfg.get("roles") or {}
    check("config pwn 工具面含 pwnlocal", "pwnlocal" in (roles.get("pwn") or {}).get("local", []))
    check("config rev 工具面含 pwnlocal", "pwnlocal" in (roles.get("rev") or {}).get("local", []))
    from ctfy_agent.crew import Crew  # noqa: PLC0415
    defaults = Crew.DEFAULT_ROLES if hasattr(Crew, "DEFAULT_ROLES") else None
    if defaults:
        check("crew 默认 pwn 工具面含 pwnlocal", "pwnlocal" in defaults["pwn"]["local"])
        check("crew 默认 rev 工具面含 pwnlocal", "pwnlocal" in defaults["rev"]["local"])


def case_errors() -> None:
    from ctfy_agent.tools_local import ToolError  # noqa: PLC0415

    t = _tools()
    try:
        t.call("pwnlocal", {"path": "nope.bin"})
        check("缺文件报错", False, "没有抛错")
    except ToolError as exc:
        check("缺文件报错", "找不到文件" in str(exc), str(exc)[:100])
    if os.name == "nt":
        tmp = Path(t._sandbox_dir())
        tmp.mkdir(parents=True, exist_ok=True)
        (tmp / "x.bin").write_bytes(b"not-an-elf")
        try:
            t.call("pwnlocal", {"path": "x.bin", "stdin_hex": "41"})
            check("Windows 明确拒绝", False, "没有抛错")
        except ToolError as exc:
            check("Windows 明确拒绝", "Linux" in str(exc), str(exc)[:100])


def case_real_run() -> None:
    """Linux 容器内真跑：/bin/echo + gdb。"""
    t = _tools()
    echo = "/bin/echo" if Path("/bin/echo").exists() else None
    if os.name != "nt" and echo is None:
        # 沙盒白名单外，直接构造一个临时副本到沙盒目录
        pass
    # 沙盒路径锚定：把 /bin/echo 复制进沙盒再跑
    from ctfy_agent.tools_local import ToolError  # noqa: PLC0415
    try:
        tmpdir = Path(t._sandbox_dir())
        tmpdir.mkdir(parents=True, exist_ok=True)
        target = tmpdir / "echo_test"
        if echo:
            target.write_bytes(Path(echo).read_bytes())
            target.chmod(0o755)
        out = t.call("pwnlocal", {"path": "echo_test", "stdin_text": "hello",
                                  "argv": ["A", "B"]})
        check("本地运行 echo 输出 argv", "A B" in out, out[:300])
        check("本地运行回报 exit=0", "exit=0" in out, out[:200])
        out2 = t.call("pwnlocal", {"path": "echo_test", "gdb": True, "argv": ["X"]})
        low = out2.lower()
        # echo 正常退出时 gdb 报 "exited normally"（无寄存器）；只有崩溃才给 rip/eip。
        check("gdb 模式有确定的运行结论",
              (("rip" in low or "eip" in low) or "exited normally" in low
               or "exited with code" in low), out2[:400])
        check("gdb 模式回报头正确", ("pwnlocal" in out2 and "gdb=true" in low), out2[:200])
    except ToolError as exc:
        check("Linux 实跑", False, f"ToolError: {exc}")
    finally:
        try:
            target.unlink()
        except Exception:  # noqa: BLE001
            pass


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    case_spec()
    case_errors()
    if os.name == "nt":
        print("[skip] Windows 开发机：跳过 Linux 实跑用例（容器内会跑）")
    else:
        case_real_run()
    print()
    if FAILS:
        print(f"smoke_pwnlocal 失败 {len(FAILS)} 项: {FAILS}")
        return 1
    print("smoke_pwnlocal 全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
