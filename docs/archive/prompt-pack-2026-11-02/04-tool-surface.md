# 工具面：模型实际看到的 schema

> 这些是每步都随请求发给模型的 JSON Schema（function calling 定义），逐字从代码导出，未经改写。
> **提示词只能引导"什么时候用哪个工具"，不能改变工具本身能做什么。** 若你判断某个工具有缺陷，
> 请写在"建议改工具"章节里，而不是假设它已修好。

## 1. rev 角色可用工具

## `elfpeek`

```json
{
  "type": "function",
  "function": {
    "name": "elfpeek",
    "description": "纯 stdlib 的 ELF/字节码静态探测（容器里没有 objdump/file/readelf）。反汇编走 capstone，v5 起镜像内置；缺失时 disasm/gadgets 自动退化（不报错）。view=info 看架构/入口/PIE/NX/canary/RELRO/入口段；sections/symbols 看节表与符号；strings 看字符串（utf16=true 扫宽字符串）；disasm 反汇编（offset/length/count/arch，无 capstone 自动退化字节视图）；gadgets 枚举 ROP 链（filter=rdi|rsi|rdx|syscall|binsh）；bytecode 处理 .pyc / PyInstaller（先不带 extract 列目录，再 extract=<名字>，PYZ 用 extract=pyz:<模块名>，func=只看某函数）；lookup 精确查符号/字符串偏移（ret2libc：symbol=system、search=/bin/sh，附 libc 版本行）；relocs 看重定位表（JUMP_SLOT 的 @地址就是 foo@got、静态链接判定、.init_array 构造函数）；dynamic 看动态段（NEEDED 依赖库、RUNPATH/RPATH、BIND_NOW→full RELRO、FLAGS_1 的 PIE 位）。",
    "parameters": {
      "type": "object",
      "properties": {
        "path": {
          "type": "string",
          "description": "沙盒内文件路径（相对沙盒根）"
        },
        "view": {
          "type": "string",
          "enum": [
            "info",
            "sections",
            "symbols",
            "strings",
            "gadgets",
            "disasm",
            "bytecode",
            "lookup",
            "relocs",
            "dynamic"
          ]
        },
        "min_len": {
          "type": "integer",
          "description": "strings 视图的最小串长（默认 4）"
        },
        "limit": {
          "type": "integer",
          "description": "最多输出条目数（默认 60）"
        },
        "offset": {
          "type": "integer",
          "description": "disasm：起始文件偏移（默认吸附到可执行段起点）"
        },
        "length": {
          "type": "integer",
          "description": "disasm：反汇编字节数（默认 128，上限 4096）"
        },
        "count": {
          "type": "integer",
          "description": "disasm：最多指令条数（默认 40，上限 200）"
        },
        "arch": {
          "type": "string",
          "description": "disasm：覆盖架构 x86/x64/arm/arm64（默认按 ELF 头自动判）"
        },
        "filter": {
          "type": "string",
          "description": "gadgets：'|' 分隔关键字，如 rdi|rsi|rdx|syscall|binsh"
        },
        "extract": {
          "type": "string",
          "description": "bytecode：PyInstaller 条目名（PYZ 内模块用 pyz:模块名）"
        },
        "func": {
          "type": "string",
          "description": "bytecode：只反汇编指定函数名（不带则看模块级）"
        },
        "utf16": {
          "type": "boolean",
          "description": "strings：扫 UTF-16 宽字符串"
        },
        "symbol": {
          "type": "string",
          "description": "lookup：精确符号名（如 system；无命中给相近清单）"
        },
        "search": {
          "type": "string",
          "description": "lookup：要查的字符串（如 /bin/sh），返回全部出现偏移"
        }
      },
      "required": [
        "path"
      ]
    }
  }
}
```

## `hexpeek`

```json
{
  "type": "function",
  "function": {
    "name": "hexpeek",
    "description": "以 xxd 风格显示文件的十六进制 + ASCII 双栏内容（偏移 8 位十六进制）。用于看文件头判断真实类型、找藏在字节里的明文、定位拼接/尾部附加数据。镜像里没有 xxd/hexdump，需要看原始字节时用它。",
    "parameters": {
      "type": "object",
      "properties": {
        "path": {
          "type": "string",
          "description": "文件路径"
        },
        "offset": {
          "type": "integer",
          "description": "起始偏移（字节），默认 0"
        },
        "length": {
          "type": "integer",
          "description": "显示字节数，默认 256，上限 1024"
        }
      },
      "required": [
        "path"
      ]
    }
  }
}
```

## `shell`

