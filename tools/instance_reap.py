# -*- coding: utf-8 -*-
"""实例池清扫器（宿主侧）：平台每队只有 1 个实例槽位，泄漏一个就堵死后面所有题。

为什么单独做一个脚本：**进程被 kill 时 finally 不执行**，实例就留在平台上占位；之后
`start_instance` 全报 "Rate limited by upstream API"，agent 会误判为时间限流并继续重试，
每题 5 次硬上限一烧光，当天该题作废（11-03 实测 21% 的题就是这么丢的）。

用法：
    python tools/instance_reap.py                 # 只列，不动（默认安全模式）
    python tools/instance_reap.py --reap          # 真的停掉「不属于 --keep」的实例
    python tools/instance_reap.py --keep CTFARENA-035 --reap
    python tools/instance_reap.py --protect CTFARENA-048 CTFARENA-049 --reap
    python tools/instance_reap.py --reap --grace 0    # 连刚起的也停（并发批跑时别这么用）

安全默认：`--reap` 之外一律 dry-run；拿不到创建时间的实例**永不自动停**（宁可漏杀）。
退出码：0=正常（含"无事可做"），2=连不上平台/读不到列表。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ctfy_agent.cli import build_config, make_platform  # noqa: E402
from ctfy_agent.instance_guard import (  # noqa: E402
    instance_challenge_id, instance_id, list_all, reap, summarize,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="平台实例池清扫（配额只有 1，泄漏即堵死）")
    parser.add_argument("--config", help="配置文件路径，默认 config/agent.json")
    parser.add_argument("--url", help="覆盖 MCP 端点")
    parser.add_argument("--run-id", default="default", help="状态目录名，仅供读本地题库")
    parser.add_argument("--keep", default="", help="永不触碰的 challenge_id（通常是当前在跑的题）")
    parser.add_argument("--protect", nargs="*", default=[], help="额外保护名单（并发批跑的兄弟题）")
    parser.add_argument("--grace", type=float, default=120.0,
                        help="只停存活超过 N 秒的实例（默认 120；0=连刚起的也停，危险）")
    parser.add_argument("--reap", action="store_true", help="真的执行 stop_instance（默认只列不动）")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    config = build_config(args)
    if config.missing():
        print("配置不完整：" + "；".join(config.missing()), file=sys.stderr)
        return 2
    platform = make_platform(config, verbose=args.verbose)
    try:
        platform.connect()
    except Exception as exc:  # noqa: BLE001
        print(f"MCP 连接失败：{exc}", file=sys.stderr)
        return 2

    items = list_all(platform)
    if items is None:
        print("读不到实例列表（平台没有 list_instances，或调用失败）。", file=sys.stderr)
        return 2

    print(f"== 当前实例（{len(items)} 个）==")
    if not items:
        print("  （空）——唯一位子是空闲的，可以放心开跑。")
        return 0
    for item in items:
        print(f"  id={instance_id(item)[:12] or '(无)'}  challenge={instance_challenge_id(item) or '未知'}"
              f"  raw={ {k: v for k, v in item.items() if k in ('status', 'created_at', 'started_at', 'ttl')} }")

    if not args.reap:
        print("\n（dry-run）加 --reap 才会真的停掉不属于 --keep 的实例。", file=sys.stderr)
        guard = reap(platform, keep=args.keep, protect=tuple(args.protect),
                     grace_seconds=args.grace, dry_run=True)
        print(summarize(guard))
        return 0

    guard = reap(platform, keep=args.keep, protect=tuple(args.protect), grace_seconds=args.grace)
    print("\n" + summarize(guard))
    for item in guard["stopped"]:
        print(f"  [停] {item['id'][:12]}  challenge={item.get('challenge_id') or '未知'}")
    for item in guard["kept"]:
        print(f"  [留] {item['id'][:12]}  challenge={item.get('challenge_id')}（{item['why']}）")
    for item in guard["skipped_unknown_age"]:
        print(f"  [跳] {item['id'][:12]}  challenge={item.get('challenge_id') or '未知'}"
              f"（存活不足 {args.grace:.0f}s 或无时间戳）")
    for item in guard["failed"]:
        print(f"  [败] {item.get('id', '')[:12]}  {item.get('error') or item.get('reason')}")
    return 0 if guard.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
