#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GUI 预览/体检：把界面窗口置顶后截自己的客户区，并打印关键控件几何。

用途
  1) 改完 gui 后不靠肉眼也能确认「控件都在、有尺寸、没溢出、主色真的画上了」；
  2) 产出截图给评审/交接看。

用法（项目根目录）
  PYTHONPATH=src python tools/gui_preview.py                 # 默认 1320x900 → runs/_gui_preview.png
  PYTHONPATH=src python tools/gui_preview.py --size 1400x940
  PYTHONPATH=src python tools/gui_preview.py --out docs/x.png --bmp   # 没装 Pillow 时存 BMP

退出码：0 = 体检通过；1 = 有布局/配色问题（同时打印 BAD 列表）。
"""
from __future__ import annotations

import argparse
import ctypes
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from ctfy_agent.gui import theme  # noqa: E402

CHECKED = (
    ("btn_start", "开始"),
    ("btn_stop", "停止"),
    ("tv", "题库"),
    ("tv_solved", "已解出"),
    ("txt_writeup", "复现正文"),
    ("txt_events", "日志"),
    ("lb_hist", "历史"),
    ("pill_status", "状态胶囊"),
)


def _grab(root, out: Path) -> tuple[int, int, str]:
    """截 root 客户区；有 Pillow 存 PNG，否则用纯 stdlib 写 BMP。

    Windows 上优先 PrintWindow(PW_RENDERFULLCONTENT)：抓的是窗口自己的内容，
    即使窗口被别的窗口压住也不会截到别人（屏幕 BitBlt 会被遮挡影响）。
    """
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    x, y = root.winfo_rootx(), root.winfo_rooty()
    w, h = root.winfo_width(), root.winfo_height()
    hdc_screen = user32.GetDC(0)
    hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
    bmp = gdi32.CreateCompatibleBitmap(hdc_screen, w, h)
    gdi32.SelectObject(hdc_mem, bmp)
    hwnd = int(root.winfo_id())
    rendered = False
    try:
        rendered = bool(user32.PrintWindow(hwnd, hdc_mem, 2))
    except Exception:  # noqa: BLE001
        rendered = False
    if not rendered:  # 兜底：直接抓屏幕（窗口置顶时可用）
        gdi32.BitBlt(hdc_mem, 0, 0, w, h, hdc_screen, x, y, 0x00CC0020)

    class BIH(ctypes.Structure):
        _fields_ = [("biSize", ctypes.c_ulong), ("biWidth", ctypes.c_long),
                    ("biHeight", ctypes.c_long), ("biPlanes", ctypes.c_ushort),
                    ("biBitCount", ctypes.c_ushort), ("biCompression", ctypes.c_ulong),
                    ("biSizeImage", ctypes.c_ulong), ("biXPelsPerMeter", ctypes.c_long),
                    ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", ctypes.c_ulong),
                    ("biClrImportant", ctypes.c_ulong)]

    bi = BIH()
    bi.biSize = ctypes.sizeof(BIH)
    bi.biWidth = w
    bi.biHeight = -h  # 负数 = top-down，和屏幕坐标一致
    bi.biPlanes = 1
    bi.biBitCount = 32
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(hdc_mem, bmp, 0, h, buf, ctypes.byref(bi), 0)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(hdc_mem)
    user32.ReleaseDC(0, hdc_screen)

    try:
        from PIL import Image  # noqa: PLC0415 - 可选依赖

        img = Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1).convert("RGB")
        img.save(out)
        return w, h, "PNG"
    except Exception:  # noqa: BLE001 - 没装 Pillow 就用 BMP 兜底
        bmp_out = out if out.suffix.lower() == ".bmp" else out.with_suffix(".bmp")
        row = w * 4
        pad = (4 - (row % 4)) % 4
        with bmp_out.open("wb") as fh:
            size = 54 + (row + pad) * h
            fh.write(b"BM" + size.to_bytes(4, "little") + b"\0\0\0\0" + (54).to_bytes(4, "little"))
            fh.write((40).to_bytes(4, "little") + w.to_bytes(4, "little")
                     + h.to_bytes(4, "little") + (1).to_bytes(2, "little")
                     + (32).to_bytes(2, "little") + (0).to_bytes(4, "little")
                     + ((row + pad) * h).to_bytes(4, "little")
                     + (2835).to_bytes(4, "little") * 2 + (0).to_bytes(4, "little") * 2)
            data = buf.raw
            for yy in range(h):
                fh.write(data[yy * row:(yy + 1) * row])
                fh.write(b"\0" * pad)
        return w, h, "BMP"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--size", default="", help="窗口尺寸，如 1400x940；默认用 App 自己的自适应尺寸")
    ap.add_argument("--out", default=str(ROOT / "runs" / "_gui_preview.png"))
    args = ap.parse_args()

    from ctfy_agent.gui.app import App  # noqa: PLC0415 - 需要 Tk 且要晚导入

    application = App()
    root = application.root
    root.attributes("-topmost", True)
    if args.size:
        root.geometry(args.size.replace("X", "x") + "+0+0")
    for _ in range(8):
        root.update()
        time.sleep(0.12)

    W, H = root.winfo_width(), root.winfo_height()
    print(f"client {W}x{H}")
    bad: list[str] = []
    for attr, label in CHECKED:
        widget = getattr(application, attr, None)
        if widget is None:
            bad.append(f"缺控件 {attr}（{label}）")
            continue
        w, h = widget.winfo_width(), widget.winfo_height()
        mapped = widget.winfo_ismapped()
        print(f"  {label:6s} {attr:12s} {w:5d}x{h:<4d} mapped={mapped}")
        if not mapped:
            continue  # 非当前页签/非当前模式的控件不算错
        if w < 20 or h < 10:
            bad.append(f"{label} 尺寸异常 {w}x{h}")
        x0 = widget.winfo_rootx() - root.winfo_rootx()
        if x0 + w > W + 2:
            bad.append(f"{label} 横向溢出 ({x0}+{w}>{W})")

    # 左栏不能横向压到右栏
    left_right = application.frm_form.winfo_rootx() + application.frm_form.winfo_width()
    nb_left = application.nb.winfo_rootx()
    print(f"  left content right={left_right} notebook left={nb_left}")
    if left_right > nb_left + 4:
        bad.append(f"左栏横向溢出右栏 {left_right}>{nb_left}")

    # 配色契约
    if str(root.cget("bg")) != theme.PAGE:
        bad.append("根窗口没用 GitHub 页面底色")
    if str(application.txt_events.cget("bg")) != theme.CANVAS:
        bad.append("日志区没用主题白底")

    # 圆角按钮真的画出来了：画布图元里有主色填充
    fills = set()
    for item in application.btn_start.find_all():
        try:
            fills.add(str(application.btn_start.itemcget(item, "fill")).upper())
        except Exception:  # noqa: BLE001
            pass
    if theme.PRIMARY.upper() not in fills and theme.PRIMARY_HOVER.upper() not in fills:
        bad.append("开始按钮没有主色填充（圆角按钮没画出来）")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    w, h, kind = _grab(root, out)
    real = out if kind == "PNG" else out.with_suffix(".bmp")
    print(f"screenshot({kind}) -> {real} ({w}x{h})")
    root.destroy()
    if bad:
        print("BAD: " + "; ".join(bad))
        return 1
    print("布局/配色体检 OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())