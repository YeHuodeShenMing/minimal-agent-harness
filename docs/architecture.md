# 架构设计题（每模块选一题）

这是与实现对应的设计稿。下文明确区分已实现的最小 runtime 与生产扩展；提交者应结合自身经历复核，尤其是故障策略和性能目标。

## 模块一，第2题：200轮会话如何压缩并保持流畅？

我会先把“事件记录”和“当前模型输入”分开。200轮原文仍可检索，工作上下文只保留完成下一步必要的信息；按token预算触发，而不是机械地每20轮总结一次。

输入按预算分配：固定系统规则、当前任务及硬约束、结构化状态、近期完整轮次、相关历史证据。预留模型输出和一次工具往返空间。先删重复日志、过大工具输出和过期检索片段，再压缩早期完整轮次；禁止只留下 tool_call 或 tool_result 的一半。

摘要格式固定为：用户事实及来源、任务目标、已确认决策、未完成事项、关键实体/ID、可回查的原文位置。外部文档中的指令不能升级成系统规则；冲突事实保留时间与来源，不能由摘要模型自行裁决。每次摘要生成新版本，原文和上一版本保留；校验失败就不切换。

流畅不等于摘要读起来漂亮。用“压缩前后能否回答相同追问”验收：名字/偏好、指代、被修订的决定、待办状态、关键否定约束。硬约束与待办由结构化存储校验；有歧义就回查原文或问用户。衡量事实保留率、错误继承率、任务成功率、输入tokens和P95延迟，避免只优化摘要压缩比。

本项目选择真实LLM摘要、保留近期完整轮次和独立todo状态；没有自动原文检索，因此明确承认闲聊细节可能丢失。压缩、结构化笔记、按需加载也是 [Anthropic context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents) 讨论的方法；这里的预算及失败策略是本设计的取舍。

## 模块二，第1题：半个月前问过的问题如何召回？

重复问题有不同意图：“按之前说的做”需要延续，“现在还一样吗”需要更新事实，“再讲一遍”需要重新解释。先识别问题实体、时间和是否依赖历史；不能仅凭向量相似度直接复读旧答案。

每条memory包含 owner、scope、类型（偏好/事实/任务/历史回答）、内容、来源消息、有效时间、更新时间、置信度和撤销状态。以当前用户权限和会话/项目边界作强过滤，再用关键词＋向量召回候选，按相关性、时间有效性、明确性、任务关联度重排。稳定偏好可以长存；天气、价格等旧回答只作背景，必须重新取数。

把少量相关片段以“历史资料”放在系统规则之后、近期对话之前，并带日期和来源。发现“以前说广州、后来改厦门”，按明确更新与时间处理；仍冲突就澄清。涉及金额、承诺、否定约束时，读取原文证据；不确定就说没有足够记录，不能把相似用户的内容补进来。

写入也要克制：会话结束或任务状态变化时提取候选，去重、合并、处理更正，用户临时引用的别人的偏好不能变成自己的偏好。支持删除并让索引、摘要同步失效。用跨用户泄漏、事实更新、无关召回、删除后召回和需/不需召回的成对测试评估。memory的价值是减少重复沟通，同时不让过时记录绑架当前请求。

本项目只实现会话内摘要和todo的按需读取，没有实现半个月跨度的向量检索。这样的边界可以用测试证明，优于把“历史全部塞给模型”称为长期memory。

## 模块三，第1题：长程任务如何防止忘记目标？

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

## 模块四，第2题：busy时新消息和异步完成事件如何处理？

生产设计采用每session一个有序mailbox、一个状态写入者。API先持久化事件并分配序号，返回已接收；不要让HTTP连接占着等长任务。异步worker只发布带 event_id、task_id、run_id、goal_version 的完成事件，不直接修改聊天历史。数据库唯一键去重，状态提交使用CAS版本号；若使用租约，必须附带单调递增的fencing token，存储拒绝旧租约持有者的写入，防止进程停顿恢复后出现双写。

busy表示正在推进某个run，不代表拒收事件。用户消息默认排队；明确“取消”“改成…”的控制事件在安全点优先处理，使当前run失效或更新目标版本。无法撤销的外部调用不能假装取消成功：记录取消请求，结果到达后对账，必要时执行补偿。

异步完成事件先保存结果，再与用户消息按session的接收序列处理。两个同刻到达的事件由持久化序号确定顺序，控制事件的优先策略需显式记录。如果任务版本仍有效，把结果作为关联的工具结果交回runtime；已取消或旧版本的结果留档，不抢占新目标。工具结果要与其原始调用配对，不能拼接到任意一条assistant消息后面。

协议上还要分清两种路径：若原工具调用仍pending，必须先补齐其tool result才能继续该条模型历史；若已用“已受理＋task_id”结束原调用，后续完成事件应作为新的runtime通知输入，或由status工具读取，不能再次给同一call_id补第二条结果。

长任务启动即返回task_id和状态；完成后的主动通知与状态更新使用outbox共同提交，消费者用通知ID去重。进程崩溃后重投递是at-least-once，外部副作用还需业务幂等键。测试同时发送用户修改、旧任务完成、重复完成和worker重启，确认顺序可解释、通知不丢、结果不串。

这类设计可参考 [Temporal消息传递机制](https://docs.temporal.io/encyclopedia/workflow-message-passing) 的异步Signal与查询分工；不意味着需要把Temporal引入本题。本项目核心 Runtime 同步执行，Web 层通过进程内线程池后台运行并由前端轮询；同 session 文件锁＋busy 拒绝重入＋显式 resume 防止重入。它没有持久任务队列、跨进程调度或上述 mailbox；进程退出会中断在途任务，需显式恢复。

## 模块五，第1题：Claude Code与OpenAI-compatible工具输出有什么差异？

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
