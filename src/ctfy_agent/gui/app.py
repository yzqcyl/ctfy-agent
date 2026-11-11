# -*- coding: utf-8 -*-
"""离线解题图形界面（中文）。入口：python -m ctfy_agent.gui.app 或双击 run_gui.cmd。
纯新增文件：只调用 hunt 离线引擎，不 import 平台、不改任何现有解题逻辑。
"""
from __future__ import annotations

import json
import queue
import shutil
import socket
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

import tkinter as tk
from tkinter import filedialog, font as tkfont, messagebox, simpledialog, ttk

from ..config import PROJECT_ROOT, load
from ..writeup import build_writeup, build_writeup_blocks, write_writeup_file
from . import limits, platform_worker, profiles, settings, theme, toolmap, worker

CATEGORIES = ["web", "pwn", "rev", "crypto", "misc", "ai"]
DIFFICULTIES = ["easy", "medium", "hard"]

_NOW = lambda: time.strftime("%H:%M:%S")


def _norm_url(u: str) -> str:
    u = str(u).strip()
    if u and "://" not in u:
        u = "http://" + u
    return u


def _can_connect(url: str, timeout: float = 2.0) -> bool:
    try:
        p = urlparse(url if "://" in url else "http://" + url)
        host, port = p.hostname, p.port or (443 if p.scheme == "https" else 80)
        if not host:
            return False
        s = socket.create_connection((host, port), timeout=timeout)
        s.close()
        return True
    except Exception:  # noqa: BLE001
        return False


def _history_entries() -> list[str]:
    """扫描 runs/ 下带 .offline 标记的离线任务目录，按更新时间倒序。"""
    runs = PROJECT_ROOT / "runs"
    if not runs.is_dir():
        return []
    entries = []
    for d in sorted(runs.iterdir(), key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True):
        if d.is_dir() and (d / ".offline").exists():
            entries.append(d.name)
    return entries


