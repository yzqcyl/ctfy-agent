#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""容器适配层：`$CTFY_WORK_DIR/task.json` → Config → Crew → `result.json`（纯 stdlib）。

实现 CTFY-0001 v2 的用户镜像契约，最小合规集只有三条，本文件把它们当成不变量：
  1. `/ctfy/work/result.json` 必须存在（任何路径都写，包括被 kill / 超时 / 顶层异常）；
  2. 必须 `exit 0`（非零退出码 = 判 failed，见规范 §3「A non-zero exit is recorded as a
     failed run regardless of what you wrote」）；
  3. 日志一律走 stderr，stdout 最多留一行最终 rollup JSON（用户镜像的 stdout 本就不被读，
     只当本地调试用）。

关键决策（与草稿 b1-adapter-plan 的差别见 docs/b1-adapter-plan.md 附注）：
  - 容器内**永不自己 stop_instance**：平台注入 `CTFY_EVAL_KEEP_INSTANCE=1`，dispatcher 回收。
  - `solved` 是**题目 id 列表**，`answers` 才是平台代交通道；两者都只写已校验的答案。
  - flavor 未知时 fail loudly：非 `openai` 一律 `refused`，不烧钱。
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import signal
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

from .config import Config, load
from .crew import Crew
from .gledger import build_global_ledger
from .llm import DeepSeekLLM
from .platform import CtfyPlatform
from .state import Store

SCHEMA_VERSION = 3  # v3（2026-11-05）：平台预启实例、token 单题作用域；见 runs/site/standards-live/CTFY-0001.md
DEFAULT_WORK_DIR = "/ctfy/work"
DEFAULT_MAX_TURNS = 160         # task.json.max_turns 为 null（无预算）时的保守上限（80→160：配合协调者 24 轮）
DEFAULT_MAX_TOKENS = 8192       # 4096 会在 thinking 块截断、一步即「无动作结束」
DEFAULT_MAX_CONTEXT = 32000  # 对齐 agent.json 与各角色提示词的「上下文 32k」承诺（原 16000 导致 16k 就丢旧步骤，模型按 32k 规划却提前失忆）
DEFAULT_MAX_TOKENS_PER_CHALLENGE = 800000
ATTACH_COPY_LIMIT_MIB = 4  # v3：平台单次把附件预放进 /ctfy/work/attachments/ 的上限；超过则 path=null
# [v7.2 G9] task.json 缺失时的短轮询等待（秒）。平台托管形态（v3 管辖，11-14/15 主流）下
# task.json 在容器启动前就位，缺失通常是环境错误；但万一 11-14/15 实际是「常驻形态、题稍后才放」，
# 短暂轮询能兜住「题还没到」的窗口，避免立即 halted 弃权。默认 30s（覆盖放题延迟，又不至于在
# 环境错误时长时间空烧容器生命周期），env AGENT_TASK_WAIT_TIMEOUT 可调；0 = 不等待（恢复旧行为）。
TASK_WAIT_TIMEOUT_DEFAULT = 30.0
TASK_POLL_INTERVAL = 5.0

# 协议（CTFY-0001）定义的 5 种 run_mode 里，我们只真正实现了 instance / qa。
# 下面三个语义完全不同，照 instance 跑只会白烧预算，仍然直接拒绝：
#   defence     —— AWD+ 守靶场，交付物是补丁（POST /patches）；
#   awd         —— 按轮攻防，进程退出即比赛结束；
#   engagement  —— 企业靶场（GAUNTLET），交付物是报告（PUT .../report）。
# 不在这张表里的（含空值、平台将来新增的）一律降级按 instance 跑，靠平台下发的
# max_turns 与本地预算兜底——宁可跑不准，也不要整题弃权。
UNSUPPORTED_RUN_MODES = ("defence", "defense", "awd", "engagement")

# [规则 v2.5 §二(五)] 入围/初选可用的 12 家白名单端点 + 正式赛智谱独家端点。
# 只用于**告警**（rollup + stderr），不用于拦截：本地联调（llm.example / Paratera 等）
# 不在名单内属于正常，但正式赛若平台没注入 LLM_* 而回落到本地配置，这条告警就是
# 「整场会挂在 LLM 调用上」的第一个可见信号（正式赛无公网，非白名单域名直接不可达）。
LLM_ENDPOINT_ALLOWLIST = (
    "api.hunyuan.cloud.tencent.com",   # 腾讯混元
    "api.deepseek.com",                # DeepSeek 官方
    "dashscope.aliyuncs.com",          # 阿里云百炼（compatible-mode）
    "qianfan.baidubce.com",            # 百度千帆
    "open.bigmodel.cn",                # 智谱（正式赛唯一）
    "ark.cn-beijing.volces.com",       # 字节火山方舟（Coding Plan 地址除外）
    "api.moonshot.cn",                 # Kimi
    "spark-api-open.xf-yun.com",       # 讯飞星火
    "api.minimax.cn",                  # MiniMax
    "api.stepfun.com",                 # 阶跃星辰
    "api.baichuan-ai.com",             # 百川
    "api.xiaomimimo.com",              # 小米 MiMo
)


