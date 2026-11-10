# -*- coding: utf-8 -*-
"""Windows 下的 Unix 文本工具兼容层（head / tail / grep / wc / xxd）。

为什么需要：agent 的 shell 工具在 Windows 上走 cmd，而模型天然会写 Linux 风格命令
（`python x.py | head -n 20`、`grep -n flag out.txt`）。实测最近一次运行里
「工具被拒绝」占全部调用的 8~12%，其中约一半就是这类命令不存在。

用法：由同目录的 <name>.cmd 调用 —— `python shim.py <name> [参数...]`。
设计原则：
  * 只实现 CTF 里最常用的子集，参数不认识就明确报错（不要静默给错结果）；
  * 一律 UTF-8 读写，符号链接/编码问题交给上层；
  * 读 stdin 也读文件，行为与 GNU 工具尽量一致。
"""
from __future__ import annotations

import sys
from pathlib import Path

USAGE = "用法: shim.py {head|tail|grep|wc|xxd} [选项] [文件...]\n"


def _force_utf8() -> None:
    """cmd 控制台默认 GBK，遇到非 GBK 字符会直接崩；统一改 UTF-8 输出。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001 - 老解释器/被重定向时忽略
            pass


def _read_inputs(files: list[str]) -> tuple[str, str]:
    """返回 (来源名, 文本)。没有文件就读 stdin。"""
    if not files:
        data = sys.stdin.buffer.read()
        return "-", data.decode("utf-8-sig", "replace")
    chunks = []
    for name in files:
        try:
            # utf-8-sig：PowerShell 写出的文件常带 BOM，别把它当成内容
            chunks.append(Path(name).read_text(encoding="utf-8-sig", errors="replace"))
        except OSError as exc:
            print(f"{name}: {exc}", file=sys.stderr)
    return ",".join(files), "".join(chunks)


def _iter_lines(text: str) -> list[str]:
    lines = text.splitlines(keepends=True)
    if lines and not lines[-1].endswith(("\n", "\r")):
        pass
    return lines


def _parse_count(args: list[str], default: int) -> tuple[int, list[str], str]:
    """解析 head/tail 的行/字节选项。

    返回 (count, 文件列表, 模式)；模式 "lines" 或 "bytes"。
    支持：-n N / -N / --lines=N / -c N / -cN / --bytes=N。
    [接手 2026-11-10] 补上 -c（字节）：模型最常用的 `tail -c 8000` 之前被当成「文件 -c」
    直接报 No such file，等于 shim 在这条最热路径上是坏的（smoke case_windows_shell_guard 实测）。
    """
    count = default
    rest: list[str] = []
    mode = "lines"
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("-n", "--lines"):
            i += 1
            if i >= len(args):
                raise SystemExit(f"{a} 缺少数字")
            count = int(args[i].replace("k", "000"))
            mode = "lines"
        elif a in ("-c", "--bytes"):
            i += 1
            if i >= len(args):
                raise SystemExit(f"{a} 缺少数字")
            count = int(str(args[i]).replace("k", "000"))
            mode = "bytes"
        elif a.startswith("-c") and a[2:].isdigit():
            count = int(a[2:].replace("k", "000"))
            mode = "bytes"
        elif a.startswith("--bytes="):
            count = int(a.split("=", 1)[1].replace("k", "000"))
            mode = "bytes"
        elif a.startswith("-n") and a[2:].isdigit():
            count = int(a[2:])
        elif a.startswith("--lines="):
            count = int(a.split("=", 1)[1])
        elif a.startswith("-") and a[1:].isdigit():
            count = int(a[1:])
        elif a in ("-f", "--follow"):  # 不支持跟随：读完即退出，别报错
            pass
        else:
            rest.append(a)
        i += 1
    return count, rest, mode


def _head_text(text: str, count: int, mode: str) -> str:
    if mode == "bytes":
        return text.encode("utf-8")[: max(0, count)].decode("utf-8", "replace")
    return "".join(_iter_lines(text)[: max(0, count)])


def _tail_text(text: str, count: int, mode: str) -> str:
    if mode == "bytes":
        if count <= 0:
            return ""
        return text.encode("utf-8")[-count:].decode("utf-8", "replace")
    lines = _iter_lines(text)
    return "".join(lines[-count:] if count else [])


def cmd_head(args: list[str]) -> int:
    count, files, mode = _parse_count(args, 10)
    _, text = _read_inputs(files)
    sys.stdout.write(_head_text(text, count, mode))
    return 0


def cmd_tail(args: list[str]) -> int:
    count, files, mode = _parse_count(args, 10)
    _, text = _read_inputs(files)
    sys.stdout.write(_tail_text(text, count, mode))
    return 0


def cmd_wc(args: list[str]) -> int:
    flags = {"-l": False, "-c": False, "-w": False}
    files: list[str] = []
    for a in args:
        if a in flags:
            flags[a] = True
        elif a in ("-lc", "-cl"):
            flags["-l"] = flags["-c"] = True
        elif a == "-":
            files.append(a)
        else:
            files.append(a)
    _, text = _read_inputs([f for f in files if f != "-"])
    picks = [f for f in ("-l", "-w", "-c") if flags[f]] or ["-l", "-w", "-c"]
    out = []
    if "-l" in picks:
        out.append(str(len(text.splitlines())))
    if "-w" in picks:
        out.append(str(len(text.split())))
    if "-c" in picks:
        out.append(str(len(text.encode("utf-8"))))
    print(" ".join(out))
    return 0


def cmd_grep(args: list[str]) -> int:
    import re

    ignore_case = invert = count_only = line_no = True_only = False
    extended = False
    pattern = None
    files: list[str] = []
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("-i", "--ignore-case"):
            ignore_case = True
        elif a in ("-v", "--invert-match"):
            invert = True
        elif a in ("-c", "--count"):
            count_only = True
        elif a in ("-n", "--line-number"):
            line_no = True
        elif a in ("-E", "--extended-regexp"):
            extended = True
        elif a in ("-F", "--fixed-strings"):
            extended = False
        elif a in ("-l", "--files-with-matches"):
            True_only = True
        elif a.startswith("-") and len(a) > 1 and not a.startswith("--"):
            for ch in a[1:]:
                if ch == "i":
                    ignore_case = True
                elif ch == "v":
                    invert = True
                elif ch == "c":
                    count_only = True
                elif ch == "n":
                    line_no = True
                elif ch == "E":
                    extended = True
                elif ch == "F":
                    extended = False
                else:
                    raise SystemExit(f"grep: 不支持的选项 -{ch}（兼容层只实现 -i -v -c -n -E -F -l）")
        elif pattern is None:
            pattern = a
        else:
            files.append(a)
        i += 1
    if pattern is None:
        raise SystemExit("grep: 缺少 pattern")

    try:
        rx = re.compile(pattern if extended else re.escape(pattern),
                        re.IGNORECASE if ignore_case else 0)
    except re.error as exc:
        raise SystemExit(f"grep: 正则非法: {exc}") from None

    _, text = _read_inputs(files)
    matched = 0
    total = 0
    for n, line in enumerate(text.splitlines(), 1):
        total += 1
        hit = bool(rx.search(line))
        if hit == invert:
            continue
        matched += 1
        if not count_only and not True_only:
            sys.stdout.write(f"{n}:{line}\n" if line_no else line + "\n")
    if count_only:
        print(matched if files else matched)
    return 0 if matched else 1


def cmd_xxd(args: list[str]) -> int:
    plain = False
    files: list[str] = []
    for a in args:
        if a in ("-p", "--plain"):
            plain = True
        else:
            files.append(a)
    _, text = _read_inputs(files)
    data = text.encode("utf-8")
    if plain:
        print(data.hex())
        return 0
    for off in range(0, len(data), 16):
        chunk = data[off:off + 16]
        hexs = chunk.hex()
        left = " ".join(hexs[i:i + 2] for i in range(0, len(hexs), 2))
        right = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        print(f"{off:08x}: {left:<48} {right}")
    return 0


COMMANDS = {
    "head": cmd_head, "tail": cmd_tail, "grep": cmd_grep,
    "wc": cmd_wc, "xxd": cmd_xxd,
}


def main(argv: list[str]) -> int:
    _force_utf8()
    if len(argv) < 2 or argv[1] not in COMMANDS:
        sys.stderr.write(USAGE)
        return 2
    return COMMANDS[argv[1]](argv[2:])


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv))
    except BrokenPipeError:  # cmd 管道提前关闭（如 | head）时不要报错
        raise SystemExit(0) from None