注意：`allow_shell=true` 时可用，超时上限 120s；输出经有界收割（头 3000 + 尾 9000 字节，中间省略并标注字节数）。

```json
{
  "type": "function",
  "function": {
    "name": "shell",
    "description": "在解题沙箱目录里执行一条 shell 命令（仅在 allow_shell=true 时可用，超时上限 120s）。返回 stdout/stderr 与退出码。",
    "parameters": {
      "type": "object",
      "properties": {
        "command": {
          "type": "string"
        },
        "timeout": {
          "type": "integer",
          "default": 60
        }
      },
      "required": [
        "command"
      ]
    }
  }
}
```

## `write_file`

```json
{
  "type": "function",
  "function": {
    "name": "write_file",
    "description": "把文本写入沙盒里的文件（自动创建父目录；默认覆盖，append=true 追加）。写脚本/载荷/字典文件一律用它，不要用 shell 的 echo/heredoc/python -c 拼——Windows 与 Linux 的引号规则不同，拼接长脚本极易写坏。路径必须是相对沙盒或沙盒内的绝对路径；单次上限 512KB。",
    "parameters": {
      "type": "object",
      "properties": {
        "path": {
          "type": "string",
          "description": "目标文件路径（如 exp.py 或 extracted/run.sh）"
        },
        "content": {
          "type": "string",
          "description": "要写入的文本内容（UTF-8）"
        },
        "append": {
          "type": "boolean",
          "description": "true=追加到文件末尾；默认 false 覆盖"
        }
      },
      "required": [
        "path",
        "content"
      ]
    }
  }
}
```

> **注意这条描述与实测行为冲突**：它要求"写脚本一律用它，不要用 shell 的 `python -c` 拼"，
> 但 63 次 rev 会话里 `python -c` 出现 130 次、`write_file` 只有 25 次会话用过。
> 原因是**步数成本**：`write_file` + `shell` 跑 = 2 步，`python -c "open('x.py','w').write(...)"` = 1 步。
> 14 步预算下模型必然选后者。这是提示词/工具面要解决的矛盾之一（见 08 任务书 T3）。

## `tcp`

```json
{
  "type": "function",
  "function": {
    "name": "tcp",
    "description": "对平台实例的 TCP 服务做一次往返：连接、发送 payload（文本或 hex）、收集回显。pwn 打栈溢出、crypto 连 padding oracle 服务、misc 测连通性都用它。只能连本题 start_instance 返回的 host:port（白名单外一律拒绝）。",
    "parameters": {
      "type": "object",
      "properties": {
        "host": {
          "type": "string",
          "description": "实例主机（start_instance 返回的 surface.services[].host）"
        },
        "port": {
          "type": "integer",
          "description": "实例端口"
        },
        "payload": {
          "type": "string",
          "description": "要发送的内容；hex_mode=true 时按 hex 串解析"
        },
        "hex_mode": {
          "type": "boolean",
          "description": "payload 是否 hex 编码（二进制 exploit 用 true）"
        },
        "expect": {
          "type": "string",
          "description": "可选：收到该子串立即返回"
        },
        "read_timeout": {
          "type": "number",
          "description": "读回显的空闲超时秒（默认 3.0）"
        },
        "max_recv": {
          "type": "integer",
          "description": "最多读多少字节（默认 4096）"
        },
        "tls": {
          "type": "boolean",
          "description": "是否先做 TLS 握手（access=tls_sni 的 443 接入用 true）"
        },
        "sni": {
          "type": "string",
          "description": "TLS SNI 主机名，默认等于 host"
        }
      },
      "required": [
        "host",
        "port"
      ]
    }
  }
}
```

## `tcp_batch`

```json
{
  "type": "function",
  "function": {
    "name": "tcp_batch",
    "description": "对同一 TCP 服务顺序发多个 payload 并汇总每个响应（padding oracle / 盲爆破用）。一次最多 200 个；返回每条的响应长度与短 hex，便于对比差异。",
    "parameters": {
      "type": "object",
      "properties": {
        "host": {
          "type": "string"
        },
        "port": {
          "type": "integer"
        },
        "payloads": {
          "type": "array",
          "items": {
            "type": "string"
          },
          "description": "payload 列表（hex_mode 默认 true）"
        },
        "hex_mode": {
          "type": "boolean",
          "description": "payload 是否 hex（默认 true）"
        },
        "read_timeout": {
          "type": "number",
          "description": "每条读超时秒（默认 2.0）"
        },
        "same_conn": {
          "type": "boolean",
          "description": "是否复用同一连接（默认 true；服务端每连接单次交互时置 false）"
        },
        "tls": {
          "type": "boolean",
          "description": "是否 TLS（access=tls_sni 时用 true）"
        },
        "sni": {
          "type": "string",
          "description": "TLS SNI 主机名，默认等于 host"
        }
      },
      "required": [
        "host",
        "port",
        "payloads"
      ]
    }
  }
}
```

