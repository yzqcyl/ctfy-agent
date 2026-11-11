# -*- coding: utf-8 -*-
"""GUI 视觉主题层：毛玻璃态（Glassmorphism）+ GitHub 配色 + Inter 字体。

为什么单开一个文件
------------------
`app.py` 已经有 1600+ 行业务逻辑（平台接入 / 批量解题 / 复现），视觉规范再混进去就没人看得懂了。
本模块只做「设计令牌 + 可复用控件」，不含任何解题/平台逻辑，也不被容器镜像使用（镜像剔除 GUI）。

设计规范来源（用户给定，逐条落在下面的常量/控件里）
-----------------------------------------------------
* 风格：毛玻璃态，GitHub 配色（浅色）。
* 字体：标题 Inter Bold，正文 Inter Regular；大标题 -0.02em 字距；
  标题行高 1.2~1.3，正文行高 1.5~1.7。
* 阴影：0 1px 3px rgba(0,0,0,0.05), 0 1px 2px rgba(0,0,0,0.1)（浅阴影）。
* 过渡：all 200ms cubic-bezier(0.4, 0, 0.2, 1)。
* 加载态：骨架屏脉冲，或简洁旋转指示器。
* 按钮：圆角 8px、内边距 12×24px、字重 500、悬停上浮 2px 并加深背景、按下缩放 0.98。
* 卡片：白底、圆角 20px、1px rgba(0,0,0,0.05) 边框、图片区覆盖半透明渐变。
* 输入框：圆角 12px、背景 #F9FAFB、聚焦边框 2px 主色、placeholder #9CA3AF。
* 图标：Feather/Heroicons 风格、20×20、颜色继承当前文字色。
* 分隔线：1px #E5E7EB，或渐变透明线。

Tk 的能力边界（如实说明，不假装做到了）
--------------------------------------
1. 没有真正的背景模糊：毛玻璃用「浅色底 + 半透明白卡片 + 细边框 + 浅阴影」近似。
2. 字体没有 letter-spacing：大标题用 Canvas 逐字绘制实现 -0.02em；ttk 标签拿不到字距，
   只能靠 Inter 本身的行距和小字号补偿（模块里保留 ``LETTER_SPACING_EM`` 常量备查）。
3. 圆角只能画出来、不能裁剪子控件：卡片/按钮/输入框用 Canvas 画圆角底，内容放在内部矩形里；
   内容四角靠内边距留白，视觉上是圆角卡片。
4. 控件不做逐帧合成动画：过渡用 ``after()`` 分帧 + cubic-bezier 采样重绘（按钮悬停/按下、骨架脉冲）。
"""
from __future__ import annotations

import math
import sys
import tkinter as tk
from tkinter import font as tkfont
from typing import Callable, Iterable, Sequence


