"""pwnsession 回归：本地起一个菜单式 TCP 服务，验证「一次调用跑完多轮收发」。

运行: python tests/smoke_pwnsession.py   （纯离线，不需要平台/实例）
覆盖：
  A. 规格接线（DOMAIN_NAMES / MISC_EXT_SPECS / pwn 角色可见 / 必填字段）
  B. banner + 多步 send/expect（菜单题典型流程）
  C. send_hex（二进制 payload）
  D. 参数校验（空 steps / 超 40 步 / 非法 hex）
  E. 白名单（LocalTools 未登记 host 时拒绝，和 tcp 共用一套门禁）
"""
import json
import pathlib
import socket
import sys
import threading

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))

FAILS = 0


def check(name: str, ok: bool, detail: object = "") -> None:
    global FAILS
    if ok:
        print(f"[ok] {name}")
    else:
        FAILS += 1
        print(f"[FAIL] {name}  {detail}")


class MenuServer:
    """极简菜单服务：banner → 1 输入消息 / 3 提交 / 4 退出。"""

    def __init__(self) -> None:
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.host, self.port = self.sock.getsockname()
        self.thread = threading.Thread(target=self._serve, daemon=True)

    def _serve(self) -> None:
        conn, _ = self.sock.accept()
        with conn:
            conn.sendall(b"== menu ==\n1. msg\n2. del\n3. submit\n4. quit\n> ")
            buf = b""
            expect_payload = False
            while True:
                try:
                    chunk = conn.recv(4096)
                except OSError:
                    return
                if not chunk:
                    return
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if expect_payload:
                        conn.sendall(b"saved %d\n> " % len(line))
                        expect_payload = False
                    elif line == b"1":
                        conn.sendall(b"input: ")
                        expect_payload = True
                    elif line == b"3":
                        conn.sendall(b"Done.\n> ")
                    elif line == b"4":
                        return
                    elif line == b"\xde\xad":
                        conn.sendall(b"hexok\n> ")
                    else:
                        conn.sendall(b"??\n> ")

    def start(self) -> "MenuServer":
        self.thread.start()
        return self

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


def case_wiring() -> None:
    from ctfy_agent.crew import COORDINATOR_PROMPT, DEFAULT_ROLES  # noqa: PLC0415
    from ctfy_agent.tools_domain_ext import DOMAIN_NAMES, PWN_SESSION_SPEC  # noqa: PLC0415
    from ctfy_agent.tools_misc_ext import MISC_EXT_SPECS  # noqa: PLC0415

    check("pwnsession 在 DOMAIN_NAMES", "pwnsession" in DOMAIN_NAMES, sorted(DOMAIN_NAMES))
    names = [s["function"]["name"] for s in MISC_EXT_SPECS]
    check("pwnsession 在 MISC_EXT_SPECS（专家工具面可见）", "pwnsession" in names, names[-6:])
    fn = PWN_SESSION_SPEC["function"]
    req = fn["parameters"].get("required") or []
    check("spec 必填 = host/port/steps", req == ["host", "port", "steps"], req)
    check("spec 声明 tls 参数", "tls" in fn["parameters"]["properties"])
    config = json.loads((HERE.parent / "config" / "agent.json").read_text(encoding="utf-8-sig"))
    pwn_local = config["roles"]["pwn"]["local"]
    check("config 的 pwn.local 含 pwnsession", "pwnsession" in pwn_local, pwn_local)
    check("DEFAULT_ROLES pwn 兜底含 pwnsession",
          "pwnsession" in DEFAULT_ROLES["pwn"]["local"], DEFAULT_ROLES["pwn"]["local"])
    check("pwn/rev 提示词是 EnIGMA 打法移植（工具映射 + IMPORTANT TIPS + 打法顺序）",
          all(k in config["roles"]["pwn"]["system"] for k in
              ("工具映射", "IMPORTANT TIPS", "PWN 打法顺序", "调试会话 tips", "pwnsession"))
          and all(k in config["roles"]["rev"]["system"] for k in
                  ("工具映射", "IMPORTANT TIPS", "REV 打法顺序", "差分")))
    check("config 的 pwn/rev local 都含 fileview（EnIGMA 的 open/goto/scroll/search 等价物）",
          "fileview" in pwn_local and "fileview" in (config["roles"]["rev"]["local"] or []),
          (pwn_local, config["roles"]["rev"]["local"]))
    check("DEFAULT_ROLES pwn/rev 兜底含 fileview",
          "fileview" in DEFAULT_ROLES["pwn"]["local"] and "fileview" in DEFAULT_ROLES["rev"]["local"],
          (DEFAULT_ROLES["pwn"]["local"], DEFAULT_ROLES["rev"]["local"]))
    check("协调者 prompt 含收口判据（连续两轮无决定性事实→单实验否则 stalled）",
          "收口判据" in COORDINATOR_PROMPT and "单实验" in COORDINATOR_PROMPT)
    check("pwn prompt 含交付判据（offset/leak/判据链）",
          "决定性事实" in config["roles"]["pwn"]["system"])
    check("rev.local 不泄漏 pwnsession（门禁按角色名单过滤）",
          "pwnsession" not in (config["roles"]["rev"]["local"] or []),
          config["roles"]["rev"]["local"])


