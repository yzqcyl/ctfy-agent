# -*- coding: utf-8 -*-
"""一步接入新平台：连上去读 tools/list，自动生成映射 + 实测 + 出体检报告。

为什么这才是靠谱方法
------------------
一个平台的全部契约就写在 MCP 的 tools/list 里：每个工具的名字、参数、必填项、
常量项（const）、是否允许多余字段（additionalProperties）。
所以接新平台不需要截图 OCR、不需要手填工具名 —— 读一遍契约就能自动接线：

  1) 工具名 → 语义动作：按权重打分自动映射（DASCTF / CTFY 实测全对）
  2) 参数名差异：platform_adapter 在运行时按目标工具 schema 自动改名 / 补 const / 丢多余
  3) 返回包装差异：自动拆 {"challenge": {...}} 这类单键壳
  4) 数据来源差异：whoami 没比赛就回退 list_competitions
  5) 分页 / limit 差异：按 schema 的 maximum 与 page / offset 自动适配

本模块只负责「接入时的一次性探测」：生成映射、跑几步只读调用验证、
并逐个动作告诉你「能跑 / 缺什么参数」，把不可用的部分提前暴露，而不是等解题时才发现。
"""
from __future__ import annotations

from . import toolmap
from .platform_worker import _items_of

# 我们现有流程会给某个动作提供的参数（通用叫法）
_SUPPLIED: dict[str, set[str]] = {
    "whoami": set(),
    "list_competitions": set(),
    "get_competition": {"competition_id"},
    "list_challenges": {"competition_id"},
    "get_challenge": {"challenge_id", "competition_id"},
    "start_instance": {"challenge_id", "competition_id"},
    "get_instance": {"instance_id", "challenge_id", "competition_id"},
    "stop_instance": {"instance_id", "challenge_id", "competition_id"},
    "renew_instance": {"instance_id", "challenge_id", "competition_id"},
    "submit_flag": {"challenge_id", "flag", "competition_id", "instance_id", "question_id"},
    "leaderboard": {"competition_id"},
    "my_solves": set(),
}

# 通用参数名 → 平台可能的等价叫法（桥接层也是按这个思路改名的）
_EQUIV: dict[str, set[str]] = {
    "competition_id": {"competition_id", "practice_ground_id", "ground_id", "contest_id"},
    "challenge_id": {"challenge_id", "problem_id", "task_id"},
    "instance_id": {"instance_id", "environment_id", "env_id"},
    "flag": {"flag", "answer"},
}


def _short(exc, limit: int = 120) -> str:
    return str(exc).replace("\n", " ")[:limit]


def _apply_map(platform, mapping: dict) -> None:
    """临时套用一套映射并清掉解析缓存。"""
    platform.tool_map = dict(mapping or {})
    platform._resolved.clear()


def capability(platform, kind: str, tool: str) -> list[str]:
    """判断某动作在当前自动流程下能否跑通：返回「我们给不了」的必填参数。"""
    schema = platform.schema_of(tool) or {}
    props = schema.get("properties") or {}
    required = list(schema.get("required") or [])
    supplied: set[str] = set()
    for name in _SUPPLIED.get(kind, set()):
        supplied |= _EQUIV.get(name, {name})
    missing: list[str] = []
    for field in required:
        if "const" in (props.get(field) or {}):
            continue  # 桥接层会自动填 const
        if field not in supplied:
            missing.append(field)
    return missing


def probe(config, log=None) -> dict:
    """连平台读契约 → 自动生成映射 → 只读实测 → 返回体检报告。

    [接手 2026-11-10] 探测完必须关掉 MCP 会话：一键接入每次都会 initialize 一条新会话，
    没有关闭出口就会在平台侧越堆越多（P2 待办）。关闭失败不影响报告。
    """
    platform = _platform_for(config)
    try:
        return _probe_with(platform, log)
    finally:
        _close_quietly(platform)


def _close_quietly(platform) -> None:
    """尽力关闭底层 MCP 会话（客户端没有 close 时静默跳过）。"""
    for holder in (getattr(platform, "client", None), platform):
        if holder is None:
            continue
        close = getattr(holder, "close", None)
        if callable(close):
            try:
                close()
                return
            except Exception:  # noqa: BLE001 - 关闭失败绝不能影响调用方
                continue


