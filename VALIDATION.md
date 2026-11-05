# 本次发布验证

日期：2026-11-05（Asia/Shanghai）。以下为本次从用户提供的 portable 包重新验证的结果。

- macOS Python 3.12，独立虚拟环境安装 `.[web,test-web]` 成功。
- `python -m unittest discover -s tests -v`：53 项通过，无跳过。存在 Starlette TestClient 的弃用提示。
- 临时数据库、占位模型配置下实际启动 Uvicorn；GET `/healthz` 返回 `{"ok":true}`，GET `/` 返回 HTTP 200。没有调用真实模型。
- 线上演示首页和 `/healthz` GET 成功。这不证明远端代码与本提交完全一致，也不证明模型和工具当前可用。
- 文本文件密钥模式扫描未命中；公开包中无真实 .env、数据库、私钥、会话数据或虚拟环境。扫描不是任意格式秘密不存在的数学证明。
- 本次未配置供应商密钥，未运行 live_check；未验证 Docker、Windows/WSL2、生产压力或真实长会话语义保真。
- 飞书原文未能访问，要求映射依据所附文档和代码，尚未独立复核原题全文。
- 完整提交文档位于 docs/Harness提交文档.docx；核心业务代码沿用原包。修正 architecture 中的后台任务旧描述，更新提交链接与忽略规则。

以下保留原包历史验证，不能视为本次重新执行的证据。

---

# 交付包验证

日期：2026-11-05（Asia/Shanghai）。

- 在导出的源码目录执行 `python -m unittest discover -s tests -v`：**53 项通过**。
- 验证范围：Runtime 循环、协议解析、恢复与事务、压缩预算、会话隔离、真实工具适配的离线模拟、匿名入口、CSRF/Origin/Host、限额、自动标题和改名竞态。
- `scripts/run_web.py --help` 正常；启动器配置测试通过：自动生成 600 权限签名密钥、HTTP 本地 Cookie 配置、单 worker 参数正确。该测试 mock 了 Uvicorn 启动，不代替完整网络启动测试。
- 原有线上版本已经浏览器验证真实天气、真实搜索、Markdown、会话改名、自动推进导览和无需邀请码访问；这些是此前线上验证，不是导出包重新调用全部外部接口。
- 本机没有 Docker，因此 Dockerfile/Compose **尚未实机构建验证**；Windows WSL2/Docker 也未在本机实测。
- Python 测试环境为 macOS Python 3.15；声明支持 Python 3.11+，本次未覆盖所有 Python 版本。
- 测试输出存在 Starlette 测试客户端弃用提示及一个迁移测试 SQLite 连接资源告警；53 项测试均通过。未将其隐藏为“完全无警告”。
- 已扫描交付内容，确认不含本项目现有供应商密钥、服务器密码或旧邀请码。采用源码白名单打包，不包含 `.env`、运行数据库、私聊、Cookie 签名密钥、虚拟环境、Git 历史。
- `MANIFEST.sha256` 记录包内文件哈希，ZIP 外部另附 SHA256。校验完整性不等于数字签名。

包内保留的 review.md、ai-collaboration.md 和 runtime-design.md 包含历史阶段记录；根目录 README 与 docs/SYSTEM_GUIDE.md 为当前版本说明。
