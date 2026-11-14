#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pwn_local.py —— P2-17 pwn 本地化闭环。

本场 pwn（clean-room）靠外部 `solve.py` 19 秒打通，说明 agent 自身缺「拿到 binary 后自己
建本地环境、自己探协议」的链路。本工具提供三件套（纯标准库，不依赖 pwntools）：

  1) `inspect_elf(path)`：解析 ELF 头/程序头/字符串，输出架构、PIE、NX、RELRO、Canary、
     libc 要求、ASan 检测、解释器路径；
  2) `probe_protocol(host, port)`：交互协议探测（空包/数字/换行/菜单），记录响应；
  3) `generate_repro(binary, out_dir)`：生成 `Dockerfile`（固定 glibc 基础镜像）、
     `run.sh`（socat 起服务）、`poc.py`（pwntools 骨架 + 偏移占位）与 `notes.md`。

用法：
    python tools/pwn_local/pwn_local.py --binary clean-room --out runs/_tmp/pwn
    python tools/pwn_local/pwn_local.py --binary clean-room --probe 127.0.0.1:31337
"""
from __future__ import annotations

import argparse
import pathlib
import struct
import sys


KNOWN_MACHINES = {3: "x86", 62: "x86-64", 40: "arm", 183: "aarch64", 243: "riscv64"}
GLIBC_PATTERN = __import__("re").compile(r"GLIBC_2\.(\d+)")


def _read(path: pathlib.Path) -> bytes:
    return path.read_bytes()


def inspect_elf(path: str | pathlib.Path) -> dict:
    """解析 ELF：架构/位数/类型/程序头/保护/GLIBC/ASan/解释器。非 ELF 抛 ValueError。"""
    data = _read(pathlib.Path(path))
    if data[:4] != b"\x7fELF":
        raise ValueError(f"不是 ELF：{path}")
    if len(data) < 52:  # ELF64 头至少 64 字节，ELF32 52 字节；截断文件不能 struct.unpack
        raise ValueError(f"ELF 文件被截断（{len(data)} 字节）：{path}")
    bits = 64 if data[4] == 2 else 32
    endian = "<" if data[5] == 1 else ">"
    machine = KNOWN_MACHINES.get(struct.unpack_from(endian + "H", data, 18)[0], "unknown")
    e_type = struct.unpack_from(endian + "H", data, 16)[0]
    entry = struct.unpack_from(endian + ("Q" if bits == 64 else "I"), data, 24)[0]
    pie = e_type == 3
    # 程序头：找 PT_GNU_STACK(0x6474e551) 判 NX；PT_GNU_RELRO(0x6474e552) 判 RELRO
    if bits == 64:
        phoff = struct.unpack_from(endian + "Q", data, 32)[0]
        phentsize, phnum = struct.unpack_from(endian + "HH", data, 54)
    else:
        phoff = struct.unpack_from(endian + "I", data, 28)[0]
        phentsize, phnum = struct.unpack_from(endian + "HH", data, 42)
    nx, relro, interp = True, False, ""
    for index in range(min(phnum, 64)):
        base = phoff + index * phentsize
        if base + phentsize > len(data):
            break
        p_type = struct.unpack_from(endian + "I", data, base)[0]
        if bits == 64:
            p_flags = struct.unpack_from(endian + "I", data, base + 4)[0]
            p_offset = struct.unpack_from(endian + "Q", data, base + 8)[0]
            p_filesz = struct.unpack_from(endian + "Q", data, base + 32)[0]
        else:
            p_offset = struct.unpack_from(endian + "I", data, base + 4)[0]
            p_flags = struct.unpack_from(endian + "I", data, base + 24)[0]
            p_filesz = struct.unpack_from(endian + "I", data, base + 16)[0]
        if p_type == 0x6474E551:
            nx = not bool(p_flags & 0x1)      # PF_X on stack = 可执行栈
        elif p_type == 0x6474E552:
            relro = True
        elif p_type == 2 and p_filesz and p_offset < len(data):   # PT_INTERP
            interp = data[p_offset:p_offset + p_filesz].split(b"\x00")[0].decode(
                "utf-8", "replace")
    text = data.decode("latin-1")
    glibc = sorted({int(match) for match in GLIBC_PATTERN.findall(text)})
    asan = "AddressSanitizer" in text or "__asan_" in text
    canary = "__stack_chk_fail" in text
    return {
        "bits": bits,
        "endian": "little" if endian == "<" else "big",
        "machine": machine,
        "type": "PIE" if pie else "EXEC",
        "nx": nx,
        "relro": relro,
        "canary": canary,
        "asan": asan,
        "glibc_minor": max(glibc) if glibc else None,
        "glibc_minor_all": glibc,
        "interp": interp,
        "entry": entry,
        "size": len(data),
    }


def base_image(report: dict) -> str:
    """按 glibc 版本挑基础镜像（比赛镜像源里只有有限 tag，超出就退 ubuntu:24.04）。"""
    minor = report.get("glibc_minor")
    if minor == 31:
        return "ubuntu:20.04"
    if minor in (35, 36, 37, 38, 39):
        return "ubuntu:24.04"
    return "ubuntu:24.04"


def probe_protocol(host: str = "127.0.0.1", port: int = 0, *, timeout: float = 3.0,
                   probes: list[bytes] | None = None) -> dict:
    """TCP 交互探测：依次发探针，记录回包（十六进制+可打印）。只用于本地/靶机。"""
    import socket

    probes = probes if probes is not None else [b"", b"1\n", b"2\n", b"\n", b"help\n"]
    responses = []
    for probe in probes:
        try:
            with socket.create_connection((host, port), timeout=timeout) as sock:
                sock.settimeout(timeout)
                if probe:
                    sock.sendall(probe)
                try:
                    data = sock.recv(4096)
                except socket.timeout:
                    data = b""
            responses.append({
                "probe": probe.decode("latin-1"),
                "bytes": len(data),
                "hex": data[:64].hex(),
                "text": data[:200].decode("utf-8", "replace"),
            })
        except OSError as exc:
            responses.append({"probe": probe.decode("latin-1"), "error":
                              f"{type(exc).__name__}: {exc}"})
    return {"target": f"{host}:{port}", "responses": responses}


DOCKERFILE = """FROM {image}
RUN apt-get update && apt-get install -y --no-install-recommends socat libc6-dbg \\
        && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY . /app