## `netcheck`

```json
{
  "type": "function",
  "function": {
    "name": "netcheck",
    "description": "连通性诊断：DNS 是否解析、TCP 端口是否可连。遇到连不上时先用它判断是「*.internal 需要 VPN」还是「端口/实例问题」，不要盲目重试烧预算。",
    "parameters": {
      "type": "object",
      "properties": {
        "host": {
          "type": "string"
        },
        "port": {
          "type": "integer",
          "description": "可选；给了就测 TCP 连接"
        },
        "tls": {
          "type": "boolean",
          "description": "可选；给了 port 且该端口是 443+tls_sni 接入时用 true"
        }
      },
      "required": [
        "host"
      ]
    }
  }
}
```

## 2. pwn 角色额外工具

## `pwngen`

```json
{
  "type": "function",
  "function": {
    "name": "pwngen",
    "description": "纯 stdlib 的 pwn payload 组装器（不要在 shell 里手搓 python 拼串，引号守卫会拦）。op=cyclic 生成 De Bruijn 模式串（发超长触发覆盖，回显交给 pwnfind 反查精确偏移）；op=build 用 parts 数组拼 payload：{hex:\"4142\"} / {raw:\"...\"} / {pad:\"A\",count:64} / {int:0x401000,width:8}（小端 p64）/ {cyclic:128}；path= 落盘到沙盒。",
    "parameters": {
      "type": "object",
      "properties": {
        "op": {
          "type": "string",
          "enum": [
            "cyclic",
            "build"
          ],
          "description": "cyclic=模式串；build=拼装"
        },
        "size": {
          "type": "integer",
          "description": "cyclic：字节数（默认 256，上限 8192）"
        },
        "n": {
          "type": "integer",
          "description": "模式串子序列长度（默认 4；32 位用 4，64 位常用 8）"
        },
        "alphabet": {
          "type": "string",
          "description": "自定义字母表（默认 a-z；pwnfind 需一致）"
        },
        "parts": {
          "type": "array",
          "items": {
            "type": "object"
          },
          "description": "build：部件数组"
        },
        "path": {
          "type": "string",
          "description": "可选：落盘到沙盒内此路径"
        }
      },
      "required": [
        "op"
      ]
    }
  }
}
```

## `pwnfind`

```json
{
  "type": "function",
  "function": {
    "name": "pwnfind",
    "description": "把崩溃回显/覆盖返回地址的 3~8 字节反查成 cyclic 模式偏移（= 覆盖返回地址前的填充长度）。给 value=<回显文本> 或 hex=<回显 hex>；自动尝试字节序反转。n/alphabet 要与 pwngen 一致。",
    "parameters": {
      "type": "object",
      "properties": {
        "value": {
          "type": "string",
          "description": "回显的文本字节（如 '6Aa7'）"
        },
        "hex": {
          "type": "string",
          "description": "回显的 hex 字节（如 '36616137'）"
        },
        "n": {
          "type": "integer",
          "description": "与 pwngen 相同的 n（默认 4）"
        },
        "alphabet": {
          "type": "string",
          "description": "与 pwngen 相同的字母表（默认 a-z）"
        }
      }
    }
  }
}
```

## 3. 交付出口（两个角色共用）

## `report`

这是结论的唯一出口：调用即结束本回合。note 只落盘、协调者看不到。

```json
{
  "type": "function",
  "function": {
    "name": "report",
    "description": "交付阶段结论的**唯一**方式，调用后本回合立即结束，结论会回到协调者手里。判断、框架识别、已拿到的 flag、卡点与下一步建议都写在这里。note 只会写进本地笔记，协调者看不到，不要用它交付结论。不要把 flag 当结论粘贴（避免污染协调者上下文）。evidence 必须是你某一次工具输出的**逐字连续片段**（原样复制，不要改写/概括）；说「发现了/拿到了/绕过了」却不给 evidence 会被拒绝重交。",
    "parameters": {
      "type": "object",
      "properties": {
        "text": {
          "type": "string"
        },
        "evidence": {
          "type": "string",
          "description": "支撑上述结论的逐字片段，必须原样复制自某一次工具输出（单次输出内的连续子串）。"
        }
      },
      "required": [
        "text",
        "evidence"
      ]
    }
  }
}
```

