# -*- coding: utf-8 -*-
"""生成 tools/shims/*.cmd（CRLF + ASCII）。

优先使用 CTFY_PYTHON 环境变量指定的解释器（由 agent 显式注入，避免 cmd 里的 python
落到应用商店占位符 → 实测 rc=9009），没有才退回 PATH 上的 python。
用法：python tools/shims/gen_shims.py
"""
from pathlib import Path

SHIMS = Path(__file__).resolve().parent
NAMES = ("head", "tail", "grep", "wc", "xxd")
TEMPLATE = (
    "@echo off\r\n"
    'if defined CTFY_PYTHON ("%CTFY_PYTHON%" "%~dp0{script}" {sub} %*) '
    'else (python "%~dp0{script}" {sub} %*)\r\n'
)


def main() -> int:
    for name in NAMES:
        path = SHIMS / f"{name}.cmd"
        path.write_text(TEMPLATE.format(script="shim.py", sub=name),
                        encoding="ascii", newline="")
        print("written:", path.name, path.stat().st_size, "B")
    path = SHIMS / "ffuf.cmd"
    path.write_text(TEMPLATE.format(script="ffuf_shim.py", sub="").replace("  %*", " %*"),
                    encoding="ascii", newline="")
    print("written:", path.name, path.stat().st_size, "B")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