RUN chmod +x /app/run.sh /app/{binary}
EXPOSE 31337
CMD ["/app/run.sh"]
"""

RUN_SH = """#!/usr/bin/env bash
set -euo pipefail
# socat 起服务：每个连接 fork 一次，stderr 不转发（避免泄露）。
exec socat TCP-LISTEN:31337,reuseaddr,fork EXEC:./{binary},stderr
"""

POC_PY = '''#!/usr/bin/env python3
"""本地复现脚本骨架（pwntools）。偏移/交互按实际 binary 调整。

用法：
    python poc.py            # 本地 ./{binary}
    python poc.py remote HOST PORT
"""
import sys

from pwn import *  # noqa: F403

BINARY = "./{binary}"
context.binary = elf = ELF(BINARY, checksec=False)
context.log_level = "info"

OFFSET_TO_RIP = 0x0   # TODO: 用 cyclic 定位
WIN = elf.symbols.get("win") or elf.symbols.get("flag") or 0x0


def start():
    if len(sys.argv) > 1 and sys.argv[1] == "remote":
        return remote(sys.argv[2], int(sys.argv[3]))
    return process(BINARY)


def main():
    io = start()
    io.recvuntil(b"> ")                     # TODO: 按真实协议改
    payload = flat({{b"A" * OFFSET_TO_RIP, p64(WIN)}}) if WIN else b"A" * OFFSET_TO_RIP
    io.sendline(payload)
    io.interactive()


if __name__ == "__main__":
    main()
'''

NOTES_MD = """# {binary} 本地复现环境

- 架构：{machine} / {bits}-bit / {type}
- 保护：NX={nx} RELRO={relro} Canary={canary} ASan={asan}
- glibc 要求：{glibc}
- 基础镜像：`{image}`

## 用法

```bash
docker build -t {slug} .
docker run --rm -p 31337:31337 {slug}
python poc.py remote 127.0.0.1 31337
```

## 下一步

1. `checksec`/本工具已给的保护 → 决定 ROP/ret2libc/格式化串路线；
2. `cyclic` 找偏移填进 `poc.py`；
3. 交互协议用 `pwn_local.py --probe` 的记录改 `recvuntil`。
"""


def generate_repro(binary: str | pathlib.Path, out_dir: str | pathlib.Path) -> dict:
    """生成 Dockerfile/run.sh/poc.py/notes.md（不启动容器）。"""
    path = pathlib.Path(binary)
    out = pathlib.Path(out_dir)
    report = inspect_elf(path)
    image = base_image(report)
    slug = path.stem.lower().replace("_", "-")
    out.mkdir(parents=True, exist_ok=True)
    files = {
        "Dockerfile": DOCKERFILE.format(image=image, binary=path.name),
        "run.sh": RUN_SH.format(binary=path.name),
        "poc.py": POC_PY.format(binary=path.name),
        "notes.md": NOTES_MD.format(
            binary=path.name, machine=report["machine"], bits=report["bits"],
            type=report["type"], nx=report["nx"], relro=report["relro"],
            canary=report["canary"], asan=report["asan"],
            glibc=report.get("glibc_minor"), image=image, slug=slug),
    }
    written = []
    for name, content in files.items():
        target = out / name
        # newline="\n"：Windows 默认把 \n 翻成 CRLF，run.sh 的 shebang 会变成
        # "#!/usr/bin/env bash\r" → 容器里 bash run.sh 直接失败（审计实测）。
        target.write_text(content, encoding="utf-8", newline="\n")
        written.append(str(target))
    return {"binary": str(path), "report": report, "image": image,
            "files": written, "out_dir": str(out)}


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    parser = argparse.ArgumentParser(description="P2-17 pwn 本地化闭环")
    parser.add_argument("--binary", required=True, help="ELF 文件路径")
    parser.add_argument("--out", default="runs/_tmp/pwn_local", help="生成复现环境目录")
    parser.add_argument("--probe", default="", help="HOST:PORT，做交互协议探测")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = inspect_elf(args.binary)
    except (OSError, ValueError) as exc:
        print(f"读取失败：{exc}")
        return 2
    result = {"inspect": report}
    if args.probe:
        host, _, port = args.probe.partition(":")
        result["probe"] = probe_protocol(host, int(port or 0))
    result["repro"] = generate_repro(args.binary, args.out)
    if args.json:
        import json

        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"{args.binary}: {report['machine']}/{report['bits']}-bit {report['type']} "
              f"NX={report['nx']} RELRO={report['relro']} Canary={report['canary']} "
              f"ASan={report['asan']} glibc=2.{report.get('glibc_minor')}")
        print(f"复现环境：{result['repro']['out_dir']}（{len(result['repro']['files'])} 个文件）")
        if args.probe:
            for item in result["probe"]["responses"]:
                print(f"  probe={item.get('probe')!r} → {item.get('text') or item.get('error')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())