def _probe_with(platform, log=None) -> dict:
    report: dict = {
        "ok": False,
        "tools": [],
        "mode": "",
        "mapping": {},
        "competitions": 0,
        "challenges": 0,
        "capabilities": [],
        "steps": [],
        "warnings": [],
        "sample": "",
    }

    def step(text: str) -> None:
        report["steps"].append(text)
        if log is not None:
            try:
                log(text)
            except Exception:  # noqa: BLE001 - 日志失败不能影响探测
                pass

    platform.connect()
    names = [str(n) for n in platform.tool_names()]
    report["tools"] = names
    step(f"连接成功，平台暴露 {len(names)} 个工具")
    if not names:
        report["warnings"].append("平台没有暴露任何工具，无法自动识别")
        return report

    vocab = set(names)
    original = dict(platform.tool_map)

    # 1) 两种模式各生成一套映射，用「能取到多少比赛/练习场」实测择优
    best: tuple[str, int, dict] | None = None
    for mode in ("competition", "practice"):
        mapping = toolmap.build_mapping(names, mode=mode)
        count = -1
        note = ""
        try:
            _apply_map(platform, mapping)
            count = len(_items_of(platform.list_competitions()))
        except Exception as exc:  # noqa: BLE001 - 某模式不可用是正常情况
            note = "（取列表失败：" + _short(exc, 60) + "）"
        finally:
            platform.tool_map = dict(original)
            platform._resolved.clear()
        label = "正式比赛" if mode == "competition" else "练习场"
        step(f"{label}模式：自动映射 {len(mapping)} 条 → 比赛/练习场 {max(count, 0)} 个{note}")
        if best is None or count > best[1]:
            best = (mode, count, mapping)

    assert best is not None
    mode, count, mapping = best
    report["mode"] = mode
    report["mapping"] = mapping
    report["competitions"] = max(count, 0)
    invalid = [v for v in mapping.values() if v not in vocab]
    if invalid:  # 理论上不可能：映射就是从真实清单里推的
        report["warnings"].append("映射里出现平台不存在工具名：" + ", ".join(invalid))
    step(f"采用{'正式比赛' if mode == 'competition' else '练习场'}模式映射（{len(mapping)} 条）")
    _apply_map(platform, mapping)

    # 2) 只读实测：比赛列表 → 题单 → 题目详情
    try:
        comps = _items_of(platform.list_competitions())
        report["competitions"] = len(comps) or report["competitions"]
        if comps:
            # 前几个比赛可能没报名/没开题，多试几个再下结论
            hit = False
            for first in comps[:5]:
                gid = str(first.get("id") or first.get("competition_id")
                          or first.get("ground_id") or "")
                gname = str(first.get("name") or first.get("title") or gid[:8])
                if not gid:
                    continue
                bridge = getattr(platform, "_arg_bridge", None)
                if bridge is not None:
                    bridge.seed(competition_id=gid)
                chs = _items_of(platform.list_challenges(gid))
                if not chs:
                    step(f"「{gname}」没有可见题目，换下一个…")
                    continue
                report["challenges"] = len(chs)
                step(f"「{gname}」读到 {len(chs)} 道题")
                hit = True
                cid = str(chs[0].get("id") or chs[0].get("challenge_id") or "")
                if cid:
                    detail = platform.get_challenge(cid, competition_id=gid)
                    if isinstance(detail, dict) and detail.get("name"):
                        report["sample"] = str(detail.get("name"))
                        step(f"题目详情可读：{detail.get('name')}（{detail.get('category') or '-'}）")
                    else:
                        report["warnings"].append("题目详情取不到 name，可能需要额外的返回结构适配")
                break
            if not hit:
                report["warnings"].append(
                    "试了前几个比赛/练习场都没有可见题目：可能该账号未报名、题目未开放，或需要先加入比赛")
        else:
            report["warnings"].append("比赛/练习场列表为空：检查 token 是否有权限，或该账号未加入任何比赛")
    except Exception as exc:  # noqa: BLE001
        report["warnings"].append("题单探测失败：" + _short(exc))

    # 3) 动作能力体检
    for kind in toolmap.ACTION_KINDS:
        tool = mapping.get(kind) or ""
        if not tool:
            report["capabilities"].append(
                {"kind": kind, "tool": "", "status": "missing", "missing": []})
            continue
        missing = capability(platform, kind, tool)
        report["capabilities"].append({
            "kind": kind, "tool": tool,
            "status": "ok" if not missing else "need_params",
            "missing": missing,
        })
    blocked = [c for c in report["capabilities"] if c["status"] == "need_params"]
    if blocked:
        report["warnings"].append(
            "以下动作平台要求额外参数、自动流程给不了（这些功能用不了）："
            + "、".join(f"{c['kind']}({'/'.join(c['missing'])})" for c in blocked))

    report["ok"] = report["competitions"] > 0 and report["challenges"] > 0
    step("探测完成：" + ("平台可用" if report["ok"] else "部分功能不可用，见下方提示"))
    return report


def _platform_for(config):
    """复用 GUI 的建平台入口（自带参数桥接）。"""
    from .platform_worker import _platform

    return _platform(config)