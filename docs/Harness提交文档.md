# Agent Harness 笔试提交文档

Minimal Agent Harness

提交账号：YeHuodeShenMing

提交日期：2026年11月5日

本项目实现了一个可持续对话的最小 Agent Harness：模型自主选择工具，Runtime 执行并回填结果，在独立会话中持久保存状态；历史过长时生成摘要并保留近期完整轮次。核心循环、模型传输、工具注册和存储均自行实现，没有使用 Agent 框架。FastAPI 仅负责网页接口。

### 提交入口

[源码与运行说明](https://github.com/YeHuodeShenMing/minimal-agent-harness)

[在线演示](https://yehuodeshenming.site)

[原始题目](https://ucnk0qbix8zv.feishu.cn/docx/BQkad8EA6oXK1rxuqbec9ycXnbd)

### 交付结论

现有交付包经本次复核，53 项离线测试通过，未跳过测试；本地 HTTP 启动与健康检查通过。线上首页和健康接口可访问。本次未提供供应商密钥，因此没有重跑真实模型、搜索、天气及压缩质量评测。历史验证记录与本次结果分开标注。

### 材料范围

本文包含实现与要求对应、Runtime 和 memory 设计、五模块架构设计回答、复现和验证、密钥管理、AI 协作及代码阅读索引。架构题中的生产扩展不等于已实现能力。

题目核对说明：本次飞书页面无法读取，要求映射与模块选题依据所提供源码包中的 SYSTEM_GUIDE、architecture 和 review 整理，尚未独立核对飞书原题全文。

AI 使用说明：项目及材料含 AI 辅助产出；本文不将它们描述为候选人独立手写的成果。提交者仍需实际阅读、运行并能解释关键代码。

## 一 实现范围与验收对应

### 核心循环

输入 → 模型决策 → 工具执行 → 结果回填 → 继续或结束。代码位置：runtime.py / model.py。

### 工具注册

calculator、todo、search、weather；名称、描述、参数 Schema。代码位置：tools.py / online_tools.py。

### 多会话与追问

user_id 与 session_id 联合隔离，近期历史和精确 todo 状态。代码位置：store.py / runtime.py。

### 长上下文

字节预算触发、真实模型滚动摘要、保留完整近期轮次。代码位置：runtime.py。

### 异常与恢复

主决策最多 8 步、网络重试、协议校验、未完成工具恢复。代码位置：model.py / runtime.py。

### 执行证据

SQLite 事件表记录参数、结果、耗时、异常及压缩归档。代码位置：store.py / web.py。

### 交互演示

匿名签名 Cookie、会话归属、轮询执行面板、Markdown 清理。代码位置：web.py / static/。

### 工程交付

CLI、网页启动器、Docker 配置、测试、设计和协作记录。代码位置：README / tests / docs。

当前实现不包含跨会话向量召回、通用任务图、持久后台队列、多机租约、流式 token 输出或外部副作用 exactly-once。Windows 原生不支持 fcntl 会话锁，应使用 WSL2 或 Docker；本次未实测这两种环境。

## 二 Runtime 与持久化机制

### 一次请求的执行路径

浏览器通过 FastAPI 提交消息，接口校验身份、CSRF、归属和额度，再交给线程池。Runtime.chat 获取会话文件锁，读取状态；有中断任务时要求先 resume，正常新消息则初始化 run_id、steps、repairs 并落库。

Runtime.loop 每步先补齐尚未执行的工具调用，再检查步数并整理上下文。模型结果经过 parse 验证：直接答案则 finish；工具调用则先保存 assistant 消息，再执行每个工具并按 call_id 回填 role=tool。模型继续读取结果决定下一步。

### 状态与 trace 原子保存

SQLite sessions 的主键为 (user_id, session_id)，JSON state 保存 messages、summary、todos、active、steps 等。events 表按序记录运行事件。Store.save 在同一事务中提交状态与事件；todo 不另开脱节的数据库写入。WAL 允许读写并发，同一会话使用非阻塞 fcntl 文件锁。

execute_pending 根据已经存在的 tool_call_id 判断未完成项。工具在 state 副本上执行，结果序列化并确认不超过 12000 字节后，才把 todos 合并并保存。异常转换为失败的工具结果，不把未提交的 todo 修改写入业务状态。

### 故障窗口与边界

assistant 调用已落盘而工具未执行：resume 会补执行。工具结果和本地状态已一起提交：resume 会跳过已答复 call_id。这支持本地待办的恢复，但外部 API 成功与 SQLite 提交之间仍存在故障窗口；未来支付或邮件工具需业务幂等键、查询或补偿。

模型主决策最多 8 步；传输最多 3 次尝试，协议异常最多允许一次重新尝试。摘要和自动标题还有额外模型请求，因此 8 步不是整个任务的总 HTTP 次数或费用上限。当前没有整任务墙钟期限。

## 三 上下文 工具与网页边界

### 上下文与 memory

每次模型输入为系统规则与当前 UTC 时间、标为不可信资料的旧摘要、近期消息；工具 Schema 单独随请求提供。todos 是精确业务状态，按需调用 todo 查询，不靠有损摘要猜测状态。没有跨会话 memory 或自动原文回查。

默认预算为 48000 UTF-8 字节，包含 prompt 与工具 Schema 的序列化估算，并非精确 token 数。超限时按 user 轮次边界切割，先尝试保留最近两个轮次，必要时保留当前轮次；不拆散 tool call 和 result。摘要资料分块为约 24000 字节，最多 16 块；输出超过 6000 字节或非法则拒绝替换。移出的原文进入 compaction 事件。

### 四个工具

calculator 通过 AST 白名单解释算术表达式，不使用 eval。todo 提供增删查改，最多 100 条并分页限制结果体积。search 调用 Tavily，最多返回 5 条链接与摘要。weather 调用 Open-Meteo 地理编码和天气接口，携带匹配地区、当地日期、单位及来源。联网失败返回明确错误，不制造替代数据。

Registry 验证的是所注册工具使用的 JSON Schema 子集，不是完整标准。工具结果和历史摘要被提示为不可信资料，这不是对提示注入安全性的证明；运行时边界仍须由代码保证。

### Web 与身份

匿名身份由随机 user_id、到期时间和 HMAC 签名构成，Cookie 有效期 7 天。生产使用 HttpOnly、Secure、SameSite=Lax；写入校验 CSRF 和 Origin，Host 受允许名单限制，接口逐次检查会话归属。清除 Cookie 后没有账号找回或跨设备续聊。

Web 使用进程内线程池，浏览器约每 1.6 秒轮询。默认全站并行 2 个任务、每访客每日 20 个任务、全站每日 80 个任务，UTC 计日；单 worker 部署。内存中的 busy 和并发限制不能直接扩展为多 worker 全局控制。Markdown 经 DOMPurify 清理，自动标题以条件更新避免覆盖手动改名。

## 四 架构题一 长会话上下文压缩

选题：模块一，第2题：200轮会话如何压缩并保持流畅？

我会先把“事件记录”和“当前模型输入”分开。200轮原文仍可检索，工作上下文只保留完成下一步必要的信息；按token预算触发，而不是机械地每20轮总结一次。

输入按预算分配：固定系统规则、当前任务及硬约束、结构化状态、近期完整轮次、相关历史证据。预留模型输出和一次工具往返空间。先删重复日志、过大工具输出和过期检索片段，再压缩早期完整轮次；禁止只留下 tool_call 或 tool_result 的一半。

摘要格式固定为：用户事实及来源、任务目标、已确认决策、未完成事项、关键实体/ID、可回查的原文位置。外部文档中的指令不能升级成系统规则；冲突事实保留时间与来源，不能由摘要模型自行裁决。每次摘要生成新版本，原文和上一版本保留；校验失败就不切换。

流畅不等于摘要读起来漂亮。用“压缩前后能否回答相同追问”验收：名字/偏好、指代、被修订的决定、待办状态、关键否定约束。硬约束与待办由结构化存储校验；有歧义就回查原文或问用户。衡量事实保留率、错误继承率、任务成功率、输入tokens和P95延迟，避免只优化摘要压缩比。

本项目选择真实LLM摘要、保留近期完整轮次和独立todo状态；没有自动原文检索，因此明确承认闲聊细节可能丢失。压缩、结构化笔记、按需加载也是 [Anthropic context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents) 讨论的方法；这里的预算及失败策略是本设计的取舍。

## 五 架构题二 历史问题召回

选题：模块二，第1题：半个月前问过的问题如何召回？

重复问题有不同意图：“按之前说的做”需要延续，“现在还一样吗”需要更新事实，“再讲一遍”需要重新解释。先识别问题实体、时间和是否依赖历史；不能仅凭向量相似度直接复读旧答案。

每条memory包含 owner、scope、类型（偏好/事实/任务/历史回答）、内容、来源消息、有效时间、更新时间、置信度和撤销状态。以当前用户权限和会话/项目边界作强过滤，再用关键词＋向量召回候选，按相关性、时间有效性、明确性、任务关联度重排。稳定偏好可以长存；天气、价格等旧回答只作背景，必须重新取数。

把少量相关片段以“历史资料”放在系统规则之后、近期对话之前，并带日期和来源。发现“以前说广州、后来改厦门”，按明确更新与时间处理；仍冲突就澄清。涉及金额、承诺、否定约束时，读取原文证据；不确定就说没有足够记录，不能把相似用户的内容补进来。

写入也要克制：会话结束或任务状态变化时提取候选，去重、合并、处理更正，用户临时引用的别人的偏好不能变成自己的偏好。支持删除并让索引、摘要同步失效。用跨用户泄漏、事实更新、无关召回、删除后召回和需/不需召回的成对测试评估。memory的价值是减少重复沟通，同时不让过时记录绑架当前请求。

本项目只实现会话内摘要和todo的按需读取，没有实现半个月跨度的向量检索。这样的边界可以用测试证明，优于把“历史全部塞给模型”称为长期memory。

## 六 架构题三 长程任务目标保持

选题：模块三，第1题：长程任务如何防止忘记目标？

我会把目标、验收条件、任务图和进度做成runtime维护的持久化对象，而不是只在首轮提示里写一句“请记住目标”。每个子任务有输入、依赖、状态、产物位置、验证证据与阻塞原因。

每次恢复、上下文压缩后、里程碑结束或检测到多次无进展时，重新注入“目标＋当前子任务＋验收条件＋最近证据”。按证据更新进度：命令退出码和测试报告可以支持完成声明，模型自己说“做好了”不够。用户中途改目标时增加目标版本，旧目标下的在途工具结果需重新判定是否仍有用。

| 方法 | 收益 | 代价/风险 |
|---|---|---|
| 每步重放全量目标/计划 | 简单，易审计 | token成本高，旧计划可能误导 |
| todo/检查点/产物清单 | 可跨进程恢复，可验收 | 状态若由模型随意改，会出现虚假完成 |
| 分阶段执行与独立验证 | 错误局部化，减少长链传播 | 增加延迟，验证器也可能犯错 |
| 独立子上下文处理子任务 | 隔离噪声，便于并行 | 交接损失、依赖与汇总复杂 |

实践上先用持久任务状态＋小步执行＋检查点，不默认增加多个agent。设预算、期限和连续无进展阈值；超限后保留证据并请求必要信息，避免无限自我反思。重启恢复和验收测试比“自我检查”提示更可靠。[Anthropic的长程harness实践](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents) 使用进度文件和增量交付等机制支持跨上下文工作；本设计进一步明确状态版本和证据门槛。

本项目todo属于可验证的持久业务状态，并有最大循环次数，但尚未实现通用任务图或自动长任务调度。

## 七 架构题四 忙碌会话与异步事件

选题：模块四，第2题：busy时新消息和异步完成事件如何处理？

生产设计采用每session一个有序mailbox、一个状态写入者。API先持久化事件并分配序号，返回已接收；不要让HTTP连接占着等长任务。异步worker只发布带 event_id、task_id、run_id、goal_version 的完成事件，不直接修改聊天历史。数据库唯一键去重，状态提交使用CAS版本号；若使用租约，必须附带单调递增的fencing token，存储拒绝旧租约持有者的写入，防止进程停顿恢复后出现双写。

busy表示正在推进某个run，不代表拒收事件。用户消息默认排队；明确“取消”“改成…”的控制事件在安全点优先处理，使当前run失效或更新目标版本。无法撤销的外部调用不能假装取消成功：记录取消请求，结果到达后对账，必要时执行补偿。

异步完成事件先保存结果，再与用户消息按session的接收序列处理。两个同刻到达的事件由持久化序号确定顺序，控制事件的优先策略需显式记录。如果任务版本仍有效，把结果作为关联的工具结果交回runtime；已取消或旧版本的结果留档，不抢占新目标。工具结果要与其原始调用配对，不能拼接到任意一条assistant消息后面。

协议上还要分清两种路径：若原工具调用仍pending，必须先补齐其tool result才能继续该条模型历史；若已用“已受理＋task_id”结束原调用，后续完成事件应作为新的runtime通知输入，或由status工具读取，不能再次给同一call_id补第二条结果。

长任务启动即返回task_id和状态；完成后的主动通知与状态更新使用outbox共同提交，消费者用通知ID去重。进程崩溃后重投递是at-least-once，外部副作用还需业务幂等键。测试同时发送用户修改、旧任务完成、重复完成和worker重启，确认顺序可解释、通知不丢、结果不串。

这类设计可参考 [Temporal消息传递机制](https://docs.temporal.io/encyclopedia/workflow-message-passing) 的异步Signal与查询分工；不意味着需要把Temporal引入本题。本项目核心 Runtime 同步执行，Web 层通过进程内线程池后台运行并由前端轮询；同 session 文件锁＋busy 拒绝重入＋显式 resume 防止重入。它没有持久任务队列、跨进程调度或上述 mailbox；进程退出会中断在途任务，需显式恢复。

## 八 架构题五 工具调用协议适配

选题：模块五，第1题：Claude Code与OpenAI-compatible工具输出有什么差异？

先区分三层：Claude Code是应用runtime；Anthropic Messages是模型协议；终端展示或CLI的JSON事件是应用输出。不能把终端里的工具标签、日志，直接当成模型原始function calling协议，更不能归纳成“Claude只输出XML，国内模型只输出JSON”。不同版本和后端需要分别验证。

对比通常涉及的**原生模型协议**：

| 项目 | Anthropic Messages | OpenAI-compatible Chat Completions（GLM/豆包等） |
|---|---|---|
| 工具定义 | name/description/input_schema | tools[].function.name/description/parameters |
| 模型发起调用 | assistant.content中的tool_use块；input是对象 | assistant.tool_calls数组；function.arguments通常是JSON字符串 |
| 回填结果 | 后续user消息中的tool_result块，用tool_use_id关联 | role=tool消息，用tool_call_id关联；content通常是字符串 |
| 暂停标记 | stop_reason=tool_use | finish_reason通常为tool_calls |
| 内容组织 | 文本、工具调用等内容块可共存 | content与tool_calls为不同字段 |

GLM官方示例明确使用JSON字符串形式的arguments，以及role=tool、tool_call_id回填；火山方舟官方Function Calling示例同样展示Chat Completions的tool消息配对。这里比较的是各自这一接口形态，不把同厂商的Responses或实时语音接口混在一起。[智谱官方工具调用](https://docs.bigmodel.cn/cn/guide/capabilities/function-calling)、[火山方舟Function Calling](https://docs.volcengine.com/docs/ark/function-calling?lang=en)。

协议依据：[Anthropic工具调用生命周期](https://platform.claude.com/docs/en/agents-and-tools/tool-use/handle-tool-calls)、[DeepSeek Tool Calls](https://api-docs.deepseek.com/guides/tool_calls/)。GLM、豆包等产品的兼容程度必须查具体接口版本，不能因宣称compatible便认为流式事件、strict schema、reasoning字段和并行调用完全一致。

我的取舍判断：内容块模型适合统一表示混合文本/图片/工具结果，代价是适配器需处理多类块与配对约束；Chat Completions生态兼容方便，代价是arguments需要再次解析，错误参数仍须运行时校验。两者都能多工具调用，实际是否并行执行由runtime决定；都不自动解决权限、幂等、超时和会话并发。

如果支持流式调用，按调用index/ID缓存参数增量，收齐并校验JSON后才执行；一段增量能单独parse成功也不能代表最终参数。错误表达也需适配：Anthropic tool_result可带is_error，兼容Chat Completions通常把业务错误写入tool content，不能混同HTTP错误。

因此内部统一成 `Decision(final_content, calls[])`、`ToolCall(id,name,args)`、`ToolResult(id,status,data)`；供应商适配层保留必要的原始块和协议元数据。本项目只实现Chat Completions适配，未假装支持Anthropic；若扩展推理模式，须按供应商要求保存/回传必要的opaque reasoning元数据，不能套用本版非思考模式的过滤策略。

## 九 运行复现与本次验证

### 本地运行

环境：Python 3.11+，macOS / Linux；本次验证使用 macOS Python 3.12。进入仓库根目录执行：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[web,test-web]'
cp .env.example .env
python scripts/run_web.py
```

在本地 .env 填入 LLM_API_KEY、LLM_MODEL、LLM_BASE_URL；搜索另填 TAVILY_API_KEY。打开 http://localhost:8000。启动器生成本地 Cookie 签名密钥和 data/web.db。CLI 入口：python -m agent_harness --user A --session window-1。

### 本次实测结果

执行 python -m unittest discover -s tests -v：53 项通过，无跳过。覆盖协议解析、工具执行、半批恢复、事务、超预算压缩、跨会话与跨进程隔离、联网适配的离线模拟、Web 权限、CSRF、限额及标题竞态。测试存在 Starlette 测试客户端弃用提示，不影响本次通过结果。

在临时数据库和占位模型配置下实际启动 Uvicorn，本地 /healthz 和首页 GET 均成功；这只证明 HTTP 启动，不代表真实模型调用。本次线上 GET 首页返回演示台，/healthz 返回 {"ok":true}；没有重测真实对话，也未证明部署源码与本次 Git 提交完全一致。

### 尚未验证

未重跑 scripts/live_check.py，因为本次未配置真实密钥。没有 Docker 构建、Windows / WSL2、200 轮真实语义保真或生产负载测试。原包中 7 / 9 个真实用例和线上交互记录属于历史材料，本次不能独立复证。

### 建议评审演示顺序

普通聊天记录名字 → 自然语言要求计算并添加待办 → 追问完成待办 → 新建会话查询确认隔离 → 返回原会话检查状态和工具 trace。需要验证压缩时，以较小预算在测试中触发并检查完整轮次与摘要；不将普通短对话冒充压缩成功。

## 十 密钥管理与交付维护

### 公开仓库的内容边界

仓库仅包含源码、测试、必要静态依赖、配置样例和提交材料；.env.example 的 API 密钥为空。供应商密钥仅由服务端环境或本地 .env 读取，不放入浏览器代码。公开的接口 URL 与模型名称属于配置，不是认证密钥。

.gitignore 排除 .env、运行数据目录、SQLite、私钥文件、虚拟环境和构建产物；上传前对实际暂存文件检查。另扫描供应商令牌、GitHub token、私钥头和疑似硬编码长密钥；未发现命中。模式扫描不能证明不存在任意未知格式的秘密。

在线工具使用固定 HTTPS 地址并拒绝重定向；错误只返回分类信息，不回显带认证信息的上游响应。模型传输读取服务端 LLM_API_KEY。数据库、运行 trace 和聊天可能包含用户信息，因此均不作为公开交付附件。

### 部署与维护

GitHub 保存源码；演示域名对应独立后端，GitHub Pages 不能运行 Python 服务。生产部署应使用全新 WEB_SECRET、HTTPS、COOKIE_SECURE=1、允许 Host 名单和受限数据目录。默认部署单 worker；增加 worker 前应迁移内存限流和 busy 状态。

仓库发布不会自动更新线上服务；后续修改需独立部署。域名、服务器和外部 API 额度仍需维护。第三方 Marked 与 DOMPurify 许可证随源码保留。

### 本次修改范围

沿用提供的业务代码；修正架构稿中的后台执行旧描述，补充完整提交文档和本次验证记录，更新提交链接，补充构建产物忽略规则，并移除配置样例中未经本次核实的供应商免费额度表述。未更改核心算法或宣称重写了项目。

## 十一 AI 协作与面试阅读索引

### 协作记录与证据

原包 docs/ai-collaboration.md 记录了 AI 辅助开发中的工具恢复、协议配对、摘要预算、模型配置和网页迭代。本次是在用户提供的成品包上复核、运行测试、核对当前源码、修正文档并发布，不把历史开发过程重新描述为本次亲历。历史文件保留阶段提示。

运行时原始 prompt 位于 runtime.py 的 SYSTEM 与 SUMMARY，工具描述和参数 Schema 位于 tools.py。公开协作记录提供任务输入及工程处理的归纳，不编造逐字对话，不记录模型私有思维链。源码审计依据是调用、参数、结果、状态和测试断言。

### 建议按这个顺序逐行阅读

### model.py 的 parse 与 ChatModel.complete

解释为何 arguments 是 JSON 字符串、为何需要检查 finish_reason 和唯一 call_id，以及重试哪些 HTTP 错误。

### tools.py 的 validate calculator todo Registry

解释 bool 与数字类型校验、AST 白名单、分页上限、工具输出大小以及新增工具如何注册。

### store.py 的 connect lock save

解释连接关闭、事务原子性、锁粒度、联合主键和同一状态与 trace 一起提交的意义。

### runtime.py 的 chat loop pending execute_pending

画出持久化顺序，说明每个崩溃窗口如何恢复、哪些外部副作用可能重复。

### runtime.py 的 prompt compact summarize

解释为何按轮次切割、如何保留调用配对、摘要失败为何不替换原文，以及字节预算局限。

### web.py 的 user_id csrf owned submit generate_title

解释匿名身份和授权的差别、单 worker 限制、任务额度与模型费用的差别、标题条件更新。

### 候选人应能独立回答的问题

如果工具成功后进程崩溃怎么办？为什么不能承诺外部 exactly-once？两个窗口如何隔离？摘要会丢什么？为何 8 步不等于 8 次请求？增加 Uvicorn worker 会破坏哪些假设？给工具增加副作用时需要补哪些保证？这些答案都应能落到源码与测试，而不是只复述文档。