# ---------------------------------------------------------------------------
# 高 DPI：先声明「我自己按物理像素画」，否则 Windows 会把整窗位图拉伸 → 字发虚
# ---------------------------------------------------------------------------
def enable_dpi_awareness() -> bool:
    """让本进程 DPI-aware（幂等、失败不抛）。Windows 10+ 用 Per-Monitor V2。

    这是「字体清晰」的关键：不声明的话，系统缩放 150% 时窗口会被位图放大 1.5 倍，
    任何字体都会糊；声明之后 Tk 直接用物理像素绘制，再配 ``apply_dpi_scaling`` 就清晰。
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes  # noqa: PLC0415 - 仅 Windows 需要
    except Exception:  # noqa: BLE001
        return False
    # Windows 10 1703+：DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
    try:
        if ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return True
    except Exception:  # noqa: BLE001
        pass
    # 老系统退路：shcore.SetProcessDpiAwareness(2=PER_MONITOR) / user32.SetProcessDPIAware()
    try:
        if ctypes.windll.shcore.SetProcessDpiAwareness(2) == 0:
            return True
    except Exception:  # noqa: BLE001
        pass
    try:
        return bool(ctypes.windll.user32.SetProcessDPIAware())
    except Exception:  # noqa: BLE001
        return False


def apply_dpi_scaling(root: tk.Misc) -> float:
    """按真实 DPI 设 tk scaling（保持旧版观感大小，但按物理像素渲染 → 不发虚）。

    旧实现固定 ``tk scaling 1.2``：在 150% 缩放的屏上等于 1.2pt 像素比再被系统位图放大 1.5 倍，
    所以又大又糊。这里取 ``(dpi/72)*0.9``：96dpi 仍是 1.2（老机器不变），144dpi 变 1.8
    （等效像素比不变，但由 Tk 直接按物理像素画）。同时刷新 ``UI_SCALE`` 供像素尺寸换算。
    """
    global UI_SCALE
    dpi = 96.0
    try:
        dpi = float(root.winfo_fpixels("1i"))
    except Exception:  # noqa: BLE001
        pass
    if dpi <= 0:
        dpi = 96.0
    scaling = round(min(2.6, max(1.0, (dpi / 72.0) * 0.9)), 3)
    try:
        root.tk.call("tk", "scaling", scaling)
    except Exception:  # noqa: BLE001
        pass
    UI_SCALE = scaling
    return scaling


#: 当前 UI 缩放（由 apply_dpi_scaling 设置；基线 1.2 = 96dpi 下的历史观感）
UI_SCALE = 1.2


def ui_px(base: float) -> int:
    """把「96dpi 下的设计像素」换算成当前 DPI 的物理像素（列宽/行高/间距用）。"""
    return max(1, int(round(float(base) * UI_SCALE / 1.2)))

# ---------------------------------------------------------------------------
# 设计令牌：GitHub 浅色配色
# ---------------------------------------------------------------------------

# 页面/容器
PAGE = "#F6F8FA"            # GitHub canvas.subtle：毛玻璃的「底」
CANVAS = "#FFFFFF"          # canvas.default：卡片/输入聚焦底色
GLASS = "#FBFCFD"           # 半透明白玻璃的近似值（白底上叠 2% 冷灰）
SUBTLE = "#F9FAFB"          # 规范给定：输入框背景
HOVER = "#F3F4F6"           # 悬停加深/变浅的中间色

# 边框/阴影
BORDER = "#E5E7EB"          # 规范给定：分隔线
BORDER_SOFT = "#F2F2F2"     # rgba(0,0,0,0.05) 叠在白底上的精确合成值（卡片 1px 边框）
BORDER_STRONG = "#D0D7DE"   # GitHub border.default
SHADOW_1 = "#EFF1F4"        # 浅阴影第一层（0 1px 3px / 5%）
SHADOW_2 = "#F4F5F7"        # 浅阴影第二层（0 1px 2px / 10% 的柔化）

# 文字
TEXT = "#1F2328"            # fg.default
TEXT_MUTED = "#57606A"      # fg.muted
TEXT_SUBTLE = "#6E7781"
PLACEHOLDER = "#9CA3AF"     # 规范给定
INVERT = "#FFFFFF"

# 主色/语义色
PRIMARY = "#0969DA"         # accent.fg
PRIMARY_HOVER = "#0550AE"   # 悬停加深
PRIMARY_SOFT = "#DDF4FF"    # 选中/浅底
SUCCESS = "#1A7F37"
SUCCESS_SOFT = "#DAFBE1"
DANGER = "#CF222E"
DANGER_HOVER = "#A40E26"
DANGER_SOFT = "#FFEBE9"
WARN = "#9A6700"
WARN_SOFT = "#FFF8C5"

# 规范常量（精确值，测试直接断言）
RADIUS_BUTTON = 8
RADIUS_CARD = 20
RADIUS_INPUT = 12
PAD_BUTTON = (24, 12)       # 内边距 12px 24px → (x, y)
PAD_BUTTON_Y, PAD_BUTTON_X = 12, 24
BUTTON_FONT_WEIGHT = 500
HOVER_LIFT_PX = 2
PRESS_SCALE = 0.98
TRANSITION_MS = 200
EASING_CSS = "cubic-bezier(0.4, 0, 0.2, 1)"
LETTER_SPACING_EM = -0.02   # 大标题字距
LINE_HEIGHT_HEADING = (1.2, 1.3)
LINE_HEIGHT_BODY = (1.5, 1.7)
SHADOW_CSS = "0 1px 3px rgba(0,0,0,0.05), 0 1px 2px rgba(0,0,0,0.1)"

# ---------------------------------------------------------------------------
# 字体：Inter 优先，中文回退
# ---------------------------------------------------------------------------

_HEADING_STACK = ("Inter", "Inter Display", "Segoe UI Variable Display", "Segoe UI", "Microsoft YaHei UI")
_BODY_STACK = ("Inter", "Segoe UI Variable Text", "Segoe UI", "Microsoft YaHei UI", "PingFang SC")
_MONO_STACK = ("JetBrains Mono", "Cascadia Code", "Consolas", "Courier New")

_inter_available: bool | None = None
_picked: dict[str, str] = {}


def _families(root: tk.Misc | None = None) -> set[str]:
    try:
        return {str(x) for x in tkfont.families(root)}
    except Exception:  # noqa: BLE001 - 无显示环境时按「无 Inter」处理
        return set()


def pick_family(kind: str = "body", available: Iterable[str] | None = None) -> str:
    """在候选栈里挑第一个可用字体；无候选时返回系统默认（空串）。"""
    fams = set(available) if available is not None else _families()
    stack = {"heading": _HEADING_STACK, "body": _BODY_STACK, "mono": _MONO_STACK}.get(kind, _BODY_STACK)
    for name in stack:
        if name in fams:
            return name
    return ""


def inter_available(root: tk.Misc | None = None) -> bool:
    global _inter_available
    if _inter_available is None:
        _inter_available = "Inter" in _families(root)
    return _inter_available


def font(kind: str = "body", size: int = 10, weight: str = "normal") -> tuple:
    """返回 tk 字体元组。weight: normal/bold（Tk 只有两档，500≈normal + 字体本身字重）。"""
    key = f"{kind}:{size}:{weight}"
    fam = _picked.get(key)
    if fam is None:
        fam = pick_family(kind)
        _picked[key] = fam
    return (fam, size, weight) if fam else ("", size, weight)


def body_font(size: int = 10) -> tuple:
    return font("body", size, "normal")


def heading_font(size: int = 15, bold: bool = True) -> tuple:
    return font("heading", size, "bold" if bold else "normal")


def mono_font(size: int = 10, bold: bool = False) -> tuple:
    return font("mono", size, "bold" if bold else "normal")


# ---------------------------------------------------------------------------
# 动效：cubic-bezier(0.4, 0, 0.2, 1)
# ---------------------------------------------------------------------------


def _bezier_x(s: float, x1: float, x2: float) -> float:
    u = 1.0 - s
    return 3.0 * u * u * s * x1 + 3.0 * u * s * s * x2 + s * s * s


def _bezier_y(s: float, y1: float, y2: float) -> float:
    u = 1.0 - s
    return 3.0 * u * u * s * y1 + 3.0 * u * s * s * y2 + s * s * s


def make_easing(x1: float = 0.4, y1: float = 0.0, x2: float = 0.2, y2: float = 1.0,
                samples: int = 32) -> Callable[[float], float]:
    """CSS cubic-bezier 采样实现：输入进度 t∈[0,1]，输出缓动后的进度。"""
    table: list[tuple[float, float]] = []
    for i in range(samples + 1):
        s = i / samples
        table.append((_bezier_x(s, x1, x2), _bezier_y(s, y1, y2)))

    def ease(t: float) -> float:
        t = 0.0 if t <= 0 else (1.0 if t >= 1 else float(t))
        lo, hi = 0.0, 1.0
        for _ in range(18):  # 二分找 x(t)=t 的参数
            mid = (lo + hi) / 2.0
            if _bezier_x(mid, x1, x2) < t:
                lo = mid
            else:
                hi = mid
        return _bezier_y((lo + hi) / 2.0, y1, y2)

    return ease


ease = make_easing()  # 规范默认曲线


def ease_out_cubic(t: float) -> float:
    t = min(1.0, max(0.0, t))
    return 1.0 - (1.0 - t) ** 3


# ---------------------------------------------------------------------------
# 颜色工具
# ---------------------------------------------------------------------------


def _to_rgb(color: str) -> tuple[int, int, int]:
    c = color.lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def _to_hex(rgb: Sequence[float]) -> str:
    return "#{:02X}{:02X}{:02X}".format(*(max(0, min(255, int(round(v)))) for v in rgb))


def mix(c1: str, c2: str, t: float) -> str:
    """把 c1 往 c2 混 t∈[0,1]（t=0 → c1，t=1 → c2）。"""
    t = min(1.0, max(0.0, float(t)))
    a, b = _to_rgb(c1), _to_rgb(c2)
    return _to_hex([a[i] + (b[i] - a[i]) * t for i in range(3)])


def with_alpha_on(alpha: float, base: str = CANVAS) -> str:
    """rgba(0,0,0,alpha) 叠在 base 上的合成色（Tk 无 alpha，用合成值近似）。"""
    return mix(base, "#000000", alpha)


def darken(color: str, amount: float = 0.08) -> str:
    return mix(color, "#000000", amount)


def lighten(color: str, amount: float = 0.5) -> str:
    return mix(color, "#FFFFFF", amount)


# ---------------------------------------------------------------------------
# 圆角几何（Canvas 画圆角矩形）
# ---------------------------------------------------------------------------


def rounded_rect_points(x0: float, y0: float, x1: float, y1: float, r: float,
                        arc_steps: int = 6) -> list[float]:
    """生成圆角矩形的闭合多边形顶点（交给 create_polygon，不平滑）。"""
    r = max(0.0, min(r, (x1 - x0) / 2.0, (y1 - y0) / 2.0))
    if r <= 0.01:
        return [x0, y0, x1, y0, x1, y1, x0, y1]
    pts: list[float] = []
    # 四角圆心
    corners = (
        (x1 - r, y0 + r, -90.0, 0.0),    # 右上
        (x1 - r, y1 - r, 0.0, 90.0),     # 右下
        (x0 + r, y1 - r, 90.0, 180.0),   # 左下
        (x0 + r, y0 + r, 180.0, 270.0),  # 左上
    )
    for cx, cy, a0, a1 in corners:
        for i in range(arc_steps + 1):
            ang = math.radians(a0 + (a1 - a0) * i / arc_steps)
            pts.append(cx + r * math.cos(ang))
            pts.append(cy + r * math.sin(ang))
    return pts


# ---------------------------------------------------------------------------
# 日期分层（题目复现栏按日期分组）
# ---------------------------------------------------------------------------


def _date_key(at: str) -> str:
    """把各类时间串归一成 YYYY-MM-DD；解析不出来返回空串。"""
    s = str(at or "").strip()
    if not s:
        return ""
    head = s[:10]
    if len(head) == 10 and head[4] == "-" and head[7] == "-" and head[:4].isdigit():
        return head
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            import datetime

            return datetime.datetime.strptime(s[:19], fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return ""


def group_by_date(rows: Iterable[dict], key: str = "at") -> list[tuple[str, list[dict]]]:
    """按日期把条目分层：返回 [(日期标题, 条目列表)]，新日期在前、同日内保持原顺序。"""
    buckets: dict[str, list[dict]] = {}
    order: list[str] = []
    for row in rows:
        day = _date_key(str(row.get(key) or "")) or "未知日期"
        if day not in buckets:
            buckets[day] = []
            order.append(day)
        buckets[day].append(row)
    known = [d for d in order if d != "未知日期"]
    known.sort(reverse=True)
    if "未知日期" in buckets:
        known.append("未知日期")
    return [(d, buckets[d]) for d in known]


def date_header_text(day: str, count: int) -> str:
    if day == "未知日期":
        return f"未知日期 · {count} 题"
    return f"{day} · {count} 题"


def filter_solved(rows: Sequence[dict], solved: Iterable[str], skip: bool) -> tuple[list[dict], list[dict]]:
    """跑题库选项：skip=True 时把已解出的题跳过。返回 (要跑的, 跳过的)。"""
    done = {str(x) for x in solved}
    if not skip:
        return list(rows), []
    keep, skipped = [], []
    for row in rows:
        cid = str(row.get("id") or "")
        (skipped if cid in done else keep).append(row)
    return keep, skipped


# ---------------------------------------------------------------------------
# 图标：Feather/Heroicons 风格（20×20 线稿，颜色继承调用方传入的正文色）
# ---------------------------------------------------------------------------

_ICON_PATHS: dict[str, tuple] = {
    # name: (strokes, filled)  坐标基于 20×20 视口，宽 1.6px 圆头线
    "play": ([[(6, 4), (16, 10), (6, 16), (6, 4)]], []),
    "stop": ([[(5, 5), (15, 5), (15, 15), (5, 15), (5, 5)]], []),
    "refresh": ([[(16, 10), (16, 5), (11, 5)], [(16, 5), (13.5, 8)],
                 [(4, 10), (4, 15), (9, 15)], [(4, 15), (6.5, 12)]], []),
    "gear": ([[(10, 7), (13, 10), (10, 13), (7, 10), (10, 7)], [(10, 2), (10, 4)],
              [(10, 16), (10, 18)], [(2, 10), (4, 10)], [(16, 10), (18, 10)]], []),
    "check": ([[(4, 10), (8, 14), (16, 5)]], []),
    "x": ([[(5, 5), (15, 15)], [(15, 5), (5, 15)]], []),
    "search": ([[(9, 4), (14, 9), (9, 14), (4, 9), (9, 4)], [(13, 13), (17, 17)]], []),
    "chevron-down": ([[(5, 8), (10, 13), (15, 8)]], []),
    "folder": ([[(3, 6), (8, 6), (10, 8), (17, 8), (17, 15), (3, 15), (3, 6)]], []),
    "copy": ([[(7, 7), (15, 7), (15, 15), (7, 15), (7, 7)], [(5, 5), (5, 13), (7, 13)]], []),
    "export": ([[(10, 3), (10, 12)], [(6, 8), (10, 12), (14, 8)],
                [(4, 15), (4, 17), (16, 17), (16, 15)]], []),
    "clock": ([[(10, 3), (16, 10), (10, 17), (4, 10), (10, 3)], [(10, 6.5), (10, 10), (12.5, 11.5)]], []),
    "bolt": ([[(11, 3), (6, 11), (10, 11), (9, 17), (14, 9), (10, 9), (11, 3)]], []),
    "folder-plus": ([[(3, 6), (8, 6), (10, 8), (17, 8), (17, 15), (3, 15), (3, 6)],
                     [(10, 10), (10, 14)], [(8, 12), (12, 12)]], []),
    "trash": ([[(5, 6), (15, 6)], [(8, 6), (8, 4), (12, 4), (12, 6)],
               [(6, 6), (6, 16), (14, 16), (14, 6)]], []),
}

ICON_SIZE = 20
ICON_STROKE = 1.6


def icon(canvas: tk.Canvas, name: str, x: float, y: float, size: float = ICON_SIZE,
         color: str = TEXT, tag: str | None = None) -> bool:
    """把 Feather 风格图标画到 canvas 的 (x, y) 左上角，返回是否命中了图标名。"""
    spec = _ICON_PATHS.get(name)
    if not spec:
        return False
    strokes, _filled = spec
    scale = size / 20.0
    tags = (tag,) if tag else ()
    for path in strokes:
        coords: list[float] = []
        for px, py in path:
            coords.extend((x + px * scale, y + py * scale))
        canvas.create_line(*coords, fill=color, width=max(1.0, ICON_STROKE * scale),
                           capstyle="round", joinstyle="round", tags=tags)
    return True


def icon_lines(name: str, x: float = 0.0, y: float = 0.0, size: float = ICON_SIZE) -> list[list[tuple[float, float]]]:
    """纯几何版本（测试用）：返回图标线段列表。"""
    spec = _ICON_PATHS.get(name)
    if not spec:
        return []
    scale = size / 20.0
    out: list[list[tuple[float, float]]] = []
    for path in spec[0]:
        out.append([(x + px * scale, y + py * scale) for px, py in path])
    return out


# 图标名字表（测试断言用）
ICON_NAMES = tuple(_ICON_PATHS)


# ---------------------------------------------------------------------------
# 基础控件
# ---------------------------------------------------------------------------


def label(master: tk.Misc, text: str = "", *, textvariable=None, muted: bool = False,
          bold: bool = False, size: int = 10, fg: str | None = None, bg: str | None = None,
          justify: str = "left", **kw) -> tk.Label:
    """tk.Label（能正确继承卡片白底）；ttk.Label 在自定义卡片里会露出页面底色。"""
    base = bg if bg is not None else _bg_of(master)
    color = fg if fg is not None else (TEXT_MUTED if muted else TEXT)
    return tk.Label(master, text=text, textvariable=textvariable, justify=justify,
                    bg=base, fg=color, font=body_font(size) if not bold else heading_font(size),
                    anchor="w", **kw)


def _bg_of(master: tk.Misc) -> str:
    """取控件背景色并归一成 #RRGGBB（Tk 可能返回 SystemButtonFace 这类颜色名）。"""
    try:
        raw = str(master.cget("bg"))
    except Exception:  # noqa: BLE001
        return PAGE
    if raw.startswith("#") and len(raw) == 7:
        return raw
    try:
        r, g, b = master.winfo_rgb(raw)
        return "#{:02X}{:02X}{:02X}".format(r >> 8, g >> 8, b >> 8)
    except Exception:  # noqa: BLE001
        return PAGE


