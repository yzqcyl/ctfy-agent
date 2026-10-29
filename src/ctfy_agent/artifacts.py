# -*- coding: utf-8 -*-
"""附件落盘：把 download_*_attachment 的返回从「塞进上下文」改成「写进沙盒 + 回摘要」。

问题（SAMPLE-010/011 实测，2026-10-28）：
  ctxcompress.shrink_tool_text 把每个工具结果压成「头 45% + 关键行 + 尾 12%」，
  上限 1600 字符（DEFAULT_TOOL_CHARS）。附件下载返回是长 JSON：
    - SAMPLE-010 licencecheck.py：2521 字符 → 中段被丢，源码不完整
    - SAMPLE-011 vaultsvc：38,653 字符（14392 字节 base64）→ 只剩前 1200 字符 ELF 头
  模型于是看不见完整文件，只能反复重下（被账本拦截）直到预算耗尽（实测 159k tok / 3 步）。
  平台返回本身是完整的（已用 MCP 直连验证：内容长度与 size 一致）。

修法：下载类工具的返回不进上下文，而是解码后写入本题沙盒，回一个短摘要
（路径 / 字节数 / sha256 / 头 32 字节 hex）。后续用 hexpeek / elfpeek / shell 读文件。

设计约束：
- 纯 stdlib；不 import solver，避免循环依赖；
- 只处理已知下载工具与可识别结构，识别不了就原样返回（不影响其他工具）；
- 文件名消毒：去路径分隔符、拒绝 ..，落盘必须仍在沙盒内。
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from pathlib import Path

DOWNLOAD_TOOLS = frozenset({"download_instance_attachment", "download_challenge_attachment"})
MAX_PERSIST_BYTES = 32 * 1024 * 1024  # 单附件上限，防解压炸弹式输入
_UNSAFE_NAME = re.compile(r"[\\/\x00:*?\"<>|]+")


def _loads_maybe(text: str):
    """尽量把返回值解成 dict；兼容「JSON 字符串里再套 JSON」的两层包装。"""
    if isinstance(text, (dict, list)):
        return text
    raw = str(text or "").strip()
    if not raw or raw[0] not in "{[":
        return None
    for _ in range(3):
        try:
            obj = json.loads(raw)
        except (ValueError, TypeError):
            return None
        if isinstance(obj, str):
            raw = obj.strip()
            continue
        return obj
    return None


def _unwrap(obj):
    """从 MCP 信封里挖出真正带 filename/content 的 dict。

    真实形状（2026-10-28 实测）：
      {"content":[{"type":"text","text":"{\"filename\":...,\"encoding\":...,\"content\":...}"}],
       "structuredContent": {...}, "isError": false}
    """
    if isinstance(obj, dict):
        if "content" in obj and ("filename" in obj or "name" in obj):
            return obj
        # 信封：content 是 [{type,text}] 列表
        blocks = obj.get("content")
        if isinstance(blocks, list):
            for block in blocks:
                if isinstance(block, dict):
                    inner = _loads_maybe(block.get("text") or "")
                    found = _unwrap(inner) if inner is not None else None
                    if found:
                        return found
        # 其他键里继续找
        for key in ("structuredContent", "result", "data", "attachment"):
            found = _unwrap(obj.get(key)) if obj.get(key) is not None else None
            if found:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = _unwrap(item)
            if found:
                return found
    return None


def _parse_call_text(text: str):
    """解析 platform.call_text() 的返回。

    真实形状（render_call_result，2026-10-28 实测）：
        ----- 工具返回 -----
        {"filename":"licencecheck.py","size":1015,"encoding":"utf-8","content":"..."}
        {
          "filename": "licencecheck.py",
          ...
        }
    即「前缀一行 + 原文 JSON + 尾部美化 JSON」。所以先剥前缀，再用 raw_decode 只取第一个 JSON 值。
    """
    raw = str(text or "")
    if raw.startswith("-----"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else ""
    raw = raw.removeprefix("[工具报错] ").lstrip()
    if not raw or raw[0] not in "{[":
        return _loads_maybe(raw)
    try:
        obj, _end = json.JSONDecoder().raw_decode(raw)
        return obj
    except ValueError:
        return _loads_maybe(raw)


def _sanitize_filename(name: str, fallback: str) -> str:
    base = (name or "").replace("\\", "/").split("/")[-1].strip()
    base = _UNSAFE_NAME.sub("_", base).strip(" .")
    if not base or base in (".", ".."):
        base = fallback
    return base


def persist_download(args: dict | None, result_text: str, sandbox_dir: str | Path | None) -> str:
    """识别下载返回并落盘；返回给模型看的短摘要。无法识别时原样返回 result_text。"""
    if not sandbox_dir:
        return result_text
    found = _unwrap(_parse_call_text(result_text))
    if not found:
        return result_text
    filename = found.get("filename") or found.get("name") or (args or {}).get("filename") or ""
    content = found.get("content")
    if not isinstance(content, str):
        return result_text
    encoding = str(found.get("encoding") or "utf-8").lower()
    try:
        if encoding in ("base64", "b64"):
            raw = base64.b64decode(content, validate=False)
        elif encoding in ("hex",):
            raw = binascii.unhexlify(content)
        else:
            raw = content.encode("utf-8", "surrogateescape")
    except (binascii.Error, ValueError):
        return result_text
    if not raw or len(raw) > MAX_PERSIST_BYTES:
        return result_text

    root = Path(sandbox_dir)
    root.mkdir(parents=True, exist_ok=True)
    name = _sanitize_filename(str(filename), "attachment.bin")
    target = (root / name).resolve()
    if not target.is_relative_to(root.resolve()):
        return result_text
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(raw)

    declared = found.get("size")
    sha = hashlib.sha256(raw).hexdigest()
    head_hex = raw[:32].hex()
    lines = [
        "[附件已落盘] 不要再次下载，也不要把内容整段打印到上下文。",
        "  路径：%s（相对本题沙盒根，可直接给 hexpeek / elfpeek / shell 用）" % name,
        "  大小：%d 字节（平台声明 %s）  sha256：%s" % (len(raw), declared if declared is not None else "-", sha[:16]),
        "  头 32 字节(hex)：%s" % head_hex,
        "  下一步：ELF/二进制用 elfpeek(path=%r)；其它用 hexpeek(path=%r, offset=...) 或 shell 处理。" % (name, name),
    ]
    note = str(found.get("note") or "").strip()
    if note:
        lines.append("  平台备注：%s" % note[:200])
    return "\n".join(lines)
