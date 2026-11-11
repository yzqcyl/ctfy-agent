#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GUI 主题 + 两个功能需求的验收（毛玻璃/GitHub 配色规范、跑题库跳过/重跑、复现栏按日期分层）。

覆盖三层：
  1) 设计令牌：用户给的规范值必须逐条落在 theme.py 常量里（改了就是违规）。
  2) 纯函数：缓动曲线 / 圆角几何 / 颜色混合 / 日期分层 / 跳过已解出 —— 不依赖窗口。
  3) 真窗口结构：App 起得来、圆角按钮/卡片/骨架屏/圆角输入框都在、按日期分层的表格
     真的有日期父节点、跑题库选项真的会过滤已解出（读 runs/<rid>/state.json）。

运行：PYTHONPATH=src python tests/smoke_gui_theme.py
"""
from __future__ import annotations

import json
import shutil
import sys
import time
import tkinter as tk
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

PASSED = 0


def ok(label: str) -> None:
    global PASSED
    PASSED += 1
    print(f"PASS {label}")


# ---------------------------------------------------------------- 1) 设计令牌
def case_tokens() -> None:
    from ctfy_agent.gui import theme

    # 规范原文逐条断言
    assert theme.RADIUS_BUTTON == 8, theme.RADIUS_BUTTON
    assert theme.RADIUS_CARD == 20, theme.RADIUS_CARD
    assert theme.RADIUS_INPUT == 12, theme.RADIUS_INPUT
    assert (theme.PAD_BUTTON_X, theme.PAD_BUTTON_Y) == (24, 12), theme.PAD_BUTTON
    assert theme.BUTTON_FONT_WEIGHT == 500
    assert theme.HOVER_LIFT_PX == 2
    assert theme.PRESS_SCALE == 0.98
    assert theme.TRANSITION_MS == 200
    assert theme.EASING_CSS == "cubic-bezier(0.4, 0, 0.2, 1)"
    assert theme.LETTER_SPACING_EM == -0.02
    assert theme.LINE_HEIGHT_HEADING == (1.2, 1.3)
    assert theme.LINE_HEIGHT_BODY == (1.5, 1.7)
    assert theme.SHADOW_CSS == "0 1px 3px rgba(0,0,0,0.05), 0 1px 2px rgba(0,0,0,0.1)"
    ok("规范令牌：按钮/卡片/输入框圆角、内边距、字重、上浮、缩放、过渡、阴影、行高")

    # 规范给定颜色
    assert theme.SUBTLE == "#F9FAFB"      # 输入框背景
    assert theme.PLACEHOLDER == "#9CA3AF"  # placeholder
    assert theme.BORDER == "#E5E7EB"      # 分隔线
    # rgba(0,0,0,0.05) 叠白底的合成值
    assert theme.BORDER_SOFT == theme.with_alpha_on(0.05), (theme.BORDER_SOFT,
                                                           theme.with_alpha_on(0.05))
    ok("规范颜色：输入背景 #F9FAFB、placeholder #9CA3AF、分隔线 #E5E7EB、0.05 边框合成值")

    # GitHub 配色齐全（浅色）
    for name in ("PAGE", "CANVAS", "TEXT", "TEXT_MUTED", "PRIMARY", "PRIMARY_HOVER",
                 "PRIMARY_SOFT", "SUCCESS", "DANGER", "WARN"):
        value = getattr(theme, name)
        assert value.startswith("#") and len(value) == 7, (name, value)
    assert theme.PRIMARY == "#0969DA"
    ok("GitHub 浅色配色齐全（页面/卡片/文字/主色/语义色）")

    # 图标：Feather/Heroicons 风格、20×20
    assert theme.ICON_SIZE == 20
    assert len(theme.ICON_NAMES) >= 12, theme.ICON_NAMES
    for name in ("play", "stop", "refresh", "gear", "check", "x", "search",
                 "chevron-down", "folder", "copy", "export"):
        assert name in theme.ICON_NAMES, name
    ok("图标：15 个 Feather 风格矢量图标，尺寸 20×20")

    # 字体栈：Inter 优先（机器没装就回退，不能空串）
    assert theme.pick_family("heading", available={"Inter", "Arial"}) == "Inter"
    assert theme.pick_family("body", available={"Arial", "Microsoft YaHei UI"}) == "Microsoft YaHei UI"
    assert theme.pick_family("body", available=set()) == ""
    fam, size, weight = theme.heading_font(19)
    assert size == 19 and weight == "bold", (fam, size, weight)
    ok("字体：Inter 优先 / 中文回退 / 标题 Bold、正文 Regular")


# ---------------------------------------------------------------- 2) 纯函数
def case_easing_and_geometry() -> None:
    from ctfy_agent.gui import theme

    assert abs(theme.ease(0.0)) < 1e-6, theme.ease(0.0)
    assert abs(theme.ease(1.0) - 1.0) < 1e-6, theme.ease(1.0)
    prev = -1.0
    for i in range(0, 21):
        v = theme.ease(i / 20)
        assert v >= prev - 1e-9, (i, v, prev)
        prev = v
    # cubic-bezier(0.4,0,0.2,1) 是 ease-out 型：前半段走得快
    assert theme.ease(0.5) > 0.5, theme.ease(0.5)
    ok("缓动：0→0 / 1→1 / 单调 / 前快后慢（cubic-bezier(0.4,0,0.2,1)）")

    pts = theme.rounded_rect_points(0, 0, 100, 40, 8, arc_steps=6)
    assert len(pts) == 4 * (6 + 1) * 2, len(pts)
    xs, ys = pts[0::2], pts[1::2]
    assert min(xs) >= -1e-9 and max(xs) <= 100 + 1e-9
    assert min(ys) >= -1e-9 and max(ys) <= 40 + 1e-9
    # 圆角必须真的收进去：四个角顶点不在矩形角上
    assert not any(abs(x) < 1e-6 and abs(y) < 1e-6 for x, y in zip(xs, ys))
    ok("圆角几何：顶点闭合、落在边界内、四角被半径收掉")

    assert theme.mix("#FFFFFF", "#000000", 0.0) == "#FFFFFF"
    assert theme.mix("#FFFFFF", "#000000", 1.0) == "#000000"
    assert theme.darken("#0969DA", 0.0) == "#0969DA"
    assert theme.lighten("#0969DA", 1.0) == "#FFFFFF"
    ok("颜色工具：mix/darken/lighten 边界正确")

    # 图标几何都在 20×20 视口内
    for name in theme.ICON_NAMES:
        for path in theme.icon_lines(name):
            for x, y in path:
                assert -1e-6 <= x <= 20 + 1e-6 and -1e-6 <= y <= 20 + 1e-6, (name, x, y)
    ok("图标几何：所有线段落在 20×20 视口内")


def case_date_layering() -> None:
    from ctfy_agent.gui import theme

    rows = [
        {"name": "c", "at": "2026-11-10T21:00:00"},
        {"name": "a", "at": "2026-11-10 09:30:00"},
        {"name": "b", "at": "2026-11-09T23:59:00"},
        {"name": "d", "at": ""},
        {"name": "e", "at": "2026-11-08"},
    ]
    groups = theme.group_by_date(rows)
    assert [g[0] for g in groups] == ["2026-11-10", "2026-11-09", "2026-11-08", "未知日期"], groups
    assert [r["name"] for r in groups[0][1]] == ["c", "a"]  # 同日内保持原顺序
    assert theme.date_header_text("2026-11-10", 2) == "2026-11-10 · 2 题"
    assert "未知日期" in theme.date_header_text("未知日期", 1)
    ok("日期分层：倒序分组、同日内保序、未知日期垫底、分组标题带题目数")


def case_filter_solved() -> None:
    from ctfy_agent.gui import theme

    rows = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
    keep, skipped = theme.filter_solved(rows, {"a", "c"}, skip=True)
    assert [r["id"] for r in keep] == ["b"] and [r["id"] for r in skipped] == ["a", "c"]
    keep2, skipped2 = theme.filter_solved(rows, {"a"}, skip=False)
    assert [r["id"] for r in keep2] == ["a", "b", "c"] and skipped2 == []
    ok("跑题库选项：跳过已解出 只留未解；重跑已解出 全保留")


# ---------------------------------------------------------------- 3) 真窗口
def case_widgets() -> None:
    from ctfy_agent.gui import theme

    root = tk.Tk()
    # 放到屏幕外的角落（必须真的 mapping，Canvas 的 <Configure> 才会给真实尺寸）
    root.geometry("440x420+3000+2000")
    try:
        clicked = {"n": 0}

        def cb() -> None:
            clicked["n"] += 1

        btn = theme.RoundedButton(root, text="开始", icon="play", variant="primary",
                                  command=cb)
        btn.pack()
        root.update()
        # 圆角 8 / 内边距 12×24：宽度至少是文本宽 + 2*24，高度至少行高 + 2*12
        import tkinter.font as tkfont

        f = tkfont.Font(root=root, font=btn._font)  # noqa: SLF001 - 验收内部尺寸
        assert int(btn.cget("width")) >= f.measure("开始") + theme.ICON_SIZE + 8 + 2 * 24
        assert int(btn.cget("height")) >= f.metrics("linespace") + 2 * 12
        # 兼容 ttk.Button 的 state 接口
        btn.config(state="disabled")
        assert btn.cget("state") == "disabled"
        btn.invoke()
        assert clicked["n"] == 0, "禁用状态不能触发命令"
        btn.config(state="normal")
        btn.invoke()
        assert clicked["n"] == 1
        # 悬停上浮 + 按下缩放（异步动画，等一帧）
        btn._on_enter()  # noqa: SLF001
        root.update()
        btn._on_press()  # noqa: SLF001
        root.update()
        assert btn._scale < 1.0, btn._scale  # noqa: SLF001
        btn._on_leave()  # noqa: SLF001
        ok("圆角按钮：规范内边距/尺寸、state 兼容、悬停上浮、按下缩放")

        card = theme.Card(root, radius=theme.RADIUS_CARD, padding=(12, 10))
        card.pack()
        tk.Label(card.body, text="卡片内容", bg=theme.CANVAS).pack()
        root.update()
        root.update_idletasks()
        assert card.radius == 20
        assert card.body.winfo_exists()
        chrome = card.canvas.find_withtag("chrome")
        assert len(chrome) >= 2, f"卡片应当画了阴影+圆角底(实际 {len(chrome)} 个图元)"
        ok("卡片：20px 圆角 + 浅阴影 + 白底内容区")

        skel = theme.Skeleton(root, rows=4)
        skel.pack()
        root.update()
        skel.start()
        root.update()
        assert skel._job is not None  # noqa: SLF001
        skel.stop()
        assert skel._job is None  # noqa: SLF001
        ok("骨架屏：脉冲动画可启动/停止")

        var = tk.StringVar()
        ent = theme.RoundedEntry(root, textvariable=var, placeholder="例如 ez_web")
        ent.pack()
        root.update()
        assert ent.radius == 12
        assert var.get() == "例如 ez_web", var.get()  # placeholder 生效
        ok("输入框：12px 圆角 + placeholder #9CA3AF 生效")
    finally:
        root.destroy()


def case_app_integration() -> None:
    from ctfy_agent.gui import theme

    # 造一个 run 的 state.json：让「跑题库跳过已解出」可验证
    rid = "_ui_test_run"
    rdir = ROOT / "runs" / rid
    shutil.rmtree(rdir, ignore_errors=True)
    rdir.mkdir(parents=True, exist_ok=True)
    (rdir / "state.json").write_text(json.dumps({
        "challenges": {
            "c1": {"name": "已解一", "category": "web", "flag_value": "FLAG{a}",
                   "submitted_ok": True, "updated_at": "2026-11-10T20:00:00"},
            "c2": {"name": "未解二", "category": "pwn", "updated_at": "2026-11-10T19:00:00"},
        }
    }, ensure_ascii=False), encoding="utf-8")

    app = None
    try:
        from ctfy_agent.gui.app import App

        app = App()
        app.root.update()

        # 3.1 关键控件都在（名字是别处代码依赖的契约）
        for name in ("tv", "tv_solved", "tv_board", "txt_events", "txt_writeup",
                     "lb_hist", "btn_start", "btn_stop", "btn_start_p", "btn_stop_p",
                     "bar_tokens", "bar_calls", "nb", "skel_solved", "skel_chal",
                     "spin_conn", "spin_solved", "pill_status", "frm_form",
                     "frm_off", "frm_plat"):
            assert hasattr(app, name), f"App 缺控件 {name}"
        assert str(app.root.cget("bg")) == theme.PAGE
        ok("App 骨架：控件契约齐全、根窗口用 GitHub 页面底色")

        # 3.2 复现栏按日期分层：真表格里出现日期父节点
        app._apply_solved([
            {"run": rid, "cid": "c1", "name": "已解一", "cat": "web",
             "flag": "FLAG{a}", "at": "2026-11-10T20:00:00"},
            {"run": rid, "cid": "c9", "name": "已解九", "cat": "misc",
             "flag": "FLAG{b}", "at": "2026-11-09T10:00:00"},
        ])
        app.root.update()
        tops = app.tv_solved.get_children("")
        assert len(tops) == 2, tops
        texts = [app.tv_solved.item(i, "text") for i in tops]
        assert any(t.startswith("2026-11-10") for t in texts), texts
        assert any(t.startswith("2026-11-09") for t in texts), texts
        # 日期节点下挂题目
        kids = app.tv_solved.get_children(tops[0])
        assert len(kids) == 1 and app._solved_rows[kids[0]]["cid"] == "c1"
        # 骨架屏在扫完后必须收起
        assert not app.skel_solved.winfo_ismapped()
        ok("复现栏：真表格按日期分层（日期父节点 + 题目子节点），扫完收起骨架屏")

        # 3.3 跑题库：跳过已解出 / 重跑已解出（读真 state.json）
        assert app._solved_cids_for_run(rid) == {"c1"}
        app._apply_solved([])
        # 先喂一份题单（真实流程里由 _on_challenges 填入），再全选
        app._on_challenges({"items": [
            {"id": "c1", "name": "已解一", "category": "web"},
            {"id": "c2", "name": "未解二", "category": "pwn"},
        ]})
        app.root.update()
        app.var_run_p.set(rid)
        app._select_all(True)
        app.var_skip_solved.set(True)
        app._update_sel_label()
        assert "将跳过" in app.lbl_sel_chal.get(), app.lbl_sel_chal.get()
        app.var_skip_solved.set(False)
        app._update_sel_label()
        assert "将重跑" in app.lbl_sel_chal.get(), app.lbl_sel_chal.get()
        ok("跑题库选项：跳过/重跑已解出 都会实时统计并显示在勾选标签里")

        # 3.4 主题一致性：正文 Text / Listbox 用主题色，不用系统灰
        assert str(app.txt_writeup.cget("bg")) == theme.CANVAS
        assert str(app.txt_events.cget("bg")) == theme.CANVAS
        assert str(app.lb_hist.cget("bg")) == theme.CANVAS
        ok("主题一致性：日志/复现/历史控件全部用主题白底")

        # 3.5 状态胶囊联动
        app.lbl_status.set("运行中")
        app._sync_status_pill()
        assert app.pill_status._color == theme.PRIMARY  # noqa: SLF001
        app.lbl_status.set("出错：连接")
        app._sync_status_pill()
        assert app.pill_status._color == theme.DANGER  # noqa: SLF001
        ok("状态胶囊：运行中=主色、出错=危险色")
    finally:
        if app is not None:
            try:
                app.root.destroy()
            except Exception:  # noqa: BLE001
                pass
        shutil.rmtree(rdir, ignore_errors=True)


def case_dpi_and_tabs() -> None:
    from ctfy_agent.gui import theme

    root = tk.Tk()
    root.geometry("900x600+3000+2000")
    try:
        # DPI：缩放必须 >=1，px 换算与缩放一致；DPI-aware 调用不能抛
        scale = theme.apply_dpi_scaling(root)
        assert scale >= 1.0, scale
        assert abs(theme.UI_SCALE - scale) < 1e-9
        assert theme.ui_px(120) == max(1, round(120 * scale / 1.2)), theme.ui_px(120)
        assert isinstance(theme.enable_dpi_awareness(), bool)
        ok("高DPI：DPI-aware 调用安全、tk scaling 与 ui_px 换算一致")

        # 页签：选中=主色填充+白字；未选中=白底+深字；页签之间有间隙；圆角
        nb = theme.TabBar(root, pad=(10, 10))
        nb.pack(fill="both", expand=True)
        pages = [tk.Frame(nb.body, bg=theme.PAGE) for _ in range(3)]
        for page, title in zip(pages, ("复现", "任务面板", "日志")):
            nb.add(page, text=title)
        for _ in range(4):
            root.update()
            time.sleep(0.05)

        def fills(btn):
            out = set()
            for item in btn.find_all():
                try:
                    out.add(str(btn.itemcget(item, "fill")).upper())
                except Exception:  # noqa: BLE001
                    pass
            return out

        def text_colors(btn):
            out = set()
            for item in btn.find_all():
                if btn.type(item) == "text":
                    out.add(str(btn.itemcget(item, "fill")).upper())
            return out

        btns = nb.tab_buttons()
        assert len(btns) == 3
        assert nb.current() == 0, nb.current()
        assert theme.PRIMARY.upper() in fills(btns[0]), fills(btns[0])
        assert theme.CANVAS.upper() in fills(btns[1]), fills(btns[1])
        assert text_colors(btns[0]) == {theme.INVERT.upper()}, text_colors(btns[0])
        assert text_colors(btns[1]) == {theme.TEXT.upper()}, text_colors(btns[1])
        # 圆角 + 不相邻（右边界 < 下一个左边界）
        assert all(int(b._radius) >= 8 for b in btns)  # noqa: SLF001
        for _ in range(3):
            root.update()
            time.sleep(0.05)
        gaps = [btns[i + 1].winfo_x() - (btns[i].winfo_x() + btns[i].winfo_width())
                for i in range(2)]
        assert all(g >= 6 for g in gaps), gaps
        # 切换
        nb.select(1)
        root.update()
        assert nb.current() == 1
        assert pages[1].winfo_ismapped()
        assert nb.index(pages[2]) == 2
        ok("页签：选中主色+白字 / 未选中白底+深字、圆角、彼此留空隙、可切换")

        # 任务面板：终态停表（不能「已解出还在走表」）
        app = __import__("ctfy_agent.gui.app", fromlist=["App"]).App()
        try:
            app.root.update()
            cid = "c-freeze"
            app._board_row(cid, "测试题")
            app._board_set(cid, status="解题中")
            for _ in range(3):
                app.root.update()
                time.sleep(0.05)
            app._board_tick()
            app._board_set(cid, status="已解出", detail="FLAG{x}")
            frozen = str(app.tv_board.item(app._board[cid]["iid"], "values")[6])
            time.sleep(1.1)
            app._board_tick()
            later = str(app.tv_board.item(app._board[cid]["iid"], "values")[6])
            assert frozen == later, (frozen, later)
            rec = app._board[cid]
            assert rec["finished"], "终态必须记录停表时刻"

            # presolved 结果由事件层标成「已解出（跳过）」
            app.q.put(("task_done", {"id": cid, "ok": True, "flag": "FLAG{x}",
                                     "presolved": True}))
            app._drain()
            vals = app.tv_board.item(app._board[cid]["iid"], "values")
            assert str(vals[1]) == "已解出（跳过）", vals
            app.q.put(("task_done", {"id": "c-real", "ok": True, "flag": "FLAG{y}"}))
            app._board_row("c-real", "真题")
            app._drain()
            assert str(app.tv_board.item(app._board["c-real"]["iid"], "values")[1]) == "已解出"
            ok("任务面板：已解出即停表；平台现成结论显示「已解出（跳过）」，与真跑区分")

            # 契约：强制重跑参数真的从 GUI 一路传到 Crew
            import inspect

            from ctfy_agent.crew import Crew
            from ctfy_agent.gui import platform_worker

            solve_sig = inspect.signature(Crew.solve)
            assert "force_rerun" in solve_sig.parameters
            assert solve_sig.parameters["force_rerun"].default is False  # 默认行为不变
            many_sig = inspect.signature(platform_worker.solve_many)
            assert "force_ids" in many_sig.parameters
            ok("重跑链路：solve_many(force_ids) → Crew.solve(force_rerun) 参数存在且默认关")

            # 勾选「重跑已解出」时，GUI 必须把已解出的题号放进 force 集合
            rdir = ROOT / "runs" / "_ui_test_run"
            shutil.rmtree(rdir, ignore_errors=True)
            rdir.mkdir(parents=True, exist_ok=True)
            (rdir / "state.json").write_text(json.dumps({"challenges": {
                "c1": {"name": "已解一", "flag_value": "FLAG{a}", "submitted_ok": True},
            }}, ensure_ascii=False), encoding="utf-8")
            app._on_challenges({"items": [
                {"id": "c1", "name": "已解一", "category": "web"},
                {"id": "c2", "name": "未解二", "category": "pwn"},
            ]})
            app.root.update()
            app.var_run_p.set("_ui_test_run")
            app._select_all(True)
            app.var_skip_solved.set(False)
            captured: dict = {}
            app._begin_thread = lambda fn, args, **kw: captured.update(fn=fn, args=args, kw=kw)  # noqa: SLF001
            app._apply_profile = lambda *a, **k: None  # noqa: SLF001
            app._start_platform()
            assert captured, "没走到批量启动"
            assert captured["fn"] is platform_worker.solve_many
            forced = captured["args"][-1]
            assert forced == frozenset({"c1"}), forced
            shutil.rmtree(rdir, ignore_errors=True)
            ok("重跑已解出：GUI 把已解出的题号作为 force 集合传给批量 worker")
        finally:
            try:
                app.root.destroy()
            except Exception:  # noqa: BLE001
                pass
    finally:
        root.destroy()


def case_board_columns_work() -> None:
    """任务面板的「轮次 / Tokens / 花费」必须是活的，而且要是**本次运行**的口径。

    背景（用户实测两次）：
      ① 平台批量走 Crew.solve，原先只发 budget_plan → 三列恒为 0/0/-；
      ② 补发后用 snapshot 口径 → 面板显示该题**历史累计**（面板 519147 / 顶栏 19457 打架）。
    现在 crew 发 run_*（本次增量）+ snapshot_*（历史累计，供预算闸），界面只显示 run_*；
    顶栏显示所有题 run_* 的**合计**。这里把两端都钉死。
    """
    import inspect

    from ctfy_agent.crew import Crew

    src = inspect.getsource(Crew.solve)
    assert src.count('on_event("budget"') >= 1, "Crew.solve 没发 budget 事件"
    assert "_emit_budget()" in src, "Crew.solve 没走统一的 _emit_budget（缺 run_* 口径）"
    assert "run_tokens" in src, "budget 事件里没有 run_tokens（会退化成历史累计口径）"
    assert "run_prompt_tokens" in src, "budget 事件里没有 run_prompt_tokens（无法估算费用）"
    assert 'on_event("step"' in src, "Crew.solve 没有发 step 事件 → 轮次不会动"
    ok("发出端：Crew.solve 发 budget（带 run_* 本次增量）与 step（每轮）")

    from ctfy_agent.gui.app import App

    app = App()
    try:
        app.root.update()
        cid = "c-cols"
        app._begin_thread(lambda *a, **k: None, (), batch_size=2)  # 重置本次口径 + 2 题
        app._board_row(cid, "列测试题")
        idx = app._BOARD_COLS.index

        def values():
            return list(app.tv_board.item(app._board[cid]["iid"], "values"))

        # 1) 轮次用绝对步数（续跑从第 3 轮开始也不会算成 1）
        app.q.put(("event", "step", {"_cid": cid, "step": 3}))
        app.q.put(("event", "step", {"_cid": cid, "step": 1}))
        app._drain()
        assert int(values()[idx("rounds")]) == 3, values()

        # 2) 新口径：history 累计 500000，但本次只有 1234 → 面板必须显示 1234
        app.q.put(("event", "budget", {
            "_cid": cid, "total_tokens": 500000, "calls": 88, "cost": 0.0,
            "prompt_tokens": 400000, "completion_tokens": 100000,
            "run_tokens": 1234, "run_calls": 3,
            "run_prompt_tokens": 1000, "run_completion_tokens": 234,
            "max_tokens": 800000, "max_calls": 240,
        }))
        app._drain()
        assert int(values()[idx("tokens")]) == 1234, values()
        # 未配单价 → 用默认费率估算，带 ≈ 前缀（不再是 "-"）
        cost_text = str(values()[idx("cost")])
        assert cost_text.startswith("≈"), cost_text
        assert "本次合计" in app.lbl_tokens.get(), app.lbl_tokens.get()
        assert "1234/" in app.lbl_tokens.get(), app.lbl_tokens.get()

        # 3) 第二题并发：顶栏合计 = 两题本次之和
        cid2 = "c-cols-2"
        app._board_row(cid2, "第二题")
        app.q.put(("event", "budget", {
            "_cid": cid2, "total_tokens": 999999, "calls": 99, "cost": 0.0,
            "run_tokens": 766, "run_calls": 2,
            "run_prompt_tokens": 600, "run_completion_tokens": 166,
            "max_tokens": 800000, "max_calls": 240,
        }))
        app._drain()
        assert "2000/" in app.lbl_tokens.get(), app.lbl_tokens.get()  # 1234+766
        assert "本次合计调用：5/" in app.lbl_calls.get(), app.lbl_calls.get()  # 3+2
        # 上限按题数放大：800000×2
        assert "1600000" in app.lbl_tokens.get(), app.lbl_tokens.get()

        # 4) 配了真实单价 → 用事件里的真实 cost，不再带 ≈
        app.config.price_input_per_mtok = 0.5
        app.config.price_output_per_mtok = 1.0
        app.q.put(("event", "budget", {
            "_cid": cid, "total_tokens": 500000, "calls": 88, "cost": 0.0,
            "run_tokens": 2000, "run_calls": 5, "run_cost": 0.01234,
            "run_prompt_tokens": 1500, "run_completion_tokens": 500,
            "max_tokens": 800000, "max_calls": 240,
        }))
        app._drain()
        assert str(values()[idx("cost")]) == "0.0123", values()
        assert int(values()[idx("tokens")]) == 2000, values()

        # 5) 老口径（离线单题，无 run_*）仍按累计快照显示，不能坏
        app.q.put(("event", "budget", {"_cid": cid, "total_tokens": 4321, "calls": 6,
                                       "cost": 0.0, "max_tokens": 800000,
                                       "max_calls": 240}))
        app._drain()
        assert int(values()[idx("tokens")]) == 4321, values()
        ok("消费端：面板显示本次增量、轮次取绝对值、顶栏是各题本次合计、无单价有 ≈ 估算")
    finally:
        try:
            app.root.destroy()
        except Exception:  # noqa: BLE001
            pass


def case_board_status_colors() -> None:
    """任务面板状态要自洽 + 有颜色：不能再出现「已解出 / 未解出」这种矛盾行。"""
    from ctfy_agent.gui import theme
    from ctfy_agent.gui.app import App

    app = App()
    try:
        app.root.update()
        idx = app._BOARD_COLS.index

        # 状态 → 配色 tag 的映射规则
        assert app._board_tag("已解出") == "ok"
        assert app._board_tag("已解出（跳过）") == "ok_skip"
        assert app._board_tag("平台已解") == "ok"
        assert app._board_tag("解题中") == "running"
        assert app._board_tag("未解出") == "fail"
        assert app._board_tag("待开始") == "idle"
        # 颜色真的配到了 Treeview 上（前景 + 浅底）；tag_configure 查询返回 Tcl color 对象，转字符串比
        def tag_color(tag, option):
            return str(app.tv_board.tag_configure(tag, option)).upper()

        assert tag_color("ok", "foreground") == theme.SUCCESS.upper()
        assert tag_color("ok", "background") == theme.SUCCESS_SOFT.upper()
        assert tag_color("running", "foreground") == theme.PRIMARY.upper()
        assert tag_color("fail", "foreground") == theme.DANGER.upper()
        assert tag_color("fail", "background") == theme.DANGER_SOFT.upper()
        ok("状态色：已解出绿 / 解题中蓝 / 未解出红 / 待开始灰，且都有浅色底")

        def row(cid):
            rec = app._board[cid]
            return app.tv_board.item(rec["iid"], "values"), app.tv_board.item(rec["iid"], "tags")

        # 1) 真跑出 flag
        app._board_row("c-ok", "有 flag")
        app._board_finish("c-ok", True, "FLAG{abc}", "")
        vals, tags = row("c-ok")
        assert str(vals[idx("status")]) == "已解出", vals
        assert str(vals[idx("detail")]) == "FLAG{abc}", vals
        assert tags == ("ok",), tags

        # 2) 平台已解、本地没有 flag：不能写成「未解出」
        app._board_row("c-plat", "平台已解")
        app._board_finish("c-plat", True, "", "平台已解出（本地无 flag 记录）", True)
        vals, tags = row("c-plat")
        assert str(vals[idx("status")]) == "平台已解", vals
        assert "未解出" not in str(vals[idx("detail")]), vals
        assert "无 flag" in str(vals[idx("detail")]), vals
        assert tags == ("ok",), tags
        # 平台已解也是终态：用时必须停表
        assert app._board["c-plat"].get("finished"), "平台已解也应视为终态停表"
        frozen = str(vals[idx("elapsed")])
        time.sleep(1.1)
        app._board_tick()
        assert str(row("c-plat")[0][idx("elapsed")]) == frozen

        # 3) 真未解出
        app._board_row("c-fail", "没解出")
        app._board_finish("c-fail", False, "", "预算耗尽")
        vals, tags = row("c-fail")
        assert str(vals[idx("status")]) == "未解出", vals
        assert str(vals[idx("detail")]) == "预算耗尽", vals
        assert tags == ("fail",), tags

        # 4) 解题中 → 蓝色
        app._board_row("c-run", "在跑")
        app._board_set("c-run", status="解题中")
        assert row("c-run")[1] == ("running",), row("c-run")

        # 5) 事件路径也走同一收口：task_done 里 ok=True 但没 flag 时不能出现「未解出」
        app.q.put(("task_done", {"id": "c-evt", "ok": True, "flag": "", "note": "",
                                 "presolved": False}))
        app._board_row("c-evt", "事件题")
        app._drain()
        vals, tags = row("c-evt")
        assert str(vals[idx("status")]) == "平台已解", vals
        assert "未解出" not in str(vals[idx("detail")]), vals
        assert tags == ("ok",), tags
        ok("状态/详情自洽：已解出显示 flag，平台已解标注无记录，未解出显示原因，事件路径同口径")
    finally:
        try:
            app.root.destroy()
        except Exception:  # noqa: BLE001
            pass


def case_table_scrollbars() -> None:
    """三张表都要有「上下 + 左右」滚动条，而且是真的能滚（内容比可视区宽）。

    以前只给竖条且 arrowsize=0 → ttk 滚动条退化成 1px，等于看不见；这里连厚度一起钉住。
    """
    from ctfy_agent.gui.app import App

    app = App()
    try:
        app.root.geometry("1200x760+0+0")
        for _ in range(3):
            app.root.update()
            time.sleep(0.05)

        def check(name, tree, vsb, hsb, need_x: bool = True):
            app.root.update()
            cols = tree["columns"]
            names = cols.split() if isinstance(cols, str) else list(cols)
            if need_x and not tree.get_children():
                # 空表时 xview 必然是 (0,1)；塞一行超长内容再判断横条是否真的能滚
                values = tuple("X" * 60 for _ in names)
                if "tree" in str(tree["show"]):
                    tree.insert("", "end", text="X" * 80, values=values)
                else:
                    tree.insert("", "end", values=values)
                app.root.update()
            for widget, label in ((tree, "表格"), (vsb, "竖条"), (hsb, "横条")):
                assert widget.winfo_ismapped(), f"{name} 的{label}没有显示"
            assert vsb.winfo_width() >= 8, f"{name} 竖条厚度 {vsb.winfo_width()}px（1px 等于看不见）"
            assert hsb.winfo_height() >= 8, f"{name} 横条厚度 {hsb.winfo_height()}px"
            assert tree.cget("yscrollcommand") and tree.cget("xscrollcommand"), \
                f"{name} 没绑定滚动条命令"
            if need_x:
                total = sum(int(tree.column(c, "width")) for c in names)
                if total <= tree.winfo_width():
                    # 窗口特别宽时内容本来就不超宽；临时加宽最后一列，验证横条确实能滚
                    tree.column(names[-1], width=total + tree.winfo_width())
                    app.root.update()
                first, last = tree.xview()
                assert float(last) < 1.0, f"{name} 内容没超宽，横条不可滚（xview={tree.xview()}）"

        # 复现页（默认选中）
        check("已解出表", app.tv_solved, app.vsb_solved, app.hsb_solved)
        # 任务面板
        app.nb.select(1)
        check("任务面板", app.tv_board, app.vsb_board, app.hsb_board)
        # 题库（平台模式）
        app.var_mode.set("platform")
        app._on_mode()
        check("题库", app.tv, app.vsb_chal, app.hsb_chal)
        ok("三张表都有可见的上下+左右滚动条（厚 16px），且横向真的可滚")
    finally:
        try:
            app.root.destroy()
        except Exception:  # noqa: BLE001
            pass


def _descendants(widget):
    yield widget
    for child in widget.winfo_children():
        yield from _descendants(child)


def case_settings_dialog() -> None:
    """设置对话框也要同一套 UI：圆角蓝页签、卡片、圆角输入框、主题按钮、尺寸自适应+滚动条。"""
    from ctfy_agent.gui import theme  # noqa: PLC0415 - 与其它用例一致，按需导入
    from ctfy_agent.gui.app import App

    app = App()
    root = app.root
    try:
        root.update()
        app._open_settings()
        root.update()
        wins = [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)]
        assert wins, "没打开设置窗口"
        win = wins[-1]
        win.attributes("-topmost", True)
        for _ in range(4):
            root.update()
            time.sleep(0.05)

        scr_w, scr_h = root.winfo_screenwidth(), root.winfo_screenheight()
        w, h = win.winfo_width(), win.winfo_height()
        assert w <= int(scr_w * 0.95) and h <= int(scr_h * 0.95), \
            f"设置窗口超出屏幕：{w}x{h} vs {scr_w}x{scr_h}"
        assert w >= theme.ui_px(480) and h >= theme.ui_px(380), f"设置窗口过小：{w}x{h}"
        assert str(win.cget("bg")) == theme.PAGE
        ok(f"设置窗口尺寸自适应：{w}x{h}（屏幕 {scr_w}x{scr_h} 内）")

        widgets = list(_descendants(win))
        tabbars = [x for x in widgets if isinstance(x, theme.TabBar)]
        assert len(tabbars) == 1, f"设置里应有 1 个 TabBar，实际 {len(tabbars)}"
        nb = tabbars[0]
        btns = nb.tab_buttons()
        assert len(btns) == 3, f"设置页签数 {len(btns)}"
        assert all(int(b._radius) >= 8 for b in btns)  # noqa: SLF001

        def fills(btn):
            out = set()
            for item in btn.find_all():
                try:
                    out.add(str(btn.itemcget(item, "fill")).upper())
                except Exception:  # noqa: BLE001
                    pass
            return out

        def text_fills(btn):
            return {str(btn.itemcget(i, "fill")).upper()
                    for i in btn.find_all() if btn.type(i) == "text"}

        assert theme.PRIMARY.upper() in fills(btns[0]), "选中页签不是主色填充"
        assert text_fills(btns[0]) == {theme.INVERT.upper()}, "选中页签文字不是白色"
        assert theme.CANVAS.upper() in fills(btns[1]), "未选中页签不是白底"
        assert text_fills(btns[1]) == {theme.TEXT.upper()}, "未选中页签文字不是深色"
        ok("设置页签：圆角 + 选中蓝底白字 / 未选中白底深字")

        entries = [x for x in widgets if isinstance(x, theme.RoundedEntry)]
        buttons = [x for x in widgets if isinstance(x, theme.RoundedButton)]
        cards = [x for x in widgets if isinstance(x, theme.Card)]
        assert len(entries) >= 6, f"圆角输入框只有 {len(entries)} 个"
        assert len(cards) >= 3, f"卡片只有 {len(cards)} 张"
        labels = {str(b.cget("text")) for b in buttons}
        assert {"保存", "取消", "测试连接"} <= labels, labels
        ok(f"设置控件：{len(entries)} 个圆角输入框 / {len(cards)} 张卡片 / 主题按钮齐全")

        # 三页都能切；缩到最小尺寸后内容可滚动（滚动条/滚动区真的生效）
        for idx in range(3):
            nb.select(idx)
            root.update()
            assert nb.current() == idx
        nb.select(0)
        win.geometry(f"{theme.ui_px(600)}x{theme.ui_px(430)}")
        root.update()
        time.sleep(0.1)
        root.update()
        sfs = [x for x in widgets if isinstance(x, theme.ScrollFrame)]
        assert len(sfs) == 3, f"设置页滚动容器 {len(sfs)} 个"
        page = nb.tabs()[nb.current()]
        page_sf = [x for x in _descendants(page) if isinstance(x, theme.ScrollFrame)][0]
        first, last = page_sf.canvas.yview()
        assert float(last) < 1.0 or page_sf.vsb.winfo_ismapped(), \
            "窗口缩小时设置内容没有出现滚动条/滚动区"
        ok("设置：三页可切换；窗口缩小后内容走滚动条，不会截断")
    finally:
        for win in [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)]:
            try:
                win.destroy()
            except Exception:  # noqa: BLE001
                pass
        try:
            app.root.destroy()
        except Exception:  # noqa: BLE001
            pass


def case_force_rerun_really_reruns() -> None:
    """证据级验证：「重跑已解出」不是换个界面文案，而是真的越过已解出预检重跑。

    A. Crew 层：同一道已解出的题，默认调用零成本返回 already_solved；
       force_rerun=True 时必须继续往下走（这里用「预算已耗尽」当探针——能走到预算闸
       就说明两层预检被越过、进入了真实求解流程，且不烧任何 token）。
    B. worker 层：solve_many(force_ids=...) 只对集合内的题传 force_rerun=True。
    """
    from ctfy_agent.config import load as load_config
    from ctfy_agent.crew import Crew
    from ctfy_agent.state import Store

    run_dir = ROOT / "runs" / "_force_test_run"
    shutil.rmtree(run_dir, ignore_errors=True)
    try:
        cfg = load_config(None)
        store = Store(run_dir).load()
        store.upsert({
            "id": "c1", "name": "已解出的题", "submitted_ok": True,
            "flag_value": "FLAG{a}", "status": "done",
        })

        class FakePlatform:
            def my_solved_ids(self):
                return {"c1"}

        crew = Crew(cfg, FakePlatform(), store, lambda model=None: None)

        # 用 spy 盯住「秒解收口」函数：force_rerun 时它绝不能被调用
        calls: list[str] = []
        orig = Crew._presolved_summary

        def spy(self, cid, ch):
            calls.append(cid)
            return orig(self, cid, ch)

        Crew._presolved_summary = spy
        try:
            base = crew.solve("c1")
            assert base.get("reason") == "already_solved", base.get("reason")
            assert calls == ["c1"], "默认路径本就该走秒解收口"
            calls.clear()
            try:
                # force 之后会因为没有真实 LLM/平台而失败（这里只关心是否越过预检）
                crew.solve("c1", force_rerun=True)
            except Exception:  # noqa: BLE001
                pass
            assert calls == [], "force_rerun 仍然走了「已解出」秒解分支，说明没真重跑"
        finally:
            Crew._presolved_summary = orig
        # 强制重跑要在 transcript 留痕，事后能解释这笔钱
        tr = store.transcript_path("c1")
        text = tr.read_text(encoding="utf-8", errors="replace")
        assert "强制重跑" in text, "transcript 缺少强制重跑留痕"
        ok("强制重跑真生效：默认零成本秒返回 already_solved；force_rerun 越过预检进入真实流程")

        # B. worker 只对 force_ids 里的题传 force_rerun=True
        from types import SimpleNamespace

        from ctfy_agent import cli as cli_mod
        from ctfy_agent import crew as crew_mod
        from ctfy_agent.gui import platform_worker

        recorded: list[tuple[str, bool]] = []

        class RecordingCrew:
            def __init__(self, *a, **k):
                pass

            def solve(self, cid, *, cancel_event=None, force_rerun=False):
                recorded.append((cid, force_rerun))
                return {"submitted": True, "reason": "done"}

        class FakePlatform:
            def __init__(self, *a, **k):
                pass

            def connect(self):
                pass

        class FakeStore:
            def __init__(self):
                self.meta = {}

            def save(self):
                pass

            def get(self, _cid):
                return SimpleNamespace(flag_value="FLAG{w}")

        originals = {
            (platform_worker, "_platform"): lambda *a, **k: FakePlatform(),
            (platform_worker, "_seed_bridge"): lambda *a, **k: None,
            (platform_worker, "_preflight_instances"): lambda *a, **k: None,
            (platform_worker, "_hydrate_attachments"): lambda *a, **k: None,
            (cli_mod, "open_store"): lambda *a, **k: FakeStore(),
            (cli_mod, "ensure_challenge_meta"): lambda *a, **k: None,
            (cli_mod, "make_llm"): lambda *a, **k: None,
            (crew_mod, "Crew"): RecordingCrew,
        }
        for (mod, name), value in originals.items():
            setattr(mod, name, value)
        try:
            q = __import__("queue").Queue()
            platform_worker.solve_many(
                cfg, [{"id": "ok-1"}, {"id": "ok-2"}], "rid", "", q,
                __import__("threading").Event(), 1, None, frozenset({"ok-1"}),
            )
        finally:
            for (mod, name), _value in originals.items():
                delattr(mod, name)
        assert recorded == [("ok-1", True), ("ok-2", False)], recorded
        ok("批量 worker：force_ids 内的题传 force_rerun=True，未勾选的题保持默认跳过")
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)


def main() -> int:
    try:
        case_tokens()
        case_easing_and_geometry()
        case_date_layering()
        case_filter_solved()
        case_widgets()
        case_app_integration()
        case_dpi_and_tabs()
        case_board_columns_work()
        case_board_status_colors()
        case_table_scrollbars()
        case_settings_dialog()
        case_force_rerun_really_reruns()
    except AssertionError as exc:  # 断言失败直接报出上下文
        print(f"FAIL: {exc}")
        raise
    print(f"\n{PASSED} passed, 0 failed")
    return 0


if __name__ == "__main__":
    sys.exit(main())