class Card(tk.Frame):
    """白底圆角卡片：Canvas 画圆角 + 1px 边框 + 两层浅阴影，内容放 ``.body``。

    * ``hug=True``（默认）：卡片尺寸跟随内容（表单/摘要卡）。
    * ``hug=False``：卡片填满父容器（大面板），内容区随卡片拉伸。
    * ``glass=True``：毛玻璃态 —— 半透明白 + 更淡的边框，叠在浅灰底上。
    """

    def __init__(self, master: tk.Misc, *, radius: int = RADIUS_CARD, padding: int | tuple = 16,
                 bg: str = CANVAS, border: str | None = None, shadow: bool = True,
                 glass: bool = False, hug: bool = True, shadow_px: int = 2, **kw):
        self._page = _bg_of(master)
        super().__init__(master, bg=self._page, bd=0, highlightthickness=0, **kw)
        self.radius = int(radius)
        self.pad_x, self.pad_y = _pad_pair(padding)
        self.fill_bg = GLASS if glass else bg
        self.border = border if border is not None else (BORDER if glass else BORDER_SOFT)
        self.shadow = shadow
        self.shadow_px = shadow_px
        self.hug = hug

        self.canvas = tk.Canvas(self, bg=self._page, bd=0, highlightthickness=0, takefocus=0)
        self.canvas.pack(fill="both", expand=True)
        self.body = tk.Frame(self.canvas, bg=self.fill_bg, bd=0, highlightthickness=0)
        self._win = self.canvas.create_window(self.pad_x, self.pad_y, window=self.body, anchor="nw")
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.body.bind("<Configure>", self._on_body_configure)
        self._last = (0, 0)

    # -- 布局同步 ------------------------------------------------------
    def _on_canvas_configure(self, event) -> None:
        if event.width <= 1 or event.height <= 1:
            return
        inner_w = max(1, event.width - 2 * self.pad_x)
        cfg: dict = {"width": inner_w}
        if not self.hug:
            cfg["height"] = max(1, event.height - 2 * self.pad_y)
        self.canvas.itemconfigure(self._win, **cfg)
        if self._last != (event.width, event.height):
            self._last = (event.width, event.height)
            self._redraw(event.width, event.height)

    def _on_body_configure(self, event) -> None:
        if not self.hug or event.width <= 1:
            return
        want_w = event.width + 2 * self.pad_x
        want_h = event.height + 2 * self.pad_y
        cur_w = int(self.canvas.cget("width") or 0)
        cur_h = int(self.canvas.cget("height") or 0)
        if abs(want_w - cur_w) > 1:
            self.canvas.configure(width=want_w)
        if abs(want_h - cur_h) > 1:
            self.canvas.configure(height=want_h)

    def _redraw(self, w: int, h: int) -> None:
        c = self.canvas
        c.delete("chrome")
        y_off = self.shadow_px if self.shadow else 0
        if self.shadow:
            # 两层浅阴影：0 1px 3px rgba(0,0,0,0.05) + 0 1px 2px rgba(0,0,0,0.1) 的近似
            for off, color in ((self.shadow_px + 1, SHADOW_1), (self.shadow_px, SHADOW_2)):
                pts = rounded_rect_points(0.5, 0.5 + off, w - 0.5, h - 0.5 + off,
                                          self.radius + 0.5)
                c.create_polygon(*pts, fill=color, outline=color, tags="chrome")
        pts = rounded_rect_points(0.5, 0.5, w - 0.5, h - 0.5 - y_off * 0, self.radius)
        c.create_polygon(*pts, fill=self.fill_bg, outline=self.border, width=1, tags="chrome")
        c.tag_lower("chrome")

    def set_padding(self, padding: int | tuple) -> None:
        self.pad_x, self.pad_y = _pad_pair(padding)
        self.canvas.coords(self._win, self.pad_x, self.pad_y)