class App:
    def __init__(self, root: tk.Tk | None = None):
        # 必须在建 Tk 窗口之前声明 DPI 感知，否则系统先把窗口位图放大，字体就糊了（幂等）
        theme.enable_dpi_awareness()
        self.config = load(None)
        # 离线模式走 OfflinePlatform（不读 token/mcp_url），平台模式走真实平台；
        # 两种模式共用同一份真实配置，所以这里绝不清空 token / 改写 mcp_url。

        self.q: queue.Queue = queue.Queue()
        self.cancel = threading.Event()
        self.thread: threading.Thread | None = None
        self.running = False
        self.started_at = 0.0
        self.run_id = ""
        self.flags: list[str] = []
        self.active_role = ""
        self._writeup_plain = ""
        self._tpos: dict[str, int] = {}
        self.mode = "offline"
        self.comp_choices: dict[str, str] = {}
        self.challenge_rows: list[dict] = []
        self._row_map: dict[str, dict] = {}
        self.graceful = threading.Event()   # 优雅停止：不再开新题，等当前题跑完
        self._board: dict[str, dict] = {}   # 任务面板：cid -> {iid, started}
        self._solved: list[dict] = []
        self._solved_rows: dict[str, dict] = {}

        self.root = root or tk.Tk()
        self.root.title("CTF 自动解题（离线 / 平台）")
        # 按真实 DPI 设 tk scaling：96dpi→1.2（保持旧观感），150%→1.8，字体按物理像素渲染
        self.ui_scale = theme.apply_dpi_scaling(self.root)
        _sw, _sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        _w = max(1100, min(1600, int(_sw * 0.60)))
        _h = max(700, min(980, int(_sh * 0.58)))
        self.root.geometry(f"{_w}x{_h}")
        self.root.minsize(1040, 660)
        self._build_ui()
        self._refresh_history()
        self._refresh_solved(background=True)  # 不卡启动：扫 runs/* 放后台线程
        self._poll_job: str | None = None
        self.root.bind("<Destroy>", lambda _e: self._cancel_poll(), add="+")
        self._poll()

    # ---------------- UI ----------------

    # ---------------- 界面骨架 ----------------

    def _build_ui(self) -> None:
        """毛玻璃 + GitHub 配色骨架（视觉全走 gui/theme.py，业务逻辑不在这里）。"""
        theme.apply(self.root)

        page = tk.Frame(self.root, bg=theme.PAGE)
        page.pack(fill="both", expand=True)

        # ---------- 顶部：毛玻璃标题条（Inter Bold + -0.02em 字距） ----------
        head = theme.Card(page, padding=(22, 12), radius=theme.RADIUS_CARD, glass=True)
        head.pack(fill="x", padx=14, pady=(14, 10))
        hl = tk.Frame(head.body, bg=theme.GLASS)
        hl.pack(side="left", anchor="w")
        brand = tk.Canvas(hl, bg=theme.GLASS, height=34, highlightthickness=0, bd=0)
        brand.pack(anchor="w")
        self._draw_brand(brand)
        tk.Label(hl, text="离线靶场 / 平台接口 · 解题 · 复现 · 任务面板", bg=theme.GLASS,
                 fg=theme.TEXT_MUTED, font=theme.body_font(9)).pack(anchor="w")

        hr = tk.Frame(head.body, bg=theme.GLASS)
        hr.pack(side="right")
        self.lbl_status = tk.StringVar(value="就绪")
        self.lbl_budget = tk.StringVar(value="预算：—")
        self.lbl_elapsed = tk.StringVar(value="耗时：—")
        self.pill_status = theme.Pill(hr, "就绪", color=theme.TEXT_MUTED, bg=theme.HOVER)
        self.pill_status.pack(side="right", padx=(10, 0))
        theme.RoundedButton(hr, text="设置", icon="gear", variant="secondary",
                            command=self._open_settings, pad_y=8, pad_x=16).pack(side="right")
        for _var in (self.lbl_elapsed, self.lbl_budget):
            tk.Label(hr, textvariable=_var, bg=theme.GLASS, fg=theme.TEXT_MUTED,
                     font=theme.body_font(9)).pack(side="right", padx=(0, 14))
        self.lbl_status.trace_add("write", lambda *a: self._sync_status_pill())

        # ---------- 预算进度：卡片里两条细进度条 ----------
        budc = theme.Card(page, padding=(20, 10), radius=16)
        budc.pack(fill="x", padx=14, pady=(0, 10))
        bud = budc.body
        bud.columnconfigure(1, weight=1)
        bud.columnconfigure(3, weight=1)
        self.lbl_tokens = tk.StringVar(value="token 预算：—")
        self.lbl_calls = tk.StringVar(value="调用：—")
        self.lbl_cost = tk.StringVar(value="费用：—")
        tk.Label(bud, textvariable=self.lbl_tokens, bg=theme.CANVAS, fg=theme.TEXT,
                 font=theme.body_font(9)).grid(row=0, column=0, sticky="w")
        self.bar_tokens = ttk.Progressbar(bud, maximum=1000)
        self.bar_tokens.grid(row=0, column=1, sticky="ew", padx=(10, 20))
        tk.Label(bud, textvariable=self.lbl_calls, bg=theme.CANVAS, fg=theme.TEXT,
                 font=theme.body_font(9)).grid(row=0, column=2, sticky="w")
        self.bar_calls = ttk.Progressbar(bud, maximum=1000)
        self.bar_calls.grid(row=0, column=3, sticky="ew", padx=(10, 20))
        tk.Label(bud, textvariable=self.lbl_cost, bg=theme.CANVAS, fg=theme.TEXT_MUTED,
                 font=theme.body_font(9)).grid(row=0, column=4, sticky="w")

        # ---------- 主体：左栏（整栏可滚动） + 右栏页签 ----------
        body = ttk.PanedWindow(page, orient="horizontal")
        body.pack(fill="both", expand=True, padx=14, pady=(0, 12))

        # 左栏 = 可滚动内容 + 固定页脚（开始/停止永远可见，不用滚动找按钮）
        left_holder = tk.Frame(body, bg=theme.PAGE)
        body.add(left_holder, weight=0)
        left_foot = tk.Frame(left_holder, bg=theme.PAGE)
        left_foot.pack(side="bottom", fill="x", padx=10, pady=(6, 8))
        left_sf = theme.ScrollFrame(left_holder, bg=theme.PAGE)
        left_sf.canvas.configure(width=theme.ui_px(372))
        left_sf.pack(side="top", fill="both", expand=True)
        left = left_sf.inner
        side = {"padx": 10}

        # 模式卡
        mcard = theme.Card(left, padding=(16, 12), radius=16)
        mcard.pack(fill="x", pady=(0, 10), **side)
        mb = mcard.body
        tk.Label(mb, text="模式", bg=theme.CANVAS, fg=theme.TEXT,
                 font=theme.heading_font(10)).pack(anchor="w")
        mrow = tk.Frame(mb, bg=theme.CANVAS)
        mrow.pack(fill="x", pady=(6, 0))
        self.var_mode = tk.StringVar(value="offline")
        ttk.Radiobutton(mrow, text="离线靶场", variable=self.var_mode, value="offline",
                        command=self._on_mode, style="Card.TRadiobutton").pack(side="left")
        ttk.Radiobutton(mrow, text="平台接口", variable=self.var_mode, value="platform",
                        command=self._on_mode,
                        style="Card.TRadiobutton").pack(side="left", padx=(16, 0))

        # 表单卡（离线 / 平台两套表单切换）
        fcard = theme.Card(left, padding=(16, 12), radius=16)
        fcard.pack(fill="x", pady=(0, 10), **side)
        self.frm_form = tk.Frame(fcard.body, bg=theme.CANVAS)
        self.frm_form.pack(fill="x")

        # ---- 离线表单 ----
        self.frm_off = tk.Frame(self.frm_form, bg=theme.CANVAS)
        self.frm_off.pack(fill="x")
        frm_new = self.frm_off
        theme.card_title(frm_new, "离线靶场", subtitle="人工起靶，地址每行一个").pack(anchor="w")
        theme.label(frm_new, "目标地址（每行一个）", muted=True,
                    size=9).pack(anchor="w", pady=(10, 0))
        c_urls = theme.Card(frm_new, radius=theme.RADIUS_INPUT, padding=(1, 1))
        c_urls.pack(fill="x", pady=(4, 0))
        self.txt_urls = theme.textbox(c_urls.body, height=2, mono=True, size=9)
        self.txt_urls.pack(fill="x")
        row1 = tk.Frame(frm_new, bg=theme.CANVAS)
        row1.pack(fill="x", pady=(10, 0))
        theme.label(row1, "题型", muted=True, size=9).pack(side="left")
        self.cb_cat = ttk.Combobox(row1, values=CATEGORIES, width=8, state="readonly")
        self.cb_cat.set("web")
        self.cb_cat.pack(side="left", padx=(6, 0))
        theme.label(row1, "难度", muted=True, size=9).pack(side="left", padx=(16, 0))
        self.cb_diff = ttk.Combobox(row1, values=DIFFICULTIES, width=8, state="readonly")
        self.cb_diff.set("medium")
        self.cb_diff.pack(side="left", padx=(6, 0))
        theme.label(frm_new, "题名（可选）", muted=True, size=9).pack(anchor="w", pady=(10, 0))
        self.var_name = tk.StringVar()
        theme.RoundedEntry(frm_new, textvariable=self.var_name,
                           placeholder="例如 ez_web").pack(fill="x", pady=(4, 0))
        theme.label(frm_new, "题面（可选，文本框或选文件）", muted=True,
                    size=9).pack(anchor="w", pady=(10, 0))
        c_brief = theme.Card(frm_new, radius=theme.RADIUS_INPUT, padding=(1, 1))
        c_brief.pack(fill="x", pady=(4, 0))
        self.txt_brief = theme.textbox(c_brief.body, height=4, size=9)
        self.txt_brief.pack(fill="x")
        theme.RoundedButton(frm_new, text="从文件载入题面", icon="folder", variant="ghost",
                            pad_x=12, pad_y=7, command=self._load_brief).pack(
            anchor="w", pady=(6, 0))
        theme.label(frm_new, "运行名（run-id）", muted=True,
                    size=9).pack(anchor="w", pady=(8, 0))
        self.var_run = tk.StringVar(value="hunt")
        theme.RoundedEntry(frm_new, textvariable=self.var_run).pack(fill="x", pady=(4, 0))
        self.var_started = tk.BooleanVar(value=True)
        ttk.Checkbutton(frm_new, text="我已人工启动靶场", variable=self.var_started,
                        style="Card.TCheckbutton").pack(anchor="w", pady=(10, 0))
        # 开始/停止按钮统一放在左栏固定页脚（见下方 left_foot），表单里不再重复放

        # ---- 平台表单（默认隐藏） ----
        self.frm_plat = tk.Frame(self.frm_form, bg=theme.CANVAS)
        theme.card_title(self.frm_plat, "平台接口",
                         subtitle="自动起靶 + 自动提交").pack(anchor="w")
        self.lbl_conn = tk.StringVar(value="未连接")
        theme.label(self.frm_plat, "平台档案", muted=True,
                    size=9).pack(anchor="w", pady=(10, 0))
        self.cb_prof = ttk.Combobox(self.frm_plat, state="readonly")
        self.cb_prof.pack(fill="x", pady=(4, 0))
        self.cb_prof.bind("<<ComboboxSelected>>", self._on_profile_change)
        self._refresh_profiles()
        rc = tk.Frame(self.frm_plat, bg=theme.CANVAS)
        rc.pack(fill="x", pady=(8, 0))
        theme.RoundedButton(rc, text="连接平台", icon="bolt", variant="secondary",
                            pad_y=8, pad_x=16, command=self._connect_platform).pack(side="left")
        self.spin_conn = theme.Spinner(rc)
        theme.label(rc, textvariable=self.lbl_conn, muted=True,
                    size=9).pack(side="left", padx=(8, 0))

        theme.label(self.frm_plat, "比赛", muted=True, size=9).pack(anchor="w", pady=(10, 0))
        self.cb_comp = ttk.Combobox(self.frm_plat, state="readonly")
        self.cb_comp.pack(fill="x", pady=(4, 0))

        theme.RoundedButton(self.frm_plat, text="刷新题单", icon="refresh",
                            variant="secondary", pad_y=8, pad_x=16,
                            command=self._fetch_challenges).pack(fill="x", pady=(8, 0))
        theme.label(self.frm_plat, "题库（勾选要跑的题，可全选）", muted=True,
                    size=9).pack(anchor="w", pady=(10, 0))
        chal_panel = theme.panel(self.frm_plat, height=theme.ui_px(236))
        chal_panel.pack(fill="x", pady=(4, 0))
        # 上下 + 左右滚动条都要：题名很长（如 "Layered Archive — 套娃压缩包"），
        # 只给竖条会把名称截断，横条让整行可以拖着看全。
        chal_panel.rowconfigure(0, weight=1)
        chal_panel.columnconfigure(0, weight=1)
        self.tv = ttk.Treeview(chal_panel, columns=("sel", "id", "cat", "name"),
                               show="headings", height=8)
        # 列宽固定（不 stretch）：总宽超出可视区时横条才有意义，名称不再被硬截断；
        # 取「比可视区略宽」的尺寸，既不把左栏撑到挤压右栏，又保证横条真的能滚
        for col, width, text in (("sel", 38, "选"), ("id", 110, "ID"),
                                 ("cat", 58, "分类"), ("name", 180, "名称")):
            self.tv.heading(col, text=text)
            self.tv.column(col, width=theme.ui_px(width),
                           minwidth=theme.ui_px(max(28, width - 12)),
                           anchor="center" if col == "sel" else "w",
                           stretch=False)
        self.vsb_chal = ttk.Scrollbar(chal_panel, orient="vertical", command=self.tv.yview)
        self.hsb_chal = ttk.Scrollbar(chal_panel, orient="horizontal", command=self.tv.xview)
        self.tv.configure(yscrollcommand=self.vsb_chal.set,
                          xscrollcommand=self.hsb_chal.set)
        self.tv.grid(row=0, column=0, sticky="nsew")
        self.vsb_chal.grid(row=0, column=1, sticky="ns")
        self.hsb_chal.grid(row=1, column=0, sticky="ew")
        self.tv.bind("<Button-1>", self._toggle_row)
        self.skel_chal = theme.Skeleton(chal_panel, rows=5, row_height=20, gap=8)

        sb = tk.Frame(self.frm_plat, bg=theme.CANVAS)
        sb.pack(fill="x", pady=(6, 0))
        theme.RoundedButton(sb, text="全选", variant="ghost", pad_x=12, pad_y=6,
                            command=lambda: self._select_all(True)).pack(side="left")
        theme.RoundedButton(sb, text="全不选", variant="ghost", pad_x=12, pad_y=6,
                            command=lambda: self._select_all(False)).pack(
            side="left", padx=(6, 0))
        self.lbl_sel_chal = tk.StringVar(value="已勾选 0 道")
        tk.Label(sb, textvariable=self.lbl_sel_chal, bg=theme.CANVAS, fg=theme.TEXT_MUTED,
                 font=theme.body_font(9)).pack(side="left", padx=(10, 0))

        # 跑题库选项：跳过已解出 / 重跑已解出
        opt = tk.Frame(self.frm_plat, bg=theme.CANVAS)
        opt.pack(fill="x", pady=(8, 0))
        tk.Label(opt, text="跑题库", bg=theme.CANVAS, fg=theme.TEXT_MUTED,
                 font=theme.body_font(9)).pack(side="left")
        self.var_skip_solved = tk.BooleanVar(value=True)
        ttk.Radiobutton(opt, text="跳过已解出（省 token）", variable=self.var_skip_solved,
                        value=True, style="Card.TRadiobutton",
                        command=self._update_sel_label).pack(side="left", padx=(8, 0))
        ttk.Radiobutton(opt, text="重跑已解出（真重跑）", variable=self.var_skip_solved,
                        value=False, style="Card.TRadiobutton",
                        command=self._update_sel_label).pack(side="left", padx=(10, 0))
        theme.label(self.frm_plat,
                    "重跑会忽略平台/本地「已解出」记录重新解题，会真实消耗 token；"
                    "已有结论的题在任务面板显示为「已解出（跳过）」。",
                    muted=True, size=9).pack(anchor="w", pady=(2, 0))

        theme.label(self.frm_plat, "运行名（run-id）", muted=True,
                    size=9).pack(anchor="w", pady=(10, 0))
        self.var_run_p = tk.StringVar(value="plat")
        theme.RoundedEntry(self.frm_plat,
                           textvariable=self.var_run_p).pack(fill="x", pady=(4, 0))

        theme.label(self.frm_plat, "并发度（勾选多题时并行求解）", muted=True,
                    size=9).pack(anchor="w", pady=(10, 0))
        cf = tk.Frame(self.frm_plat, bg=theme.CANVAS)
        cf.pack(fill="x", pady=(4, 0))
        self.var_conc = tk.IntVar(value=1)
        self.sp_conc = ttk.Spinbox(cf, from_=1, to=3, width=5, textvariable=self.var_conc)
        self.sp_conc.pack(side="left")
        self.lbl_conc = tk.StringVar(value="")
        tk.Label(cf, textvariable=self.lbl_conc, bg=theme.CANVAS, fg=theme.TEXT_SUBTLE,
                 font=theme.body_font(9)).pack(side="left", padx=(8, 0))
        self._refresh_conc_limit()

        # 左栏固定页脚：离线/平台两套「开始/停止」按钮，按模式切换显示
        theme.separator(left_foot).pack(fill="x", pady=(0, 8))
        self.foot_off = tk.Frame(left_foot, bg=theme.PAGE)
        self.btn_start = theme.RoundedButton(self.foot_off, text="开始", icon="play",
                                             variant="primary", command=self._start)
        self.btn_start.pack(side="left", fill="x", expand=True)
        self.btn_stop = theme.RoundedButton(self.foot_off, text="停止", icon="stop",
                                            variant="secondary", command=self._stop)
        self.btn_stop.config(state="disabled")
        self.btn_stop.pack(side="left", fill="x", expand=True, padx=(8, 0))
        self.foot_off.pack(fill="x")

        self.foot_plat = tk.Frame(left_foot, bg=theme.PAGE)
        self.btn_start_p = theme.RoundedButton(self.foot_plat, text="开始", icon="play",
                                               variant="primary", command=self._start)
        self.btn_start_p.pack(side="left", fill="x", expand=True)
        self.btn_stop_p = theme.RoundedButton(self.foot_plat, text="停止", icon="stop",
                                              variant="secondary", command=self._stop)
        self.btn_stop_p.config(state="disabled")
        self.btn_stop_p.pack(side="left", fill="x", expand=True, padx=(8, 0))

        # ---------- 任务历史卡 ----------
        hcard = theme.Card(left, padding=(16, 12), radius=16)
        hcard.pack(fill="both", expand=True, **side)
        hb2 = hcard.body
        theme.card_title(hb2, "任务历史").pack(anchor="w")
        theme.label(hb2, "双击查看；续跑沿用原 run-id", muted=True,
                    size=9).pack(anchor="w", pady=(2, 6))
        lh = tk.Frame(hb2, bg=theme.CANVAS)
        lh.pack(fill="both", expand=True)
        self.lb_hist = tk.Listbox(lh, height=8)
        theme.style_listbox(self.lb_hist)
        hsb = ttk.Scrollbar(lh, orient="vertical", command=self.lb_hist.yview)
        self.lb_hist.configure(yscrollcommand=hsb.set)
        hsb.pack(side="right", fill="y")
        self.lb_hist.pack(side="left", fill="both", expand=True)
        self.lb_hist.bind("<Double-Button-1>", lambda e: self._view_history())
        hb3 = tk.Frame(hb2, bg=theme.CANVAS)
        hb3.pack(fill="x", pady=(8, 0))
        for text, cmd in (("刷新", self._refresh_history), ("查看", self._view_history),
                          ("续跑", self._resume_history), ("删除", self._del_history)):
            theme.RoundedButton(hb3, text=text, variant="secondary", pad_x=10, pad_y=6,
                                command=cmd).pack(side="left", fill="x", expand=True,
                                                  padx=(0, 6))

        # ---------- 右栏：复现 / 任务面板 / 日志 ----------
        right = tk.Frame(body, bg=theme.PAGE)
        body.add(right, weight=1)
        # 圆角胶囊页签：选中蓝底白字、未选中白底深字、页签之间留空隙（ttk.Notebook 做不到）
        nb = theme.TabBar(right, pad=(10, 10))
        nb.pack(fill="both", expand=True)
        self.nb = nb

        self._build_writeup_tab(nb)
        self._build_board_tab(nb)
        self.txt_events = self._add_text_tab(nb, "日志")
        # 兼容旧调用：工具流与过程都并入同一份日志，不再各占一个页签
        self.txt_tools = self.txt_events
        self.txt_process = self.txt_events

        theme.separator(right).pack(fill="x", pady=(0, 8))
        fsub = tk.Frame(right, bg=theme.PAGE)
        fsub.pack(fill="x", pady=(0, 0))
        theme.RoundedButton(fsub, text="复制选中 flag", icon="copy", variant="secondary",
                            pad_x=14, pad_y=7, command=self._copy_flag).pack(side="left")
        theme.RoundedButton(fsub, text="复制复现稿", icon="copy", variant="secondary",
                            pad_x=14, pad_y=7, command=self._copy_writeup).pack(
            side="left", padx=(8, 0))
        theme.RoundedButton(fsub, text="导出报告", icon="export", variant="primary",
                            pad_x=14, pad_y=7, command=self._export_report).pack(
            side="left", padx=(8, 0))

    def _draw_brand(self, canvas: tk.Canvas) -> None:
        """标题逐字绘制：实现 -0.02em 字距（Tk 字体没有 letter-spacing API）。"""
        text = "CTF 自动解题"
        size = 19
        f = tkfont.Font(root=canvas, font=theme.heading_font(size))
        tracking = theme.LETTER_SPACING_EM * size  # em → px 近似（1em≈字号 pt）
        x = 2.0
        for ch in text:
            canvas.create_text(x, 17, text=ch, anchor="w", fill=theme.TEXT, font=f)
            x += f.measure(ch) + tracking
        canvas.configure(width=max(80, int(x) + 4))

    def _sync_status_pill(self) -> None:
        text = (self.lbl_status.get() or "就绪").strip()
        if any(k in text for k in ("出错", "错误", "失败")):
            color, bg = theme.DANGER, theme.DANGER_SOFT
        elif any(k in text for k in ("运行", "解题中", "连接中", "拉取", "生成")):
            color, bg = theme.PRIMARY, theme.PRIMARY_SOFT
        elif any(k in text for k in ("完成", "已解出", "拿到")):
            color, bg = theme.SUCCESS, theme.SUCCESS_SOFT
        else:
            color, bg = theme.TEXT_MUTED, theme.HOVER
        self.pill_status.set(text[:18], color=color, bg=bg)

    def _add_text_tab(self, nb, title: str) -> tk.Text:
        f = tk.Frame(nb.body, bg=theme.PAGE)
        nb.add(f, text=title)
        card = theme.Card(f, padding=(2, 2), radius=16, hug=False)
        card.pack(fill="both", expand=True, padx=10, pady=10)
        txt = theme.textbox(card.body, height=10, mono=True, size=10)
        sb = ttk.Scrollbar(card.body, orient="vertical", command=txt.yview)
        txt.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        txt.pack(side="left", fill="both", expand=True)
        return txt

    # ---------------- 复现：已解出列表 + 按需生成 ----------------

    def _build_writeup_tab(self, nb) -> None:
        f = tk.Frame(nb.body, bg=theme.PAGE)
        nb.add(f, text="复现")
        bar = tk.Frame(f, bg=theme.PAGE)
        bar.pack(fill="x", padx=10, pady=(10, 6))
        theme.RoundedButton(bar, text="刷新已解出", icon="refresh", variant="secondary",
                            pad_y=8, pad_x=16,
                            command=self._refresh_solved).pack(side="left")
        theme.RoundedButton(bar, text="生成/查看复现", icon="bolt", variant="primary",
                            pad_y=8, pad_x=16,
                            command=self._gen_writeup_selected).pack(side="left", padx=(8, 0))
        self.lbl_solved = tk.StringVar(
            value="已解出 0 题（选中后点「生成/查看复现」，不点不生成）")
        tk.Label(bar, textvariable=self.lbl_solved, bg=theme.PAGE, fg=theme.TEXT_MUTED,
                 font=theme.body_font(9)).pack(side="left", padx=(12, 0))
        self.spin_solved = theme.Spinner(bar)
        self.spin_solved.pack(side="left", padx=(6, 0))

        theme.separator(f).pack(fill="x", padx=10, pady=(0, 8))
        pane = ttk.PanedWindow(f, orient="horizontal")
        pane.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        lcard = theme.Card(pane, padding=(2, 2), radius=16, hug=False)
        pane.add(lcard, weight=2)
        # tree 列放「日期分组标题」，题目作为子节点 → 按日期分层
        lbody = lcard.body
        lbody.rowconfigure(0, weight=1)
        lbody.columnconfigure(0, weight=1)
        self.tv_solved = ttk.Treeview(lbody, columns=("cat", "flag", "run"),
                                      show="tree headings", height=12, selectmode="browse")
        self.tv_solved.heading("#0", text="日期 / 题目")
        self.tv_solved.column("#0", width=theme.ui_px(190), stretch=False, anchor="w")
        for col, width, text in (("cat", 54, "分类"), ("flag", 210, "flag"),
                                 ("run", 90, "运行")):
            self.tv_solved.heading(col, text=text)
            self.tv_solved.column(col, width=theme.ui_px(width), anchor="w",
                                  stretch=False)
        self.vsb_solved = ttk.Scrollbar(lbody, orient="vertical",
                                        command=self.tv_solved.yview)
        self.hsb_solved = ttk.Scrollbar(lbody, orient="horizontal",
                                        command=self.tv_solved.xview)
        self.tv_solved.configure(yscrollcommand=self.vsb_solved.set,
                                 xscrollcommand=self.hsb_solved.set)
        self.tv_solved.grid(row=0, column=0, sticky="nsew")
        self.vsb_solved.grid(row=0, column=1, sticky="ns")
        self.hsb_solved.grid(row=1, column=0, sticky="ew")
        self.tv_solved.bind("<Double-Button-1>", lambda e: self._gen_writeup_selected())
        self.skel_solved = theme.Skeleton(lcard.body, rows=7, row_height=22, gap=9)

        rcard = theme.Card(pane, padding=(2, 2), radius=16, hug=False)
        pane.add(rcard, weight=3)
        self.txt_writeup = theme.textbox(rcard.body, height=10, mono=True, size=10)
        sbr = ttk.Scrollbar(rcard.body, orient="vertical", command=self.txt_writeup.yview)
        self.txt_writeup.configure(yscrollcommand=sbr.set)
        sbr.pack(side="right", fill="y")
        self.txt_writeup.pack(side="left", fill="both", expand=True)
        self._config_writeup_tags()
        self.txt_writeup.insert(
            "1.0",
            "左侧按日期分层列出已解出的题目；选中一题点「生成/查看复现」才会生成正文。\n")

    def _refresh_solved(self, background: bool = False) -> None:
        """扫描 runs/* 收集「已解出」的题（只读 state.json，不生成复现、不花 token）。

        [接手 2026-11-10] 两处修正：
        1) 启动时放后台线程扫（实测 600+ run 目录约 1s，直接在 __init__ 里扫会卡住窗口）；
        2) runs/ 下有历史遗留的不可读目录（早期 pip/临时目录用 0o700 建、创建者都读不了），
           原来只 try 住 read_text，`is_file()` 上抛 PermissionError 会让整个刷新崩掉。
        """
        self._solved_loading(True)
        if background:
            def _bg() -> None:
                rows = self._scan_solved()
                try:
                    self.after(0, self._apply_solved, rows)
                except (tk.TclError, RuntimeError, AttributeError):  # 窗口已关 / selftest 桩对象
                    pass

            threading.Thread(target=_bg, name="scan-solved", daemon=True).start()
            return
        self._apply_solved(self._scan_solved())

    def _solved_loading(self, on: bool) -> None:
        """骨架屏：扫描期间盖住列表（脉冲动画），扫完收起。"""
        try:
            if on:
                self.skel_solved.place(relx=0, rely=0, relwidth=1, relheight=1)
                self.skel_solved.lift()
                self.skel_solved.start()
                self.spin_solved.start()
                self.lbl_solved.set("扫描 runs/ 中…")
            else:
                self.skel_solved.stop()
                self.skel_solved.place_forget()
                self.spin_solved.stop()
        except (tk.TclError, AttributeError):
            pass

    def _scan_solved(self) -> list[dict]:
        """纯扫描：读 runs/*/state.json 收集已解出条目（不碰界面，可放线程里跑）。"""
        rows: list[dict] = []
        base = PROJECT_ROOT / "runs"
        try:
            entries = list(base.iterdir()) if base.is_dir() else []
        except OSError:
            return rows
        for d in entries:
            try:
                if not d.is_dir():
                    continue
                st = d / "state.json"
                if not st.is_file():
                    continue
                data = json.loads(st.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError):  # 不可读/半截文件：跳过，不拖垮刷新
                continue
            for cid, ch in (data.get("challenges") or {}).items():
                if not isinstance(ch, dict):
                    continue
                flag = str(ch.get("flag_value") or "")
                if not (ch.get("submitted_ok") or flag):
                    continue
                rows.append({
                    "run": d.name, "cid": cid,
                    "name": str(ch.get("name") or cid),
                    "cat": str(ch.get("category") or "-"),
                    "flag": flag,
                    "at": str(ch.get("updated_at") or d.stat().st_mtime),
                })
        rows.sort(key=lambda r: str(r["at"]), reverse=True)
        return rows

    def _apply_solved(self, rows: list[dict]) -> None:
        """把扫描结果按日期分层填进列表（必须在主线程调用）。"""
        self._solved = rows
        tv = self.tv_solved
        tv.delete(*tv.get_children())
        self._solved_rows = {}
        days = theme.group_by_date(rows)
        total = 0
        for day, items in days:
            node = tv.insert("", "end", text=theme.date_header_text(day, len(items)),
                             open=True, tags=("date",))
            for r in items:
                iid = tv.insert(node, "end", text=r["name"],
                                values=(r["cat"], r["flag"] or "（未记录）", r["run"]))
                self._solved_rows[iid] = r
                total += 1
        tv.tag_configure("date", font=theme.heading_font(10),
                         foreground=theme.TEXT_MUTED)
        if total:
            self.lbl_solved.set(
                f"已解出 {total} 题 · {len(days)} 个日期（选中后点「生成/查看复现」）")
        else:
            self.lbl_solved.set("未发现已解出的题目（跑完一题后点「刷新已解出」）")
        self._solved_loading(False)

    def _gen_writeup_selected(self) -> None:
        sel = self.tv_solved.selection()
        if not sel:
            messagebox.showinfo("复现", "先在左侧选一道已解出的题。")
            return
        row = self._solved_rows.get(sel[0])
        if not row:
            messagebox.showinfo("复现", "请选择具体题目（日期分组标题下面那一行）。")
            return
        # 运行中不能改 self.run_id：_tail_transcript / _export_report 都靠它定位当前 run，
        # 改了会让过程日志断流、导出写到历史目录去。
        if self.running:
            self._set_status(f"复现（查看）：{row['name']}（运行中，不切换当前目录）")
        else:
            self.run_id = row["run"]
            self._set_status(f"复现：{row['name']}")
        self._show_writeup(row["run"], row["cid"])

    # ---------------- 任务面板（动态表） ----------------

    _BOARD_COLS = ("title", "status", "detail", "rounds", "tokens", "cost", "elapsed")
    _BOARD_HEADS = {"title": "题目", "status": "状态", "detail": "详情", "rounds": "轮次",
                    "tokens": "Tokens", "cost": "花费", "elapsed": "用时"}

    def _build_board_tab(self, nb) -> None:
        f = tk.Frame(nb.body, bg=theme.PAGE)
        nb.add(f, text="任务面板")
        bar = tk.Frame(f, bg=theme.PAGE)
        bar.pack(fill="x", padx=10, pady=(10, 6))
        self.lbl_board = tk.StringVar(value="还没有任务")
        tk.Label(bar, textvariable=self.lbl_board, bg=theme.PAGE, fg=theme.TEXT_MUTED,
                 font=theme.body_font(9)).pack(side="left")
        theme.RoundedButton(bar, text="清空", icon="trash", variant="secondary",
                            pad_y=8, pad_x=16,
                            command=self._board_clear).pack(side="right")
        card = theme.Card(f, padding=(2, 2), radius=16, hug=False)
        card.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        body = card.body
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)
        widths = {"title": 150, "status": 78, "detail": 280, "rounds": 58,
                  "tokens": 78, "cost": 66, "elapsed": 72}
        self.tv_board = ttk.Treeview(body, columns=self._BOARD_COLS,
                                     show="headings", height=14)
        for col in self._BOARD_COLS:
            self.tv_board.heading(col, text=self._BOARD_HEADS[col])
            self.tv_board.column(col, width=theme.ui_px(widths[col]),
                                 minwidth=theme.ui_px(max(30, widths[col] - 20)),
                                 stretch=False,
                                 anchor="w" if col in ("title", "detail") else "center")
        # 上下 + 左右滚动条：详情/题目名较长时，横条能看全，不被硬截断
        self.vsb_board = ttk.Scrollbar(body, orient="vertical",
                                       command=self.tv_board.yview)
        self.hsb_board = ttk.Scrollbar(body, orient="horizontal",
                                       command=self.tv_board.xview)
        self.tv_board.configure(yscrollcommand=self.vsb_board.set,
                                xscrollcommand=self.hsb_board.set)
        self.tv_board.grid(row=0, column=0, sticky="nsew")
        self.vsb_board.grid(row=0, column=1, sticky="ns")
        self.hsb_board.grid(row=1, column=0, sticky="ew")

    def _board_clear(self) -> None:
        for iid in self.tv_board.get_children():
            self.tv_board.delete(iid)
        self._board.clear()
        self.lbl_board.set("还没有任务")

    # 这些状态表示这一题已经停了：用时列必须冻结，不跟着整批 run 一直走
    _BOARD_FINAL = ("已解出", "已解出（跳过）", "未解出", "已取消", "出错")

    def _board_row(self, cid: str, title: str = "") -> dict:
        rec = self._board.get(cid)
        if rec is None:
            iid = self.tv_board.insert(
                "", "end", values=(title or cid[:8], "待开始", "", 0, 0, "-", "00:00"))
            rec = {"iid": iid, "started": time.monotonic(), "finished": None}
            self._board[cid] = rec
            self.lbl_board.set(f"共 {len(self._board)} 题")
        elif title:
            self._board_set(cid, title=title)
        return rec

    def _board_set(self, cid: str, **kw) -> None:
        rec = self._board.get(cid)
        if not rec:
            return
        vals = list(self.tv_board.item(rec["iid"], "values"))
        status = kw.get("status")
        if status in self._BOARD_FINAL and not rec.get("finished"):
            # 第一次进入终态：记下停表时刻，并把用时列就地定死
            rec["finished"] = time.monotonic()
            sec = int(rec["finished"] - float(rec.get("started") or rec["finished"]))
            vals[self._BOARD_COLS.index("elapsed")] = f"{sec // 60:02d}:{sec % 60:02d}"
        elif status == "解题中":
            rec["started"] = time.monotonic()
            rec["finished"] = None
        for key, value in kw.items():
            if key in self._BOARD_COLS:
                vals[self._BOARD_COLS.index(key)] = value
        self.tv_board.item(rec["iid"], values=vals)

    def _board_bump_round(self, cid: str) -> None:
        rec = self._board.get(cid)
        if not rec:
            return
        vals = list(self.tv_board.item(rec["iid"], "values"))
        vals[self._BOARD_COLS.index("rounds")] = int(vals[self._BOARD_COLS.index("rounds")] or 0) + 1
        self.tv_board.item(rec["iid"], values=vals)

    def _board_tick(self) -> None:
        """运行中刷新「用时」列：已到终态的题不再计时（否则会出现「已解出还在走表」）。"""
        idx = self._BOARD_COLS.index("elapsed")
        now = time.monotonic()
        for rec in self._board.values():
            if rec.get("finished"):
                continue
            vals = list(self.tv_board.item(rec["iid"], "values"))
            sec = int(now - float(rec.get("started") or now))
            vals[idx] = f"{sec // 60:02d}:{sec % 60:02d}"
            self.tv_board.item(rec["iid"], values=vals)

    # ---------------- 历史 / 输入 ----------------

    def _load_brief(self) -> None:
        path = filedialog.askopenfilename(
            title="选择题面文件", filetypes=[("文本/Markdown", "*.md *.txt"), ("所有文件", "*.*")]
        )
        if path:
            self.txt_brief.delete("1.0", "end")
            self.txt_brief.insert("1.0", Path(path).read_text(encoding="utf-8", errors="replace"))

    def _refresh_history(self) -> None:
        self.lb_hist.delete(0, "end")
        for name in _history_entries():
            self.lb_hist.insert("end", name)

    def _selected_run(self) -> str:
        sel = self.lb_hist.curselection()
        return self.lb_hist.get(sel[0]) if sel else ""

    def _view_history(self) -> None:
        rid = self._selected_run()
        if not rid:
            return
        if not self.running:
            self.run_id = rid  # 运行中不覆盖，避免打断过程日志/导出
        m = PROJECT_ROOT / "runs" / rid / ".offline"
        if m.exists():
            try:
                d = json.loads(m.read_text(encoding="utf-8"))
                self._set_status("查看历史：" + str(d.get("name") or rid))
            except Exception:  # noqa: BLE001
                pass
        self._refresh_solved()
        self._show_writeup(rid)
        self.nb.select(0)

    def _resume_history(self) -> None:
        rid = self._selected_run()
        if not rid or self.running:
            return
        if not messagebox.askyesno("续跑", f"在 run {rid!r} 上继续解题？（沿用已累计的用量与状态）"):
            return
        self.run_id = rid
        self._begin_thread(worker.resume, (self.config, rid, self.q, self.cancel))

    def _del_history(self) -> None:
        rid = self._selected_run()
        if not rid:
            return
        if self.running and rid == self.run_id:
            messagebox.showwarning("删除", "该任务正在运行，请先停止。")
            return
        if not messagebox.askyesno("删除任务", f"确定删除 run {rid!r}？（连同 flag / state / 转录，不可恢复）"):
            return
        shutil.rmtree(PROJECT_ROOT / "runs" / rid, ignore_errors=True)
        self._refresh_history()
        self._set_status("已删除 " + rid)

    # ---------------- 运行 ----------------

    def _collect_urls(self) -> list[str]:
        raw = [ln.strip() for ln in self.txt_urls.get("1.0", "end").splitlines() if ln.strip()]
        return [_norm_url(u) for u in raw]

    def _start(self) -> None:
        if self.running:
            return
        if self.mode == "platform":
            self._start_platform()
        else:
            self._start_offline()

    def _start_offline(self) -> None:
        urls = self._collect_urls()
        if not urls:
            messagebox.showwarning("缺少目标", "请至少填一个靶场地址。")
            return
        # 连通性预检（可选，未勾选“已启动”也照样提示，但给机会强制开始）
        bad = [u for u in urls if not _can_connect(u)]
        if bad and not messagebox.askyesno(
            "目标不可达",
            "以下地址连不上，确认已人工启动靶场后仍要开始吗？\n" + "\n".join("  " + u for u in bad),
        ):
            return

        spec = {
            "urls": urls,
            "category": self.cb_cat.get(),
            "difficulty": self.cb_diff.get(),
            "name": self.var_name.get().strip(),
            "brief": self.txt_brief.get("1.0", "end").strip(),
            "run_id": self.var_run.get().strip() or "hunt",
        }
        self.run_id = spec["run_id"]
        self._begin_thread(worker.start_hunt, (self.config, spec, self.q, self.cancel))

    def _begin_thread(self, fn, args) -> None:
        self.cancel.clear()
        self.graceful.clear()
        self.running = True
        self.started_at = time.monotonic()
        self.flags = []
        self.active_role = ""
        for box in (self.txt_events, self.txt_writeup):
            box.delete("1.0", "end")
        self._board_clear()
        self._tpos = {}
        self._reset_budget()
        if not self._priced():
            self._log("[提示] 未配置单价（agent.json 的 price_input/output_per_mtok=0）："
                      "任务面板「花费」列显示 -，填了单价后自动按 token 计价。")
        self._set_status("运行中")
        self._set_running(True)
        self.thread = threading.Thread(target=fn, args=args, daemon=True)
        self.thread.start()
        self._refresh_history()
        self.nb.select(1)  # 切到「任务面板」看实时状态

    def _stop(self) -> None:
        if not self.running:
            return
        mode = self._ask_stop_mode()
        if mode == "now":
            self.graceful.clear()
            self.cancel.set()
            self._set_status("强制停止中（正在解的题会被立即掐断）")
        elif mode == "after":
            self.graceful.set()
            self._set_status("当前题解完后停止（不再开新题）")

    def _ask_stop_mode(self) -> str:
        """停止方式对话框：返回 'now'（立即）/ 'after'（解完当前）/ ''（取消）。"""
        win = tk.Toplevel(self.root)
        win.title("停止")
        win.transient(self.root)
        win.resizable(False, False)
        try:
            win.grab_set()
        except Exception:  # noqa: BLE001
            pass
        ttk.Label(win, text="要怎么停？", font=("", 11, "bold")).pack(anchor="w", padx=16, pady=(14, 4))
        ttk.Label(
            win, foreground="#555", justify="left",
            text="· 立即停止：强制掐断正在解的题，马上结束\n"
                 "· 解完再停：不再开新题，等当前这道跑完再停",
        ).pack(anchor="w", padx=16)
        choice = {"v": ""}
        btns = ttk.Frame(win)
        btns.pack(fill="x", padx=16, pady=14)

        def pick(value: str) -> None:
            choice["v"] = value
            win.destroy()

        ttk.Button(btns, text="立即停止", command=lambda: pick("now")).pack(
            side="left", fill="x", expand=True)
        ttk.Button(btns, text="解完再停", command=lambda: pick("after")).pack(
            side="left", fill="x", expand=True, padx=(8, 0))
        ttk.Button(btns, text="取消", command=win.destroy).pack(side="left", padx=(8, 0))
        win.update_idletasks()
        win.wait_window()
        return choice["v"]

    # ---------------- 模式 / 平台接口 ----------------

    def _on_mode(self) -> None:
        if self.running:
            self.var_mode.set(self.mode)  # 运行中禁止切模式
            return
        self.mode = self.var_mode.get()
        if self.mode == "offline":
            self.frm_plat.pack_forget()
            self.frm_off.pack(fill="x")
            self.foot_plat.pack_forget()
            self.foot_off.pack(fill="x")
        else:
            self.frm_off.pack_forget()
            self.frm_plat.pack(fill="x")
            self.foot_off.pack_forget()
            self.foot_plat.pack(fill="x")

    def _connect_platform(self) -> None:
        if self.running:
            return
        self._apply_profile(self.cb_prof.get())
        self.lbl_conn.set("连接中…")
        try:
            self.spin_conn.start()
        except (AttributeError, tk.TclError):
            pass
        threading.Thread(target=platform_worker.test_connection,
                         args=(self.config, self.q), daemon=True).start()

    def _fetch_challenges(self) -> None:
        if self.running:
            return
        cid = self.comp_choices.get(self.cb_comp.get(), "")
        if not cid:
            messagebox.showwarning("未选比赛", "请先点「连接平台」，拿到比赛列表后再选。")
            return
        self.lbl_conn.set("拉取题单…")
        self._chal_loading(True)
        threading.Thread(target=platform_worker.fetch_challenges,
                         args=(self.config, cid, self.q), daemon=True).start()

    def _refresh_conc_limit(self) -> None:
        info = limits.effective_max("platform")
        eff = info["max"]
        self.sp_conc.config(to=eff)
        try:
            cur = int(self.var_conc.get())
        except Exception:  # noqa: BLE001
            cur = 1
        if cur < 1:
            cur = 1
        if cur > eff:
            cur = eff
        self.var_conc.set(cur)
        self.lbl_conc.set(f"最大 {eff}（" + "；".join(info["reasons"]) + "）")

    def _solved_cids_for_run(self, rid: str) -> set[str]:
        """读某次 run 的 state.json，返回「已解出」的题目 id 集合（跑题库跳过用）。"""
        st = PROJECT_ROOT / "runs" / (rid or "run") / "state.json"
        try:
            data = json.loads(st.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            return set()
        out: set[str] = set()
        for cid, ch in (data.get("challenges") or {}).items():
            if not isinstance(ch, dict):
                continue
            if ch.get("submitted_ok") or str(ch.get("flag_value") or ""):
                out.add(str(cid))
        return out

    def _start_platform(self) -> None:
        rows = self._checked_rows()
        if not rows:
            messagebox.showwarning("未勾选题目", "请先在题库列表里勾选（可点「全选」）。")
            return
        comp = self.comp_choices.get(self.cb_comp.get(), "")
        rid = (self.var_run_p.get() or "").strip() or "plat"
        # 跑题库选项：跳过 / 重跑「已解出」（以该 run-id 的 state.json 为准）
        solved = self._solved_cids_for_run(rid)
        skip = bool(getattr(self, "var_skip_solved", None) and self.var_skip_solved.get())
        rows, skipped = theme.filter_solved(rows, solved, skip)
        if skipped:
            names = "、".join(str(r.get("id") or "") for r in skipped[:6])
            more = f"… 共 {len(skipped)} 道" if len(skipped) > 6 else ""
            self._log(f"[跑题库] 跳过已解出：{names}{more}")
        # 「重跑已解出」：Crew 默认对已解出的题零成本直接返回（平台 my_solved_ids /
        # state.json 的 submitted_ok）。要真的重跑就必须显式跳过这两层预检，否则会
        # 「瞬间全部已解出、0 token」——这正是之前看起来像 bug 的现象。
        forced: frozenset[str] = frozenset()
        if not skip and solved:
            forced = frozenset(str(r.get("id") or "") for r in rows
                               if str(r.get("id") or "") in solved)
            if forced:
                self._log(f"[跑题库] 强制重跑已解出 {len(forced)} 道"
                          f"（忽略平台/缓存已解记录，会真实消耗 token）")
        if not rows:
            messagebox.showinfo(
                "无需重跑",
                f"勾选的题在 run={rid} 里都已解出。\n"
                "要继续跑请把选项切到「重跑已解出」，或换一个运行名。")
            return
        self.run_id = rid
        self._apply_profile(self.cb_prof.get())
        try:
            conc = int(self.var_conc.get())
        except Exception:  # noqa: BLE001
            conc = 1
        info = limits.effective_max("platform")
        eff = info["max"]
        if conc < 1:
            conc = 1
        if conc > eff:
            messagebox.showerror(
                "并发度超限",
                f"平台/本机不支持 {conc} 并发，最大并发为 {eff}。\n\n"
                + "\n".join(info["reasons"])
                + "\n\n已将并发度调整为 " + str(eff) + "，请重新点「开始」。",
            )
            self.var_conc.set(eff)
            self._refresh_conc_limit()
            return
        self.var_conc.set(conc)
        chals = [{"id": str(r.get("id") or ""), "name": str(r.get("name") or "")} for r in rows]
        self._begin_thread(platform_worker.solve_many,
                           (self.config, chals, rid, comp, self.q, self.cancel, conc,
                            self.graceful, forced))

    def _on_conn_ok(self, data: dict) -> None:
        try:
            self.spin_conn.stop()
        except (AttributeError, tk.TclError):
            pass
        comps = data.get("competitions") or []
        self.comp_choices = {}
        labels: list[str] = []
        for c in comps:
            cid = str(c.get("id") or "")
            if not cid:
                continue
            title = str(c.get("name") or cid)
            phase = c.get("phase")
            label = f"{title}（{phase}）" if phase else title
            if label in self.comp_choices:  # 重名时加短 id 区分
                label = f"{title} [{cid[:8]}]"
            self.comp_choices[label] = cid
            labels.append(label)
        self.cb_comp["values"] = labels
        if labels:
            self.cb_comp.current(0)
            self.lbl_conn.set(f"已连接（{len(labels)} 个比赛/练习场）")
        else:
            self.lbl_conn.set("已连接（无比赛信息）")

    def _chal_loading(self, on: bool) -> None:
        """题单骨架屏：拉取期间盖住列表，拉完收起。"""
        try:
            if on:
                self.skel_chal.place(relx=0, rely=0, relwidth=1, relheight=1)
                self.skel_chal.lift()
                self.skel_chal.start()
            else:
                self.skel_chal.stop()
                self.skel_chal.place_forget()
        except (tk.TclError, AttributeError):
            pass

    def _on_challenges(self, data: dict) -> None:
        self._chal_loading(False)
        rows = data.get("items") or []
        self.challenge_rows = rows
        self.tv.delete(*self.tv.get_children())
        self._row_map = {}
        for r in rows:
            cid = str(r.get("id") or "")
            iid = self.tv.insert(
                "", "end",
                values=("☐", cid, str(r.get("category") or "-"), str(r.get("name") or "")),
            )
            self._row_map[iid] = r
        self._update_sel_label()
        self.lbl_conn.set(f"题单已刷新（{len(rows)} 道）")

    def _toggle_row(self, event) -> None:
        iid = self.tv.identify_row(event.y)
        if not iid:
            return
        vals = list(self.tv.item(iid, "values"))
        vals[0] = "☐" if vals[0] == "☑" else "☑"
        self.tv.item(iid, values=vals)
        self._update_sel_label()

    def _select_all(self, checked: bool) -> None:
        mark = "☑" if checked else "☐"
        for iid in self.tv.get_children():
            vals = list(self.tv.item(iid, "values"))
            vals[0] = mark
            self.tv.item(iid, values=vals)
        self._update_sel_label()

    def _checked_rows(self) -> list[dict]:
        out: list[dict] = []
        for iid in self.tv.get_children():
            if self.tv.item(iid, "values")[0] == "☑":
                row = self._row_map.get(iid)
                if row:
                    out.append(row)
        return out

    def _update_sel_label(self) -> None:
        rows = self._checked_rows()
        rid = ""
        try:
            rid = (self.var_run_p.get() or "").strip()
        except (AttributeError, tk.TclError):
            rid = ""
        solved = self._solved_cids_for_run(rid or "plat") if rows else set()
        done = [r for r in rows if str(r.get("id") or "") in solved]
        skip = True
        try:
            skip = bool(self.var_skip_solved.get())
        except (AttributeError, tk.TclError):
            skip = True
        tail = f" · 其中 {len(done)} 道已解出（{'将跳过' if skip else '将重跑'}）" if done else ""
        self.lbl_sel_chal.set(f"已勾选 {len(rows)} 道{tail}")

    def _refresh_profiles(self) -> None:
        data = profiles.load()
        self._profiles = data
        names = list(data["profiles"].keys())
        self.cb_prof["values"] = names
        active = data.get("active") or (names[0] if names else "")
        if active in names:
            self.cb_prof.set(active)
            self._apply_profile(active)

    def _apply_profile(self, name: str) -> None:
        data = getattr(self, "_profiles", None) or profiles.load()
        prof = data["profiles"].get(name)
        if not prof:
            return
        profiles.apply(self.config, prof)  # 含 mcp_url / token / tool_map
        n = len(self.config.extra.get("tool_map") or {})
        tail = f"，工具映射 {n} 条" if n else "，工具名自动识别"
        self.lbl_conn.set(f"未连接（{name}{tail}）")

    def _on_profile_change(self, _event=None) -> None:
        self._apply_profile(self.cb_prof.get())

    def _open_settings(self) -> None:
        data = profiles.load()
        cur = settings.current(self.config)
        win = tk.Toplevel(self.root)
        win.title("设置")
        win.transient(self.root)
        win.configure(bg=theme.PAGE)
        theme.enable_dpi_awareness()
        _sw, _sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        _w = min(theme.ui_px(760), max(theme.ui_px(640), _sw - theme.ui_px(120)))
        _h = min(theme.ui_px(640), max(theme.ui_px(480), _sh - theme.ui_px(140)))
        win.geometry(f"{_w}x{_h}")
        win.minsize(theme.ui_px(620), theme.ui_px(460))
        win.resizable(True, True)

        # 顶部毛玻璃标题条（和主窗口同一套视觉）
        head = theme.Card(win, padding=(18, 10), radius=16, glass=True)
        head.pack(fill="x", padx=12, pady=(12, 8))
        tk.Label(head.body, text="设置", bg=theme.GLASS, fg=theme.TEXT,
                 font=theme.heading_font(15)).pack(side="left")
        theme.label(head.body, "平台档案 · 模型 · 预算", muted=True,
                    size=9).pack(side="left", padx=(10, 0))

        # 圆角胶囊页签：选中蓝底白字、未选中白底深字、彼此留空隙（与主界面同一套）
        nb = theme.TabBar(win, pad=(8, 8))
        nb.pack(fill="both", expand=True, padx=12, pady=(0, 6))

        def _page(title: str):
            """建一页：滚动容器 + 白卡片，内容放 card.body（超长自动出滚动条）。"""
            page = tk.Frame(nb.body, bg=theme.PAGE)
            sf = theme.ScrollFrame(page, bg=theme.PAGE)
            # 给滚动容器一个「舒服」的期望尺寸：窗口按它自适应，缩小后自动出滚动条
            sf.canvas.configure(width=theme.ui_px(540), height=theme.ui_px(300))
            sf.pack(fill="both", expand=True)
            card = theme.Card(sf.inner, padding=(18, 16), radius=16)
            card.pack(fill="x", padx=6, pady=(0, 10))
            nb.add(page, text=title)
            body = card.body
            body.columnconfigure(1, weight=1)
            return body

        _lb = dict(size=9)

        # ---------- 平台档案 ----------
        t1 = _page("平台档案")
        theme.label(t1, "档案", **_lb).grid(row=0, column=0, sticky="w", pady=4)
        prof_name = tk.StringVar(value=data.get("active") or "")
        cb_prof = ttk.Combobox(t1, textvariable=prof_name, state="readonly", width=22)
        cb_prof.grid(row=0, column=1, sticky="we", pady=4)
        cb_prof["values"] = list(data["profiles"].keys())
        prof_btns = tk.Frame(t1, bg=theme.CANVAS)
        prof_btns.grid(row=0, column=2, sticky="e", padx=(8, 0))
        for _text, _cmdf in (("新建", lambda: _new_prof()),
                             ("重命名", lambda: _rename_prof()),
                             ("删除", lambda: _del_prof()),
                             ("恢复", lambda: _restore_prof())):
            theme.RoundedButton(prof_btns, text=_text, variant="secondary",
                                pad_x=10, pad_y=6, command=_cmdf).pack(side="left",
                                                                       padx=(0, 6))

        theme.label(t1, "MCP 地址", **_lb).grid(row=1, column=0, sticky="w", pady=4)
        prof_url = tk.StringVar()
        theme.RoundedEntry(t1, textvariable=prof_url).grid(
            row=1, column=1, columnspan=2, sticky="we", pady=4)

        theme.label(t1, "Token", **_lb).grid(row=2, column=0, sticky="w", pady=4)
        prof_tok = tk.StringVar()
        theme.RoundedEntry(t1, textvariable=prof_tok, show="*").grid(
            row=2, column=1, columnspan=2, sticky="we", pady=4)

        tok_hint = tk.StringVar()
        theme.label(t1, textvariable=tok_hint, muted=True,
                    size=9).grid(row=3, column=1, columnspan=2, sticky="w")
        theme.label(t1, "工具名映射（可选 JSON；留空=自动识别。不同平台工具名不一致时再填）",
                    muted=True, size=9).grid(row=4, column=1, columnspan=2,
                                             sticky="w", pady=(8, 2))

        tm_card = theme.Card(t1, radius=theme.RADIUS_INPUT, padding=(1, 1))
        tm_card.grid(row=5, column=1, columnspan=2, sticky="we")
        prof_tm = theme.textbox(tm_card.body, height=6, mono=True, size=9)
        prof_tm.pack(fill="both", expand=True)

        map_btns = tk.Frame(t1, bg=theme.CANVAS)
        map_btns.grid(row=6, column=1, columnspan=2, sticky="w", pady=(8, 0))
        theme.RoundedButton(map_btns, text="自动生成映射…", variant="secondary",
                            pad_x=12, pad_y=7,
                            command=lambda: _gen_map()).pack(side="left")
        theme.RoundedButton(map_btns, text="一键接入（自动识别）", variant="secondary",
                            pad_x=12, pad_y=7,
                            command=lambda: _onboard()).pack(side="left", padx=(8, 0))

        # ---------- 模型 ----------
        t2 = _page("模型")
        base = tk.StringVar(value=cur["llm_base_url"])
        model = tk.StringVar(value=cur["model"])
        key = tk.StringVar()
        theme.label(t2, "API Key", **_lb).grid(row=0, column=0, sticky="w", pady=4)
        theme.RoundedEntry(t2, textvariable=key, show="*").grid(
            row=0, column=1, sticky="we", pady=4)
        theme.label(t2, "已配置，留空不修改" if cur["llm_api_key_set"] else "未配置，请填写",
                    muted=True, size=9).grid(row=1, column=1, sticky="w")
        theme.label(t2, "Base URL", **_lb).grid(row=2, column=0, sticky="w", pady=4)
        theme.RoundedEntry(t2, textvariable=base).grid(row=2, column=1, sticky="we", pady=4)
        theme.label(t2, "模型名", **_lb).grid(row=3, column=0, sticky="w", pady=4)
        theme.RoundedEntry(t2, textvariable=model).grid(row=3, column=1, sticky="we", pady=4)
        theme.label(t2, "单价在 config/agent.json 的 price_*_per_mtok 配置；配了才会算花费。",
                    muted=True, size=9).grid(row=4, column=1, sticky="w", pady=(6, 0))

        # ---------- 预算 ----------
        t3 = _page("预算")
        mtok = tk.StringVar(value=cur["max_tokens_per_challenge"])
        mcalls = tk.StringVar(value=cur["max_llm_calls"])
        theme.label(t3, "单题 token 上限", **_lb).grid(row=0, column=0, sticky="w", pady=4)
        theme.RoundedEntry(t3, textvariable=mtok,
                           width=theme.ui_px(180)).grid(row=0, column=1, sticky="w", pady=4)
        theme.label(t3, "单题调用次数上限", **_lb).grid(row=1, column=0, sticky="w", pady=4)
        theme.RoundedEntry(t3, textvariable=mcalls,
                           width=theme.ui_px(180)).grid(row=1, column=1, sticky="w", pady=4)
        theme.label(t3, "预算按题累计（跨进程继承）；改小不会退钱，只影响后续开跑。",
                    muted=True, size=9).grid(row=2, column=1, sticky="w", pady=(6, 0))

        def _load_prof_fields(name):
            prof = data["profiles"].get(name) or {}
            prof_url.set(prof.get("mcp_url") or "")
            prof_tok.set("")
            prof_tm.delete("1.0", "end")
            tm = prof.get("tool_map") or {}
            if tm:
                prof_tm.insert("1.0", json.dumps(tm, ensure_ascii=False, indent=2))
            tok_hint.set("已配置，留空则不修改" if profiles.token_of(prof) else "未配置，请填写该平台的 token")

        def _on_select(_e=None):
            _load_prof_fields(prof_name.get())

        def _new_prof():
            name = profiles.new_profile(data, "新平台", prof_url.get().strip())
            profiles.save(data)
            cb_prof["values"] = list(data["profiles"].keys())
            prof_name.set(name)
            _load_prof_fields(name)

        def _del_prof():
            name = prof_name.get()
            if len(data["profiles"]) <= 1:
                messagebox.showinfo("删除档案", "至少保留一个平台档案。")
                return
            if not messagebox.askyesno(
                    "删除档案",
                    f"确定删除平台档案「{name}」？\n\n"
                    "· 配置和工具映射会进回收站，可用「恢复」找回\n"
                    "· token 会一并从 .env 删除（恢复档案后需要重新填写）"):
                return
            profiles.delete_profile(data, name)
            profiles.save(data)
            cb_prof["values"] = list(data["profiles"].keys())
            prof_name.set(data.get("active") or next(iter(data["profiles"])))
            _load_prof_fields(prof_name.get())

        def _rename_prof():
            old = prof_name.get()
            if old not in data["profiles"]:
                return
            new = simpledialog.askstring(
                "重命名档案", f"把档案「{old}」改成：", initialvalue=old, parent=win)
            if new is None:
                return
            new = new.strip()
            if not new or new == old:
                return
            try:
                renamed = profiles.rename_profile(data, old, new)
            except ValueError as exc:
                messagebox.showerror("重命名失败", str(exc), parent=win)
                return
            profiles.save(data)
            cb_prof["values"] = list(data["profiles"].keys())
            prof_name.set(renamed)
            _load_prof_fields(renamed)

        def _restore_prof():
            items = profiles.deleted_profiles()
            if not items:
                messagebox.showinfo("恢复档案", "回收站是空的。")
                return
            lines = [f"{i}：{it.get('name')}（删除于 {it.get('deleted_at', '?')}）"
                     for i, it in enumerate(items)]
            choice = simpledialog.askstring(
                "恢复档案",
                "输入要恢复的编号：\n\n" + "\n".join(lines),
                initialvalue="0", parent=win)
            if choice is None:
                return
            try:
                index = int(choice.strip())
            except ValueError:
                messagebox.showerror("恢复档案", "请输入编号数字", parent=win)
                return
            try:
                restored = profiles.restore_profile(data, index)
            except ValueError as exc:
                messagebox.showerror("恢复失败", str(exc), parent=win)
                return
            profiles.save(data)
            cb_prof["values"] = list(data["profiles"].keys())
            prof_name.set(restored)
            _load_prof_fields(restored)
            messagebox.showinfo(
                "恢复档案",
                f"已恢复为「{restored}」，映射已一并找回。\n\n"
                "token 在删除时已清除，请重新填写后再保存。",
                parent=win)

        def _gen_map():
            gw = tk.Toplevel(win)
            gw.title("自动生成工具映射")
            gw.transient(win)
            gw.configure(bg=theme.PAGE)
            gw.geometry(f"{theme.ui_px(720)}x{theme.ui_px(640)}")
            gw.minsize(theme.ui_px(560), theme.ui_px(480))
            gstatus = tk.StringVar(value="")
            mode_var = tk.StringVar(value="competition")

            theme.label(gw, "① 读取平台工具清单（下一步标准化）", size=11,
                        bold=True).pack(anchor="w", padx=12, pady=(10, 2))
            src_row = ttk.Frame(gw)
            src_row.pack(fill="x", padx=12)
            gstatus_lbl = theme.label(src_row, textvariable=gstatus, muted=True, size=9)

            theme.label(gw, "工具清单（每行一个；也可直接粘贴参数名或表格）",
                        muted=True, size=9).pack(anchor="w", padx=12, pady=(8, 0))
            names_box = theme.textbox(gw, height=8, mono=True, size=9)
            names_box.pack(fill="both", expand=True, padx=12, pady=(2, 0))

            mode_row = ttk.Frame(gw)
            mode_row.pack(fill="x", padx=12, pady=(8, 0))
            ttk.Label(mode_row, text="② 平台模式").pack(side="left")
            ttk.Radiobutton(mode_row, text="正式比赛", variable=mode_var,
                            value="competition").pack(side="left")
            ttk.Radiobutton(mode_row, text="练习场", variable=mode_var,
                            value="practice").pack(side="left")
            theme.RoundedButton(mode_row, text="③ 自动分析", variant="secondary",
                                pad_x=12, pad_y=6,
                                command=lambda: _analyze()).pack(side="left", padx=(14, 0))

            theme.label(gw, "生成的映射（JSON；点「应用」写回档案）",
                        muted=True, size=9).pack(anchor="w", padx=12, pady=(8, 0))
            prev_box = theme.textbox(gw, height=10, mono=True, size=9)
            prev_box.pack(fill="both", expand=True, padx=12, pady=(2, 0))

            def _set_names(names):
                names_box.delete("1.0", "end")
                names_box.insert("1.0", "\n".join(names))

            def _bg(fn, label, on_ok):
                local = queue.Queue()

                def run():
                    try:
                        local.put(("ok", fn()))
                    except Exception as exc:  # noqa: BLE001
                        local.put(("err", f"{type(exc).__name__}: {exc}"))

                threading.Thread(target=run, daemon=True).start()
                gstatus.set(label)

                def poll():
                    try:
                        kind, val = local.get_nowait()
                    except queue.Empty:
                        gw.after(150, poll)
                        return
                    if kind == "ok":
                        on_ok(val)
                    else:
                        gstatus.set("失败")
                        messagebox.showerror("获取失败", str(val), parent=gw)

                gw.after(150, poll)

            def _read_remote():
                import copy
                cfg = copy.copy(self.config)
                cfg.mcp_url = (prof_url.get() or "").strip() or cfg.mcp_url
                tok = (prof_tok.get() or "").strip()
                if tok:
                    cfg.token = tok

                def done(names):
                    _set_names(names)
                    gstatus.set(f"已从平台读取 {len(names)} 个工具名")

                _bg(lambda: toolmap.read_platform_tools(cfg), "连接平台读取工具清单…", done)

            def _pick_image():
                path = filedialog.askopenfilename(
                    parent=gw, title="选择工具清单截图",
                    filetypes=[("图片", "*.png *.jpg *.jpeg *.bmp *.webp"), ("所有文件", "*.*")])
                if not path:
                    return

                def done(text):
                    names = toolmap.parse_names(text)
                    _set_names(names)
                    gstatus.set(f"OCR 得到 {len(names)} 个候选（可能不准，请核对后再分析）")

                _bg(lambda: toolmap.ocr_image(path), "识别截图中（约几秒）…", done)

            def _analyze():
                names = toolmap.parse_names(names_box.get("1.0", "end"))
                mapping = toolmap.build_mapping(names, mode=mode_var.get())
                prev_box.delete("1.0", "end")
                prev_box.insert("1.0", toolmap.mapping_json(mapping))
                gstatus.set(f"生成 {len(mapping)} 条映射")

            def _apply_map():
                if not prev_box.get("1.0", "end").strip():
                    _analyze()
                txt = prev_box.get("1.0", "end").strip()
                try:
                    mapping = json.loads(txt)
                except Exception:  # noqa: BLE001
                    messagebox.showerror("JSON 不合法", "映射不是合法 JSON，请检查", parent=gw)
                    return

                def finish(final, note):
                    prof_tm.delete("1.0", "end")
                    prof_tm.insert("1.0", toolmap.mapping_json(final))
                    gw.destroy()
                    if note:
                        messagebox.showinfo("已应用", note, parent=win)

                import copy
                cfg = copy.copy(self.config)
                cfg.mcp_url = (prof_url.get() or "").strip() or cfg.mcp_url
                tok = (prof_tok.get() or "").strip()
                if tok:
                    cfg.token = tok

                box = queue.Queue()

                def run():
                    try:
                        box.put(("ok", toolmap.read_platform_tools(cfg)))
                    except Exception as exc:  # noqa: BLE001
                        box.put(("err", f"{type(exc).__name__}: {exc}"))

                threading.Thread(target=run, daemon=True).start()
                gstatus.set("连接平台校验工具名…")

                def poll():
                    try:
                        kind, val = box.get_nowait()
                    except queue.Empty:
                        gw.after(150, poll)
                        return
                    if kind == "ok":
                        fixed, unresolved = toolmap.repair_mapping(mapping, val, mode_var.get())
                        changed = [k for k in mapping if fixed.get(k) != mapping.get(k)]
                        note = f"已用平台真实工具名校验，纠正 {len(changed)} 条"
                        if changed:
                            note += "：\n" + "\n".join(
                                f"  {k}: {mapping[k]} → {fixed[k]}" for k in changed)
                        if unresolved:
                            note += "\n\n仍无法匹配（保持原样）：" + ", ".join(unresolved)
                        finish(fixed, note + "\n\n记得点「保存」写盘。")
                    else:
                        if messagebox.askyesno(
                                "无法校验",
                                f"连不上平台，无法校验工具名：\n{val}\n\n仍然应用这份未校验的映射吗？",
                                parent=gw):
                            finish(mapping, "")

                gw.after(150, poll)

            theme.RoundedButton(src_row, text="从平台读取（标准化）", variant="secondary",
                                pad_x=12, pad_y=7, command=_read_remote).pack(side="left")
            theme.RoundedButton(src_row, text="选择题图 OCR…", variant="secondary",
                                pad_x=12, pad_y=7,
                                command=_pick_image).pack(side="left", padx=(6, 0))
            gstatus_lbl.pack(side="left", padx=(10, 0))

            btns = ttk.Frame(gw)
            btns.pack(fill="x", padx=12, pady=(8, 10))
            theme.RoundedButton(btns, text="应用", variant="primary", pad_x=18, pad_y=8,
                                command=_apply_map).pack(side="right")
            theme.RoundedButton(btns, text="取消", variant="secondary", pad_x=16, pad_y=8,
                                command=gw.destroy).pack(side="right", padx=(0, 8))

        def _onboard():
            """一键接入：读平台契约 → 自动生成映射 → 只读实测 → 体检报告。"""
            import copy
            cfg = copy.copy(self.config)
            cfg.mcp_url = (prof_url.get() or "").strip() or cfg.mcp_url
            tok = (prof_tok.get() or "").strip()
            if tok:
                cfg.token = tok
            if not (cfg.mcp_url or "").strip():
                messagebox.showerror("一键接入", "请先填 MCP 地址", parent=win)
                return

            ow = tk.Toplevel(win)
            ow.title("一键接入 · 自动识别")
            ow.transient(win)
            ow.configure(bg=theme.PAGE)
            ow.geometry(f"{theme.ui_px(720)}x{theme.ui_px(580)}")
            ow.minsize(theme.ui_px(560), theme.ui_px(440))
            head = tk.StringVar(value="正在连接平台…")
            theme.label(ow, textvariable=head, size=11, bold=True).pack(
                anchor="w", padx=12, pady=(10, 4))
            box = theme.textbox(ow, mono=True, size=9)
            box.pack(fill="both", expand=True, padx=12, pady=(0, 8))
            bar = ttk.Frame(ow)
            bar.pack(fill="x", padx=12, pady=(0, 10))
            local: queue.Queue = queue.Queue()

            def run():
                try:
                    from . import onboard  # noqa: PLC0415

                    local.put(("ok", onboard.probe(
                        cfg, log=lambda t: local.put(("log", t)))))
                except Exception as exc:  # noqa: BLE001
                    local.put(("err", f"{type(exc).__name__}: {exc}"))

            threading.Thread(target=run, daemon=True).start()

            def close_and_save():
                ow.destroy()
                _save()

            def render(report):
                ok = bool(report.get("ok"))
                mode = "正式比赛" if report.get("mode") == "competition" else "练习场"
                head.set(
                    f"{'接入成功' if ok else '部分可用'} · {len(report.get('tools') or [])} 个工具 · "
                    f"{mode}模式 · {report.get('competitions', 0)} 个比赛 / "
                    f"{report.get('challenges', 0)} 道题")
                box.insert("end", "\n== 动作能力体检 ==\n")
                for cap in report.get("capabilities") or []:
                    mark = {"ok": "OK   ", "missing": "缺工具 ", "need_params": "需参数 "}.get(
                        cap["status"], cap["status"])
                    extra = ("  缺: " + ",".join(cap["missing"])) if cap.get("missing") else ""
                    box.insert("end", f" {mark} {cap['kind']:18} {cap['tool']}{extra}\n")
                warns = report.get("warnings") or []
                if warns:
                    box.insert("end", "\n== 提示 ==\n")
                    for w in warns:
                        box.insert("end", f" · {w}\n")
                box.insert("end", "\n映射已填入下方「工具名映射」，确认无误点「保存到档案」。\n")
                box.see("end")
                prof_tm.delete("1.0", "end")
                prof_tm.insert("1.0", toolmap.mapping_json(report.get("mapping") or {}))
                theme.RoundedButton(bar, text="保存到档案", variant="primary",
                                    pad_x=18, pad_y=8,
                                    command=close_and_save).pack(side="right")
                theme.RoundedButton(bar, text="关闭", variant="secondary",
                                    pad_x=16, pad_y=8,
                                    command=ow.destroy).pack(side="right", padx=(0, 8))

            def poll():
                try:
                    kind, val = local.get_nowait()
                except queue.Empty:
                    ow.after(120, poll)
                    return
                if kind == "log":
                    box.insert("end", str(val) + "\n")
                    box.see("end")
                    ow.after(120, poll)
                    return
                if kind == "err":
                    head.set("接入失败")
                    box.insert("end", "\n[错误] " + str(val) + "\n")
                    box.see("end")
                    return
                render(val)

            ow.after(120, poll)

        cb_prof.bind("<<ComboboxSelected>>", _on_select)
        _load_prof_fields(prof_name.get() or next(iter(data["profiles"])))

        status = tk.StringVar(value="")
        theme.separator(win).pack(fill="x", padx=14, pady=(0, 8))
        bf = tk.Frame(win, bg=theme.PAGE)
        bf.pack(fill="x", padx=14, pady=(0, 12))
        theme.label(bf, textvariable=status, muted=True, size=9).pack(side="left")

        def _test():
            import copy
            import queue as _q
            tmp = copy.copy(self.config)
            prof = dict(data["profiles"].get(prof_name.get()) or {})
            prof["mcp_url"] = (prof_url.get() or "").strip() or prof.get("mcp_url", "")
            profiles.apply(tmp, prof)
            if (prof_tok.get() or "").strip():
                tmp.token = prof_tok.get().strip()
            if (base.get() or "").strip():
                tmp.llm_base_url = base.get().strip()
            if (model.get() or "").strip():
                tmp.model = model.get().strip()
            if (key.get() or "").strip():
                tmp.llm_api_key = key.get().strip()
            local: _q.Queue = _q.Queue()
            threading.Thread(target=platform_worker.test_connection, args=(tmp, local),
                             daemon=True).start()

            def poll():
                try:
                    item = local.get_nowait()
                except _q.Empty:
                    win.after(200, poll)
                    return
                status.set("连接成功" if item[0] == "conn_ok" else "连接失败：" + str(item[1])[:56])

            status.set("测试中…")
            win.after(200, poll)

        def _save():
            name = prof_name.get()
            prof = data["profiles"].setdefault(name, {})
            prof["mcp_url"] = (prof_url.get() or "").strip()
            tm_raw = prof_tm.get("1.0", "end").strip()
            if tm_raw:
                try:
                    prof["tool_map"] = json.loads(tm_raw)
                except Exception:  # noqa: BLE001
                    messagebox.showerror("设置", "工具名映射不是合法 JSON，已取消保存")
                    return
            else:
                prof["tool_map"] = {}
            data["active"] = name
            profiles.save(data)
            profiles.set_token(prof, prof_tok.get())
            profiles.sync_active_to_agent_json(data)
            profiles.apply(self.config, prof)
            msgs = ["平台档案已保存：" + name]
            msgs += settings.save(self.config, {
                "model": model.get(), "llm_base_url": base.get(), "llm_api_key": key.get(),
                "max_tokens_per_challenge": mtok.get(), "max_llm_calls": mcalls.get(),
            })
            win.destroy()
            self._refresh_profiles()
            messagebox.showinfo("设置", "\n".join(msgs))

        theme.RoundedButton(bf, text="测试连接", icon="bolt", variant="secondary",
                            pad_x=14, pad_y=8,
                            command=_test).pack(side="left", padx=(12, 0))
        theme.RoundedButton(bf, text="保存", icon="check", variant="primary",
                            pad_x=18, pad_y=8, command=_save).pack(side="right")
        theme.RoundedButton(bf, text="取消", variant="secondary",
                            pad_x=16, pad_y=8,
                            command=win.destroy).pack(side="right", padx=(0, 8))
        # 尺寸自适应：先按内容期望大小算，再夹到屏幕 90%（小屏/高 DPI 都不会超出屏幕）
        try:
            win.update_idletasks()
            req_w = max((p.winfo_reqwidth() for p in nb.tabs()), default=theme.ui_px(540))
            req_h = max((p.winfo_reqheight() for p in nb.tabs()), default=theme.ui_px(300))
            f_w = min(int(_sw * 0.90), max(req_w + theme.ui_px(60), theme.ui_px(620)))
            f_h = min(int(_sh * 0.88), max(req_h + theme.ui_px(150), theme.ui_px(470)))
            win.geometry(f"{f_w}x{f_h}")
            win.minsize(theme.ui_px(560), theme.ui_px(420))
        except (tk.TclError, ValueError):
            pass


    # ---------------- 事件循环 ----------------

    def _poll(self) -> None:
        try:
            self._drain()
        except Exception:  # noqa: BLE001 - 渲染异常绝不能带崩主循环
            pass
        if self.running:
            self._tail_transcript()
            self._board_tick()
            self.lbl_elapsed.set(f"耗时：{self._fmt_elapsed()}")
        try:
            self._poll_job = self.root.after(100, self._poll)
        except tk.TclError:  # 窗口已销毁（关窗/selftest），别再排队
            self._poll_job = None

    def _cancel_poll(self) -> None:
        job, self._poll_job = getattr(self, "_poll_job", None), None
        if job:
            try:
                self.root.after_cancel(job)
            except (tk.TclError, ValueError):
                pass

    def _fmt_elapsed(self) -> str:
        s = int(time.monotonic() - self.started_at)
        return f"{s // 60:02d}:{s % 60:02d}"

    def _drain(self) -> None:
        while True:
            try:
                item = self.q.get_nowait()
            except queue.Empty:
                return
            kind = item[0]
            if kind == "event":
                self._on_event(item[1], item[2])
            elif kind == "answer":
                self._add_flag(item[2])
            elif kind == "done":
                self._on_done(item[1])
            elif kind == "error":
                self._set_status("出错：" + item[1])
                self._log(f"[错误] {item[1]}: {item[2]}")
                self._set_running(False)
            elif kind == "conn_ok":
                self._on_conn_ok(item[1])
            elif kind == "conn_err":
                try:
                    self.spin_conn.stop()
                except (AttributeError, tk.TclError):
                    pass
                self.lbl_conn.set("连接失败")
                messagebox.showerror("连接失败", item[1])
            elif kind == "challenges":
                self._on_challenges(item[1])
            elif kind == "challenges_err":
                self._chal_loading(False)
                self.lbl_conn.set("题单拉取失败")
                messagebox.showerror("拉取失败", item[1])
            elif kind == "task_start":
                d = item[1]
                cid = str(d.get("id") or "")
                self._board_row(cid, str(d.get("name") or ""))
                self._board_set(cid, status="解题中")
                self._log(f"===== 第 {d.get('index')}/{d.get('total')} 题：{d.get('id')} {d.get('name') or ''} =====")
                self._set_status(f"批量解题：第 {d.get('index')}/{d.get('total')} 题")
            elif kind == "task_done":
                d = item[1]
                cid = str(d.get("id") or "")
                presolved = bool(d.get("presolved"))
                if d.get("ok"):
                    # 「已解出（跳过）」= 平台/缓存已有结论，0 token 零成本收口；
                    # 这样界面上突然出现一批「已解出」时，原因一眼可见。
                    status = "已解出（跳过）" if presolved else "已解出"
                    self._board_set(cid, status=status, detail=str(d.get("flag") or ""))
                    tail = "（已有解出记录，未重烧）" if presolved else ""
                    self._log(f"✓ {d.get('id')}  {d.get('flag') or ''}{tail}")
                else:
                    self._board_set(cid, status="未解出",
                                    detail=str(d.get("note") or "未解出")[:60])
                    self._log(f"✗ {d.get('id')}  {d.get('note') or '未解出'}")
            elif kind == "batch_note":
                self._log(str(item[1]))

    def _on_event(self, kind: str, p: dict) -> None:
        cid = (p.get("_cid") or "").strip()
        tag = f"[{cid}] " if cid else ""
        if kind == "delegate":
            role = p.get("role") or ""
            self.active_role = role
            self._log(f"{tag}[{_NOW()}] 派活 → {role}：{str(p.get('task'))[:140]}")
            self._tool_line(f"{tag}───── 派活 → {role} ─────")
        elif kind == "report":
            self._log(f"{tag}[{_NOW()}] {p.get('role')} 交结论：{str(p.get('report'))[:200]}")
            self._tool_line(f"{tag}───── {p.get('role')} 结论 ─────")
        elif kind == "flag_accepted":
            self._log(f"{tag}[!] 记录候选通过")
            self._tool_line(f"{tag}[!] 候选通过")
        elif kind == "budget":
            self._update_budget(p or {})
            if cid and cid in self._board:
                tok = int((p or {}).get("total_tokens") or 0)
                cost = (p or {}).get("cost")
                self._board_set(cid, tokens=tok, cost=self._fmt_cost(cost))
        elif kind == "budget_exhausted":
            self._log(f"{tag}[{_NOW()}] 预算耗尽：{p.get('note') or p}")
            self.bar_tokens["value"] = 1000
            self.bar_calls["value"] = 1000
        elif kind == "step":
            if cid:
                self._board_bump_round(cid)
            self._tool_line(f"{tag}◆ 第{p.get('step')}步：{self._short(p.get('content'))}")
        elif kind == "tool":
            self._tool_line(f"{tag}• [{self.active_role or '?'}] {p.get('name')} {self._short(p.get('args'))} → {self._short(p.get('result'))}")
        elif kind == "instance_guard":
            self._log(f"{tag}[{_NOW()}] {str(p.get('note') or '')[:160]}")
        elif kind == "attachments":
            self._log(f"{tag}[{_NOW()}] 附件：{str(p.get('note') or '')[:400]}")
            self._tool_line(f"{tag}───── 附件已落盘 ─────")
        elif kind == "attachment":
            name = p.get("name") or p.get("file_id") or "?"
            if p.get("ok"):
                self._tool_line(f"{tag}  附件 {name} → 沙盒 {p.get('bytes', '?')} 字节")
            else:
                self._log(f"{tag}[!] 附件 {name} 下载失败：{str(p.get('error') or '')[:160]}")
        elif kind == "attachments_error":
            self._log(f"{tag}[!] 附件拉取失败（不影响解题）：{str(p.get('error') or '')[:160]}")

    def _tool_line(self, line: str) -> None:
        self.txt_tools.insert("end", line + "\n")
        self.txt_tools.see("end")

    @staticmethod
    def _short(v) -> str:
        s = str(v)
        return s if len(s) <= 120 else s[:117] + "…"

    def _reset_budget(self) -> None:
        self.bar_tokens["value"] = 0
        self.bar_calls["value"] = 0
        self.lbl_tokens.set("token 预算：—")
        self.lbl_calls.set("调用：—")
        self.lbl_cost.set("费用：—")
        self.lbl_budget.set("预算：—")

    def _priced(self) -> bool:
        """有没有配 token 单价（没配时花费恒为 0，显示 - 而不是误导性的 0.0）。"""
        return bool(getattr(self.config, "price_input_per_mtok", 0.0)
                    or getattr(self.config, "price_output_per_mtok", 0.0))

    def _fmt_cost(self, cost) -> str:
        if cost is None:
            return "-"
        try:
            value = float(cost)
        except (TypeError, ValueError):
            return "-"
        if value <= 0 and not self._priced():
            return "-"
        return f"{value:.4f}"

    def _update_budget(self, b: dict) -> None:
        tok = int(b.get("total_tokens") or 0)
        calls = int(b.get("calls") or 0)
        mtok = int(b.get("max_tokens") or getattr(self.config, "max_tokens_per_challenge", 0) or 0)
        mcalls = int(b.get("max_calls") or getattr(self.config, "max_llm_calls", 0) or 0)
        cost = b.get("cost")
        self.bar_tokens["value"] = min(1000, int(tok / mtok * 1000)) if mtok else 0
        self.bar_calls["value"] = min(1000, int(calls / mcalls * 1000)) if mcalls else 0
        self.lbl_tokens.set(f"token 预算：{tok}/{mtok}" if mtok else f"token：{tok}")
        self.lbl_calls.set(f"调用：{calls}/{mcalls}" if mcalls else f"调用：{calls}")
        if cost is not None:
            self.lbl_cost.set(f"费用：{self._fmt_cost(cost)}")
        self.lbl_budget.set(f"预算：token {tok} / 调用 {calls}")

    def _on_done(self, result: dict) -> None:
        self._set_running(False)
        cands = result.get("candidates") or []
        fv = result.get("flag_value") or ""
        if result.get("batch"):
            res = result.get("results") or []
            okn = sum(1 for r in res if r.get("ok"))
            self._log(f"===== 批量完成：{okn}/{len(res)} 题成功 =====")
            for r in res:
                mark = "✓" if r.get("ok") else "✗"
                detail = r.get("flag") or r.get("note") or "未解出"
                presolved = bool(r.get("presolved"))
                self._log(f"{mark} {r.get('id')}  {detail}"
                          + ("（跳过：已有解出记录）" if presolved else ""))
                cid = str(r.get("id") or "")
                if cid in self._board:
                    status = ("已解出（跳过）" if presolved else "已解出") if r.get("ok") \
                        else "未解出"
                    self._board_set(cid, status=status, detail=str(detail)[:60])
            self._set_status(f"批量完成：{okn}/{len(res)} 题成功")
        else:
            if fv:
                self._log(f"[!] 最终 flag：{fv}")
                self._set_status("完成（拿到 flag）")
            elif cands:
                self._set_status("完成（有候选，待人工核对）")
            else:
                self._set_status("结束（未发现候选）")
        self.flags = cands
        self._refresh_history()
        self._refresh_solved()
        self.nb.select(0)
        # Flag 候选不再单独占页签：有候选就写进复现页正文末尾
        if cands:
            self.txt_writeup.insert("end", "\n═══ Flag 候选 ═══\n")
            for cand in cands:
                self.txt_writeup.insert("end", cand + "\n", "flag")
            self._writeup_plain = (self._writeup_plain or "") + "\nFlag 候选：\n" + "\n".join(cands)
        self.txt_writeup.insert(
            "end", "\n（在左侧选中已解出的题，点「生成/查看复现」查看完整思路）\n")

    def _set_running(self, v: bool) -> None:
        self.running = v
        for btn in (getattr(self, "btn_start", None), getattr(self, "btn_start_p", None)):
            if btn is not None:
                btn.config(state="disabled" if v else "normal")
        for btn in (getattr(self, "btn_stop", None), getattr(self, "btn_stop_p", None)):
            if btn is not None:
                btn.config(state="normal" if v else "disabled")
        if not v:  # 结束后收掉所有旋转指示器（骨架屏由各自的 _loading 收）
            for sp in (getattr(self, "spin_conn", None), getattr(self, "spin_solved", None)):
                if sp is not None:
                    try:
                        sp.stop()
                    except (tk.TclError, AttributeError):
                        pass

    # ---------------- 输出 ----------------

    def _log(self, line: str) -> None:
        self.txt_events.insert("end", line + "\n")
        self.txt_events.see("end")

    def _add_flag(self, answer: str) -> None:
        if answer and answer not in self.flags:
            self.flags.append(answer)
        self._log(f"[{_NOW()}] 候选：{answer}")

    def _append_flags(self, text: str) -> None:
        """兼容保留：Flag 候选并入复现页，不再单独占页签。"""
        self.txt_writeup.insert("end", text + "\n")

    def _show_writeup(self, rid: str, cid: str | None = None) -> None:
        """把落盘产物蒸馏成可复现思路写进「复现」页；cid 给定时只生成该题。"""
        rdir = PROJECT_ROOT / "runs" / rid
        self._writeup_plain = build_writeup(rdir, cid)
        self.txt_writeup.delete("1.0", "end")
        self._config_writeup_tags()
        for blk in build_writeup_blocks(rdir, cid):
            kind = blk[0]
            if kind == "meta":
                self.txt_writeup.insert("end", blk[1] + "  ", "meta_l")
                self.txt_writeup.insert("end", blk[2] + "\n")
            elif kind == "section":
                self.txt_writeup.insert("end", "═══ " + blk[1] + " ═══\n", "sec")
            elif kind == "plan":
                self.txt_writeup.insert("end", blk[1] + "> ", "round")
                self.txt_writeup.insert("end", blk[2] + "\n")
            elif kind == "report":
                self.txt_writeup.insert("end", blk[1] + " ", "rep_t")
                self.txt_writeup.insert("end", blk[2] + "\n", "rep")
            elif kind == "flag":
                self.txt_writeup.insert("end", blk[1] + "\n", "flag")
            elif kind == "plain":
                self.txt_writeup.insert("end", blk[1] + "\n")
        try:
            write_writeup_file(rdir, cid)  # 本地计算，不花 token
        except Exception:  # noqa: BLE001
            pass

    def _config_writeup_tags(self) -> None:
        w = self.txt_writeup
        w.tag_configure("sec", foreground=theme.PRIMARY, font=theme.mono_font(11, bold=True),
                        spacing1=10, spacing3=4)
        w.tag_configure("meta_l", foreground=theme.TEXT_SUBTLE,
                        font=theme.mono_font(10, bold=True))
        w.tag_configure("round", foreground=theme.WARN, font=theme.mono_font(10, bold=True))
        w.tag_configure("rep_t", foreground=theme.TEXT, font=theme.mono_font(10, bold=True))
        w.tag_configure("rep", lmargin1=18, lmargin2=18, spacing3=10)
        w.tag_configure("flag", foreground=theme.SUCCESS, font=theme.mono_font(12, bold=True),
                        background=theme.SUCCESS_SOFT, spacing1=8, spacing3=2)

    def _tail_transcript(self) -> None:
        tdir = PROJECT_ROOT / "runs" / self.run_id / "transcripts"
        if not tdir.is_dir():
            return
        for fp in sorted(tdir.glob("*.jsonl")):
            key = str(fp)
            off = self._tpos.get(key, 0)
            size = fp.stat().st_size
            if size <= off:
                continue
            with fp.open("r", encoding="utf-8", errors="replace") as fh:
                fh.seek(off)
                for line in fh:
                    line = line.rstrip("\n")
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except Exception:  # noqa: BLE001
                        rec = {"raw": line}
                    role = rec.get("role") or rec.get("step") or "?"
                    txt = self._record_summary(rec)
                    self.txt_process.insert("end", f"[{role}] {txt}\n")
                self._tpos[key] = fh.tell()
        self.txt_process.see("end")

    @staticmethod
    def _record_summary(rec: dict) -> str:
        for key in ("content", "note", "report", "reason", "task"):
            if rec.get(key):
                return str(rec[key])[:220]
        return json.dumps(rec, ensure_ascii=False)[:220]

    def _set_status(self, text: str) -> None:
        self.lbl_status.set(text)

    # ---------------- 导出 ----------------

    def _copy_flag(self) -> None:
        try:
            sel = self.txt_writeup.get("sel.first", "sel.last").strip()
        except Exception:  # noqa: BLE001
            sel = ""
        if not sel and self.flags:
            sel = self.flags[0]
        if sel:
            self.root.clipboard_clear()
            self.root.clipboard_append(sel)
            self._set_status("已复制：" + sel)
        else:
            messagebox.showinfo("没有可复制的 flag", "还没有候选 flag（先跑题，或去复现页选中文本）。")

    def _copy_writeup(self) -> None:
        txt = (self._writeup_plain or self.txt_writeup.get("1.0", "end")).strip()
        if not txt or txt.startswith("(解题中") or txt.startswith("(该任务目录"):
            messagebox.showinfo("没有可复制的内容", "复现稿还没生成（解题中，或该题还无结果）。")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(txt)
        self._set_status("已复制整篇复现稿")

    def _export_report(self) -> None:
        rid = self.run_id or self._selected_run() or "hunt"
        rdir = PROJECT_ROOT / "runs" / rid
        writeup_txt = build_writeup(rdir)
        out = rdir / "report.md"
        lines = [
            f"# 离线解题报告 · {rid}",
            "",
            f"- 生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
            f"- 状态：{self.lbl_status.get()}",
            "",
            writeup_txt,
        ]
        out.write_text("\n".join(lines), encoding="utf-8")

        jdata = {
            "run_id": rid,
            "status": self.lbl_status.get(),
            "exported_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "flag_value": self.flags[0] if self.flags else "",
            "candidates": self.flags,
            "writeup": writeup_txt,
        }
        (rdir / "report.json").write_text(json.dumps(jdata, ensure_ascii=False, indent=2), encoding="utf-8")

        self._set_status("报告已导出：" + str(out))
        messagebox.showinfo("导出", f"已写入：\n{out}\n{rdir / 'report.json'}")

    def run(self) -> None:
        self.root.mainloop()


def main() -> int:
    import sys
    import traceback

    if "--selftest" in sys.argv:
        app = App()
        app.root.update_idletasks()
        app.root.update()
        print("GUI selftest OK")
        app.root.destroy()
        return 0
    try:
        App().run()
        return 0
    except Exception:  # noqa: BLE001 - pythonw 无控制台，错误必须落盘 + 弹窗
        err = traceback.format_exc()
        try:
            log = PROJECT_ROOT / "runs" / "gui-error.log"
            log.parent.mkdir(parents=True, exist_ok=True)
            log.write_text(err, encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
        try:
            r = tk.Tk()
            r.withdraw()
            messagebox.showerror("界面启动失败", "错误已写入 runs/gui-error.log\n\n" + err[-600:])
            r.destroy()
        except Exception:  # noqa: BLE001
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())