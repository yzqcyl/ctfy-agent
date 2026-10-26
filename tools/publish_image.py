# -*- coding: utf-8 -*-
"""一条命令把镜像发布链路跑完：建仓 → 推送 → 触发构建 → 轮询结果 → 改包可见性。

只用 stdlib（容器契约也是纯 stdlib，这里保持一致）。

用法（token 只放环境变量，不落盘、不进 argv）：
    $env:GH_TOKEN = "ghp_xxx"
    python tools/publish_image.py                # 全流程
    python tools/publish_image.py --status       # 只看最近一次构建
    python tools/publish_image.py --visibility   # 只改 ghcr 包可见性

需要的 PAT 权限（classic token）：
    repo            —— 建仓 + 推送
    workflow        —— 触发/管理 Actions
    write:packages  —— 把容器包改成 public，供平台匿名拉取
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

API = "https://api.github.com"
REPO_NAME = "ctfy-agent"
WORKFLOW_FILE = "build-agent-image.yml"
PACKAGE_NAME = "ctfy-agent"
ROOT = Path(__file__).resolve().parents[1]


def token() -> str:
    value = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""
    if not value.strip():
        raise SystemExit("缺少 GH_TOKEN 环境变量。先设置：$env:GH_TOKEN = \"ghp_xxx\"")
    return value.strip()


def api(method: str, path: str, payload: dict | None = None, *, token_value: str | None = None,
        expect: tuple[int, ...] = (200, 201, 204)) -> tuple[int, object]:
    url = path if path.startswith("http") else API + path
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("X-GitHub-Api-Version", "2022-11-28")
    if token_value:
        request.add_header("Authorization", f"Bearer {token_value}")
    if data:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            body = response.read().decode("utf-8", "replace")
            parsed = json.loads(body) if body.strip() else None
            if response.status not in expect:
                raise SystemExit(f"{method} {path} -> HTTP {response.status}: {body[:400]}")
            return response.status, parsed
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        if exc.code in expect:
            try:
                return exc.code, json.loads(body) if body.strip() else None
            except json.JSONDecodeError:
                return exc.code, None
        raise SystemExit(f"{method} {path} -> HTTP {exc.code}: {body[:400]}") from None
    except urllib.error.URLError as exc:
        raise SystemExit(f"连不上 GitHub API：{exc.reason}") from None


def whoami(tok: str) -> dict:
    _, user = api("GET", "/user", token_value=tok)
    assert isinstance(user, dict)
    print(f"[身份] {user.get('login')}")
    return user


def ensure_repo(tok: str, owner: str) -> None:
    status, _ = api("GET", f"/repos/{owner}/{REPO_NAME}", token_value=tok, expect=(200, 404))
    if status == 200:
        print(f"[仓库] {owner}/{REPO_NAME} 已存在")
    else:
        print(f"[仓库] 创建 {owner}/{REPO_NAME}（私有）")
        api("POST", "/user/repos", {
            "name": REPO_NAME,
            "private": True,
            "auto_init": False,
            "description": "TPCTF 2026 agent container image + cloud build",
        }, token_value=tok)

    # 让 workflow 有写权限（默认是只读，否则推 GHCR 会 403）
    try:
        api("PUT", f"/repos/{owner}/{REPO_NAME}/actions/permissions/workflow",
            {"default_workflow_permissions": "write", "can_approve_pull_request_reviews": False},
            token_value=tok)
        print("[仓库] workflow 权限已设为 read/write")
    except SystemExit as exc:
        print(f"[仓库] 警告：改 workflow 权限失败（可手动在 Settings→Actions 勾选）：{exc}")


def git(*args: str) -> subprocess.CompletedProcess:
    proc = subprocess.run(["git", *args], cwd=str(ROOT), capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    return proc


def push(tok: str, owner: str) -> None:
    """认证走 http.extraHeader：令牌不进远程 URL，也不会被写进 .git/config。"""
    plain = f"https://github.com/{owner}/{REPO_NAME}.git"
    current = git("remote", "get-url", "origin")
    if current.returncode != 0:
        git("remote", "add", "origin", plain)
    else:
        git("remote", "set-url", "origin", plain)

    branch = git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip() or "main"
    basic = base64.b64encode(f"x-access-token:{tok}".encode()).decode()
    print(f"[推送] {branch} → {owner}/{REPO_NAME}")
    proc = git("-c", f"http.extraHeader=Authorization: Basic {basic}",
               "push", plain, f"HEAD:refs/heads/{branch}")
    if proc.returncode != 0:
        raise SystemExit(f"推送失败：\n{(proc.stderr or proc.stdout)[-1500:]}")
    print("[推送] 完成")


def dispatch(tok: str, owner: str, tag: str = "v1") -> int:
    branch = git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip() or "main"
    print(f"[触发] workflow {WORKFLOW_FILE} @ {branch} tag={tag}")
    # 必须显式带 inputs.tag：workflow 的默认值是 v1，漏传就会移动已提交的 v1 tag（tag_moved 判负）
    api("POST", f"/repos/{owner}/{REPO_NAME}/actions/workflows/{WORKFLOW_FILE}/dispatches",
        {"ref": branch, "inputs": {"tag": tag}}, token_value=tok, expect=(204,))
    # 拿刚触发的那次 run
    for _ in range(10):
        time.sleep(3)
        _, runs = api("GET", f"/repos/{owner}/{REPO_NAME}/actions/runs?per_page=5", token_value=tok)
        items = (runs or {}).get("workflow_runs") or []
        for run in items:
            if run.get("name") == "build-agent-image" or WORKFLOW_FILE in str(run.get("path")):
                if run.get("status") in ("queued", "in_progress"):
                    return int(run["id"])
        if items:
            return int(items[0]["id"])
    raise SystemExit("没找到刚触发的 run")


def watch(tok: str, owner: str, run_id: int) -> dict:
    print(f"[等待] run {run_id}")
    last = ""
    for _ in range(180):  # 最多等 15 分钟
        time.sleep(10)
        _, run = api("GET", f"/repos/{owner}/{REPO_NAME}/actions/runs/{run_id}", token_value=tok)
        assert isinstance(run, dict)
        line = f"status={run.get('status')} conclusion={run.get('conclusion')}"
        if line != last:
            print("        " + line)
            last = line
        if run.get("status") == "completed":
            _, jobs = api("GET", f"/repos/{owner}/{REPO_NAME}/actions/runs/{run_id}/jobs", token_value=tok)
            for job in (jobs or {}).get("jobs") or []:
                print(f"\n  作业 {job.get('name')} → {job.get('conclusion')}")
                for step in job.get("steps") or []:
                    print(f"    [{step.get('conclusion') or step.get('status'):>10}] {step.get('name')}")
            print(f"\n  日志页：{run.get('html_url')}")
            return run
    raise SystemExit("等待超时，请到 Actions 页面查看")


def set_visibility(tok: str) -> None:
    path = f"/user/packages/container/{PACKAGE_NAME}"
    try:
        api("PATCH", path, {"visibility": "public"}, token_value=tok)
        print(f"[包] {PACKAGE_NAME} 已设为 public —— 平台可以匿名拉取")
    except SystemExit as exc:
        print(f"[包] 自动改可见性失败：{exc}")
        print("     请手动：GitHub 头像 → Your packages → ctfy-agent → Package settings → "
              "Danger Zone → Change visibility → Public")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="发布 agent 镜像到 ghcr（一条命令）")
    parser.add_argument("--status", action="store_true", help="只看最近一次构建")
    parser.add_argument("--visibility", action="store_true", help="只改 ghcr 包可见性")
    parser.add_argument("--skip-watch", action="store_true", help="触发后不等待")
    parser.add_argument("--tag", default="v1", help="镜像 tag（发新版本用 v2；覆盖 v1 会让平台标记 tag_moved）")
    args = parser.parse_args(argv)

    tok = token()

    if args.status:
        owner = whoami(tok)["login"]
        _, runs = api("GET", f"/repos/{owner}/{REPO_NAME}/actions/runs?per_page=3", token_value=tok)
        for run in (runs or {}).get("workflow_runs") or []:
            print(f"  {run.get('run_number')}: {run.get('status')}/{run.get('conclusion')} "
                  f"{run.get('html_url')}")
        return 0

    if args.visibility:
        set_visibility(tok)
        return 0

    user = whoami(tok)
    owner = str(user["login"])
    tag = (args.tag or "v1").strip()
    if tag == "v1":
        print("[警告] tag=v1 会移动已冻结并提交审核的 digest（平台会判 tag_moved）。"
              "发新版本请用 --tag v2。")
    ensure_repo(tok, owner)
    push(tok, owner)
    run_id = dispatch(tok, owner, tag)
    if not args.skip_watch:
        run = watch(tok, owner, run_id)
        if run.get("conclusion") != "success":
            print("\n构建没成功，先看上面失败的是哪一步。")
            return 1
        set_visibility(tok)
        print(f"\n镜像地址：ghcr.io/{owner}/{PACKAGE_NAME}:{tag}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