def _pad_pair(padding: int | tuple) -> tuple[int, int]:
    if isinstance(padding, (tuple, list)):
        if len(padding) == 2:
            px, py = padding
        elif len(padding) == 4:  # (left, top, right, bottom) → x/y 取简单平均
            px = int((padding[0] + padding[2]) / 2)
            py = int((padding[1] + padding[3]) / 2)
        else:  # pragma: no cover - 兜底
            px = py = 16
    else:
        px = py = int(padding)
    return int(px), int(py)


class GlassHeader(Card):
    """顶部毛玻璃标题条：浅色玻璃卡片 + 底部渐变分隔线 + 逐字字距的大标题。"""

    def __init__(self, master: tk.Misc, title: str = "", subtitle: str = "", **kw):
        kw.setdefault("radius", RADIUS_CARD)
        kw.setdefault("padding", (20, 14))
        kw.setdefault("glass", True)
        super().__init__(master, **kw)
        self.title = title
        self.subtitle = subtitle

    def draw_brand(self, canvas: tk.Canvas | None = None) -> None:  # pragma: no cover - 视觉调试用
        pass


class RoundedButton(tk.Canvas):
    """规范按钮：圆角 8px、内边距 12×24、悬停上浮 2px 且加深、按下缩放 0.98。

    与 ttk.Button 兼容的最小 API：``config(state=...)`` / ``configure(text=...)`` /
    ``invoke()``。过渡走 200ms cubic-bezier 分帧重绘。
    """

    VARIANTS = {
        "primary": (PRIMARY, INVERT, None, PRIMARY_HOVER),
        "secondary": (CANVAS, TEXT, BORDER, HOVER),
        "ghost": (None, TEXT_MUTED, None, HOVER),
        "danger": (DANGER, INVERT, None, DANGER_HOVER),
        "success": (SUCCESS, INVERT, None, "#116329"),
    }

    def __init__(self, master: tk.Misc, text: str = "", command: Callable | None = None, *,
                 variant: str = "secondary", icon: str | None = None, radius: int = RADIUS_BUTTON,
                 pad_x: int = PAD_BUTTON_X, pad_y: int = PAD_BUTTON_Y, width: int | None = None,
                 height: int | None = None, font: tuple | None = None, tooltip: str = "", **kw):
        base_bg = _bg_of(master)
        super().__init__(master, bg=base_bg, bd=0, highlightthickness=0,
                         takefocus=1, cursor="hand2", **kw)
        self._command = command
        self._variant = variant if variant in self.VARIANTS else "secondary"
        self._radius = int(radius)
        self._pad_x, self._pad_y = int(pad_x), int(pad_y)
        self._font = font or body_font(10)
        self._icon = icon
        self._text = str(text)
        self._state = "normal"
        self._tooltip = tooltip
        self._lift = 0.0          # 当前上浮像素（0..HOVER_LIFT_PX）
        self._scale = 1.0         # 当前缩放（PRESS_SCALE..1.0）
        self._hover = False
        self._pressed = False
        self._anim_id: str | None = None
        self._measure_required = width is None or height is None
        self._min_w = int(width or 0)
        self._min_h = int(height or 0)
        self._compute_size()
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<FocusIn>", lambda e: self._draw())
        self.bind("<FocusOut>", lambda e: self._draw())
        self._draw()

    # -- 尺寸 ----------------------------------------------------------
    def _compute_size(self) -> None:
        try:
            f = tkfont.Font(root=self, font=self._font)
            text_w = f.measure(self._text) if self._text else 0
            line_h = f.metrics("linespace")
        except Exception:  # noqa: BLE001
            text_w, line_h = max(40, len(self._text) * 8), 16
        icon_w = ICON_SIZE + 8 if self._icon else 0
        w = max(self._min_w, text_w + icon_w + 2 * self._pad_x)
        h = max(self._min_h, line_h + 2 * self._pad_y)
        # 上浮 2px 需要顶部留白，否则会被裁掉
        self.configure(width=w, height=h + HOVER_LIFT_PX)

    # -- 动画 ----------------------------------------------------------
    def _animate(self, lift_target: float, scale_target: float) -> None:
        if self._anim_id:
            try:
                self.after_cancel(self._anim_id)
            except Exception:  # noqa: BLE001
                pass
            self._anim_id = None
        frames = max(4, int(TRANSITION_MS / 16))
        start = (self._lift, self._scale)
        step = {"i": 0}

        def tick() -> None:
            step["i"] += 1
            t = ease(step["i"] / frames)
            self._lift = start[0] + (lift_target - start[0]) * t
            self._scale = start[1] + (scale_target - start[1]) * t
            self._draw()
            if step["i"] < frames:
                self._anim_id = self.after(16, tick)
            else:
                self._anim_id = None

        tick()

    # -- 状态 ----------------------------------------------------------
    def _on_enter(self, _e=None) -> None:
        if self._state == "disabled":
            return
        self._hover = True
        self.configure(cursor="hand2")
        self._animate(HOVER_LIFT_PX, 1.0)

    def _on_leave(self, _e=None) -> None:
        self._hover = False
        self._pressed = False
        if self._state != "disabled":
            self._animate(0.0, 1.0)
        else:
            self._draw()

    def _on_press(self, _e=None) -> None:
        if self._state == "disabled":
            return
        self._pressed = True
        self._animate(HOVER_LIFT_PX, PRESS_SCALE)

    def _on_release(self, _e=None) -> None:
        if self._state == "disabled":
            return
        was_pressed = self._pressed
        self._pressed = False
        self._animate(HOVER_LIFT_PX, 1.0)
        if was_pressed and self._command:
            try:
                self.after(10, self._command)  # 让按下动画先出帧
            except Exception:  # noqa: BLE001
                self._command()

    def invoke(self) -> None:
        if self._state != "disabled" and self._command:
            self._command()

    # -- 兼容 ttk.Button 的最小接口 -------------------------------------
    def set_variant(self, variant: str) -> None:
        """切换配色变体并立即重绘（页签「选中=主色 / 未选中=白底」用它）。"""
        if variant in self.VARIANTS:
            self._variant = variant
            self._draw()

    def config(self, **kw):  # noqa: N802 - 对齐 tk 习惯
        if "state" in kw:
            self._state = "disabled" if str(kw.pop("state")) == "disabled" else "normal"
            self._draw()
        if "text" in kw:
            self._text = str(kw.pop("text"))
            self._compute_size()
            self._draw()
        if "command" in kw:
            self._command = kw.pop("command")
        if kw:
            super().config(**kw)

    configure = config  # noqa: N815

    def cget(self, key):
        if key == "state":
            return self._state
        if key == "text":
            return self._text
        return super().cget(key)

    # -- 绘制 ----------------------------------------------------------
    def _draw(self) -> None:
        self.delete("all")
        w = int(self.cget("width") or 100)
        h = int(self.cget("height") or 36)
        fill, fg, border, hover_fill = self.VARIANTS[self._variant]
        disabled = self._state == "disabled"
        if disabled:
            fill = mix(self._page(), CANVAS, 0.6)
            fg = TEXT_SUBTLE
            border = BORDER_SOFT

        base_fill = fill if fill else self._page()
        if self._hover and not disabled:
            target = hover_fill or darken(base_fill, 0.06)
            base_fill = mix(base_fill, target, 0.6)
        if self._pressed and not disabled:
            base_fill = darken(base_fill, 0.10)

        # 缩放围绕中心
        cx, cy = w / 2.0, (h - HOVER_LIFT_PX) / 2.0
        bw, bh = (w - 2) * self._scale, (h - HOVER_LIFT_PX - 2) * self._scale
        x0, y0 = cx - bw / 2.0, cy - bh / 2.0 + HOVER_LIFT_PX / 2.0 - self._lift
        x1, y1 = x0 + bw, y0 + bh

        if self._variant in ("primary", "danger", "success") and not disabled:
            # 主按钮的浅阴影
            pts = rounded_rect_points(x0, y0 + 1.5, x1, y1 + 1.5, self._radius)
            self.create_polygon(*pts, fill=SHADOW_1, outline=SHADOW_1)
        pts = rounded_rect_points(x0, y0, x1, y1, self._radius)
        outline = border if border else (BORDER if self._variant == "secondary" else "")
        self.create_polygon(*pts, fill=base_fill,
                            outline=outline or "", width=1 if outline else 0)
        if self.focus_get() is self:  # 键盘焦点环（无障碍）
            fpts = rounded_rect_points(x0 - 1.5, y0 - 1.5, x1 + 1.5, y1 + 1.5, self._radius + 1.5)
            self.create_polygon(*fpts, fill="", outline=PRIMARY, width=1)

        # 内容（图标 20×20 + 文本，垂直居中，整体水平居中）
        total_w = 0
        text_w = 0
        try:
            f = tkfont.Font(root=self, font=self._font)
            text_w = f.measure(self._text) if self._text else 0
        except Exception:  # noqa: BLE001
            text_w = 0
        icon_w = ICON_SIZE + 8 if self._icon else 0
        total_w = text_w + icon_w
        start_x = cx - total_w / 2.0
        if self._icon:
            icon(self, str(self._icon), start_x, cy - ICON_SIZE / 2.0 + HOVER_LIFT_PX / 2.0 - self._lift,
                 size=ICON_SIZE, color=fg)
            start_x += ICON_SIZE + 8
        if self._text:
            self.create_text(start_x, cy + HOVER_LIFT_PX / 2.0 - self._lift, text=self._text,
                             anchor="w", fill=fg, font=self._font)

    def _page(self) -> str:
        return _bg_of(self)