def _llm_endpoint_note(base_url: str) -> str:
    """LLM 端点不在规则白名单内时返回告警文本（在名单内返回空串）。"""
    try:
        host = (urlparse(str(base_url or "")).hostname or "").lower()
    except ValueError:
        host = ""
    if not host:
        return "LLM 端点为空：无法核对规则白名单（检查 LLM_BASE_URL 注入）"
    for allow in LLM_ENDPOINT_ALLOWLIST:
        if host == allow or host.endswith("." + allow):
            return ""
    return (
        f"LLM 端点 {host} 不在赛事规则 v2.5 白名单内（12 家 + 正式赛智谱）。"
        "本地联调属正常；正式赛出现此告警 = 平台未注入 LLM_* 且回落到了本地配置，"
        "无公网环境下所有 LLM 调用都会失败——立即人工核查注入。"
    )

_RESULT_PATH: Path | None = None
_CHALLENGE_ID = ""


# ---------- 基础工具 ----------


def _log(message: str) -> None:
    print(message, file=sys.stderr)


def _load_json(path: Path) -> dict | None:
    """宽容读：缺文件 / 坏 JSON 都返回 None，调用方降级处理。"""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


_ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _expand_env(text: str, env) -> str:
    """展开 `${CTFY_TOKEN}` 这类占位——平台在 mcp.json 里写的是字面量，不是值。"""
    def repl(match):
        return env.get(match.group(1), "")

    return _ENV_RE.sub(repl, text)


def _as_int(value, default):
    if value in (None, ""):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value, default):
    if value in (None, ""):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _split_host_port(value: str) -> tuple[str, int | None]:
    """把平台端点的 `host:port` 或 `scheme://host:port/path` 解析成 (host, port)。

    平台的 `target_endpoints[].url` 两种形态都出现过（tcp 题给 `host:port`，web 题给 URL），
    这里统一处理；端口解析失败返回 None（不猜默认端口，免得登记出一堆假可访问端点）。
    """
    text = str(value or "").strip()
    if not text:
        return "", None
    if "://" not in text:
        text = "tcp://" + text
    try:
        parsed = urlparse(text)
    except ValueError:
        return "", None
    host = (parsed.hostname or "").strip()
    try:
        port = parsed.port
    except ValueError:
        port = None
    return host, port


# ---------- rollup ----------


def _rollup(*, challenge_id="", instance_id="", answers=None, solved=None,
            fully_solved=False, turns=0, tool_calls_made=0, input_tokens=0,
            output_tokens=0, stop_reason="done", writeup="",
            llm_endpoint_alert="") -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "challenge_id": challenge_id,
        "instance_id": instance_id,
        "answers": answers or {},
        "solved": solved or [],
        "fully_solved": fully_solved,
        "turns": turns,
        "tool_calls_made": tool_calls_made,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "stop_reason": stop_reason,
        "writeup": writeup,
        # 空串 = 端点在白名单内（或没走到构建 config）。非空 = 规则审计 #6 的告警，
        # 11-14/15 盯 result.json 时顺手看这个字段即可。
        "llm_endpoint_alert": llm_endpoint_alert,
    }


