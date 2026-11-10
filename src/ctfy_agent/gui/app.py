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
from tkinter import filedialog, messagebox, simpledialog, ttk

from ..config import PROJECT_ROOT, load
from ..writeup import build_writeup, build_writeup_blocks, write_writeup_file
from . import limits, platform_worker, profiles, settings, toolmap, worker

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
        self.root.geometry("1200x780")
        try:
            self.root.tk.call("tk", "scaling", 1.2)
        except Exception:  # noqa: BLE001
            pass
        self._build_ui()
        self._refresh_history()
        self._refresh_solved()
        self._poll()

    # ---------------- UI ----------------

    def _build_ui(self) -> None:
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:  # noqa: BLE001
            pass

        # 顶部状态栏
        top = ttk.Frame(self.root, padding=(10, 6))
        top.pack(fill="x")
        self.lbl_status = tk.StringVar(value="就绪")
        self.lbl_budget = tk.StringVar(value="预算：—")
        self.lbl_elapsed = tk.StringVar(value="耗时：—")
        ttk.Label(top, text="CTF 自动解题", font=("", 14, "bold")).pack(side="left")
        ttk.Label(top, textvariable=self.lbl_status, foreground="#0a5").pack(side="left", padx=(24, 0))
        ttk.Button(top, text="⚙ 设置", command=self._open_settings).pack(side="right")
        ttk.Label(top, textvariable=self.lbl_elapsed).pack(side="right", padx=(0, 16))
        ttk.Label(top, textvariable=self.lbl_budget).pack(side="right")

        # 预算进度条（token / 调用 / 费用）
        bud = ttk.Frame(self.root, padding=(10, 0))
        bud.pack(fill="x", pady=(0, 4))
        self.lbl_tokens = tk.StringVar(value="token 预算：—")
        self.lbl_calls = tk.StringVar(value="调用：—")
        self.lbl_cost = tk.StringVar(value="费用：—")
        ttk.Label(bud, textvariable=self.lbl_tokens).grid(row=0, column=0, sticky="w")
        self.bar_tokens = ttk.Progressbar(bud, maximum=1000, length=260)
        self.bar_tokens.grid(row=0, column=1, padx=6)
        ttk.Label(bud, textvariable=self.lbl_calls).grid(row=0, column=2, sticky="w", padx=(14, 0))
        self.bar_calls = ttk.Progressbar(bud, maximum=1000, length=180)
        self.bar_calls.grid(row=0, column=3, padx=6)
        ttk.Label(bud, textvariable=self.lbl_cost).grid(row=0, column=4, padx=(14, 0))

        body = ttk.PanedWindow(self.root, orient="horizontal")
        body.pack(fill="both", expand=True, padx=10, pady=(0, 6))

        # 左栏
        left = ttk.Frame(body, width=340)
        body.add(left, weight=0)

        # 模式切换
        msf = ttk.Frame(left)
        msf.pack(fill="x", padx=(0, 8), pady=(0, 6))
        ttk.Label(msf, text="模式：").pack(side="left")
        self.var_mode = tk.StringVar(value="offline")
        ttk.Radiobutton(msf, text="离线靶场", variable=self.var_mode, value="offline",
                        command=self._on_mode).pack(side="left")
        ttk.Radiobutton(msf, text="平台接口", variable=self.var_mode, value="platform",
                        command=self._on_mode).pack(side="left", padx=(8, 0))

        # 表单容器：固定位置，内部在「离线 / 平台」两套表单间切换
        self.frm_form = ttk.Frame(left)
        self.frm_form.pack(fill="x", padx=(0, 8), pady=(0, 8))
        self.frm_off = ttk.LabelFrame(self.frm_form, text="离线靶场（人工起靶）", padding=8)
        self.frm_off.pack(fill="x")
        frm_new = self.frm_off

        ttk.Label(frm_new, text="目标地址（每行一个）").pack(anchor="w")
        self.txt_urls = tk.Text(frm_new, height=2, font=("Consolas", 10))
        self.txt_urls.pack(fill="x")

        row1 = ttk.Frame(frm_new)
        row1.pack(fill="x", pady=4)
        ttk.Label(row1, text="题型").pack(side="left")
        self.cb_cat = ttk.Combobox(row1, values=CATEGORIES, width=9, state="readonly")
        self.cb_cat.set("web")
        self.cb_cat.pack(side="left", padx=4)
        ttk.Label(row1, text="难度").pack(side="left", padx=(12, 0))
        self.cb_diff = ttk.Combobox(row1, values=DIFFICULTIES, width=9, state="readonly")
        self.cb_diff.set("medium")
        self.cb_diff.pack(side="left", padx=4)

        ttk.Label(frm_new, text="题名（可选）").pack(anchor="w", pady=(6, 0))
        self.var_name = tk.StringVar()
        ttk.Entry(frm_new, textvariable=self.var_name).pack(fill="x")

        ttk.Label(frm_new, text="题面（可选，文本框或选文件）").pack(anchor="w", pady=(6, 0))
        self.txt_brief = tk.Text(frm_new, height=5, font=("", 10))
        self.txt_brief.pack(fill="x")
        ttk.Button(frm_new, text="从文件载入题面…", command=self._load_brief).pack(anchor="w", pady=2)

        ttk.Label(frm_new, text="运行名（run-id）").pack(anchor="w", pady=(6, 0))
        self.var_run = tk.StringVar(value="hunt")
        ttk.Entry(frm_new, textvariable=self.var_run).pack(fill="x")

        self.var_started = tk.BooleanVar(value=True)
        ttk.Checkbutton(frm_new, text="我已人工启动靶场", variable=self.var_started).pack(anchor="w", pady=6)

        btns = ttk.Frame(frm_new)
        btns.pack(fill="x")
        self.btn_start = ttk.Button(btns, text="▶ 开始", command=self._start)
        self.btn_start.pack(side="left", fill="x", expand=True)
        self.btn_stop = ttk.Button(btns, text="■ 停止", command=self._stop, state="disabled")
        self.btn_stop.pack(side="left", fill="x", expand=True, padx=(6, 0))

        # 平台接口表单（默认隐藏）
        self.frm_plat = ttk.LabelFrame(self.frm_form, text="平台接口（自动起靶 + 提交）", padding=8)
        self.lbl_conn = tk.StringVar(value="未连接")
        ttk.Label(self.frm_plat, text="平台").pack(anchor="w")
        self.cb_prof = ttk.Combobox(self.frm_plat, state="readonly")
        self.cb_prof.pack(fill="x")
        self.cb_prof.bind("<<ComboboxSelected>>", self._on_profile_change)
        self._refresh_profiles()
        rc = ttk.Frame(self.frm_plat)
        rc.pack(fill="x")
        ttk.Button(rc, text="连接平台", command=self._connect_platform).pack(side="left")
        ttk.Label(rc, textvariable=self.lbl_conn, foreground="#777").pack(side="left", padx=(8, 0))

        ttk.Label(self.frm_plat, text="比赛").pack(anchor="w", pady=(6, 0))
        self.cb_comp = ttk.Combobox(self.frm_plat, state="readonly")
        self.cb_comp.pack(fill="x")

        ttk.Button(self.frm_plat, text="刷新题单", command=self._fetch_challenges).pack(fill="x", pady=(6, 0))
        ttk.Label(self.frm_plat, text="题目（勾选要跑的，可全选）").pack(anchor="w", pady=(6, 0))
        tvf = ttk.Frame(self.frm_plat)
        tvf.pack(fill="x")
        self.tv = ttk.Treeview(tvf, columns=("sel", "id", "cat", "name"), show="headings", height=10)
        for col, width, text in (("sel", 34, "选"), ("id", 118, "ID"), ("cat", 54, "分类"), ("name", 180, "名称")):
            self.tv.heading(col, text=text)
            self.tv.column(col, width=width, anchor="center" if col == "sel" else "w",
                           stretch=(col == "name"))
        vsb = ttk.Scrollbar(tvf, orient="vertical", command=self.tv.yview)
        self.tv.configure(yscrollcommand=vsb.set)
        self.tv.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.tv.bind("<Button-1>", self._toggle_row)
        self.lbl_sel_chal = tk.StringVar(value="已勾选 0 道")
        sb = ttk.Frame(self.frm_plat)
        sb.pack(fill="x", pady=(2, 0))
        ttk.Button(sb, text="全选", command=lambda: self._select_all(True)).pack(side="left")
        ttk.Button(sb, text="全不选", command=lambda: self._select_all(False)).pack(side="left", padx=(6, 0))
        ttk.Label(sb, textvariable=self.lbl_sel_chal, foreground="#666").pack(side="left", padx=(10, 0))

        ttk.Label(self.frm_plat, text="运行名（run-id）").pack(anchor="w", pady=(6, 0))
        self.var_run_p = tk.StringVar(value="plat")
        ttk.Entry(self.frm_plat, textvariable=self.var_run_p).pack(fill="x")

        ttk.Label(self.frm_plat, text="并发度（勾选多题时并行求解）").pack(anchor="w", pady=(6, 0))
        cf = ttk.Frame(self.frm_plat)
        cf.pack(fill="x")
        self.var_conc = tk.IntVar(value=1)
        self.sp_conc = ttk.Spinbox(cf, from_=1, to=3, width=5, textvariable=self.var_conc)
        self.sp_conc.pack(side="left")
        self.lbl_conc = tk.StringVar(value="")
        ttk.Label(cf, textvariable=self.lbl_conc, foreground="#888").pack(side="left", padx=(8, 0))
        self._refresh_conc_limit()

        pb = ttk.Frame(self.frm_plat)
        pb.pack(fill="x", pady=(8, 0))
        self.btn_start_p = ttk.Button(pb, text="▶ 开始", command=self._start)
        self.btn_start_p.pack(side="left", fill="x", expand=True)
        self.btn_stop_p = ttk.Button(pb, text="■ 停止", command=self._stop, state="disabled")
        self.btn_stop_p.pack(side="left", fill="x", expand=True, padx=(6, 0))

        # 历史
        frm_hist = ttk.LabelFrame(left, text="任务历史", padding=8)
        frm_hist.pack(fill="both", expand=True, padx=(0, 8))
        self.lb_hist = tk.Listbox(frm_hist)
        self.lb_hist.pack(fill="both", expand=True)
        self.lb_hist.bind("<Double-Button-1>", lambda e: self._view_history())
        hb = ttk.Frame(frm_hist)
        hb.pack(fill="x", pady=(6, 0))
        ttk.Button(hb, text="刷新", command=self._refresh_history).pack(side="left", fill="x", expand=True)
        ttk.Button(hb, text="查看", command=self._view_history).pack(side="left", fill="x", expand=True, padx=(4, 0))
        ttk.Button(hb, text="续跑", command=self._resume_history).pack(side="left", fill="x", expand=True, padx=(4, 0))
        ttk.Button(hb, text="删除", command=self._del_history).pack(side="left", fill="x", expand=True, padx=(4, 0))

        # 右栏：3 个页签（复现 / 任务面板 / 日志）
        # 原来的「进展 / 工具流 / 过程 / Flag 候选」合并精简：日志一份、Flag 进复现。
        right = ttk.Frame(body)
        body.add(right, weight=1)
        nb = ttk.Notebook(right)
        nb.pack(fill="both", expand=True)
        self.nb = nb

        self._build_writeup_tab(nb)
        self._build_board_tab(nb)
        self.txt_events = self._add_text_tab(nb, "日志")
        # 兼容旧调用：工具流与过程都并入同一份日志，不再各占一个页签
        self.txt_tools = self.txt_events
        self.txt_process = self.txt_events

        fsub = ttk.Frame(right)
        fsub.pack(fill="x", pady=(4, 0))
        ttk.Button(fsub, text="复制选中 flag", command=self._copy_flag).pack(side="left")
        ttk.Button(fsub, text="复制复现稿", command=self._copy_writeup).pack(side="left", padx=(6, 0))
        ttk.Button(fsub, text="导出报告", command=self._export_report).pack(side="left", padx=(6, 0))

    def _add_text_tab(self, nb, title: str) -> tk.Text:
        f = ttk.Frame(nb)
        nb.add(f, text=title)
        txt = tk.Text(f, wrap="word", font=("Consolas", 10))
        sb = ttk.Scrollbar(f, orient="vertical", command=txt.yview)
        txt.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        txt.pack(fill="both", expand=True)
        return txt

    # ---------------- 复现：已解出列表 + 按需生成 ----------------

    def _build_writeup_tab(self, nb) -> None:
        f = ttk.Frame(nb)
        nb.add(f, text="复现")
        bar = ttk.Frame(f)
        bar.pack(fill="x")
        ttk.Button(bar, text="刷新已解出", command=self._refresh_solved).pack(side="left")
        ttk.Button(bar, text="生成/查看复现", command=self._gen_writeup_selected).pack(side="left", padx=(6, 0))
        self.lbl_solved = tk.StringVar(value="已解出 0 题（选中后点「生成/查看复现」，不点不生成）")
        ttk.Label(bar, textvariable=self.lbl_solved, foreground="#666").pack(side="left", padx=(10, 0))

        pane = ttk.PanedWindow(f, orient="horizontal")
        pane.pack(fill="both", expand=True, pady=(4, 0))
        lf = ttk.Frame(pane)
        pane.add(lf, weight=1)
        cols = ("name", "cat", "flag", "run")
        self.tv_solved = ttk.Treeview(lf, columns=cols, show="headings", height=12)
        for col, width, text in (("name", 150, "题目"), ("cat", 58, "分类"),
                                 ("flag", 170, "flag"), ("run", 80, "运行")):
            self.tv_solved.heading(col, text=text)
            self.tv_solved.column(col, width=width, anchor="w", stretch=col in ("name", "flag"))
        sbv = ttk.Scrollbar(lf, orient="vertical", command=self.tv_solved.yview)
        self.tv_solved.configure(yscrollcommand=sbv.set)
        sbv.pack(side="right", fill="y")
        self.tv_solved.pack(side="left", fill="both", expand=True)
        self.tv_solved.bind("<Double-Button-1>", lambda e: self._gen_writeup_selected())

        rf = ttk.Frame(pane)
        pane.add(rf, weight=3)
        self.txt_writeup = tk.Text(rf, wrap="word", font=("Consolas", 10))
        sbr = ttk.Scrollbar(rf, orient="vertical", command=self.txt_writeup.yview)
        self.txt_writeup.configure(yscrollcommand=sbr.set)
        sbr.pack(side="right", fill="y")
        self.txt_writeup.pack(fill="both", expand=True)
        self._config_writeup_tags()
        self.txt_writeup.insert("1.0", "左侧是已解出的题目；选中一题点「生成/查看复现」才会生成正文。\n")

    def _refresh_solved(self) -> None:
        """扫描 runs/* 收集「已解出」的题（只读 state.json，不生成复现、不花 token）。"""
        rows: list[dict] = []
        base = PROJECT_ROOT / "runs"
        if base.is_dir():
            for d in base.iterdir():
                st = d / "state.json"
                if not d.is_dir() or not st.is_file():
                    continue
                try:
                    data = json.loads(st.read_text(encoding="utf-8-sig"))
                except Exception:  # noqa: BLE001
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
                        "at": str(ch.get("updated_at") or ""),
                    })
        rows.sort(key=lambda r: r["at"], reverse=True)
        self._solved = rows
        self.tv_solved.delete(*self.tv_solved.get_children())
        self._solved_rows = {}
        for r in rows:
            iid = self.tv_solved.insert(
                "", "end",
                values=(r["name"], r["cat"], r["flag"] or "（未记录）", r["run"]))
            self._solved_rows[iid] = r
        self.lbl_solved.set(f"已解出 {len(rows)} 题（选中后点「生成/查看复现」）")

    def _gen_writeup_selected(self) -> None:
        sel = self.tv_solved.selection()
        if not sel:
            messagebox.showinfo("复现", "先在左侧选一道已解出的题。")
            return
        row = self._solved_rows.get(sel[0])
        if not row:
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
        f = ttk.Frame(nb)
        nb.add(f, text="任务面板")
        bar = ttk.Frame(f)
        bar.pack(fill="x")
        self.lbl_board = tk.StringVar(value="还没有任务")
        ttk.Label(bar, textvariable=self.lbl_board, foreground="#666").pack(side="left")
        ttk.Button(bar, text="清空", command=self._board_clear).pack(side="right")
        widths = {"title": 150, "status": 78, "detail": 280, "rounds": 56,
                  "tokens": 70, "cost": 56, "elapsed": 72}
        self.tv_board = ttk.Treeview(f, columns=self._BOARD_COLS, show="headings", height=14)
        for col in self._BOARD_COLS:
            self.tv_board.heading(col, text=self._BOARD_HEADS[col])
            self.tv_board.column(col, width=widths[col], stretch=col in ("title", "detail"),
                                 anchor="w" if col in ("title", "detail") else "center")
        sb = ttk.Scrollbar(f, orient="vertical", command=self.tv_board.yview)
        self.tv_board.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.tv_board.pack(fill="both", expand=True, pady=(4, 0))

    def _board_clear(self) -> None:
        for iid in self.tv_board.get_children():
            self.tv_board.delete(iid)
        self._board.clear()
        self.lbl_board.set("还没有任务")

    def _board_row(self, cid: str, title: str = "") -> dict:
        rec = self._board.get(cid)
        if rec is None:
            iid = self.tv_board.insert(
                "", "end", values=(title or cid[:8], "待开始", "", 0, 0, "-", "00:00"))
            rec = {"iid": iid, "started": time.monotonic()}
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
        """运行中刷新「用时」列。"""
        idx = self._BOARD_COLS.index("elapsed")
        for rec in self._board.values():
            vals = list(self.tv_board.item(rec["iid"], "values"))
            sec = int(time.monotonic() - rec["started"])
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
        else:
            self.frm_off.pack_forget()
            self.frm_plat.pack(fill="x")

    def _connect_platform(self) -> None:
        if self.running:
            return
        self._apply_profile(self.cb_prof.get())
        self.lbl_conn.set("连接中…")
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

    def _start_platform(self) -> None:
        rows = self._checked_rows()
        if not rows:
            messagebox.showwarning("未勾选题目", "请先在题目列表里勾选（可点「全选」）。")
            return
        comp = self.comp_choices.get(self.cb_comp.get(), "")
        rid = (self.var_run_p.get() or "").strip() or "plat"
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
                            self.graceful))

    def _on_conn_ok(self, data: dict) -> None:
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

    def _on_challenges(self, data: dict) -> None:
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
        self.lbl_sel_chal.set(f"已勾选 {len(self._checked_rows())} 道")

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
        win.resizable(False, False)
        win.geometry("700x560")

        nb = ttk.Notebook(win)
        nb.pack(fill="both", expand=True, padx=12, pady=(12, 0))

        # ---------- 平台档案 ----------
        t1 = ttk.Frame(nb, padding=10)
        nb.add(t1, text="平台档案")
        ttk.Label(t1, text="档案").grid(row=0, column=0, sticky="w", pady=3)
        prof_name = tk.StringVar(value=data.get("active") or "")
        cb_prof = ttk.Combobox(t1, textvariable=prof_name, state="readonly", width=30)
        cb_prof.grid(row=0, column=1, sticky="w", pady=3)
        cb_prof["values"] = list(data["profiles"].keys())
        ttk.Button(t1, text="新建", command=lambda: _new_prof()).grid(row=0, column=2, padx=(8, 0))
        ttk.Button(t1, text="重命名", command=lambda: _rename_prof()).grid(row=0, column=3, padx=(4, 0))
        ttk.Button(t1, text="删除", command=lambda: _del_prof()).grid(row=0, column=4, padx=(4, 0))
        ttk.Button(t1, text="恢复", command=lambda: _restore_prof()).grid(row=0, column=5, padx=(4, 0))

        ttk.Label(t1, text="MCP 地址").grid(row=1, column=0, sticky="w", pady=3)
        prof_url = tk.StringVar()
        ttk.Entry(t1, textvariable=prof_url, width=62).grid(row=1, column=1, columnspan=4, sticky="we", pady=3)
        ttk.Label(t1, text="Token").grid(row=2, column=0, sticky="w", pady=3)
        prof_tok = tk.StringVar()
        ttk.Entry(t1, textvariable=prof_tok, show="*", width=62).grid(row=2, column=1, columnspan=4, sticky="we", pady=3)
        tok_hint = tk.StringVar()
        ttk.Label(t1, textvariable=tok_hint, foreground="#888").grid(row=3, column=1, columnspan=4, sticky="w")
        ttk.Label(t1, text="工具名映射（可选 JSON；留空=自动识别。不同平台工具名不一致时再填）",
                  foreground="#888").grid(row=4, column=1, columnspan=4, sticky="w", pady=(6, 0))
        prof_tm = tk.Text(t1, height=6, width=62, font=("Consolas", 9))
        prof_tm.grid(row=5, column=1, columnspan=4, sticky="we", pady=3)
        ttk.Button(t1, text="自动生成映射…", command=lambda: _gen_map()).grid(
            row=6, column=1, sticky="w", pady=(2, 0))
        ttk.Button(t1, text="一键接入（自动识别）", command=lambda: _onboard()).grid(
            row=6, column=2, columnspan=2, sticky="w", padx=(6, 0), pady=(2, 0))

        # ---------- 模型 ----------
        t2 = ttk.Frame(nb, padding=10)
        nb.add(t2, text="模型")
        base = tk.StringVar(value=cur["llm_base_url"])
        model = tk.StringVar(value=cur["model"])
        key = tk.StringVar()
        ttk.Label(t2, text="API Key").grid(row=0, column=0, sticky="w", pady=3)
        ttk.Entry(t2, textvariable=key, show="*", width=56).grid(row=0, column=1, sticky="we", pady=3)
        ttk.Label(t2, text=("已配置，留空则不修改" if cur["llm_api_key_set"] else "未配置，请填写"),
                  foreground="#888").grid(row=1, column=1, sticky="w")
        ttk.Label(t2, text="Base URL").grid(row=2, column=0, sticky="w", pady=3)
        ttk.Entry(t2, textvariable=base, width=56).grid(row=2, column=1, sticky="we", pady=3)
        ttk.Label(t2, text="模型名").grid(row=3, column=0, sticky="w", pady=3)
        ttk.Entry(t2, textvariable=model, width=56).grid(row=3, column=1, sticky="we", pady=3)

        # ---------- 预算 ----------
        t3 = ttk.Frame(nb, padding=10)
        nb.add(t3, text="预算")
        mtok = tk.StringVar(value=cur["max_tokens_per_challenge"])
        mcalls = tk.StringVar(value=cur["max_llm_calls"])
        ttk.Label(t3, text="单题 token 上限").grid(row=0, column=0, sticky="w", pady=3)
        ttk.Entry(t3, textvariable=mtok, width=18).grid(row=0, column=1, sticky="w", pady=3)
        ttk.Label(t3, text="单题调用次数上限").grid(row=1, column=0, sticky="w", pady=3)
        ttk.Entry(t3, textvariable=mcalls, width=18).grid(row=1, column=1, sticky="w", pady=3)

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
            gw.geometry("780x660")
            gstatus = tk.StringVar(value="")
            mode_var = tk.StringVar(value="competition")

            ttk.Label(gw, text="① 获取平台工具清单（第一条最准）",
                      font=("", 10, "bold")).pack(anchor="w", padx=12, pady=(10, 2))
            src_row = ttk.Frame(gw)
            src_row.pack(fill="x", padx=12)
            gstatus_lbl = ttk.Label(src_row, textvariable=gstatus, foreground="#0a6a9a")

            ttk.Label(gw, text="工具清单（每行一个；可直接粘贴，或在下面修正）",
                      foreground="#666").pack(anchor="w", padx=12, pady=(8, 0))
            names_box = tk.Text(gw, height=8, font=("Consolas", 9))
            names_box.pack(fill="both", expand=True, padx=12, pady=(2, 0))

            mode_row = ttk.Frame(gw)
            mode_row.pack(fill="x", padx=12, pady=(8, 0))
            ttk.Label(mode_row, text="② 平台模式").pack(side="left")
            ttk.Radiobutton(mode_row, text="正式比赛", variable=mode_var,
                            value="competition").pack(side="left")
            ttk.Radiobutton(mode_row, text="练习场", variable=mode_var,
                            value="practice").pack(side="left")
            ttk.Button(mode_row, text="③ 自动分析", command=lambda: _analyze()).pack(
                side="left", padx=(14, 0))

            ttk.Label(gw, text="生成的映射（JSON，点「应用」写回档案）",
                      foreground="#666").pack(anchor="w", padx=12, pady=(8, 0))
            prev_box = tk.Text(gw, height=10, font=("Consolas", 9))
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

            ttk.Button(src_row, text="从平台读取（最准）", command=_read_remote).pack(side="left")
            ttk.Button(src_row, text="选择截图 OCR…", command=_pick_image).pack(side="left", padx=(6, 0))
            gstatus_lbl.pack(side="left", padx=(10, 0))

            btns = ttk.Frame(gw)
            btns.pack(fill="x", padx=12, pady=(8, 10))
            ttk.Button(btns, text="应用", command=_apply_map).pack(side="right")
            ttk.Button(btns, text="取消", command=gw.destroy).pack(side="right", padx=(0, 8))

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
            ow.geometry("780x600")
            head = tk.StringVar(value="正在连接平台…")
            ttk.Label(ow, textvariable=head, font=("", 10, "bold")).pack(
                anchor="w", padx=12, pady=(10, 4))
            box = tk.Text(ow, font=("Consolas", 9))
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
                ttk.Button(bar, text="保存到档案", command=close_and_save).pack(side="right")
                ttk.Button(bar, text="关闭", command=ow.destroy).pack(side="right", padx=(0, 8))

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
        bf = ttk.Frame(win)
        bf.pack(fill="x", padx=14, pady=(10, 10))
        ttk.Label(bf, textvariable=status, foreground="#0a6a9a").pack(side="left")

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

        ttk.Button(bf, text="测试连接", command=_test).pack(side="left", padx=(10, 0))
        ttk.Button(bf, text="取消", command=win.destroy).pack(side="right")
        ttk.Button(bf, text="保存", command=_save).pack(side="right", padx=(0, 8))

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
        self.root.after(100, self._poll)

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
                self.lbl_conn.set("连接失败")
                messagebox.showerror("连接失败", item[1])
            elif kind == "challenges":
                self._on_challenges(item[1])
            elif kind == "challenges_err":
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
                if d.get("ok"):
                    self._board_set(cid, status="已解出", detail=str(d.get("flag") or ""))
                    self._log(f"✓ {d.get('id')}  {d.get('flag') or ''}")
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
                self._board_set(cid, tokens=tok, cost="-" if cost is None else cost)
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
            self.lbl_cost.set(f"费用：{cost}")
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
                self._log(f"{mark} {r.get('id')}  {detail}")
                cid = str(r.get("id") or "")
                if cid in self._board:
                    self._board_set(cid, status="已解出" if r.get("ok") else "未解出",
                                    detail=str(detail)[:60])
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
        w.tag_configure("sec", foreground="#1a4d80", font=("Consolas", 11, "bold"), spacing1=10, spacing3=4)
        w.tag_configure("meta_l", foreground="#8a8a8a", font=("Consolas", 10, "bold"))
        w.tag_configure("round", foreground="#9a5b00", font=("Consolas", 10, "bold"))
        w.tag_configure("rep_t", foreground="#111111", font=("Consolas", 10, "bold"))
        w.tag_configure("rep", lmargin1=18, lmargin2=18, spacing3=10)
        w.tag_configure("flag", foreground="#0a7a1a", font=("Consolas", 12, "bold"),
                        background="#e8f4e8", spacing1=8, spacing3=2)

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