class Divider(tk.Frame):
    """1px 分隔线；``fade=True`` 时用三段透明渐变近似（中间实、两端虚）。"""

    def __init__(self, master: tk.Misc, *, fade: bool = False, color: str = BORDER,
                 thickness: int = 1, **kw):
        self._page = _bg_of(master)
        super().__init__(master, bg=self._page, bd=0, highlightthickness=0, **kw)
        if fade:
            for amount in (0.75, 0.25, 0.0, 0.25, 0.75):
                tk.Frame(self, bg=mix(self._page, color, 1.0 - amount), height=thickness).pack(
                    fill="x")
        else:
            tk.Frame(self, bg=color, height=thickness).pack(fill="x")


class Skeleton(tk.Frame):
    """骨架屏：若干条圆角占位块做脉冲呼吸（1.2s 循环，ease 采样）。"""

    def __init__(self, master: tk.Misc, *, rows: int = 6, row_height: int = 22, gap: int = 10,
                 radius: int = 8, **kw):
        self._page = _bg_of(master)
        super().__init__(master, bg=self._page, bd=0, highlightthickness=0, **kw)
        self._rows = int(rows)
        self._row_h = int(row_height)
        self._gap = int(gap)
        self._radius = int(radius)
        self._phase = 0.0
        self._job: str | None = None
        self.canvas = tk.Canvas(self, bg=self._page, bd=0, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda e: self._draw())
        self.bind("<Destroy>", lambda e: self.stop())  # 窗口销毁时收掉 after 定时器
        self._draw()

    def start(self) -> None:
        if self._job:
            return
        self._tick()

    def stop(self) -> None:
        if self._job:
            try:
                self.after_cancel(self._job)
            except Exception:  # noqa: BLE001
                pass
            self._job = None

    def _tick(self) -> None:
        self._phase = (self._phase + 0.06) % 2.0
        self._draw()
        self._job = self.after(50, self._tick)

    def _draw(self) -> None:
        c = self.canvas
        c.delete("all")
        w = max(60, c.winfo_width())
        wave = abs(1.0 - self._phase) if self._phase <= 1 else (self._phase - 1.0)
        base = mix("#F3F4F6", "#E5E7EB", ease(min(1.0, wave)))
        y = 0
        widths = (0.96, 0.72, 0.88, 0.62, 0.80, 0.55)
        for i in range(self._rows):
            bw = int(w * widths[i % len(widths)])
            pts = rounded_rect_points(0, y, bw, y + self._row_h, self._radius)
            c.create_polygon(*pts, fill=base, outline="")
            y += self._row_h + self._gap
            if y > c.winfo_height():
                break


