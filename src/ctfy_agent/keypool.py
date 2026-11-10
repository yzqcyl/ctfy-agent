"""API Key 池：主 key 耗尽/失效时自动切备选，无需人工（加固方案 §2，2026-11-10）。

只在「已经注定失败」的响应上触发轮换：
- 401/403（key 无效/被吊销）、402（余额不足）→ 立即切；
- 429 且 body 明确是配额类（insufficient_quota / quota exceeded / arrearage / 额度）→ 立即切；
- 429 纯限速（rate limit / TPM / RPM）→ 不切，走现有退避（多 key 轮询可能被判绕限速）；
- 5xx / 网络错误 → 绝不切（服务端抖动，切了更糟）。

合规硬门禁（两道闸，详见 docs/plan-hardening-2026-11-10.md §2）：
1. 结构性：镜像不含 .env（Dockerfile 只 COPY src/ + agent.json），且 load_dotenv 不覆盖
   已有环境变量 → 正式赛容器里 LLM_API_KEYS 天然不存在，pool 恒为单 key。
2. 代码断言：LLM_API_KEYS 与平台网关 base_url（*.tsinghua.edu.cn）共存时，强制只用
   平台注入的 LLM_API_KEY 单 key——正式赛 1B 额度按平台下发的 handle 计，
   用自备 key 绕过 = 违反「不得绕过资源限制」。

预算账本与本模块无关：budget.record() 按每次响应的 usage 记账，切 key 不重置。

日志与事件只记 key 指纹（sha256 前 8 位），绝不记明文。
"""

from __future__ import annotations

import hashlib
import threading
import time

# 平台网关域名片段：命中即视为「平台注入场景」，禁用多 key（闸二）
_PLATFORM_GATEWAY_MARKERS = ("tsinghua.edu.cn",)

# 429 body 里的配额类字样（区别于纯限速）
_QUOTA_MARKERS = (
    "insufficient_quota",
    "quota exceeded",
    "quota_exceeded",
    "exceeded your current quota",
    "arrearage",
    "欠费",
    "额度",
    "余额",
)
# 400 body 里「该 key 无此模型权限」的字样 → 当死 key 处理继续切
_MODEL_PERMISSION_MARKERS = ("model", "permission")


def _fp8(key: str) -> str:
    """key 指纹：只用于日志/事件，可安全落盘。"""
    return hashlib.sha256(str(key).encode("utf-8")).hexdigest()[:8]


def classify_key_failure(status: int | None, body: str) -> str | None:
    """把一次 LLM HTTP 失败分类成 key 语义；不归本模块管的返回 None。

    返回："dead"（key 失效，立即切）/ "quota"（额度耗尽，立即切）/
    "ratelimit"（纯限速，不切，走退避）/ "transient"（服务端/网络抖动，不切）。
    """
    if status in (401, 403):
        return "dead"
    if status == 402:
        return "quota"
    low = str(body or "").lower()
    if status == 429:
        if any(m in low for m in _QUOTA_MARKERS):
            return "quota"
        return "ratelimit"
    if status == 400 and any(m in low for m in _MODEL_PERMISSION_MARKERS):
        # 新 key 没开通该模型：换 key 有意义，当 dead 继续切
        return "dead"
    if status is not None and 500 <= status < 600:
        return "transient"
    return None


class KeyPool:
    """线程安全的 key 轮转池。单 key 时 rotate() 恒 False（= 现状行为）。"""

    def __init__(self, keys):
        seen: list[str] = []
        for k in keys or []:
            k = str(k).strip()
            if k and k not in seen:
                seen.append(k)
        self._keys = seen
        self._idx = 0
        self._lock = threading.Lock()
        # 事件只存指纹：{idx, fp8, reason, at}，明文永不出现在 transcript/日志里
        self.events: list[dict] = []

    def __len__(self) -> int:
        return len(self._keys)

    @property
    def exhausted(self) -> bool:
        """没有下一个可切的 key（空池或已在最后一个）。"""
        with self._lock:
            return not self._keys or self._idx + 1 >= len(self._keys)

    def current(self) -> str:
        with self._lock:
            if not self._keys:
                return ""
            return self._keys[min(self._idx, len(self._keys) - 1)]

    def rotate(self, reason: str) -> bool:
        """切到下一个 key；没有下一个返回 False（调用方应抛 all_keys_dead）。"""
        with self._lock:
            if self._idx + 1 >= len(self._keys):
                return False
            self._idx += 1
            self.events.append(
                {"idx": self._idx, "fp8": _fp8(self._keys[self._idx]),
                 "reason": str(reason)[:80], "at": round(time.time(), 3)}
            )
            return True

    def label(self) -> str:
        """日志用标签：key#2(ab12cd34)，无明文。"""
        with self._lock:
            if not self._keys:
                return "key#0(none)"
            return f"key#{self._idx + 1}({_fp8(self._keys[min(self._idx, len(self._keys) - 1)])})"


def pool_from_env(env: dict, base_url: str, primary: str = "") -> KeyPool:
    """按环境构造 pool。只认 LLM_API_KEYS（逗号分隔）；未配置 → 单 key（= 现状）。

    primary：调用方已解析的主 key 兜底（例如 config.llm_api_key，来自 .env 的
    DEEPSEEK_API_KEY 映射），优先级低于 LLM_API_KEY/OPENAI_API_KEY。

    闸二：base_url 命中平台网关时，无视 LLM_API_KEYS，只用平台注入的单 key。
    """
    single = str(env.get("LLM_API_KEY") or env.get("OPENAI_API_KEY") or primary or "").strip()
    raw = str(env.get("LLM_API_KEYS") or "").strip()
    if raw:
        low_url = str(base_url or "").lower()
        if any(m in low_url for m in _PLATFORM_GATEWAY_MARKERS):
            # 平台注入场景：额度按平台 handle 计，多 key = 绕过资源限制（违规）
            return KeyPool([single])
        keys = [k.strip() for k in raw.split(",") if k.strip()]
        if single and single not in keys:
            keys.insert(0, single)  # 主 key 优先，LLM_API_KEYS 作备选
        return KeyPool(keys or [single])
    return KeyPool([single])
