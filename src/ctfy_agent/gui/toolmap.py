# -*- coding: utf-8 -*-
"""工具清单 → 语义动作映射（自动生成平台档案的 tool_map）。

三种来源：
  1. read_platform_tools(config) —— 连上 MCP 平台读真实 tools/list（最准，推荐）
  2. parse_names(text)           —— 从粘贴文本里提取工具名
  3. ocr_image(path)             —— Windows 自带 OCR 识别截图（有噪声，需人工校对）

产出 {语义动作: 平台真实工具名}，交给 platform.resolve() 使用：
tool_map 优先 > 默认工具名 > 按词自动归类。
纯增量模块，不改动 platform.py / cli.py。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# 需要映射的语义动作（与 platform.DEFAULT_TOOLS 的键对齐）
ACTION_KINDS = (
    "whoami",
    "list_competitions",
    "get_competition",
    "list_challenges",
    "get_challenge",
    "start_instance",
    "get_instance",
    "stop_instance",
    "renew_instance",
    "submit_flag",
    "leaderboard",
    "my_solves",
)

# 每个动作：(正向词权重, 负向词权重)。分数 >= _THRESHOLD 才认为可信。
_SPEC: dict[str, tuple[dict[str, int], dict[str, int]]] = {
    "whoami": (
        {"whoami": 6, "me": 2, "profile": 2, "self": 2, "agent": 1},
        {"list": 3, "competition": 3, "challenge": 2, "flag": 3, "environment": 3, "note": 3, "next": 3},
    ),
    "list_competitions": (
        {"competition": 3, "contest": 3, "ground": 3, "list": 3},
        {"challenge": 3, "scoreboard": 3, "join": 3, "flag": 3, "environment": 2, "note": 3, "get": 2},
    ),
    "get_competition": (
        {"competition": 3, "detail": 3, "get": 2, "info": 2},
        {"list": 3, "challenge": 3, "scoreboard": 3, "join": 3, "flag": 3, "environment": 3},
    ),
    "list_challenges": (
        {"challenge": 4, "problem": 4, "list": 3, "task": 3},
        {"environment": 4, "submit": 4, "start": 4, "attachment": 3, "scoreboard": 3, "get": 3},
    ),
    "get_challenge": (
        {"challenge": 4, "problem": 4, "detail": 3, "get": 2, "info": 1},
        {"list": 4, "environment": 4, "submit": 4, "start": 4, "attachment": 3},
    ),
    "start_instance": (
        {"start": 4, "launch": 4, "create": 3, "environment": 3, "instance": 2},
        {"delete": 5, "stop": 5, "extend": 4, "renew": 4, "submit": 4},
    ),
    "get_instance": (
        {"environment": 4, "instance": 3, "status": 2, "get": 1},
        {"start": 5, "delete": 5, "stop": 5, "extend": 5, "renew": 5, "submit": 4, "flag": 3},
    ),
    "stop_instance": (
        {"delete": 4, "stop": 4, "destroy": 4, "environment": 2, "instance": 2},
        {"start": 5, "extend": 4, "renew": 4},
    ),
    "renew_instance": (
        {"extend": 5, "renew": 5, "environment": 2, "instance": 2},
        {"start": 4, "delete": 5, "stop": 4},
    ),
    "submit_flag": (
        {"submit": 5, "flag": 4, "answer": 3},
        # AWD / 批量提交是另一套语义（要 flags 数组），绝不能顶替普通提交
        {"list": 3, "get": 2, "environment": 3, "awd": 6, "batch": 3},
    ),
    "leaderboard": (
        {"scoreboard": 5, "leaderboard": 5, "rank": 4},
        {"challenge": 2, "flag": 3},
    ),
    "my_solves": (
        {"solve": 4, "solved": 4, "my": 2},
        {"competition": 3, "environment": 3, "list": 2},
    ),
}

_THRESHOLD = 4

# OCR 常见替换：破折号/项目符号/全角符号 → 下划线
_OCR_FIX = {
    "\u2013": "_", "\u2014": "_", "\u2212": "_", "\u00b7": "_", "\u2022": "_",
    "\uff3f": "_", "\uff0d": "_", "\u3000": " ", "\uff01": "!", "\uff0e": ".",
}


def _sing(t: str) -> str:
    """极简词干归一：去复数 s，避开 status/analysis 这类。"""
    if len(t) > 3 and t.endswith("s") and not (t.endswith("ss") or t.endswith("us") or t.endswith("is")):
        return t[:-1]
    return t


def _tokens(name: str) -> set[str]:
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", str(name))
    return {_sing(p) for p in re.split(r"[^A-Za-z0-9]+", s.lower()) if p}


def build_mapping(names: list[str], mode: str = "competition") -> dict[str, str]:
    """从真实工具名列表推出 {动作: 工具名}。mode: competition / practice。"""
    cand = [(str(n).strip(), _tokens(n)) for n in names if str(n).strip()]
    if not cand:
        return {}
    if mode == "practice":
        bias_pos, bias_neg = {"practice": 2, "ground": 2}, {"competition": 3}
    else:
        bias_pos, bias_neg = {"competition": 2}, {"practice": 3, "ground": 3}

    scored: dict[str, list[tuple[int, str]]] = {}
    for kind in ACTION_KINDS:
        pos, neg = _SPEC[kind]
        rows = []
        for name, toks in cand:
            score = 0
            for t, w in pos.items():
                if t in toks:
                    score += w
            for t, w in bias_pos.items():
                if t in toks:
                    score += w
            for t, w in neg.items():
                if t in toks:
                    score -= w
            for t, w in bias_neg.items():
                if t in toks:
                    score -= w
            # 特殊：get_me 这类极短身份工具（CTFY 的 whoami 就叫 get_me）
            if kind == "whoami" and "me" in toks and not ({"solve", "list", "note", "next"} & toks):
                score += 4
            rows.append((score, name))
        rows.sort(key=lambda x: (-x[0], len(x[1]), x[1]))
        scored[kind] = rows

    # 贪心：按「最高可信分」从大到小分配，一个工具名只给一个动作
    order = sorted(ACTION_KINDS, key=lambda k: -(scored[k][0][0] if scored[k] else 0))
    out: dict[str, str] = {}
    used: set[str] = set()
    for kind in order:
        for score, name in scored[kind]:
            if score >= _THRESHOLD and name not in used:
                out[kind] = name
                used.add(name)
                break
    return out


def normalize_text(text: str) -> str:
    """把 OCR/粘贴文本里的非 ASCII 噪声压成 ASCII 友好形态。"""
    s = str(text or "")
    for src, dst in _OCR_FIX.items():
        s = s.replace(src, dst)
    # 只保留 ASCII 可打印（工具名都是 ASCII）
    s = "".join(ch if 32 <= ord(ch) < 127 else " " for ch in s)
    return s


_IDENT = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+")
_LONG_IDENT = re.compile(r"\b[A-Za-z][A-Za-z0-9]{5,}\b")
_CAMEL = re.compile(r"\b[a-z]+[A-Z][A-Za-z0-9]+\b")


def parse_names(text: str) -> list[str]:
    """从任意文本里抽出像工具名的标识符，去重保序。"""
    raw = normalize_text(text)
    found: list[str] = []

    def add(value: str) -> None:
        v = str(value).strip().strip(",.;:()[]{}<>\"'")
        if len(v) < 3:
            return
        if v not in found:
            found.append(v)

    # 1) JSON 里的 name / tool 字段（MCP tools/list 原文）
    for m in re.finditer(r'"(?:name|tool)"\s*:\s*"([^"]{2,120})"', raw):
        add(m.group(1))
    # 2) 带下划线的标识符（最常见）
    for m in _IDENT.finditer(raw):
        add(m.group(0))
    # 3) camelCase 长标识
    for m in _CAMEL.finditer(raw):
        add(m.group(0))
    # 4) 兜底：很长的单段标识（无下划线）
    for m in _LONG_IDENT.finditer(raw):
        if "_" not in m.group(0):
            add(m.group(0))
    return found


# ---------------- 平台直读（最准） ----------------

def read_platform_tools(config) -> list[str]:
    """连上平台读真实 tools/list。失败抛异常由调用方显示。"""
    from ..cli import make_platform  # noqa: PLC0415

    platform = make_platform(config)
    platform.connect()
    return [str(n) for n in platform.tool_names()]


# ---------------- 图片 OCR（Windows 自带引擎） ----------------

_OCR_SCRIPT = Path(__file__).with_name("ocr_tools.ps1")


def _new_workdir() -> Path:
    """建工作目录：优先工作区 runs/_gui_tmp（GUI 的 cwd 就是项目根，一定可写），
    cwd 不可写时再回落到系统临时目录。"""
    try:
        d = Path.cwd() / "runs" / "_gui_tmp" / f"ocr-{os.getpid()}-{time.time_ns()}"
        d.mkdir(parents=True, exist_ok=True)
        return d
    except Exception:  # noqa: BLE001
        return Path(tempfile.mkdtemp(prefix="ctf-ocr-"))


def ocr_image(path: str, scale: int = 3) -> str:
    """用 Windows 自带 OCR 识别截图，返回识别文本（有噪声，需人工校对）。

    放大后再识别可明显提升准确率；识别结果按词间距合并，减少下划线被误读成空格的损失。
    """
    src = Path(path)
    if not src.is_file():
        raise FileNotFoundError(str(src))
    if not _OCR_SCRIPT.is_file():
        raise RuntimeError(f"缺少 OCR 脚本：{_OCR_SCRIPT}")
    if not sys.platform.startswith("win"):
        raise RuntimeError("图片识别仅支持 Windows")

    work = _new_workdir()
    try:
        img = src
        if scale and scale > 1:
            try:
                from PIL import Image  # noqa: PLC0415

                im = Image.open(src).convert("RGB")
                im = im.resize((im.width * scale, im.height * scale), Image.LANCZOS)
                img = work / "scaled.png"
                im.save(img)
            except Exception:  # noqa: BLE001 - 无 Pillow 就直接原图识别
                img = src
        out = work / "out.txt"
        err = work / "err.txt"
        sout = work / "stdout.txt"
        serr = work / "stderr.txt"
        cmd = [
            "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", str(_OCR_SCRIPT.resolve()),
            "-ImagePath", str(Path(img).resolve()),
            "-OutPath", str(out.resolve()), "-ErrPath", str(err.resolve()),
            "-Gap", "18",
        ]
        with open(sout, "w", encoding="utf-8") as fo, open(serr, "w", encoding="utf-8") as fe:
            subprocess.run(cmd, stdout=fo, stderr=fe, timeout=120, check=False)
        if err.is_file():
            message = err.read_text(encoding="utf-8", errors="replace").strip()
            if message:
                raise RuntimeError("OCR 失败：" + message[:300])
        if not out.is_file():
            detail = ""
            for extra in (serr, sout):
                if extra.is_file():
                    detail = extra.read_text(encoding="utf-8", errors="replace").strip()[:300]
                    if detail:
                        break
            raise RuntimeError("OCR 没有产出结果（可能是系统 OCR 组件不可用）" + (("：" + detail) if detail else ""))
        return out.read_text(encoding="utf-8", errors="replace")
    finally:
        shutil.rmtree(work, ignore_errors=True)


def mapping_json(mapping: dict[str, str]) -> str:
    return json.dumps(mapping, ensure_ascii=False, indent=2)


def repair_mapping(mapping: dict, names: list[str], mode: str = "competition"):
    """用平台真实工具名清单校验映射：有效的保留，无效的按语义重新推断。

    这是防「OCR 碎片被当成工具名」的关键一步 —— 碎片在真实清单里找不到，
    就会按该动作的语义重新匹配到真正存在的工具。

    返回 (新映射, 仍无法匹配的动作列表)。
    无法匹配的动作**不写进结果** —— 留着无效名会让 platform.resolve() 直接抛错，
    丢掉则回退到平台的自动归类，最多取不到，不会把整条链路弄崩。
    """
    vocab = {str(n) for n in names if str(n).strip()}
    fresh = build_mapping(list(vocab), mode=mode)
    fixed: dict[str, str] = {}
    unresolved: list[str] = []
    for kind, tool in (mapping or {}).items():
        tool = str(tool or "")
        if tool in vocab:
            fixed[kind] = tool
        elif kind in fresh:
            fixed[kind] = fresh[kind]
        else:
            unresolved.append(kind)
    return fixed, unresolved