class Spinner(tk.Canvas):
    """简洁旋转指示器（圆弧扫过），用于连接/拉取这类不确定进度。"""

    def __init__(self, master: tk.Misc, *, size: int = 18, color: str = PRIMARY, **kw):
        self._page = _bg_of(master)
        super().__init__(master, width=size, height=size, bg=self._page, bd=0,
                         highlightthickness=0, **kw)
        self._size = size
        self._color = color
        self._angle = 0.0
        self._job: str | None = None
        self.bind("<Destroy>", lambda e: self.stop())  # 窗口销毁时收掉 after 定时器
        self._draw()

    def start(self) -> None:
        if self._job:
            return
        self._tick()

    def stop(self) -> None:
        if self._job:
            try:
                self.after_cancel(self._job)
            except Exception:  # noqa: BLE001 - 销毁过程中取消定时器可能失败
                pass
            self._job = None
        try:
            self.delete("all")
        except tk.TclError:
            pass

    def _tick(self) -> None:
        self._angle = (self._angle + 24) % 360
        self._draw()
        self._job = self.after(40, self._tick)

    def _draw(self) -> None:
        self.delete("all")
        s = self._size
        pad = 2
        self.create_arc(pad, pad, s - pad, s - pad, start=self._angle, extent=250,
                        style="arc", outline=self._color, width=2)


