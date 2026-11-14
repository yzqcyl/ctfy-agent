# Echo Notes —— 出题参考解与设计说明

## 题目目标

`Echo Notes` 是一个极简留言墙：选手在「预览」表单里填写标题与正文，服务端把标题
渲染成预览卡片。目标是拿到部署环境里的口令（平台动态铸造，`FLAG{...}` 格式）。

## 解法步骤（参考解只依赖选手可获得的输入）

1. 打开首页，确认 `/preview` 会把标题原样回显。
2. 提交标题 `{{7*7}}`：预览卡片显示 `49`，说明标题被当作服务端模板渲染（服务端模板注入）。
3. 利用 Jinja2 的 `cycler` 全局对象拿到 `os` 模块，执行 `popen`：
   `{{ cycler.__init__.__globals__.os.popen("find / -maxdepth 4 -type f ...").read() }}`。
4. 在命令输出里定位形如 `FLAG{...}` 的内容并提交。

完整参考解见 `solution/exploit.py`（`class Exploit(ExploitBase)`，`run()` 里先探测再 RCE）。

## 部署形态

- `services/web/Dockerfile`：`python:3.12-slim-bookworm` + `flask==3.0.3`（TUNA 源）。
- `docker-compose.yml`：web 服务挂载 `flagdata:/answers:ro`；平台 `flag-rotator` 服务负责把
  运行时铸造的 flag 写进共享卷。源码里没有任何字面 flag，也没有读取平台内部 API 的捷径。
- 健康检查 `GET /healthz`，资源限制 0.5 CPU / 512MiB。

## 出题实现备注

- 标题走 `render_template_string`（漏洞点），正文只做普通转义（对照陷阱，避免多入口）。
- 平台的静态检查要求：`metadata.yaml` 不写 `id`（META005）、`description` 不暴露漏洞类别
  （META007）、源码不含字面 `FLAG{...}`（SOURCE001）、参考解不读 `/answers/` 等平台内部路径
  （EXPLOIT007）。
- 审核要点：一眼可见「渲染用户输入」这一教科书漏洞，参考解 4 步内完成；难度靠 Jinja2 全局
  对象链而不是偏门机制，便于人工判断可解性。