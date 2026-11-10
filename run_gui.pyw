# -*- coding: utf-8 -*-
"""GUI 启动引导（由 pythonw.exe 运行，无控制台窗口）。

双击 run_gui.vbs 或 run_gui.cmd 时会经由这里启动图形界面。
手动运行：pythonw run_gui.pyw
"""

import os
import sys
import traceback

_ROOT = os.path.dirname(os.path.abspath(__file__))


def _bootstrap():
    os.chdir(_ROOT)
    src = os.path.join(_ROOT, "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    from ctfy_agent.gui.app import main  # noqa: PLC0415

    return main()


if __name__ == "__main__":
    try:
        raise SystemExit(_bootstrap())
    except SystemExit:
        raise
    except Exception:  # noqa: BLE001 - pythonw 无控制台，启动期错误必须落盘
        try:
            os.makedirs(os.path.join(_ROOT, "runs"), exist_ok=True)
            with open(os.path.join(_ROOT, "runs", "gui-error.log"), "w", encoding="utf-8") as fh:
                traceback.print_exc(file=fh)
        except Exception:  # noqa: BLE001
            pass
        raise
