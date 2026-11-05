# Minimal Agent Harness

一个从零实现核心循环的 Agent，带可交互网页演示。模型自主决定直接回复或调用工具，支持多会话、连续追问、上下文压缩、持久化和执行日志。

**在线体验：[yehuodeshenming.site](https://yehuodeshenming.site)** · 无需邀请码，点击左侧「新手导览」体验完整流程。

## 阅读顺序

- [完整笔试提交文档](docs/Harness提交文档.md)：项目说明、五模块架构回答、验证和面试阅读索引。

- [提交与迁移说明](START_HERE.md)：另一台电脑运行和上传 GitHub 的步骤。
- [系统详细介绍](docs/SYSTEM_GUIDE.md)：前后端、用户身份、存储、Runtime、设计取舍与题目对应关系。
- [五模块架构设计回答](docs/architecture.md)：每模块选一题；明确区分已实现能力与生产扩展方案。
- [AI 协作与问题解决记录](docs/ai-collaboration.md)：历史记录及当前阶段补充。
- [当前验证结果](VALIDATION.md)：验证范围、局限和复现命令。
- [Runtime 设计细节](docs/runtime-design.md)：协议解析、工具注册、memory 放置与压缩。

## 技术方案

前端使用原生 HTML/CSS/JavaScript，Marked + DOMPurify 渲染 Markdown；FastAPI + 单 worker Uvicorn 提供接口。核心 Runtime、模型 HTTP 调用、工具注册与执行均自行实现，不使用 Agent 框架。数据库为 SQLite（WAL），线上通过 Caddy 提供 HTTPS，systemd 管理进程。

四个工具：安全算术计算器、会话待办、Tavily 联网搜索、Open-Meteo 天气预报。模型默认配置为 `deepseek-flash`，实际可用性以你的供应商账号为准。

## 本地运行（macOS / Linux / Windows WSL2）

需要 Python 3.11+；Windows 原生环境不支持本项目的 `fcntl` 会话锁，请使用 WSL2 或下方 Docker 方式。

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[web,test-web]'
cp .env.example .env
# 编辑 .env，填写你自己的 LLM_API_KEY；搜索另填 TAVILY_API_KEY
python scripts/run_web.py
```

浏览器打开 http://localhost:8000 。启动器自动在 `data/web-secret` 创建并复用 Cookie 签名密钥，SQLite 保存在 `data/web.db`。这些运行数据不提交到 GitHub。

CLI 不需要 Web 依赖：在项目根目录运行 `python -m agent_harness --user A --session window-1`。网页匿名身份与 CLI 的 `--user` 命名空间是不同入口。

## Docker（Windows / macOS / Linux）

安装 Docker 和 Compose 后，将 `.env.example` 复制为 `.env`，填入自己的密钥：

```bash
docker compose up --build -d
docker compose logs -f
```

访问 http://localhost:8000 。数据写入命名卷 `agent_data`。`docker compose down` 保留数据；**不要添加 `-v`，除非明确要删除数据**。此配置只绑定本机回环地址，不是公网 HTTPS 部署配置。

## 测试

```bash
python -m unittest discover -s tests -v
```

离线测试不需要真实密钥。`python scripts/live_check.py` 会调用真实模型及工具、消耗供应商额度，按需运行；当前发布未重跑完整历史真实场景脚本。

## 当前边界

- 匿名签名 Cookie 有效期 7 天，没有账号注册、跨设备登录或旧身份找回；清除 Cookie 后不能继续访问原会话。
- 每浏览器每日 20 个任务、全站默认每日 80 个任务，UTC 计日；这些是任务次数限制，不是 token/金额上限。重建匿名身份不构成可靠的实名限额。
- 最多两个任务并行，同一会话不允许重复执行；当前按单 Web worker 部署。
- 历史压缩为有损摘要，UTF-8 字节预算不是精确 token 预算；无向量库、跨会话检索或自动原文回查。
- 原子提交和避免重复待办仅适用于本地 SQLite 状态，不保证任意外部工具的 exactly-once。
- Open-Meteo 免费服务适合非商业演示；Tavily 需要自备密钥。外部接口故障会明确返回，不使用假数据兜底。

## 演示与提交

GitHub 负责保存源码，现有域名访问的是独立 Ubuntu VPS。上传仓库不需要迁移服务器或复制线上密钥；GitHub Pages 不能运行本项目的 Python 后端。请保留第三方前端依赖的许可证文件。
