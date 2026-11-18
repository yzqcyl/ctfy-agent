#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fetch_comp_attachments.py — 赛前/赛中拉取比赛附件，自动识别 WireGuard VPN 配置并落盘。

用途（VPS 宿主直接跑，纯 stdlib）：
    PYTHONPATH=src python3 tools/fetch_comp_attachments.py \
        --competition-id <id> --out /root/vpn

行为：
  1) list_competition_attachments → 显示清单；
  2) 下载所有附件到 --out；
  3) 扫描文本文件里是否像 WireGuard conf（[Interface]/[Peer]/PrivateKey），是则复制为
     /root/vpn/wg0.conf（600），供 watch.sh 里 wg-quick up 使用。
退出码 0=完成（即使没附件），2=平台调用失败。
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

WG_RE = re.compile(r"\[Interface\]|\[Peer\]|PrivateKey\s*=", re.I)


def unwrap(res):
    if isinstance(res, dict):
        if "structuredContent" in res:
            return res["structuredContent"]
        for it in res.get("content") or []:
            t = it.get("text") if isinstance(it, dict) else None
            if t:
                try:
                    return json.loads(t)
                except ValueError:
                    continue
    return res


def platform():
    from ctfy_agent.config import load  # noqa: PLC0415
    from ctfy_agent.gui import profiles  # noqa: PLC0415
    from ctfy_agent.platform import CtfyPlatform  # noqa: PLC0415

    cfg = load()
    data = profiles.load()
    name = data.get("active") or "CTFY"
    profiles.apply(cfg, (data.get("profiles") or {}).get(name) or {})
    p = CtfyPlatform(cfg.mcp_url, cfg.token, timeout=60, connect_timeout=25,
                     tool_map=cfg.extra.get("tool_map") or {})
    p.connect()
    return p


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--competition-id", required=True)
    ap.add_argument("--out", default="/root/vpn")
    args = ap.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    p = platform()
    data = unwrap(p.call("list_competition_attachments", {"competition_id": args.competition_id}))
    atts = data.get("attachments") if isinstance(data, dict) else data
    atts = atts or []
    print(f"比赛附件 {len(atts)} 个：")
    for a in atts:
        print(f"  - {a.get('name')} ({a.get('size')} B) id={a.get('id') or a.get('file_id')}")
    if not atts:
        print("（没有附件；VPN 配置可能尚未公布）")
        return 0

    tool = p.try_resolve("download_competition_attachment") or "download_competition_attachment"
    ok = 0
    for a in atts:
        name = str(a.get("name") or "attachment")
        try:
            res = unwrap(p.call(tool, {"competition_id": args.competition_id,
                                       "attachment_id": a.get("id") or a.get("file_id"),
                                       "name": name}))
        except Exception as exc:  # noqa: BLE001
            print(f"  ! 下载 {name} 失败：{exc}")
            continue
        raw = None
        if isinstance(res, dict):
            b64 = res.get("content_base64") or res.get("base64") or res.get("data")
            if isinstance(b64, str) and b64:
                try:
                    raw = base64.b64decode(b64)
                except ValueError:
                    raw = None
        if raw is None:
            print(f"  ! {name} 回执里没有内容（结构：{list(res)[:6] if isinstance(res, dict) else type(res)}）")
            continue
        dest = out / name
        dest.write_bytes(raw)
        print(f"  [ok] {name} -> {dest} ({len(raw)} B)")
        try:
            text = raw.decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            text = ""
        if WG_RE.search(text):
            wg = out / "wg0.conf"
            wg.write_text(text, encoding="utf-8")
            wg.chmod(0o600)
            print(f"  [vpn] 识别为 WireGuard 配置 → {wg}")
        ok += 1
    print(f"完成：{ok}/{len(atts)} 个附件落盘")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())