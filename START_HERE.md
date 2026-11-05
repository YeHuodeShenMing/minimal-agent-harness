# 从另一台电脑提交

## 1. 只提交答题与演示链接：不需要运行项目

解压后，`agent-harness-portable` 文件夹就是仓库根目录。包内没有 `.env`、供应商密钥、服务器登录信息、Cookie 签名密钥、线上数据库、个人聊天或虚拟环境。

1. 在可登录 GitHub 的电脑上新建一个空仓库，例如 `minimal-agent-harness`。
2. 不选择初始化 README，避免与本包的 README 冲突。
3. 用 GitHub Desktop 选择 **File → Add local repository**。如果提示不是 Git 仓库，选择创建本地仓库；位置选择本次解压的源码目录。
4. 检查 Changes：应包含 `agent_harness/`、`tests/`、`docs/`、`README.md` 等；不要包含 `.env`、`data/` 或 `.venv/`。
5. 提交后选择 Publish repository，确认仓库可见性满足招聘方要求；公开仓库便于直接评审。
6. 打开 GitHub 仓库页面，确认 README 中的演示链接可点击。在 About 的 Website 中填 `https://yehuodeshenming.site`。

也可以使用 Git 命令（在解压的源码目录执行，将占位符替换为自己的仓库地址）：

```bash
git init
git add .
git status --short
git commit -m "Submit minimal agent harness and interactive demo"
git branch -M main
git remote add origin https://github.com/YeHuodeShenMing/minimal-agent-harness.git
git push -u origin main
```

如 Git 提示缺少作者信息，按你自己的身份设置 `git config user.name` 和 `git config user.email`。使用 GitHub 支持的浏览器认证、GitHub Desktop 或个人访问令牌，不使用 GitHub 账号密码作为 Git HTTPS 密码。

如果已经初始化了远端 README，最简单的方式是先 clone 仓库，把本包内容复制进去再 commit/push；不要强制覆盖远端历史。

## 2. 提交给招聘方的文字

替换仓库地址后使用：

> 你好，这是我的最小 Agent Harness 笔试提交：
> - 源码与运行说明：https://github.com/YeHuodeShenMing/minimal-agent-harness
> - 在线演示：https://yehuodeshenming.site
> - 架构设计回答：仓库 docs/architecture.md
> - AI 协作与问题解决记录：仓库 docs/ai-collaboration.md
> 核心 Runtime 自行实现，支持真实模型调用、四个工具、多会话隔离、追问、上下文压缩及执行日志。网页无需邀请码，可通过「新手导览」交互体验。密钥不包含在仓库中。

## 3. 如果要在另一台电脑运行

按 README 的 Python 或 Docker 步骤操作。为了运行，需要你自己的 DeepSeek/兼容供应商密钥；搜索另需 Tavily 密钥。现有线上部署已经配置好，不需要向评审方提供任何密钥。

macOS / Linux 可使用 Python 3.11+；Windows 请使用 WSL2 或 Docker，因为核心会话锁使用 Unix 的 `fcntl`。不要直接复制旧电脑的 `.venv`。

依赖需要联网安装。此包是完整源码交付包，不是内置 Python 与所有依赖的离线安装器。Docker 配置也需要首次联网构建。

## 4. 独立部署到新服务器（可选）

现有网址已经可用，提交 GitHub 不需要执行这一节。

1. 在 Ubuntu 安装 Python 3.11+、venv、Caddy，并创建系统服务账户 `agentweb`。
2. 将源码放到 `/opt/minimal-agent`，建立虚拟环境并安装 `.[web]`。
3. 创建 `/var/lib/minimal-agent`，所有者设为 `agentweb`，权限 700。
4. 创建 `/opt/minimal-agent/.env`，填写 LLM 和 Tavily 配置，所有者 `agentweb`，权限 600。
5. 创建 `/etc/minimal-agent.env`，仅 root 可读，内容参考 `deploy/web.env.example`。用 `python3 -c 'import secrets; print(secrets.token_hex(32))'` 生成全新的 WEB_SECRET。
6. 复制 `deploy/minimal-agent.service` 至 `/etc/systemd/system/`；修改 Caddyfile 中的域名为自己的域名，配置 A 记录并放行 80/443。
7. 执行 `sudo systemctl daemon-reload`、`sudo systemctl enable --now minimal-agent`，校验 Caddy 配置后启动/重载 Caddy。
8. 检查 `/healthz`、真实对话、工具调用、刷新持久化和陌生浏览器会话隔离。

生产服务仅监听本机 8000，由 Caddy 对外提供 HTTPS；`COOKIE_SECURE=1`。不要把本地 HTTP 的 Cookie 配置直接用于公网。

## 5. 交付边界

包内 `docs/architecture.md` 是架构设计稿，请结合自己的理解复核后提交，尤其是其中生产扩展方案；它们不等于项目全部已经实现。AI 协作过程已注明，不把自动生成的材料当作未经复核的个人经历。

现有服务器与域名的续费、API 余额和公网可用性仍需要维护。上传 GitHub 不会自动同步后续线上修改，也不会让 GitHub 托管 Python 服务。