def case_fileview() -> None:
    """EnIGMA open/goto/scroll/search 的等价物：按行窗口 / 关键词搜行 / 二进制拒绝。"""
    from ctfy_agent.tools_domain_ext import DOMAIN_NAMES, DomainToolError, call_domain  # noqa: PLC0415

    check("fileview 在 DOMAIN_NAMES", "fileview" in DOMAIN_NAMES, sorted(DOMAIN_NAMES))
    doc = HERE.parent / "docs" / "domain-tools.md"
    out = call_domain("fileview", {"path": str(doc), "mode": "lines", "start_line": 1, "count": 5})
    check("fileview lines 带行号窗口", "| " in out and "共" in out and "行" in out, out[:200])
    hit = call_domain("fileview", {"path": str(doc), "mode": "search", "needle": "pwnsession"})
    check("fileview search 命中 pwnsession", "命中" in hit and "pwnsession" in hit, hit[:200])
    try:
        call_domain("fileview", {"path": str(HERE.parent / "src" / "ctfy_agent" / "pwn_kit.py"),
                                 "mode": "lines"})
        check("fileview 对二进制/文本都能读", True)
    except DomainToolError:  # 源码是文本，不该拒绝
        check("fileview 对二进制/文本都能读", False, "源码被误判为二进制")
    try:
        call_domain("fileview", {"path": str(HERE.parent / "no_such_file_xyz"), "mode": "lines"})
        check("fileview 缺文件报错", False, "没有抛错")
    except Exception as exc:  # noqa: BLE001
        check("fileview 缺文件报错", "不存在" in str(exc) or "no_such" in str(exc), str(exc)[:120])


def case_templates() -> None:
    """会话内计算：受限算式 + 发送模板（第 5 轮卡点：offset 与发送跨调用）。"""
    from ctfy_agent.tools_domain_ext import DomainToolError, _tmpl_eval, _tmpl_expand  # noqa: PLC0415

    check("模板算式：算术/位运算", _tmpl_eval("0x48 + 8*2", {}) == 0x58)
    check("模板算式：变量", _tmpl_eval("off + 4", {"off": 72}) == 76)
    check("模板 pack：8 字节小端 hex", _tmpl_expand("{pack(0x48)}", {}, as_hex=True) == "4800000000000000")
    check("模板 p32", _tmpl_expand("{p32(off+1)}", {"off": 0x41}, as_hex=True) == "42000000")
    check("模板 hex()", _tmpl_expand("addr={hex(0x10+1)}", {}, as_hex=False) == "addr=0x11")
    check("模板 {变量}", _tmpl_expand("{off}", {"off": 72}, as_hex=False) == "72")
    for bad in ("{pack(off)}", "{nope}"):
        try:
            _tmpl_expand(bad, {}, as_hex=False)
            check("模板非法用法报错 %r" % bad, False, "没有抛错")
        except DomainToolError:
            check("模板非法用法报错 %r" % bad, True)


def case_session() -> None:
    from ctfy_agent.tools_domain_ext import call_domain  # noqa: PLC0415

    srv = MenuServer().start()
    try:
        out = call_domain("pwnsession", {
            "host": srv.host, "port": srv.port,
            "steps": [
                {"send": "1\n", "expect": "input:"},
                {"send": "hello\n", "expect": "saved"},
                {"send": "3\n", "expect": "Done"},
            ],
        })
        check("会话收 paner: banner 出现", "menu" in out and "banner" in out, out[:200])
        check("会话收: 输入回显 saved 5", "saved 5" in out, out[:400])
        check("会话收: 提交回显 Done", "Done" in out, out[:400])
        check("会话收: 标注了步数", "3 步" in out, out[-80:])
    finally:
        srv.close()

    srv = MenuServer().start()
    try:
        out = call_domain("pwnsession", {
            "host": srv.host, "port": srv.port,
            "steps": [{"send_hex": "dead", "expect": "hexok"}],
        })
        check("send_hex 二进制 payload 生效", "hexok" in out, out[:300])
    finally:
        srv.close()


def case_errors() -> None:
    from ctfy_agent.tools_domain_ext import DomainToolError, call_domain  # noqa: PLC0415
    from ctfy_agent.tools_local import ToolError  # noqa: PLC0415

    for label, args in (
        ("空 steps 报错", {"host": "127.0.0.1", "port": 9, "steps": []}),
        ("steps 非数组报错", {"host": "127.0.0.1", "port": 9, "steps": "x"}),
        ("超 40 步报错", {"host": "127.0.0.1", "port": 9,
                         "steps": [{"wait": 0.01}] * 41}),
        ("非法 hex 报错", {"host": "127.0.0.1", "port": 9,
                          "steps": [{"send_hex": "zz"}]}),
    ):
        try:
            call_domain("pwnsession", args)
            check(label, False, "没有抛异常")
        except (DomainToolError, ToolError) as exc:
            check(label, True, str(exc)[:80])


def case_whitelist() -> None:
    from ctfy_agent.tools_local import LocalTools  # noqa: PLC0415

    class _Store:
        def note(self, *a, **k):
            return None

        def append_transcript(self, *a, **k):
            return None

    tools = LocalTools(_Store(), "CHAL-PWN", allow_shell=False)  # type: ignore[arg-type]
    try:
        tools.call("pwnsession", {"host": "192.0.2.1", "port": 9,
                                  "steps": [{"wait": 0.01}]})
        check("白名单：未登记 host 被拒", False, "没有抛错")
    except Exception as exc:  # noqa: BLE001 - ToolError 或 DomainToolError 都算拒绝
        check("白名单：未登记 host 被拒",
              "白名单" in str(exc) or "不在" in str(exc) or "登记" in str(exc),
              f"{type(exc).__name__}: {exc}"[:160])


def main() -> int:
    case_wiring()
    case_fileview()
    case_templates()
    case_session()
    case_errors()
    case_whitelist()
    if FAILS:
        print(f"\nPWN SESSION smoke FAIL：{FAILS}")
        return 1
    print("\nPWN SESSION smoke OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())