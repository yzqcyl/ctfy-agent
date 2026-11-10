# -*- coding: utf-8 -*-
"""题目附件自动下载：把平台挑战的 `files[]` 落进本题沙盒。

为什么需要（2026-11-10 实测，runs/plat 26 题）
---------------------------------------------
13 道未解题里有 7 道的收尾结论是「沙盒目录为空 / 平台未下发任何附件」：
unipickle、satisfied、winxy-pistol、vorpal-sword、spn、TranscendentRenovation、
Not My Mod Files Bestie。模型当时只能去 GitHub 抓官方仓库（还常被 DNS 拦），
或者靠题面猜 flag。而平台**本来就有**附件：

  get_practice_challenge → challenge.files[].id / file.original_name / size
  ctf2_get_attachment_url(practice_ground_id, challenge_id, file_id) → 5 分钟签名 URL

问题只是：这两步都不在「一键接入」生成的 9 条 tool_map 里，模型工具面看不到，
也没有任何代码在题目就绪时主动去拉。于是沙盒一直是空的。

本模块做的事（不碰 platform.py / solver.py / cli.py 的既有行为）
--------------------------------------------------------------
1. 读一次 get_challenge 原始回执，挖出 files[] / attachments[]；
2. 逐个取签名 URL 并下载进 `runs/<run>/sandbox/<challenge_id>/`；
3. 回一份「已落盘清单」文本，调用方塞进 store 笔记（会进协调者 brief）。

兼容两种平台形态：
  * URL 模式（DASCTF）：附件工具返回 {url, file_name, size} → 自己 urllib 下载；
  * 内容模式（CTFY）：附件工具直接返回 {filename, content, encoding} → 复用 artifacts 落盘。

安全与工程约束：纯标准库；单文件 32MB 上限；文件名消毒（拒绝路径穿越）；
签名 URL 只在内存里用、不写进 state/日志；任何一步失败都只记一条 warning，绝不拦解题。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

from .artifacts import MAX_PERSIST_BYTES, _sanitize_filename, _unwrap

MANIFEST_NAME = "_attachments.json"
_CHUNK = 64 * 1024
_UNSAFE_TOKENS = re.compile(r"(?:token|sig|signature|expires)=", re.I)
_DEFAULT_TIMEOUT = 60.0


# ---------------------------------------------------------------- 沙盒与清单
def sandbox_root(store, challenge_id: str) -> Path:
    """与 LocalTools._sandbox_dir() 同口径：runs/<run>/sandbox/<challenge_id>。"""
    run_dir = getattr(store, "run_dir", None)
    if run_dir is None:
        run_dir = Path(store)
    return Path(run_dir) / "sandbox" / str(challenge_id)


def _mask_url(url: str) -> str:
    """日志/清单里只留 origin，签名 token 不外泄。"""
    try:
        scheme, rest = url.split("://", 1)
        host = rest.split("/", 1)[0]
        return f"{scheme}://{host}/...（签名已隐藏）"
    except ValueError:
        return "（不可解析 URL）"


# ---------------------------------------------------------------- 平台返回解析
def _dig_envelope(value):
    """从 MCP 回执信封里挖出业务对象。

    [接手 2026-11-10 实测] DASCTF 的 `ctf2_get_practice_challenge` 返回的是
    `{"content": [{"type":"text","text":"{\\"challenge\\":{…files[]…}}" }], "structuredContent": {…}}`。
    platform._as_json 只接受**字符串**，喂 dict 会抛 AttributeError 被吞掉，于是上层拿到的
    是整个信封 → `_file_entries` 一律为空 → 误报「题目没有附件」（unipickle.py 就这么漏的）。
    这里按「structuredContent → content[].text 里的 JSON」顺序拆一层。
    """
    if not isinstance(value, dict):
        return value
    for key in ("structuredContent", "structured_content"):
        inner = value.get(key)
        if isinstance(inner, dict) and inner:
            return inner
    content = value.get("content")
    if isinstance(content, list):
        for part in content:
            text = part.get("text") if isinstance(part, dict) else None
            if isinstance(text, str) and text.strip():
                try:
                    parsed = json.loads(text)
                except ValueError:
                    continue
                if isinstance(parsed, (dict, list)):
                    return parsed
    return value


def _as_json(platform, result):
    """把 MCP 信封解成 python 对象；平台可能自带 _as_json（只吃字符串，见 _dig_envelope）。"""
    payload = result
    if isinstance(result, str):
        try:
            from .platform import render_call_result  # noqa: PLC0415

            payload = render_call_result(result)
        except Exception:  # noqa: BLE001
            payload = result
    payload = _dig_envelope(payload)
    if isinstance(payload, str):
        try:
            parsed = platform._as_json(payload)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001 - 假平台/无该方法时自己解
            text = payload.strip()
            if text.startswith("-----"):
                text = text.split("\n", 1)[1] if "\n" in text else ""
            try:
                parsed = json.loads(text)
            except (ValueError, TypeError):
                return None
        payload = parsed
    payload = _dig_envelope(payload)
    # artifacts._unwrap 只认「带 content/structuredContent 的下载信封」，对普通业务
    # 对象（{"id","files"}）会返回 None —— 这里挖不到就退回原值，别把有效解析结果吞掉。
    dug = _unwrap(payload)
    return dug if dug is not None else payload


def _file_entries(info) -> list[dict]:
    """从题目回执里挖附件条目，统一成 {file_id, name, size, mime}。"""
    if not isinstance(info, dict):
        return []
    buckets = []
    for key in ("files", "attachments", "task_files", "resources"):
        value = info.get(key)
        if isinstance(value, list):
            buckets.extend(value)
        elif isinstance(value, dict):
            inner = value.get("files") if isinstance(value.get("files"), list) else None
            if inner:
                buckets.extend(inner)
    out: list[dict] = []
    seen: set[str] = set()
    for raw in buckets:
        if not isinstance(raw, dict):
            continue
        meta = raw.get("file") if isinstance(raw.get("file"), dict) else {}
        file_id = str(raw.get("id") or raw.get("file_id") or meta.get("id") or "")
        name = str(
            raw.get("file_name") or raw.get("name") or raw.get("original_name")
            or meta.get("original_name") or meta.get("file_name") or ""
        )
        size = raw.get("size") if raw.get("size") is not None else meta.get("size")
        mime = str(raw.get("mime_type") or meta.get("mime_type") or "")
        key = file_id or name
        if not key or key in seen:
            continue
        seen.add(key)
        out.append({"file_id": file_id, "name": name, "size": size, "mime": mime})
    return out


def _find_url(obj, depth: int = 0) -> str:
    """递归找第一个 http(s) URL（签名链在信封深处）。"""
    if depth > 8:
        return ""
    if isinstance(obj, dict):
        for key in ("url", "download_url", "href", "signed_url"):
            value = obj.get(key)
            if isinstance(value, str) and value.startswith(("http://", "https://")):
                return value
        for value in obj.values():
            found = _find_url(value, depth + 1)
            if found:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = _find_url(item, depth + 1)
            if found:
                return found
    elif isinstance(obj, str):
        text = obj.strip()
        if text.startswith(("http://", "https://")):
            return text
        if text.startswith("{"):
            try:
                return _find_url(json.loads(text), depth + 1)
            except (ValueError, TypeError):
                return ""
    return ""


def _pick_attachment_tools(platform) -> tuple[str, str]:
    """返回 (url 工具名, 内容工具名)，任一可能为空。

    不写死平台前缀：按工具名语义挑。DASCTF 是 ctf2_get_attachment_url（URL 模式），
    CTFY 是 download_challenge_attachment（内容模式）。
    """
    try:
        names = list(platform.tool_names())
    except Exception:  # noqa: BLE001
        names = []
    if not names:
        return "", ""
    url_tool = content_tool = ""
    for name in names:
        low = name.lower()
        if "attachment" not in low:
            continue
        if "url" in low and not url_tool:
            url_tool = name
        elif "download" in low and not content_tool:
            content_tool = name
    # 内容工具优先走语义注册表（CTFY 的 9 个动作名由 platform.py 维护）
    if not content_tool:
        try:
            content_tool = platform.resolve("download_challenge_attachment")
        except Exception:  # noqa: BLE001 - 该平台没有内容式下载工具
            content_tool = ""
    return url_tool, content_tool


def _attachment_args(platform, tool: str, *, file_id: str, challenge_id: str,
                     competition_id: str, name: str = "") -> dict:
    """按目标工具 schema 组装参数；只给 schema 认识的键，其余交给参数桥接补。"""
    generic = {
        "file_id": file_id,
        "challenge_id": challenge_id,
        "competition_id": competition_id,
        "practice_ground_id": competition_id,
        "filename": name,
        "file_name": name,
    }
    try:
        props = platform.prop_names(tool)
    except Exception:  # noqa: BLE001
        props = set()
    if not props:  # 无 schema（假平台）→ 全给，调用方自己筛
        return {k: v for k, v in generic.items() if v}
    args = {k: v for k, v in generic.items() if k in props and v}
    # 有的平台两个都叫 challenge 的字段：practice_ground_id 常是必填，补上
    if "practice_ground_id" in props and competition_id:
        args["practice_ground_id"] = competition_id
    return args


# ---------------------------------------------------------------- 下载
def _download(url: str, target: Path, *, timeout: float = _DEFAULT_TIMEOUT,
              opener=None) -> tuple[int, str]:
    """流式下载到 target，返回 (字节数, sha256)。超限抛 ValueError。"""
    req = urllib.request.Request(url, headers={"User-Agent": "ctfy-agent/attachments"})
    sha = hashlib.sha256()
    total = 0
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".part")
    open_fn = opener or urllib.request.urlopen
    with open_fn(req, timeout=timeout) as resp, open(tmp, "wb") as fh:
        while True:
            chunk = resp.read(_CHUNK)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_PERSIST_BYTES:
                fh.close()
                try:
                    tmp.unlink()
                except OSError:
                    pass
                raise ValueError(f"附件超过 {MAX_PERSIST_BYTES // (1024 * 1024)}MB 上限，已放弃")
            sha.update(chunk)
            fh.write(chunk)
    os.replace(tmp, target)
    return total, sha.hexdigest()


def _fetch_one_url_mode(platform, tool: str, entry: dict, *, challenge_id: str,
                        competition_id: str, root: Path, timeout: float,
                        opener=None) -> dict:
    args = _attachment_args(platform, tool, file_id=entry["file_id"], challenge_id=challenge_id,
                            competition_id=competition_id, name=entry["name"])
    raw = platform.call_raw(tool, args)
    info = _as_json(platform, raw)
    url = _find_url(info)
    if not url:
        return {**entry, "ok": False, "error": "附件工具未返回可用 URL"}
    name = _sanitize_filename(str(entry["name"] or (info or {}).get("file_name") or ""), "attachment.bin")
    target = root / name
    if target.is_file() and entry.get("size") is not None:
        try:
            if target.stat().st_size == int(entry["size"]):
                return {**entry, "ok": True, "name": name, "path": name,
                        "bytes": target.stat().st_size, "skipped": True,
                        "sha256": hashlib.sha256(target.read_bytes()).hexdigest()}
        except (OSError, TypeError, ValueError):
            pass
    size, sha = _download(url, target, timeout=timeout, opener=opener)
    return {**entry, "ok": True, "name": name, "path": name, "bytes": size,
            "sha256": sha, "url_origin": _mask_url(url)}


def _fetch_one_content_mode(platform, tool: str, entry: dict, *, challenge_id: str,
                            competition_id: str, root: Path) -> dict:
    """CTFY 式：工具直接回 {filename, content, encoding}。复用 artifacts 的落盘逻辑。"""
    from .artifacts import persist_download  # noqa: PLC0415

    args = _attachment_args(platform, tool, file_id=entry["file_id"], challenge_id=challenge_id,
                            competition_id=competition_id, name=entry["name"])
    text = platform.call_text(tool, args)
    summary = persist_download({"filename": entry.get("name")}, text, root)
    if not summary or summary == text:  # 没能识别成附件内容
        return {**entry, "ok": False, "error": "附件工具未返回可落盘内容"}
    # 摘要行形如「  路径：vaultsvc.bin（相对本题沙盒根，可直接给 …）」——中文括号要排除，
    # 否则 \S+ 会把「（相对…」一起吞进文件名。
    m = re.search(r"路径：([^（\s]+)", summary)
    name = m.group(1) if m else (entry.get("name") or "")
    target = root / name
    record = {**entry, "ok": True, "name": name, "path": name}
    try:
        record["bytes"] = target.stat().st_size
        record["sha256"] = hashlib.sha256(target.read_bytes()).hexdigest()
    except OSError:
        pass
    return record


def _challenge_detail_candidates(platform, competition_id: str) -> list[str]:
    """可能的「题目详情」工具，按优先级排列。

    [接手 2026-11-10 实测] 不同平台/模式下详情工具不是同一个：
      * 正式比赛：`get_challenge`（DASCTF 里是 `ctf2_get_competition_challenge`）；
      * 练习场：`ctf2_get_practice_challenge` —— 用比赛版去查练习场题，回执里没有 files[]，
        于是「题目没有附件」的假结论（unipickle.py 就这么被漏掉的）。
    `resolve("get_challenge")` 只会给出档案里映射的那一个，所以这里再补上同族兄弟，
    只在**前一个没取到附件**时才继续试（最多 3 个），不给正常路径加任何额外调用。
    """
    names: list[str] = []
    try:
        resolved = platform.resolve("get_challenge")
    except Exception:  # noqa: BLE001 - 映射缺失不算失败
        resolved = ""
    if resolved:
        names.append(str(resolved))
    try:
        for name in platform.tool_names():
            low = str(name).lower()
            if name in names:
                continue
            if "attachment" in low or "list" in low or "next" in low or "start" in low:
                continue
            if "get" not in low or "challenge" not in low:
                continue
            names.append(str(name))
    except Exception:  # noqa: BLE001
        pass
    # 练习场优先：带 competition_id 时，练习场工具往往是真正有 files[] 的那个
    if competition_id:
        practice = [n for n in names if "practice" in n.lower()]
        names = practice + [n for n in names if n not in practice]
    return names[:3]


def _read_challenge_detail(platform, challenge_id: str, competition_id: str) -> tuple[dict, str]:
    """读题目详情，返回 (详情 dict, 实际用的工具名)。取不到时返回 ({}, "")。"""
    last_error = ""
    for tool in _challenge_detail_candidates(platform, competition_id):
        args: dict = {"challenge_id": challenge_id}
        if competition_id:
            args["competition_id"] = competition_id
            args["practice_ground_id"] = competition_id  # 桥接/直连两种口径都覆盖
        try:
            raw = platform.call_raw(tool, args)
        except Exception as exc:  # noqa: BLE001 - 换下一个工具
            last_error = f"{tool}: {exc}"
            continue
        info = _as_json(platform, raw) or {}
        if isinstance(info.get("challenge"), dict):  # 未拆包装的兜底
            info = info["challenge"]
        if _file_entries(info):
            return info, tool
    return {}, last_error


def fetch_challenge_files(platform, store, challenge_id: str, *, competition_id: str = "",
                          timeout: float = _DEFAULT_TIMEOUT, opener=None,
                          on_event=None) -> list[dict]:
    """题目附件全部落进本题沙盒，返回清单（含失败项，供上层提示）。

    调用一次题目详情拿 files[]，再逐个下载。任何异常都在内部消化成
    {"ok": False, "error": ...}，不抛出——附件是加分项，不能拦解题。
    """
    root = sandbox_root(store, challenge_id)
    records: list[dict] = []
    try:
        info, used_tool = _read_challenge_detail(platform, challenge_id, competition_id)
        entries = _file_entries(info) if info else []
        if entries and on_event:
            on_event("attachments", {"challenge_id": challenge_id,
                                     "note": f"从 {used_tool} 读到 {len(entries)} 个附件"})
    except Exception as exc:  # noqa: BLE001 - 取不到附件清单不算失败
        if on_event:
            on_event("attachments_error", {"challenge_id": challenge_id, "error": str(exc)})
        return []
    if not entries:
        return []

    url_tool, content_tool = _pick_attachment_tools(platform)
    if not url_tool and not content_tool:
        if on_event:
            on_event("attachments_error", {"challenge_id": challenge_id,
                                           "error": "平台上没有任何附件下载工具"})
        return [{**e, "ok": False, "error": "无附件下载工具"} for e in entries]

    root.mkdir(parents=True, exist_ok=True)
    for entry in entries:
        try:
            if entry.get("file_id") and url_tool:
                rec = _fetch_one_url_mode(platform, url_tool, entry, challenge_id=challenge_id,
                                          competition_id=competition_id, root=root,
                                          timeout=timeout, opener=opener)
            elif content_tool:
                rec = _fetch_one_content_mode(platform, content_tool, entry,
                                              challenge_id=challenge_id,
                                              competition_id=competition_id, root=root)
            else:
                rec = {**entry, "ok": False, "error": "该附件没有 file_id，且平台无内容式下载工具"}
        except Exception as exc:  # noqa: BLE001 - 单个附件失败不放弃其它
            rec = {**entry, "ok": False, "error": f"{type(exc).__name__}: {exc}"}
        records.append(rec)
        if on_event:
            on_event("attachment", {"challenge_id": challenge_id, **rec})

    try:
        (root / MANIFEST_NAME).write_text(
            json.dumps([{k: v for k, v in r.items() if k != "url_origin"} for r in records],
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError:
        pass
    return records


def manifest_note(records: list[dict]) -> str:
    """给模型看的短清单（塞进 store 笔记，会进协调者 brief）。"""
    ok = [r for r in records if r.get("ok")]
    bad = [r for r in records if not r.get("ok")]
    if not ok and not bad:
        return ""
    lines = ["[附件已落盘] 题目附件已在本题沙盒根目录，直接用文件名读，不要再去外网找："]
    for r in ok:
        extra = "（已存在，跳过下载）" if r.get("skipped") else ""
        lines.append(f"- {r.get('name')}（{r.get('bytes', '?')} 字节，sha256 {str(r.get('sha256') or '')[:16]}）{extra}")
    if bad:
        lines.append("下载失败的附件（可忽略，别卡在这里）：")
        for r in bad:
            lines.append(f"- {r.get('name') or r.get('file_id') or '?'}：{r.get('error')}")
    lines.append("清单文件：%s" % MANIFEST_NAME)
    return "\n".join(lines)


def hydrate(platform, store, challenge_id: str, *, competition_id: str = "",
            on_event=None) -> str:
    """一站式入口：下载附件 + 写 store 笔记载入 brief，返回清单文本（无附件则空串）。"""
    records = fetch_challenge_files(platform, store, challenge_id,
                                    competition_id=competition_id, on_event=on_event)
    note = manifest_note(records)
    if note:
        try:
            store.add_note(challenge_id, note)
            store.save()
        except Exception:  # noqa: BLE001 - 笔记写失败不影响文件已落盘
            pass
    return note


def main(argv=None) -> int:
    """手工验证入口（只读平台，不跑题）：
    PYTHONPATH=src python -m ctfy_agent.attachments --challenge-id <id> \\
        --run-id plat --competition-id <ground id>
    """
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="把题目附件下载进本题沙盒（只读平台）")
    parser.add_argument("--challenge-id", required=True)
    parser.add_argument("--run-id", default="attachments")
    parser.add_argument("--competition-id", default="")
    parser.add_argument("--profile", default="")
    args = parser.parse_args(argv)

    from .cli import make_platform, open_store  # noqa: PLC0415
    from .config import load  # noqa: PLC0415

    config = load()
    platform = make_platform(config, verbose=True)
    try:
        from .gui.platform_adapter import install  # noqa: PLC0415
        from .gui.profiles import apply as apply_profile, load as load_profiles  # noqa: PLC0415

        data = load_profiles()
        name = args.profile or data.get("active") or ""
        if name and name in (data.get("profiles") or {}):
            apply_profile(config, data["profiles"][name])
            platform = install(make_platform(config, verbose=True))
            bridge = getattr(platform, "_arg_bridge", None)
            if bridge is not None and args.competition_id:
                bridge.seed(competition_id=args.competition_id)
    except Exception:  # noqa: BLE001 - 没有档案也能跑（单平台场景）
        pass
    platform.connect()
    store = open_store(config, args.run_id)
    try:
        note = hydrate(platform, store, args.challenge_id, competition_id=args.competition_id)
    finally:
        # 只读探测也该关会话（否则每次手工验证都在平台侧留一条会话）
        client = getattr(platform, "client", None)
        if client is not None and hasattr(client, "close"):
            try:
                client.close()
            except Exception:  # noqa: BLE001 - 关闭失败不影响结果
                pass
    print(note or "本题没有附件")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())