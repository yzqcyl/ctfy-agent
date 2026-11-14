"""Echo Notes —— 留言墙（出题模板，Flask）。

设计要点（审核视角）：
  * 只有「标题预览」走服务端模板渲染（漏洞点），正文走普通转义（对照陷阱）；
  * flag 由平台 flag-rotator 在运行时写入共享卷并挂载到 /answers/flag，源码里没有任何
    字面 flag，也没有读取平台内部 API 的捷径；
  * 参考解只能像选手一样：先确认模板渲染，再用 RCE 在容器里找 flag 文件。
"""
from __future__ import annotations

import os

from flask import Flask, redirect, render_template, request, url_for

app = Flask(__name__)

NOTES: list[dict] = []


@app.get("/healthz")
def healthz() -> str:
    return "ok"


@app.get("/")
def index():
    return render_template("index.html", notes=NOTES, preview=None)


@app.post("/preview")
def preview():
    title = str(request.form.get("title") or "")
    body = str(request.form.get("body") or "")
    # 漏洞点：标题进服务端模板渲染（题目目标就藏在这里）。
    # 正文只做普通转义，避免引入第二个等价入口。
    rendered = render_template_string_compat(title)
    return render_template("index.html", notes=NOTES, preview={"title": rendered, "body": body})


@app.post("/post")
def post():
    title = str(request.form.get("title") or "").strip()[:200]
    body = str(request.form.get("body") or "").strip()[:1000]
    if title:
        NOTES.append({"title": title, "body": body})
    return redirect(url_for("index"))


def render_template_string_compat(source: str) -> str:
    """把用户输入当 Jinja 模板渲染（预览功能的历史实现，正是注入点）。"""
    try:
        from flask import render_template_string

        return render_template_string(source)
    except Exception as exc:  # noqa: BLE001 - 渲染错误回显成提示，保持服务可用
        return f"[preview error] {type(exc).__name__}"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT") or 8000))