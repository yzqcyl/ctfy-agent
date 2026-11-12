#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""smoke_local_tools.py — B1 write_file / B2 shell 自动改写 / A1 环境失败断路器 的离线回归。

依据 docs/archive/audit-agent-2026-10-30.md §4-5：
- v0 批 265 次 shell 调用被 Windows 守卫直接拒收（每次再烧一轮 ≈12k tok）；
- 14 次 report 被拒之外，SAMPLE-003/004 各把 53%/54% 预算烧在接入层；
- 模型写脚本只能用 shell 拼引号，跨平台极易写坏 → 新增 write_file。
运行: python tests/smoke_local_tools.py
"""
from __future__ import annotations

import json
import pathlib
import shutil
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ctfy_agent import Store  # noqa: E402
from ctfy_agent.progress import EnvFailureTracker, failure_kind  # noqa: E402
from ctfy_agent.tools_local import LocalTools, ToolError  # noqa: E402
from ctfy_agent.tools_misc_ext import MISC_EXT_SPECS, NAMES, write_file  # noqa: E402

FAILS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"[{'ok' if cond else 'FAIL'}] {name}" + (f"  {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def _local(tmp: pathlib.Path) -> LocalTools:
    store = Store(tmp / "store").load()
    store.challenges.clear()
    return LocalTools(store, "web-01", cache={})


def main() -> int:
    # 工作区内建临时目录（DSH 文件沙盒只允许写工作区）；
    # 不用 tempfile.mkdtemp：它在 Windows 沙盒里给新目录设出的 ACL 会导致子目录 mkdir 被拒。
    tmp = ROOT / "runs" / "_tmp_local_tools"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        return _run(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _run(tmp: pathlib.Path) -> int:

    # ---- B1: write_file（ext 层 + LocalTools 沙盒层）----
    target = tmp / "work" / "exp.py"
    res = json.loads(write_file(str(target), "print('hi')\n"))
    check("write_file 建父目录并写入", target.is_file() and target.read_text(encoding="utf-8") == "print('hi')\n")
    check("write_file 返回摘要", res.get("bytes_written", 0) == len("print('hi')\n"))
    write_file(str(target), "print('more')\n", append=True)
    check("write_file 追加模式", "more" in target.read_text(encoding="utf-8"))
    try:
        write_file(str(target), "x" * (600 * 1024))
        check("write_file 超限拒绝", False)
    except Exception as exc:  # noqa: BLE001
        check("write_file 超限拒绝", "上限" in str(exc))
    try:
        write_file(str(tmp), "x")
        check("write_file 目标是目录时拒绝", False)
    except Exception as exc:  # noqa: BLE001
        check("write_file 目标是目录时拒绝", "目录" in str(exc))

    local = _local(tmp)
    sandbox_rel = local.call("write_file", {"path": "work/a.py", "content": "print(1)\n"})
    check("LocalTools.write_file 在沙盒内可用", '"bytes_written"' in sandbox_rel)
    try:
        local.call("write_file", {"path": "../escape.txt", "content": "x"})
        check("LocalTools.write_file 越界拒绝", False)
    except ToolError as exc:
        check("LocalTools.write_file 越界拒绝", "越界" in str(exc))

    # ---- B2: Windows 命令别名自动改写 ----
    rw = LocalTools._windows_rewrite
    check("改写 python3 → python", rw("python3 exp.py") == "python exp.py")
    # [接手 2026-11-10] `dir -la` 是无效开关（dir 不认 Unix 短选项），必须换成 dir 自己的写法
    check("改写 ls → dir", rw("ls -la work") == "dir /a work")
    check("改写 ls -R → dir /s /b", rw("ls -R src") == "dir /s /b src")
    check("改写 cat → type", rw("cat a.txt | grep flag") == "type a.txt | grep flag")
    check("改写链式命令", rw("pwd && python3 -V") == "cd && python -V")
    check("无别名时原样返回", rw("curl -s http://a/b") == "curl -s http://a/b")
    check("引号包住的别名也改写", rw('"python3" x.py') == "python x.py")

    # ---- A1: 环境失败分类 + 断路器 ----
    dns = "目标 s4moxdpjck4ennv2q.internal:9999 DNS：失败（gaierror: [Errno 11001] getaddrinfo failed）"
    check("DNS 失败归类", failure_kind("netcheck", dns) == "dns")
    check("缺工具归类", failure_kind("fuzz", "[工具被拒绝] ffuf 未安装（本镜像应内置）") == "bin")
    check("工具拒绝归类 blocked", failure_kind("netcheck", "[工具被拒绝] 还没有登记任何可访问实例。") == "blocked")
    check("拒绝前缀不遮蔽 DNS 归类", failure_kind("tcp", "[工具被拒绝] DNS 解析失败：x.internal") == "dns")
    check("正常输出不归类", failure_kind("http_request", '{"status": 200, "body": "ok"}') is None)
    check("超时归类", failure_kind("shell", "命令超时（60s）: python x.py") == "timeout")

    tr = EnvFailureTracker()
    notes = [tr.observe("netcheck", dns) for _ in range(3)]
    check("连续 3 次 → 环境侧提示", bool(notes[2]) and "环境侧" in notes[2], str(notes))
    check("提示只在第 3 次出现", notes[0] is None and notes[1] is None)
    more = [tr.observe("netcheck", dns) for _ in range(3)]
    check("连续 6 次 → 收口建议", bool(more[2]) and "收口" in more[2], str(more))
    check("成功输出重置连续计数", tr.observe("http_request", '{"status":200}') is None and tr.streak == 0)
    check("统计口径", tr.stats()["env_failures"] == 6 and tr.stats()["max_streak"] == 6, json.dumps(tr.stats()))

    class _Budget:
        max_tokens = 1000
        total_tokens = 500

    tr2 = EnvFailureTracker()
    for _ in range(3):
        tr2.observe("tcp", dns)
    ratio_note = tr2.observe("tcp", dns, budget=_Budget())
    check("环境失败占比高 → 预算侧提示", bool(ratio_note) and "预算已用" in ratio_note, str(ratio_note))

    # ---- A2: {v} 模板不能当主机名 ----
    local.allowed_hosts = {"real.internal"}
    local.allowed_endpoints = {"real.internal:1337"}
    try:
        local.call("tcp", {"host": "host{v}", "port": 1337, "payload": "x"})
        check("host 位模板被拒", False)
    except ToolError as exc:
        check("host 位模板被拒并发指引", "模板" in str(exc) and "{v}" in str(exc), str(exc))

    # ---- 跨 run 沙盒污染护栏（2026-11-11 真实踩坑）----
    # 每个 run 的沙盒互相隔离：读到别的 run 只可能是历史残留（旧 run 从官方仓库抓的占位 flag.txt）。
    runs_root = tmp  # store 在 tmp/store，沙盒在 tmp/store/sandbox/<cid>
    sibling = tmp / "plat" / "sandbox" / "old-cid" / "src"
    sibling.mkdir(parents=True, exist_ok=True)
    (sibling / "flag.txt").write_text("flag{stale_repo_placeholder}\n", encoding="utf-8")
    own = local._sandbox_dir()
    own.mkdir(parents=True, exist_ok=True)
    guard = local._cross_run_guard
    check("跨 run 读取被拒（命中兄弟 run 名）",
          "其它运行目录" in guard("type ..\\..\\plat\\sandbox\\old-cid\\src\\flag.txt"), guard("type ..\\..\\plat\\sandbox\\x"))
    check("跨 run 读取被拒（正斜杠也算）",
          "其它运行目录" in guard("cat ../plat/sandbox/old-cid/src/flag.txt"))
    check("本 run 内正常命令放行", guard("type work\\exp.py") == "" and guard("python s1.py") == "")
    check("不含 sandbox 的命令不受影响", guard("cd .. && dir") == "")
    # [回归 2026-11-11 二次实测] runs/ 下有个历史误建目录就**叫 `runs`**；只按名字裸匹配会把
    # 合法命令也拒掉（本地 plat 测试真实误伤 5 次：`cd E:\...\runs\plat\sandbox\SAMPLE-003 && ...`
    # 与 `wsl.exe -e bash /mnt/e/.../runs/plat/sandbox/...` 都被拦，模型只好绕路浪费步数）。
    (tmp / "runs").mkdir(parents=True, exist_ok=True)
    check("兄弟目录名叫 runs 时不误伤本 run 绝对路径",
          guard("cd /d E:\\x\\runs\\store\\sandbox\\web-01 && python s1.py") == "",
          guard("cd /d E:\\x\\runs\\store\\sandbox\\web-01 && python s1.py"))
    check("兄弟目录名叫 runs 时不误伤 wsl 绝对路径",
          guard("wsl.exe -e bash /mnt/e/x/runs/store/sandbox/web-01/run1.sh") == "")
    check("跨 run 仍被拒（路径段精确匹配）",
          "其它运行目录" in guard("type ..\\..\\plat\\sandbox\\old-cid\\src\\flag.txt"))
    try:
        local.shell("type ..\\..\\plat\\sandbox\\old-cid\\src\\flag.txt")
        check("shell() 真的拒绝跨 run 读取", False)
    except ToolError as exc:
        check("shell() 真的拒绝跨 run 读取", "其它运行目录" in str(exc), str(exc)[:120])

    # ---- 规格面：write_file 可见、decode_blob 必填、burst 上限文案 ----
    names = {s["function"]["name"] for s in MISC_EXT_SPECS}
    check("write_file 已注册为工具", "write_file" in names and "write_file" in NAMES)
    decode = next(s for s in MISC_EXT_SPECS if s["function"]["name"] == "decode_blob")
    check("decode_blob 必填 text", decode["function"]["parameters"].get("required") == ["text"])
    from ctfy_agent.tools_local import BURST_SPEC  # noqa: PLC0415

    desc = BURST_SPEC["function"]["parameters"]["properties"]["values"]["description"]
    check("http_burst 上限文案含分批指引", "拆成多批" in desc)

    print()
    if FAILS:
        print(f"失败 {len(FAILS)} 项: {FAILS}")
        return 1
    print("smoke_local_tools 全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())