class ScrollFrame(tk.Frame):
    """竖向滚动容器：``.inner`` 里放内容，内容超高自动出滚动条（细滚动条 + 滚轮）。"""

    def __init__(self, master: tk.Misc, *, bg: str | None = None, **kw):
        self._page = _bg_of(master)
        super().__init__(master, bg=self._page, bd=0, highlightthickness=0, **kw)
        self.canvas = tk.Canvas(self, bg=bg or self._page, bd=0, highlightthickness=0)
        self.vsb = tk.Scrollbar(self, orient="vertical", command=self.canvas.yview,
                                width=8, bd=0, highlightthickness=0,
                                troughcolor=self._page, activebackground=BORDER_STRONG,
                                bg=BORDER, relief="flat")
        self.canvas.configure(yscrollcommand=self._on_scroll_set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.inner = tk.Frame(self.canvas, bg=bg or self._page, bd=0, highlightthickness=0)
        self._win = self.canvas.create_window(0, 0, anchor="nw", window=self.inner)
        self.inner.bind("<Configure>", self._on_inner_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.canvas.bind("<Enter>", self._bind_wheel)
        self.canvas.bind("<Leave>", self._unbind_wheel)

    def _on_inner_configure(self, _e=None) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, event) -> None:
        self.canvas.itemconfigure(self._win, width=event.width)

    def _on_scroll_set(self, first: str, last: str) -> None:
        if float(first) <= 0.0 and float(last) >= 1.0:
            self.vsb.pack_forget()
        else:
            self.vsb.pack(side="right", fill="y")
        self.vsb.set(first, last)

    def _bind_wheel(self, _e=None) -> None:
        self.canvas.bind_all("<MouseWheel>", self._on_wheel)

    def _unbind_wheel(self, _e=None) -> None:
        self.canvas.unbind_all("<MouseWheel>")

    def _on_wheel(self, event) -> None:
        try:
            self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        except Exception:  # noqa: BLE001
            pass


class Pill(tk.Canvas):
    """状态胶囊（圆角标签）：用于「就绪 / 运行中 / 已连接」等状态色。"""

    def __init__(self, master: tk.Misc, text: str = "", *, color: str = TEXT_MUTED,
                 bg: str = HOVER, font: tuple | None = None, pad_x: int = 10,
                 pad_y: int = 4, radius: int = 999, **kw):
        self._page = _bg_of(master)
        super().__init__(master, bg=self._page, bd=0, highlightthickness=0, **kw)
        self._text = text
        self._color = color
        self._pill_bg = bg
        self._font = font or body_font(9)
        self._pad_x, self._pad_y = pad_x, pad_y
        self._radius = radius
        self._resize()

    def set(self, text: str, color: str | None = None, bg: str | None = None) -> None:
        self._text = str(text)
        if color:
            self._color = color
        if bg:
            self._pill_bg = bg
        self._resize()

    def _resize(self) -> None:
        try:
            f = tkfont.Font(root=self, font=self._font)
            w = f.measure(self._text) + 2 * self._pad_x
            h = f.metrics("linespace") + 2 * self._pad_y
        except Exception:  # noqa: BLE001
            w, h = 60, 22
        self.configure(width=max(28, w), height=max(20, h))
        self._draw()

    def _draw(self) -> None:
        self.delete("all")
        w = int(self.cget("width") or 60)
        h = int(self.cget("height") or 22)
        r = min(h / 2.0, w / 2.0)
        pts = rounded_rect_points(0.5, 0.5, w - 0.5, h - 0.5, r)
        self.create_polygon(*pts, fill=self._pill_bg, outline="")
        self.create_text(w / 2.0, h / 2.0, text=self._text, fill=self._color, font=self._font)


class TabBar(tk.Frame):
    """圆角胶囊页签栏（替代 ttk.Notebook）。

    ttk 的页签做不到「选中蓝色填充+白字 / 未选中白底+深字 / 页签之间留空隙 / 圆角」，
    所以自己实现，同时保留 ``select()`` / ``add()`` 这两个调用方在用的 API。

    page 必须是 ``tabbar.body`` 的子控件（创建时用 ``nb.body`` 当父容器）。
    """

    def __init__(self, master: tk.Misc, *, bg: str | None = None, gap: int = 8,
                 pad: tuple[int, int] = (10, 8), radius: int = 10, **kw):
        self._page_bg = bg or _bg_of(master)
        super().__init__(master, bg=self._page_bg, **kw)
        self._gap = int(gap)
        self._radius = int(radius)
        self._tabbar = tk.Frame(self, bg=self._page_bg)
        self._tabbar.pack(fill="x", padx=10, pady=pad)
        self.body = tk.Frame(self, bg=self._page_bg)
        self.body.pack(fill="both", expand=True)
        self.body.rowconfigure(0, weight=1)
        self.body.columnconfigure(0, weight=1)
        self._tabs: list[dict] = []
        self._current = -1

    # -- 构建 ----------------------------------------------------------
    def add(self, page: tk.Misc, text: str = "", icon: str | None = None) -> int:
        idx = len(self._tabs)
        btn = RoundedButton(self._tabbar, text=text, icon=icon, variant="secondary",
                            radius=self._radius, pad_x=18, pad_y=8,
                            font=body_font(10),
                            command=lambda i=idx: self.select(i))
        btn.pack(side="left", padx=(0, self._gap))
        page.grid(row=0, column=0, sticky="nsew")
        page.grid_remove()
        self._tabs.append({"btn": btn, "page": page})
        if self._current < 0:
            self.select(0)
        return idx

    # -- 选中 ----------------------------------------------------------
    def _index_of(self, which) -> int:
        if isinstance(which, int) and not isinstance(which, bool):
            return which if 0 <= which < len(self._tabs) else -1
        if which is None:
            return -1
        text = str(which)
        if text in ("", "current"):
            return self._current
        for i, tab in enumerate(self._tabs):
            if tab["page"] is which or str(tab["page"]) == text:
                return i
        if text.isdigit():
            idx = int(text)
            return idx if 0 <= idx < len(self._tabs) else -1
        return -1

    def select(self, which) -> None:
        idx = self._index_of(which)
        if idx < 0:
            return
        if self._current == idx:
            self._tabs[idx]["page"].grid()
            return
        if 0 <= self._current < len(self._tabs):
            self._tabs[self._current]["page"].grid_remove()
        self._current = idx
        page = self._tabs[idx]["page"]
        page.grid()
        page.lift()
        for i, tab in enumerate(self._tabs):
            # 选中=主色填充+白字；未选中=白底+深字
            tab["btn"].set_variant("primary" if i == idx else "secondary")

    def index(self, which) -> int:
        return self._index_of(which)

    def current(self) -> int:
        return self._current

    def tabs(self) -> list:
        return [tab["page"] for tab in self._tabs]

    def tab_buttons(self) -> list:
        return [tab["btn"] for tab in self._tabs]


def textbox(master: tk.Misc, *, height: int = 5, mono: bool = False, size: int = 10,
            bg: str = CANVAS, **kw) -> tk.Text:
    """规范输入文本域：白底、无边框、正文行高 1.5~1.7、选中色跟主色。"""
    w = tk.Text(master, height=height, wrap="word", relief="flat", bd=0,
                highlightthickness=0, bg=bg, fg=TEXT, insertbackground=PRIMARY,
                selectbackground=PRIMARY_SOFT, selectforeground=TEXT,
                font=mono_font(size) if mono else body_font(size),
                padx=10, pady=8, spacing1=2, spacing2=2, spacing3=2, **kw)
    return w


class RoundedEntry(tk.Frame):
    """圆角输入框：12px 圆角、背景 #F9FAFB、聚焦 2px 主色边框、placeholder #9CA3AF。"""

    def __init__(self, master: tk.Misc, *, textvariable: tk.StringVar | None = None,
                 placeholder: str = "", width: int | None = None, radius: int = RADIUS_INPUT,
                 font: tuple | None = None, **kw):
        self._page = _bg_of(master)
        super().__init__(master, bg=self._page, bd=0, highlightthickness=0, **kw)
        self.radius = int(radius)
        self.placeholder = placeholder
        self.var = textvariable if textvariable is not None else tk.StringVar()
        self._focused = False
        self.canvas = tk.Canvas(self, bg=self._page, bd=0, highlightthickness=0,
                                width=width or 200, height=38)
        self.canvas.pack(fill="both", expand=True)
        self.entry = tk.Entry(self.canvas, textvariable=self.var, relief="flat", bd=0,
                              highlightthickness=0, bg=SUBTLE, fg=TEXT,
                              insertbackground=PRIMARY, selectbackground=PRIMARY_SOFT,
                              selectforeground=TEXT, font=font or body_font(10))
        self._win = self.canvas.create_window(12, 19, anchor="w", window=self.entry)
        self.canvas.bind("<Configure>", self._on_resize)
        self.entry.bind("<FocusIn>", self._on_focus_in)
        self.entry.bind("<FocusOut>", self._on_focus_out)
        self.var.trace_add("write", lambda *a: self._sync_placeholder())
        self._sync_placeholder()
        self._draw()

    # 兼容 tk.Entry 常用接口
    def get(self) -> str:
        return self.var.get()

    def set(self, value: str) -> None:
        self.var.set(value)

    def insert(self, _index, value: str) -> None:
        self.var.set(self.var.get() + value)

    def delete(self, _first, _last=None) -> None:
        self.var.set("")

    def focus_set(self) -> None:  # noqa: D401
        self.entry.focus_set()

    def _on_resize(self, event) -> None:
        self.canvas.coords(self._win, 12, max(10, event.height // 2))
        self.canvas.itemconfigure(self._win, width=max(20, event.width - 24))
        self._draw(event.width, event.height)

    def _on_focus_in(self, _e=None) -> None:
        self._focused = True
        if self.placeholder and self.var.get() == self.placeholder:
            self.var.set("")
        self._draw()

    def _on_focus_out(self, _e=None) -> None:
        self._focused = False
        self._sync_placeholder()
        self._draw()

    def _sync_placeholder(self) -> None:
        if not self.placeholder:
            return
        cur = self.var.get()
        if not cur and not self._focused:
            self.var.set(self.placeholder)
        if self.var.get() == self.placeholder:
            self.entry.configure(fg=PLACEHOLDER)
        elif self.entry.cget("fg") == PLACEHOLDER:
            self.entry.configure(fg=TEXT)

    def _draw(self, w: int | None = None, h: int | None = None) -> None:
        self.canvas.delete("chrome")
        w = int(w or self.canvas.winfo_width() or 200)
        h = int(h or self.canvas.winfo_height() or 38)
        if w <= 2 or h <= 2:
            return
        fill = CANVAS if self._focused else SUBTLE
        pts = rounded_rect_points(1, 1, w - 1, h - 1, self.radius)
        self.canvas.create_polygon(*pts, fill=fill, outline="", tags="chrome")
        if self._focused:
            pts2 = rounded_rect_points(1, 1, w - 1, h - 1, self.radius)
            self.canvas.create_polygon(*pts2, fill="", outline=PRIMARY, width=2, tags="chrome")
        else:
            self.canvas.create_polygon(*pts, fill="", outline=BORDER_SOFT, width=1, tags="chrome")
        self.canvas.tag_lower("chrome")


def separator(master: tk.Misc, **kw) -> Divider:
    return Divider(master, fade=True, **kw)


def overlay_gradient(canvas: tk.Canvas, x0: float, y0: float, x1: float, y1: float,
                     top: str = "#FFFFFF", bottom: str | None = None, *, steps: int = 24,
                     tag: str = "overlay") -> None:
    """图片/横幅区域的半透明渐变覆盖（规范：图片区覆盖半透明渐变）。

    做法：逐条水平细线从 ``top`` 混到 ``bottom``（bottom 默认完全透明 = 卡片底色），
    适用于卡片里的图片/横幅区域；本 GUI 目前没有图片位，留给以后加截图/题图时用。
    """
    bottom = bottom or _bg_of(canvas)
    h = max(1.0, float(y1) - float(y0))
    strip = h / steps
    for i in range(steps):
        t = i / max(1, steps - 1)
        color = mix(top, bottom, t)
        canvas.create_rectangle(x0, y0 + i * strip, x1, y0 + (i + 1) * strip + 0.5,
                                fill=color, outline=color, tags=tag)


def panel(master: tk.Misc, *, height: int | None = None, bg: str = CANVAS,
          border: str = BORDER_SOFT) -> tk.Frame:
    """细边框白底面板（列表/文本容器）。

    Tk 不能把子控件裁剪成圆角，所以大面板只用 1px 细边框 + 白底（圆角留给 Card）。
    """
    f = tk.Frame(master, bg=bg, bd=0, highlightthickness=1,
                 highlightbackground=border, highlightcolor=border)
    if height:
        f.configure(height=int(height))
        f.pack_propagate(False)
    return f


def card_title(parent: tk.Misc, text: str, *, subtitle: str = "", bg: str = CANVAS) -> tk.Frame:
    """卡片内的标题块（Inter Bold 标题 + 灰色副标题）。"""
    box = tk.Frame(parent, bg=bg)
    tk.Label(box, text=text, bg=bg, fg=TEXT, font=heading_font(11), anchor="w").pack(anchor="w")
    if subtitle:
        tk.Label(box, text=subtitle, bg=bg, fg=TEXT_MUTED, font=body_font(9),
                 anchor="w").pack(anchor="w", pady=(2, 0))
    return box


# ---------------------------------------------------------------------------
# ttk 全局主题（对话框等 ttk 控件自动继承）
# ---------------------------------------------------------------------------


def apply(root: tk.Misc):  # noqa: N802 - 返回 style 方便调用方微调
    """把 GitHub 配色 + Inter 字体套到 ttk（clam 主题）上。"""
    from tkinter import ttk

    root.configure(bg=PAGE)
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except Exception:  # noqa: BLE001
        pass

    body = body_font(10)
    bold = heading_font(10)
    style.configure(".", font=body, background=PAGE, foreground=TEXT,
                    bordercolor=BORDER, lightcolor=SUBTLE, darkcolor=BORDER,
                    troughcolor=HOVER, focuscolor=PRIMARY, selectbackground=PRIMARY_SOFT,
                    selectforeground=TEXT, fieldbackground=SUBTLE, insertcolor=TEXT)
    style.configure("TFrame", background=PAGE)
    style.configure("Card.TFrame", background=CANVAS)
    style.configure("TLabel", background=PAGE, foreground=TEXT, font=body)
    style.configure("Card.TLabel", background=CANVAS, foreground=TEXT, font=body)
    style.configure("Muted.TLabel", background=PAGE, foreground=TEXT_MUTED, font=body)
    style.configure("CardMuted.TLabel", background=CANVAS, foreground=TEXT_MUTED, font=body)
    style.configure("Title.TLabel", background=PAGE, foreground=TEXT, font=heading_font(16))
    style.configure("Subtitle.TLabel", background=PAGE, foreground=TEXT_MUTED, font=body)

    # 按钮：规范内边距 12×24（对话框里 ttk 按钮沿用，尺寸略收以适配对话框）
    style.configure("TButton", padding=(16, 9), font=bold, relief="flat",
                    background=SUBTLE, foreground=TEXT, bordercolor=BORDER,
                    lightcolor=SUBTLE, darkcolor=BORDER)
    style.map("TButton",
              background=[("pressed", BORDER), ("active", HOVER), ("disabled", PAGE)],
              foreground=[("disabled", TEXT_SUBTLE)],
              bordercolor=[("focus", PRIMARY)])
    style.configure("Accent.TButton", background=PRIMARY, foreground=INVERT,
                    padding=(16, 9), font=bold)
    style.map("Accent.TButton",
              background=[("pressed", PRIMARY_HOVER), ("active", PRIMARY_HOVER),
                          ("disabled", mix(PRIMARY, PAGE, 0.5))],
              foreground=[("disabled", INVERT)])
    style.configure("Danger.TButton", background=DANGER, foreground=INVERT, font=bold)
    style.map("Danger.TButton", background=[("active", DANGER_HOVER), ("pressed", DANGER_HOVER)])

    # 输入类：背景 #F9FAFB、focus 主色
    for name, extra in (("TEntry", {}), ("TCombobox", {"arrowsize": 14}),
                        ("TSpinbox", {"arrowsize": 10})):
        style.configure(name, padding=7, fieldbackground=SUBTLE, background=SUBTLE,
                        foreground=TEXT, bordercolor=BORDER, lightcolor=SUBTLE,
                        darkcolor=BORDER, insertcolor=TEXT, **extra)
        style.map(name,
                  bordercolor=[("focus", PRIMARY), ("hover", BORDER_STRONG)],
                  lightcolor=[("focus", PRIMARY)],
                  fieldbackground=[("readonly", SUBTLE), ("disabled", PAGE)],
                  foreground=[("disabled", TEXT_SUBTLE)])
    style.map("TCombobox", fieldbackground=[("readonly", SUBTLE)],
              selectbackground=[("readonly", SUBTLE)], selectforeground=[("readonly", TEXT)])
    root.option_add("*TCombobox*Listbox.background", CANVAS)
    root.option_add("*TCombobox*Listbox.foreground", TEXT)
    root.option_add("*TCombobox*Listbox.selectBackground", PRIMARY_SOFT)
    root.option_add("*TCombobox*Listbox.selectForeground", TEXT)

    # 表格：行高跟字体走（高 DPI 下固定像素会把字挤扁），正文比标签略大一点更清楚
    try:
        row_h = tkfont.Font(root=root, font=body_font(11)).metrics("linespace") + ui_px(10)
    except Exception:  # noqa: BLE001
        row_h = ui_px(30)
    row_h = max(ui_px(28), row_h)
    style.configure("Treeview", background=CANVAS, fieldbackground=CANVAS, foreground=TEXT,
                    rowheight=row_h, borderwidth=0, relief="flat", font=body_font(11))
    style.configure("Treeview.Heading", background=PAGE, foreground=TEXT_MUTED,
                    font=heading_font(10), relief="flat",
                    padding=(ui_px(8), ui_px(7)), bordercolor=BORDER)
    style.map("Treeview",
              background=[("selected", PRIMARY)],
              foreground=[("selected", INVERT)],
              fieldbackground=[("disabled", PAGE)])
    style.map("Treeview.Heading", background=[("active", HOVER)],
              foreground=[("active", TEXT)])
    style.configure("Treeview", bordercolor=BORDER_SOFT)

    # 页签
    style.configure("TNotebook", background=PAGE, borderwidth=0, bordercolor=BORDER_SOFT)
    style.configure("TNotebook.Tab", padding=(18, 9), background=PAGE,
                    foreground=TEXT_MUTED, font=body, bordercolor=BORDER_SOFT)
    style.map("TNotebook.Tab",
              background=[("selected", CANVAS), ("active", HOVER)],
              foreground=[("selected", TEXT), ("active", TEXT)],
              expand=[("selected", (0, 0, 0, 0))])

    # 进度条 / 滚动条
    style.configure("Horizontal.TProgressbar", troughcolor=BORDER, background=PRIMARY,
                    bordercolor=BORDER, lightcolor=PRIMARY, darkcolor=PRIMARY, thickness=6)
    style.configure("Vertical.TScrollbar", background=BORDER, troughcolor=PAGE,
                    bordercolor=PAGE, lightcolor=BORDER, darkcolor=BORDER, arrowsize=0,
                    width=8, relief="flat")
    style.map("Vertical.TScrollbar", background=[("active", BORDER_STRONG)])
    style.configure("Horizontal.TScrollbar", background=BORDER, troughcolor=PAGE,
                    bordercolor=PAGE, arrowsize=0, relief="flat")

    # 单选/复选/容器（卡片内用 Card.* 变体，白底）
    for name in ("Card.TRadiobutton", "Card.TCheckbutton"):
        style.configure(name, background=CANVAS, foreground=TEXT, font=body,
                        indicatorcolor=SUBTLE, focuscolor=PRIMARY)
        style.map(name, background=[("active", CANVAS)],
                  indicatorcolor=[("selected", PRIMARY), ("pressed", PRIMARY_HOVER)])
    for name in ("TRadiobutton", "TCheckbutton"):
        style.configure(name, background=PAGE, foreground=TEXT, font=body,
                        indicatorcolor=CANVAS, focuscolor=PRIMARY)
        style.map(name, background=[("active", PAGE)],
                  indicatorcolor=[("selected", PRIMARY), ("pressed", PRIMARY_HOVER)])
    style.configure("TLabelframe", background=PAGE, bordercolor=BORDER_SOFT,
                    relief="flat", borderwidth=1)
    style.configure("TLabelframe.Label", background=PAGE, foreground=TEXT_MUTED, font=bold)
    style.configure("TPanedwindow", background=PAGE, sashwidth=8, sashrelief="flat")

    # 深色 Toplevel 里的 ttk 也保持浅色（避免系统主题串色）
    return style


def style_text(widget: tk.Text, *, mono: bool = False, size: int = 10) -> None:
    """统一 Text 控件的字体/行高/选中色（正文行高 1.5~1.7 由 spacing 近似）。"""
    widget.configure(font=mono_font(size) if mono else body_font(size),
                     bg=CANVAS, fg=TEXT, insertbackground=PRIMARY,
                     selectbackground=PRIMARY_SOFT, selectforeground=TEXT,
                     relief="flat", bd=0, highlightthickness=0,
                     spacing1=2, spacing2=2, spacing3=2,
                     padx=10, pady=8, wrap="word")


def style_listbox(lb: tk.Listbox) -> None:
    lb.configure(font=body_font(10), bg=CANVAS, fg=TEXT, relief="flat", bd=0,
                 highlightthickness=0, selectbackground=PRIMARY_SOFT, selectforeground=TEXT,
                 activestyle="none")
