> 以下是实现设计与演进记录；最新运行方式见根目录 README。

# Minimal Agent Harness

从零实现的命令行 Agent。Python 标准库完成模型传输、工具注册与执行、对话循环、SQLite 持久化、上下文压缩和执行日志，不使用任何 Agent 框架。默认连接真实 DeepSeek API；天气接入 Open-Meteo，搜索接入 Tavily，均使用真实接口。

## 快速运行

需要 **Python 3.11+、macOS 或 Linux**；不需要安装第三方运行依赖。

```bash
cd agent-harness
cp .env.example .env  # 已有 .env 时不要覆盖
# 编辑 .env，填入自己的密钥及供应商实际支持的模型 ID
python3 -m agent_harness --user A --session window-1
```

`.env` 自动加载；进程环境变量优先。密钥、数据库均已加入 `.gitignore`。按用户要求使用 **DeepSeek-V4.1-Flash-1017**。它对应的官方 API model ID 是 `deepseek-flash`；直接传版本展示名会返回400。[官方模型说明](https://api-docs.deepseek.com/quick/_start/pricing/)。程序不会偷偷回退到其他模型。

```dotenv
LLM_API_KEY=填写你自己的密钥
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-flash
```

终端窗口一：

```bash
python3 -m agent_harness --user A --session window-1
# 我叫小林，查广州天气，并添加待办：明天带伞
# 我叫什么？
# 将刚才带伞的待办标记为完成，先查列表再修改
```

终端窗口二：

```bash
python3 -m agent_harness --user A --session window-2
# 写一句周报开头，并添加待办：周五交周报
```

退出重开同一 user/session 即可续聊。两个窗口的历史、摘要、待办完全隔离。交互命令：`/session NAME` 切换窗口、`/trace` 看日志、`/resume` 恢复被中断任务、`/quit` 退出。单次调用及日志：

```bash
python3 -m agent_harness --user A --session window-1 -m '使用calculator计算(128+72)*3'
python3 -m agent_harness --user A --session window-1 --trace
python3 -m agent_harness --user A --session window-1 --resume
```

这是本地 CLI；`--user` 是命名空间，不是身份认证。没有把可伪造 user_id 的接口当作多租户服务发布。

## 实现与设计

```text
CLI -> Runtime -> Context -> ChatModel -> parse Decision
           ^                       |          |
           |                       |    final / tool_calls
           |                       |          v
           +------- tool result <- Registry + schema validation
                              |
                   SQLite state + trace transaction
```

- `model.py`：真实 `/chat/completions` 传输；从协议提取最终回复、多个 function calls、可选简短决策摘要。工具 arguments 再单独 JSON 解码，错误反馈给模型纠正。截断、重复 call ID、畸形响应不可直接执行。
- `tools.py`：注册时提供 name、description、parameters schema。注册表输出给模型，工具选择不依赖关键词路由。新增工具无需修改主循环。
- `runtime.py`：接收输入 → 模型决策 → 按顺序执行一组工具 → 配对 tool_call_id 回填 → 继续，直到 final 或预算/异常终止。独立调用与多工具调用都遵循同一循环。
- `store.py`：SQLite 主键 `(user_id, session_id)`，状态和事件事务写入；同一 session 由跨进程文件锁保护，不同 session 可独立调用模型。

工具：

| 工具 | 参数 | 行为 |
|---|---|---|
| calculator | expression | AST 白名单算术，不使用 eval；限制长度、节点数、幂指数和结果范围 |
| todo | action, text?, id?, offset?, limit? | add/list/complete/delete；会话级持久化，最多100条；列表默认最多5条且按输出字节限额分页，按next_offset续查 |
| search | query, time_range（可选） | Tavily 联网搜索，返回网页标题、摘要、来源链接；需 TAVILY_API_KEY |
| weather | city, country_code/date（可选） | Open-Meteo 当前天气估算及未来 7 天预报，返回匹配地点、当地时间、单位和来源 |

参数校验实现本项目使用的 JSON Schema 子集：type、required、enum、additionalProperties、maxLength、minimum/maximum；不是完整 JSON Schema 引擎。字段间约束（如 add 必须有 text）在工具中校验。

## Context 与 memory

分三层，避免把模型看到的摘要当作唯一真相：

1. **原始事件**：用户输入、工具调用及结果、最终回复保存在 SQLite。压缩移出的消息记录在 compaction 事件里，不破坏 tool call/result 配对。
2. **结构化状态**：todo 独立于消息摘要持久化。模型需要待办时自主调用 todo.list；不用每轮塞入全量列表，也不会因摘要遗漏丢失待办。
3. **工作上下文**：系统规则 → 历史摘要（用户角色、明确标注不可信资料）→ 最近完整对话和当前轮。每次模型调用前加载同一会话的摘要、消息；这是基础会话 memory 的召回时机。新会话不跨窗口召回。

用户与助手的正常回复、工具 schema、必要工具结果进入 context；私有 chain-of-thought 不请求、不落库、不回灌。协议如果额外给出简短 `reasoning_summary`，只记入 decision trace。默认模型可能不返回该扩展字段，不伪造“思考过程”。

默认输入上限 12000 UTF-8 bytes，工具输出上限12000 bytes，模型响应体上限2MB。工作上下文与 schema 合计达到48000 bytes 时，使用**真实 LLM**将早期完整轮次压为事实摘要，通常保留最近两轮；仍超预算时保留当前轮并重做摘要。摘要输入也有独立预算：原始消息分成每块最多24000 bytes的资料，按顺序带入上一块摘要，每次请求校验不超过64000 bytes；单次摘要流程最多16块。单条历史消息超过24000 bytes或总块数超限时明确停止并保留原文。分块可能切开资料中的调用/结果，但这些都是被引用的JSON数据；实际对话历史仍只按完整轮次裁剪。摘要最多6000 bytes，失败或超长保留原历史并明确报错，不默默裁掉关键约束。当前轮本身过长则停止，用户可拆分任务。原始存档可人工查 trace；本版没有语义检索、向量库或自动原文回查工具。

这里以 UTF-8 bytes 作为保守长度代理，并为输出留出空间；不是精确 tokenizer 计数，schema 封装与供应商特殊计费也有差异。适用于支持足够窗口的目标模型，实际窗口较小需调低 Runtime.budget。摘要是有损的：保存原文、保留最近轮次及结构化待办降低损失，但不能保证所有闲聊细节都能召回。架构题另述生产系统的证据回查设计。

## 异常、恢复与 trace

每次用户任务最多8次主决策模型调用，每次最多8个工具调用；每次压缩最多尝试两个保留范围，每个范围最多16次分块摘要调用（通常仅1次）；这些额外调用单独受限。网络单次超时40秒；429/部分5xx/网络错误最多3次指数退避；401/400不盲目重试。模型协议错误允许一次重试；工具参数错误、除零、未知工具均以结构化失败返回，让模型继续处理。已执行的待办不会因最终回复失败回滚。

保存 assistant 的 tool_calls 后逐一执行并 checkpoint：todo 副作用、tool result 和 trace 在同一事务中提交。中断后显式 `/resume`；已提交的 todo 不会重复添加，未提交的本地变更会从旧状态重做。模型请求可能重复计费，不承诺外部系统的 exactly-once。未来接入付款/发信等外部副作用必须增加幂等键与 outbox，不能套用本地事务的保证。

同一 session 忙时拒绝新的调用并提示重试；不并发改写历史。对 busy 消息排队、异步工具通知的生产方案在架构题中说明，本版只实现同步工具。工具执行日志含 run_id、call_id、工具名、参数、结果、耗时及 UTC 时间；错误、压缩、模型开始与最终回复也有事件。日志可能含用户输入，不应公开上传生产数据。

## 验证

离线测试通过可控 ScriptedModel 验证 runtime 分支，而不依赖模型随机性；这不是把运行模式替换成 mock。

```bash
python3 -W error::ResourceWarning -m unittest discover -s tests -v
```

覆盖直接回复、纯对话追问、工具追问、多工具、跨用户/窗口隔离、跨实例续聊、参数错误及纠正、模型错误、循环上限、上下文压缩/失败、输入边界、工具输出事务回滚、真实跨进程 busy 锁、中断前后恢复和 todo 副作用去重。CI 在 Python 3.11/3.13 上执行离线测试；本地已验证版本见测试记录，不宣称 CI 已在远程运行。

真实集成测试（会产生 API 费用，使用临时数据库与合成数据）：

```bash
python3 scripts/live_check.py
```

9个场景验证天气+待办、另一窗口周报+待办、重启后名字追问、工具追问完成待办、另一用户隔离、搜索+计算、未点名工具的自主选择、自然追问删除待办、真实摘要压缩后事实追问。压缩场景预置合成长历史，摘要和回答都由真实模型产生。成功后写 `docs/validation-live.json`，保留实际模型标识、工具结果和耗时；不含密钥。

## 提交材料

- [五模块架构设计回答](docs/architecture.md)
- [AI Prompt 与问题解决记录](docs/ai-collaboration.md)
- [验证记录与限制](docs/validation.md)
- [当前真实 API 测试原始记录](docs/validation-live.json)
- [逐项复核与修复记录](docs/review.md)

尚未创建/推送 GitHub 仓库；提供目标地址后，仅推送本项目源码、文档与合成测试记录，不上传 `.env` 或本地数据库。

## 网页演示与服务器部署

新增轻量 Web 适配层，复用同一个 Runtime；核心循环仍不依赖 Agent 框架。

```bash
python3 -m venv .venv
.venv/bin/pip install '.[web,test-web]'
# 配置 WEB_SECRET（32+字符），以及原有 LLM_* 变量
# 本地 HTTP 调试设 COOKIE_SECURE=0；线上 HTTPS 必须为 1
.venv/bin/uvicorn agent_harness.web:create_app --factory --host 127.0.0.1 --port 8000
```

页面包含多会话聊天、待办、工具执行日志、上下文用量和历史摘要。任务在线程池执行，前端轮询进度；单进程最多两个任务，同一会话拒绝重入。意外中断后可以恢复任务。浏览器使用签名 HttpOnly Cookie 隔离身份，写请求验证 CSRF 和 Origin；助手回复经 Marked 解析和 DOMPurify 白名单清理后渲染 Markdown，用户输入按纯文本展示。访客无需邀请码，打开页面自动获得浏览器身份；清除 Cookie 或七天后过期会失去对旧会话的访问。

每浏览器每天最多20次任务，全站默认每天80次（UTC计日，可用 WEB_DAILY_LIMIT 调整），这限制任务数而非 token 或金额。日志和会话保存在 SQLite；不自动删除，应定期备份及检查磁盘。天气和搜索调用真实第三方接口，城市名或搜索词会发送至对应服务商。

服务器文件模板见 `deploy/`。部署采用一个 Uvicorn worker、专用系统用户和 Caddy 自动 HTTPS：

- `/opt/minimal-agent`：代码与 Python 虚拟环境
- `/opt/minimal-agent/.env`：模型配置，仅服务账户可读
- `/etc/minimal-agent.env`：Cookie签名密钥、域名和限额，仅 root 可读
- `/var/lib/minimal-agent/web.db`：线上会话与日志
- `systemctl status minimal-agent caddy`：服务状态
- `journalctl -u minimal-agent -n 50`：服务日志

运行网页测试：`.venv/bin/python -m unittest discover -s tests -v`。

### 免费天气与搜索接口

- **Open-Meteo**：无需注册或密钥；免费接口用于非商业用途，上限 600 次/分钟、5,000 次/小时、10,000 次/天、300,000 次/月。当前站点为个人演示用途。商用需另行购买授权。天气为模型估算和预报，回答附 Open-Meteo 和 GeoNames 来源。详见 https://open-meteo.com/en/pricing 。
- **Tavily**：在 https://app.tavily.com 免费注册，在 `.env` 配置 `TAVILY_API_KEY`。每月 1,000 积分，无需信用卡；固定使用 `basic` 搜索且关闭自动升级参数，每次 1 积分，最多返回 5 条结果。不要开启付费升级或自动充值即可保持免费计划。详见 https://docs.tavily.com/documentation/api-credits 。
- 密钥只在服务端读取，不包含在 API 响应、工具日志或网页中。修改密钥后需重启服务。未配置搜索密钥、无结果、额度耗尽和接口异常会明确返回，不用模拟结果替代。
- 请求超时 15 秒，响应最多 512 KB，不跟随重定向。工具结果保留来源且限制大小；网页摘要视为不可信数据，不覆盖系统指令。
- 较早的测试记录可能包含旧版 mock 工具结果，仅代表当时版本。新调用均走上述真实接口。

### 会话标题

首条消息发送后先显示内容短标题，回复完成后由模型概括为简短标题（单独一次模型调用，不写入对话上下文）。标题生成失败保留内容短标题。侧栏铅笔按钮可重命名任何自己的会话，最多 60 字；手动标题不会被后台自动命名覆盖。标题保存在 SQLite，旧会话数据自动迁移保留。