def _write_rollup(path: Path, rollup: dict, *, announce: bool = False) -> None:
    """原子写 result.json（tmp + os.replace → 一定是普通文件，O_NOFOLLOW 拒读不到符号链接）。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(rollup, ensure_ascii=False)
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
    if announce:
        print(text)  # stdout 一行最终回执（仅本地调试用）


_STOP_MAP = {
    "flag_accepted": "done",
    "coordinator_done": "done",
    "no_more_actions": "done",
    "step_limit": "max_turns",
    # [接手 2026-11-10] 重复被拦动作断路器收口：本质是「轮次被同一个无用动作烧光」，
    # 对平台口径与 step_limit 同类（max_turns）。不加这条会落到默认 "done" 被误报成解出。
    "action_loop": "max_turns",
    "budget_exhausted": "cut_off",
    "wall_clock": "cut_off",   # P0-3：时间到与预算耗尽对平台是同一类（都是"没做完"）
    "llm_error": "halted",
    "environment_error": "halted",  # [v6.4] 连续 N 次专家结论指向环境侧故障（实例/网关/节点不可达）
}


def _map_stop_reason(reason: str) -> str:
    return _STOP_MAP.get(reason, "done")


def _count_tool_calls(transcript_path) -> int:
    try:
        total = 0
        for line in Path(transcript_path).read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            total += len(rec.get("tool_calls") or [])
        return total
    except (OSError, ValueError, TypeError):
        return 0


# ---------- 信号：被 kill / 超时也有文件 ----------


def _on_signal(signum, frame):
    if _RESULT_PATH is not None:
        _write_rollup(
            _RESULT_PATH,
            _rollup(challenge_id=_CHALLENGE_ID, stop_reason="halted",
                    writeup=f"signal {signum}"),
        )
    os._exit(0)


def _register_signals() -> None:
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _on_signal)
        except (ValueError, OSError):
            pass  # 非主线程里注册会失败，忽略


# ---------- MCP 端点解析 ----------


def _resolve_mcp_url(work_dir: Path, platform_url: str, env) -> str | None:
    """MCP 端点两条路：mcp.json（平台写好的客户端配置）→ CTFY_PLATFORM_URL + /api/v1/mcp/。

    BYO 镜像拿不到 CTFY_MCP_URL；mcp.json 里的 url 带 ${VAR} 字面量，要自己展开。
    """
    mcp_json = work_dir / "mcp.json"
    if mcp_json.is_file():
        data = _load_json(mcp_json)
        servers = (data or {}).get("mcpServers") or {}
        for srv in servers.values():
            url = (srv or {}).get("url")
            if url:
                return _expand_env(str(url), env)
    if platform_url:
        return platform_url.rstrip("/") + "/api/v1/mcp/"
    return None


# ---------- Config 组装 ----------


def _build_config(task: dict | None, work_dir: Path, mcp_url: str | None, env) -> Config:
    # 从 config/agent.json 拿角色 profile（web allow_shell 等），再逐项覆盖容器关键字段
    config = load(use_env_file=False)

    max_turns = None
    if task:
        max_turns = _as_int(task.get("max_turns"), None)
    if max_turns is None:
        max_turns = _as_int(env.get("CTFY_EVAL_MAX_TURNS"), None)

    questions = (task or {}).get("questions") or []
    gated = _as_int((task or {}).get("gated_questions"), 0)

    config.mcp_url = mcp_url or config.mcp_url
    config.token = str(env.get("CTFY_TOKEN") or "")
    config.llm_api_key = str(env.get("LLM_API_KEY") or env.get("OPENAI_API_KEY") or "")
    config.llm_base_url = str(env.get("LLM_BASE_URL") or env.get("OPENAI_BASE_URL") or config.llm_base_url)
    config.model = str(env.get("LLM_MODEL") or env.get("OPENAI_MODEL") or config.model)
    config.max_tokens = _as_int(env.get("AGENT_MAX_TOKENS"), DEFAULT_MAX_TOKENS)
    # 调用数预算：task.max_turns（平台下发）> env AGENT_MAX_TURNS > agent.json > 160。
    # [fix 2026-11-03] 此前 agent.json 的 max_llm_calls 同样被忽略，与实际生效值不一致。
    _calls_cap = config.max_llm_calls or DEFAULT_MAX_TURNS
    # [接手 2026-11-11] 记录生效来源：交接文档里多次把「改了 agent.json 还是 160」当 bug，
    # 实际是平台下发的 task.max_turns 优先。来源只写进 config.extra（新键，不改变任何分支）。
    if max_turns:
        _calls_source = "平台下发 task.max_turns"
    elif env.get("AGENT_MAX_TURNS"):
        _calls_source = "环境变量 AGENT_MAX_TURNS"
    else:
        _calls_source = "config/agent.json max_llm_calls"
    config.extra["max_llm_calls_source"] = _calls_source
    config.max_llm_calls = max_turns or _as_int(env.get("AGENT_MAX_TURNS"), _calls_cap)
    config.max_context_tokens = _as_int(env.get("AGENT_MAX_CONTEXT_TOKENS"), DEFAULT_MAX_CONTEXT)
    # [fix 2026-11-03] 单题 token 上限统一：以 config/agent.json 为唯一权威值（800k），
    # env AGENT_MAX_TOKENS_PER_CHALLENGE 可覆盖；0 仍表示「不限」（Budget 池语义）。
    # 背景：此前 agent.json=1.2M / 本文件硬写 800k / config.DEFAULTS=300k 三处打架，
    # 实际生效的只有本文件的硬写值，配置文件写了等于没写。
    _tok_cap = config.max_tokens_per_challenge
    if _tok_cap is None:
        _tok_cap = DEFAULT_MAX_TOKENS_PER_CHALLENGE
    config.max_tokens_per_challenge = _as_int(env.get("AGENT_MAX_TOKENS_PER_CHALLENGE"), _tok_cap)
    config.submit_limit = max(3, (len(questions) + gated) * 2)
    config.runs_dir = str(work_dir / "runs")
    # 读超时：长上下文+长输出的单次生成实测能挂满 300s（120s 默认太紧）。容器侧抬到 240s；
    # 配合 llm._post 把 TimeoutError 归一成可重试 LLMError，单次超时不再击穿整题。
    config.timeout = _as_float(env.get("AGENT_TIMEOUT"), 240.0)
    # P1 修复：握手超时与 tools/call、LLM 分离。握手（initialize/tools/list）在容器连 ctfy.io
    # 时会 TLS/SSE 挂死，用 40s 短超时让「平台恢复后 1 分钟内重连」；connect_deadline 保留
    # 60–90 分钟总窗口，避免平台整机故障时快速失败丢掉 SAMPLE-001/002 这类熬过去的题。
    config.connect_timeout = _as_float(env.get("AGENT_CONNECT_TIMEOUT"), 40.0)
    config.connect_deadline = _as_float(env.get("AGENT_CONNECT_DEADLINE"), 5400.0)

    # 归一化角色 profile：网关会按 LLM_MODEL 改写 model（多模型名是误导），
    # 且 agent.json 里 web 的 max_tokens=4096 会截断思考块——统一到容器默认值。
    for spec in (config.extra.get("roles") or {}).values():
        if isinstance(spec, dict):
            spec.pop("model", None)
            spec["max_tokens"] = config.max_tokens

    # 容器内实例生命周期归平台：永不自己 stop_instance
    config.extra["keep_instance_on_success"] = True
    config.extra["keep_instance_on_fail"] = True

    # 平台「预下发实例」模式（2026-11-03 实测 SAMPLE-004）：task.json 直接给 target_instance /
    # target_endpoints，token 无权 start_instance。标记后全链路禁止自启，避免拿 403 白烧预算。
    task_data = task or {}
    config.extra["instance_preissued"] = bool(
        str(task_data.get("target_instance") or "").strip()
        or (task_data.get("target_endpoints") or [])
    )

    competition_id = (task or {}).get("competition_id") or env.get("CTFY_EVAL_COMPETITION_ID", "")
    if competition_id:
        config.extra["competition_id"] = str(competition_id)
    if questions:
        config.extra["default_question_id"] = str(questions[0].get("id") or "flag")
    # QA 模式（run_mode=qa）：纯问答/静态分析题，没有实例。禁用自启实例，否则
    # 白烧一轮 403（实测 SAMPLE-005）；提交由 solver 的 is_qa_only → submit_qa_answer 分支接管。
    if str((task or {}).get("run_mode") or "") == "qa":
        config.extra["qa_mode"] = True
        config.extra["auto_start_instance"] = False
    # 未知 run_mode 降级说明：写进 extra 便于赛后复盘「这题是按什么模式跑的」。
    _mode = str((task or {}).get("run_mode") or "")
    if _mode not in ("instance", "qa"):
        config.extra["degraded_run_mode"] = _mode or "(empty)"
    return config


# ---------- 题面入库 ----------


def _seed_challenge(store: Store, task: dict | None, env) -> str:
    """把 task.json 的题面写进 state。只信 task.json（权威），题型线索交给模型自己 get_challenge。"""
    task = task or {}
    challenge = task.get("challenge") or {}
    cid = str(challenge.get("id") or env.get("CTFY_EVAL_CHALLENGE_ID") or "challenge")

    desc = str(challenge.get("description") or "")
    blocks = []
    for q in task.get("questions") or []:
        qid = str(q.get("id") or "flag")
        prompt = str(q.get("prompt") or "")
        line = f"【问题 {qid}】{prompt}" if prompt else f"【问题 {qid}】"
        if qid != "flag":
            line += f"（submit 时 question_id 用 `{qid}`）"
        blocks.append(line)
    gated = _as_int(task.get("gated_questions"), 0)
    if gated and gated > 0:
        blocks.append(
            f"【门控题】本题还有 {gated} 道门控题未解锁：每答对一题后必须重新 "
            "get_challenge 拉取新题面，否则会漏掉后续分。"
        )
    if blocks:
        desc = (desc + "\n\n" if desc else "") + "\n".join(blocks)

    # ---------- 平台预下发的运行环境（P0，2026-11-03） ----------
    # task.json 直接给了已启动实例 / 可达端点 / 附件路径 / 访问范围；旧实现只写 id/name/description，
    # 于是容器内 agent 去调 start_instance（平台 token 不允许）→ 403 → 判定「没有实例」→ 收口。
    # 实测代价：SAMPLE-004 44 轮 / 649,031 token / $0.0715，一次都没连上真实服务。
    instance_id = str(task.get("target_instance") or "").strip()
    endpoints: list[dict] = []
    for raw in task.get("target_endpoints") or []:
        if not isinstance(raw, dict):
            continue
        host, port = _split_host_port(str(raw.get("url") or ""))
        if not host:
            continue
        endpoints.append(
            {
                "label": str(raw.get("label") or ""),
                "type": str(raw.get("type") or ""),
                "host": host,
                "port": port,
                "url": str(raw.get("url") or ""),
            }
        )
    attachments = []
    for a in (task.get("attachments") or []):
        if not isinstance(a, dict):
            continue
        name = str(a.get("name") or "")
        path = str(a.get("path") or "").strip()
        attachments.append({"name": name, "path": path, "size": a.get("size")})
    scope = [str(item) for item in (task.get("scope") or []) if str(item).strip()]
    platform_env = bool(instance_id or endpoints or attachments)
    if platform_env:
        lines = ["【平台已下发的运行环境（权威信息，直接使用，不要重复申请）】"]
        if instance_id:
            lines.append(f"- 实例已启动：id={instance_id}。禁止调用 start_instance（本 token 无权，会 403）。")
        for ep in endpoints:
            target = f"{ep['host']}:{ep['port']}" if ep["port"] else ep["host"]
            lines.append(f"- 可达端点（{ep['label'] or ep['type'] or 'target'}）：{target}（可直接连）")
        for att in attachments:
            if att["path"]:
                lines.append(f"- 附件已就位：{att['path']}（直接读本地文件，不要调下载接口）")
            else:
                # [v3 修复 A3] path:null 表示单次拷贝 >4MiB 未预放本地，须走平台通道下载；
                # 旧提示「不要调下载接口」会把模型引向一个不存在的本地文件，附件题直接 0 分。
                # 主通道 = MCP 工具 download_instance_attachment（MODEL_TOOLS 已放行）；
                # REST GET 会被 http_request 的靶场白名单拦（平台域名不在 target_endpoints）。
                lines.append(
                    f"- 附件 {att['name']} 未拷贝到本地（>{ATTACH_COPY_LIMIT_MIB}MiB，size={att['size']}B）："
                    f"用平台工具 list_instance_attachments 确认后以 download_instance_attachment 下载"
                    f"（参数用上面的实例 id 和附件名）；不要用 http_request 打平台 API（会被白名单拦）"
                )
        if scope:
            lines.append(f"- 允许访问范围：{', '.join(scope)}")
        desc = (desc + "\n\n" if desc else "") + "\n".join(lines)

    item = store.upsert(
        {
            "id": cid,
            "name": str(challenge.get("name") or ""),
            "description": desc,
            # QA 模式无实例：is_qa_only 让 _submit 走 submit_qa_answer，而不是 instance 分支
            # （否则会被「本题还没有实例」拦死）。tags 里通常没有 qa/mcq 字样，必须显式标记。
            "is_qa_only": str((task or {}).get("run_mode") or "") == "qa",
        }
    )
    if platform_env:
        instance = item.instance if isinstance(item.instance, dict) else {}
        if instance_id:
            instance["id"] = instance_id
        instance.setdefault("source", "platform")
        instance["endpoints"] = endpoints
        instance["attachments"] = attachments
        instance["scope"] = scope
        item.instance = instance
        if instance_id:
            item.instance_id = instance_id
    store.save()
    return cid


def _enrich_facets(store: Store, platform, cid: str) -> None:
    """best-effort 题型线索入库（category/tags/difficulty/solves_count），失败不拦路。

    只补「题型先验」四样，绝不碰 name/description——seed 里拼进去的【问题】与门控题提示
    不能被 get_challenge 的原文覆盖。A3 方法卡 / brief 题型行 / 初始提示都读这里的 tags。
    解题循环内 get_challenge 只回文本、不回写 store，所以这一刀必须在开跑前做。
    """
    item = store.get(cid)
    if item is not None and (item.tags or item.difficulty):
        return  # 已有题型线索，不重复取
    try:
        data = platform.get_challenge(cid)
    except Exception as exc:  # noqa: BLE001 - 线索缺失不该拦住解题
        _log(f"[warn] 题型线索补齐失败（继续解题）：{exc}")
        return
    if not isinstance(data, dict):
        return
    from .cli import normalize_challenge  # noqa: PLC0415 - 复用平台字段名归一

    norm = normalize_challenge(data)
    store.upsert(
        {
            "id": cid,
            "category": norm.get("category"),
            "tags": norm.get("tags"),
            "difficulty": norm.get("difficulty"),
            "solves_count": norm.get("solves_count"),
        }
    )
    store.save()


# ---------- 最终 rollup ----------


def _state_answers(config: Config, challenge_id: str) -> tuple[dict, list, str]:
    """[v6.8] 从已落盘的 state.json 恢复「已通过题号 → 答案」。

    实测 SAMPLE-001 v6.7：平台重启后进程重连 MCP 失败（DNS 短暂异常），旧实现直接写
    `answers={}` 的 result.json，把已经拿到的 slug 分从结果里抹掉了。连不上平台时也必须
    把 state 里的事实写回去。
    """
    try:
        store = Store(config.run_dir(challenge_id or "run")).load()
        challenge = store.get(challenge_id)
    except Exception:  # noqa: BLE001 - 恢复失败不该毁掉 result.json
        return {}, [], ""
    if challenge is None:
        return {}, [], ""
    per_qid = dict(getattr(challenge, "answers_by_qid", {}) or {})
    answers = {str(k): str(v) for k, v in per_qid.items() if v}
    if not answers and challenge.flag_value:
        answers[str(config.extra.get("default_question_id") or "flag")] = str(challenge.flag_value)
    return answers, sorted(answers), str(getattr(challenge, "instance_id", "") or "")


def _final_rollup(store: Store, challenge_id: str, summary: dict, config: Config) -> dict:
    challenge = store.get(challenge_id)
    qid = str(config.extra.get("default_question_id") or "flag")

    # [v6.3] 多问题题：answers 按「题号 → 该题号通过的答案」逐条写回。
    # 旧实现只写一个 default_question_id + 单值 flag_value，第二问通过时会把第一问的答案覆盖、
    # 最终把第一问的值当 default 题号交上去（SAMPLE-001 实测：slug 已解但 flag 记成 slug）。
    per_qid = dict(getattr(challenge, "answers_by_qid", {}) or {}) if challenge is not None else {}
    answers: dict = {str(k): str(v) for k, v in per_qid.items() if v}
    if not answers and challenge is not None and challenge.flag_value:
        answers[qid] = challenge.flag_value  # 老路径兜底：flag_value 只在平台校验通过后记录
    solved: list = sorted(answers) if (summary.get("submitted") or per_qid) else []

    usage = summary.get("usage") or {}
    budget = summary.get("budget") or {}
    ledger = summary.get("ledger") or {}
    reason = str(summary.get("reason") or "done")
    writeup = (
        f"reason={reason} steps={summary.get('steps')} "
        f"tokens={summary.get('tokens_spent', 0)} "
        f"ledger={json.dumps(ledger, ensure_ascii=False)}"
    )
    # [2026-11-10 加固-需求3a] 未解题归档标注：写在 writeup 最前面（协议明确超长是截断不是拒收，
    # 截断只砍尾部；writeup 不参与评分）。只写已发生的事实（用量/原因/轮换次数），
    # 绝不写未验证的答案——answers 走 per-question wrong-attempt cap，蒙错会烧掉题号额度。
    if _map_stop_reason(reason) != "done":
        _tokens = int(usage.get("prompt_tokens") or 0) + int(usage.get("completion_tokens") or 0)
        _ann = (
            f"[未解归档] reason={reason} tokens={_tokens}"
            f" calls={budget.get('calls', '?')} steps={summary.get('steps', '?')}"
        )
        if summary.get("keypool_rotations"):
            _ann += f" keypool_rotations={summary['keypool_rotations']}"
        _ann += "（本题未解出，用量与收口原因供重跑参考）"
        writeup = f"{_ann} | {writeup}"
    # [规则 v2.5 审计 #1 监控面] 降级跑的题要把「按什么模式跑的」写进 result.json，
    # 11-14 入围测试盯盘时 grep degraded_run_mode 就能定位平台下发了我们不认识的 run_mode。
    _degraded = str(config.extra.get("degraded_run_mode") or "")
    if _degraded and _degraded != "(empty)":
        writeup += f" degraded_run_mode={_degraded!r}(按 instance 通用链路降级跑)"

    return _rollup(
        challenge_id=challenge_id,
        instance_id=(challenge.instance_id if challenge is not None else ""),
        answers=answers,
        solved=solved,
        fully_solved=bool(summary.get("submitted")),
        turns=int(budget.get("calls") or summary.get("steps") or 0),
        tool_calls_made=_count_tool_calls(summary.get("transcript")),
        input_tokens=int(usage.get("prompt_tokens") or 0),
        output_tokens=int(usage.get("completion_tokens") or 0),
        stop_reason=_map_stop_reason(reason),
        writeup=writeup[:2000],
        llm_endpoint_alert=str(config.extra.get("llm_endpoint_alert") or ""),
    )


# ---------- 主流程 ----------


def run_agent(
    *,
    task_path: Path,
    work_dir: Path,
    env=None,
    platform: CtfyPlatform | None = None,
    llm_factory=None,
    result_path: Path | None = None,
) -> int:
    """跑一整次 agent。返回退出码（按契约恒为 0；失败语义全部写进 stop_reason）。

    `platform` / `llm_factory` / `result_path` 是测试注入点：给了就用注入的，
    不给就从 env 构建真实客户端、结果路径取 task.json 的 result.path。
    """
    env = dict(env if env is not None else os.environ)
    global _RESULT_PATH, _CHALLENGE_ID  # noqa: PLW0603 - 信号处理器需要

    task = _load_json(Path(task_path))
    challenge_id = str((task or {}).get("challenge", {}).get("id") or env.get("CTFY_EVAL_CHALLENGE_ID", ""))
    _CHALLENGE_ID = challenge_id
    if result_path is None:
        result_path = work_dir / "result.json"
        if task:
            result_path = Path(task.get("result", {}).get("path") or result_path)
    else:
        result_path = Path(result_path)
    _RESULT_PATH = result_path

    # 0) 先写 provisional：之后任何一步挂了 / 被 kill，盘上都有文件
    _write_rollup(
        result_path,
        _rollup(challenge_id=challenge_id, stop_reason="halted", writeup="provisional"),
    )

    # 1) task.json 必须存在且可解析。
    #    [v7.2 G9] 缺失时不再立即 halted，而是短轮询等待一段时间：万一 11-14/15 实际是
    #    常驻形态（平台稍后才放题），短暂等待能兜住「题还没到」的窗口。默认 60s，
    #    AGENT_TASK_WAIT_TIMEOUT 可调，0 = 不等待（恢复旧行为）。
    if task is None:
        wait_s = _as_float(env.get("AGENT_TASK_WAIT_TIMEOUT"), TASK_WAIT_TIMEOUT_DEFAULT)
        wait_s = max(0.0, wait_s)
        if wait_s > 0:
            _log(f"[G9] task.json 缺失，短轮询等待（最多 {wait_s:.0f}s，每 {TASK_POLL_INTERVAL:.0f}s 一次）…")
            deadline = time.monotonic() + wait_s
            while time.monotonic() < deadline:
                time.sleep(min(TASK_POLL_INTERVAL, max(0.0, deadline - time.monotonic())))
                task = _load_json(Path(task_path))
                if task is not None:
                    _log(f"[G9] 等到 task.json，继续正常流程")
                    challenge_id = str(task.get("challenge", {}).get("id")
                                       or env.get("CTFY_EVAL_CHALLENGE_ID", ""))
                    _CHALLENGE_ID = challenge_id
                    break
        if task is None:
            _write_rollup(
                result_path,
                _rollup(challenge_id=challenge_id, stop_reason="halted",
                        writeup=(f"task.json missing or malformed（已轮询等待 {wait_s:.0f}s，"
                                 "疑似常驻形态但未等到题，或平台环境错误）")),
                announce=True,
            )
            return 0

    # 2) dry run：注册门，最便宜，且不碰网络、不要凭证
    if env.get("CTFY_EVAL_DRY_RUN") == "1" or (task or {}).get("dry_run"):
        _write_rollup(
            result_path,
            _rollup(challenge_id=challenge_id, stop_reason="dry_run"),
            announce=True,
        )
        return 0

    # 3) 已知做不了的 run_mode 直接拒绝（不烧钱）；**未知/新增**的降级按 instance 跑。
    #    为什么放宽（2026-11-10 规则 v2.5 审计）：旧实现把所有没见过的模式一律 refused，
    #    等于平台换个词我们就整题弃权——0 分且不可恢复。而 defence/awd/engagement 的语义
    #    完全不同（守靶场 / 按轮攻防 / 企业靶场报告），照 instance 跑只会白烧预算，仍拒绝。
    run_mode = str(task.get("run_mode") or "")
    if run_mode in UNSUPPORTED_RUN_MODES:
        _write_rollup(
            result_path,
            _rollup(challenge_id=challenge_id, stop_reason="refused",
                    writeup=f"unsupported run_mode={run_mode!r}"),
            announce=True,
        )
        return 0

    # 4) 凭证 + flavor 校验（fail loudly，不烧钱）
    flavor = str(env.get("LLM_FLAVOR") or "")
    token = str(env.get("CTFY_TOKEN") or "")
    api_key = str(env.get("LLM_API_KEY") or env.get("OPENAI_API_KEY") or "")
    if flavor != "openai":
        _write_rollup(
            result_path,
            _rollup(challenge_id=challenge_id, stop_reason="refused",
                    writeup=f"unsupported LLM_FLAVOR={flavor!r} (only openai client is bundled)"),
            announce=True,
        )
        return 0
    if not token or not api_key:
        _write_rollup(
            result_path,
            _rollup(challenge_id=challenge_id, stop_reason="refused",
                    writeup="missing CTFY_TOKEN or LLM_API_KEY"),
            announce=True,
        )
        return 0

    # 5) MCP 端点
    mcp_url = None
    if platform is None:
        mcp_url = _resolve_mcp_url(work_dir, str(env.get("CTFY_PLATFORM_URL") or ""), env)
        if not mcp_url:
            _write_rollup(
                result_path,
                _rollup(challenge_id=challenge_id, stop_reason="halted",
                        writeup="no MCP endpoint (no mcp.json, no CTFY_PLATFORM_URL)"),
                announce=True,
            )
            return 0

    config = _build_config(task, work_dir, mcp_url, env)

    # [规则 v2.5 审计 #6] 端点白名单告警：只记录不拦截（本地联调非白名单属正常）。
    # 正式赛若平台未注入 LLM_* 回落本地配置，这条是「整场挂在 LLM 调用上」的第一可见信号。
    _endpoint_note = _llm_endpoint_note(config.llm_base_url)
    if _endpoint_note:
        config.extra["llm_endpoint_alert"] = _endpoint_note
        _log(f"[warn] {_endpoint_note}")

    # 6) 平台连接（或复用注入）
    if platform is None:
        platform = CtfyPlatform(mcp_url, token, timeout=config.timeout,
                                 connect_timeout=config.connect_timeout)
        connect_err = None
        # P1 修复：改 deadline 窗口（默认 90 分钟）+ 短握手超时 + 封顶退避/抖动。
        # 旧版 `range(4)` × 240s 握手最坏 ~112 分钟，且平台恢复后最长 ~240s+backoff 才重连；
        # 现在握手失败快速返回，平台一恢复就在 ~40s 内重连，总窗口仍兜住 60–90 分钟故障。
        deadline = max(float(config.connect_deadline or 5400.0), 1.0)
        started = time.monotonic()
        attempt = 0
        while True:
            try:
                platform.connect()
                connect_err = None
                break
            except Exception as exc:  # noqa: BLE001
                connect_err = exc
                attempt += 1
                if time.monotonic() - started >= deadline:
                    break
                # 退避封顶 40s + 0~1.5s 抖动（多容器同时建连时错峰，避免叠加洪峰）
                delay = min(5.0 * (2 ** min(attempt - 1, 6)), 40.0) + random.uniform(0.0, 1.5)
                time.sleep(delay)
        if connect_err is not None:
            answers, solved, inst = _state_answers(config, challenge_id)
            _write_rollup(
                result_path,
                _rollup(
                    challenge_id=challenge_id,
                    instance_id=inst,
                    answers=answers,
                    solved=solved,
                    fully_solved=False,
                    stop_reason="halted",
                    writeup=f"MCP connect failed: {connect_err}（已从 state 保留 {len(answers)} 个已通过题号）",
                ),
                announce=True,
            )
            return 0

    # 7) state + 题面入库
    try:
        run_id = challenge_id or "run"
        store = Store(config.run_dir(run_id)).load()
    except ValueError as exc:
        _write_rollup(
            result_path,
            _rollup(challenge_id=challenge_id, stop_reason="halted", writeup=f"bad run id: {exc}"),
            announce=True,
        )
        return 0
    challenge_id = _seed_challenge(store, task, env)  # 回写：兜底 id 与 store/solve/rollup 一致
    _CHALLENGE_ID = challenge_id
    _enrich_facets(store, platform, challenge_id)

    # 8) LLM 工厂（或复用注入）；强制所有角色用 LLM_MODEL（网关会改 model，多模型名是误导）
    key_pool = None
    if llm_factory is None:
        # [2026-11-10 加固-需求1] KeyPool：只有配了 LLM_API_KEYS（≥2 个有效 key）才启用；
        # 未配置时 key_pool=None，DeepSeekLLM 行为与现状逐字节一致。
        # 合规闸：pool_from_env 在平台网关 base_url 下强制单 key（正式赛额度按平台 handle 计）。
        from .keypool import pool_from_env

        # 注意：不往 key/凭证解析链里回落 config.llm_api_key（DEEPSEEK_API_KEY）——
        # 「缺凭证 → refused」是保分契约（smoke.py case_agent_degraded 钉死），不能松。
        _pool = pool_from_env(env, config.llm_base_url)
        if len(_pool) > 1:
            key_pool = _pool
            _log(f"[keypool] 启用 {len(_pool)} 个 key（首 {key_pool.label()}），401/402/配额类429 自动切换")

        def llm_factory(model=None):
            return DeepSeekLLM(
                api_key=config.llm_api_key,
                model=config.model,
                base_url=config.llm_base_url,
                temperature=config.temperature,
                max_tokens=config.max_tokens,
                debug=config.debug,
                # 读超时用题级 timeout（默认 120s）：长上下文+长输出的单次生成可能超过 120s，
                # 不传就会退回 llm 默认值，偶发 raw TimeoutError 直接击穿整题（实测 3.3min 崩两次）。
                timeout=config.timeout,
                key_pool=key_pool,
                # 全局 1B 总量总账（跨题/跨进程重启连续）；max_total_tokens=0 时为 None
                gledger=build_global_ledger(config, run_id=os.environ.get("CTFY_RUN_ID", "")),
            )

    # 9) 跑 Crew
    try:
        crew = Crew(config, platform, store, llm_factory)
        # 集群-B 编排入口：config 不加 clusterb_k 时 k=1，与 crew.solve 逐字等价（零回归）
        summary = crew.solve_clusterb(challenge_id)
        # [2026-11-10 加固-需求1 铁律3] 自动动作留痕：key 轮换事件落 transcript（只记指纹，无明文）
        if key_pool is not None and key_pool.events:
            for _ev in key_pool.events:
                store.append_transcript(
                    challenge_id,
                    {"role": "keypool",
                     "note": f"[KeyPool] 切换到 key#{_ev['idx'] + 1}({_ev['fp8']})，原因={_ev['reason']}"},
                )
            summary.setdefault("keypool_rotations", len(key_pool.events))
    except Exception as exc:  # noqa: BLE001 - 任何解题异常都不许毁掉 result.json
        _write_rollup(
            result_path,
            _rollup(challenge_id=challenge_id, stop_reason="halted", writeup=f"crew failed: {exc}"),
            announce=True,
        )
        return 0

    # 10) 最终 rollup
    _write_rollup(result_path, _final_rollup(store, challenge_id, summary, config), announce=True)
    _RESULT_PATH = None  # 结果已落盘：随后 SIGTERM 变纯 exit 0，不再用空 answers 覆盖
    return 0


def main(argv=None) -> int:
    global _RESULT_PATH, _CHALLENGE_ID  # noqa: PLW0603 - 信号处理器需要

    parser = argparse.ArgumentParser(
        prog="ctfy_agent agent",
        description="容器适配层：读 task.json → 跑 Crew → 写 result.json（CTFY-0001 v2）。",
    )
    parser.add_argument(
        "--task",
        default=None,
        help="task.json 路径（默认 $CTFY_WORK_DIR/task.json，本地测试用绝对路径覆盖）",
    )
    args = parser.parse_args(argv)

    work_dir = Path(os.environ.get("CTFY_WORK_DIR") or DEFAULT_WORK_DIR)
    task_path = Path(args.task) if args.task else work_dir / "task.json"
    if args.task and not os.environ.get("CTFY_WORK_DIR"):
        # 本地测试：--task 给定后，把结果写到 fixture 同目录，避免碰 /ctfy/work
        work_dir = task_path.resolve().parent

    # 信号只在真实入口注册（run_agent 保持纯函数，测试注入点无全局副作用）
    _RESULT_PATH = None
    _CHALLENGE_ID = ""
    _register_signals()
    try:
        return run_agent(task_path=task_path, work_dir=work_dir)
    except SystemExit:
        raise
    except BaseException as exc:  # noqa: BLE001 - 顶层兜底：绝不带非零码退出
        _log(f"[fatal] {type(exc).__name__}: {exc}")
        if _RESULT_PATH is not None:
            try:
                _write_rollup(
                    _RESULT_PATH,
                    _rollup(challenge_id=_CHALLENGE_ID, stop_reason="halted",
                            writeup=f"{type(exc).__name__}: {exc}"),
                )
            except Exception:  # noqa: BLE001 - 写盘也失败就放弃，仍保证 exit 0
                _log("[fatal] 兜底写 result.json 也失败")
        return 0


if __name__ == "__main__":
    sys.exit(main())
