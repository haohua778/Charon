# 句子级 Claim 方案交付记录

实现依据：桌面 `design-claim-granularity.md`，2026-09-27。rubric 内容采用附录 A 草案，全部标为 `0.1-draft`。机器只提出审阅 flags，人工决定保持 pending。

## 已实现范围

| 文件 | 改动 |
|---|---|
| `app/schemas.py` | Unit、句子范围 Claim、嵌套 rubric、问题答案、复核契约；删除旧摘要身份字段、响应复制模型和请求 thread_id |
| `app/segment.py`（新增） | 英文 Markdown 确定性切分、真实章节、ID 解析、原文范围验证、行内标注 |
| `app/core/rubrics.py`、`app/core/config.py`、`.env.example` | 新 rubric 校验、变体结构校验、默认调用上限 80 |
| `app/llm/client.py`、`app/llm/fake.py` | temperature=0、一次校验修复、max_calls 整数预算、共享离线输出协议 |
| `app/agent/graph.py`、`state.py`、`nodes.py`、`rules.py`、`__init__.py` | Runtime context、Send 并行、覆盖和原文验证、聚合、单轮复核子图、简单记录写入、移除重新导出 |
| `app/tools/cues.py`、`detectors.py`、`__init__.py` | 始终开启的线索、候选证据、三种代码检查、移除重新导出 |
| `app/matching.py`（新增） | 聚合、召回、一致性共用的确定性一对一重叠匹配 |
| `app/api/review.py`、`errors.py` | 成功直接返回 ReviewRecord、公开新安全错误码、复核失败仍返回完整审阅 |
| `rubrics/` | 两种分析类型 × 两种变体，共四份草案；移除旧 synthetic 两份 |
| `eval/run.py`、`eval/metrics.py` | end_to_end 命名、固定输入自校验、excerpt、no-verify、总预算、复核分布、直接使用实际运行结果 |
| `tests/` | 分句、契约、规则、工具、DAG、复核、预算、API、评测离线验收 |
| `README.md`、`AGENTS.md` | 新契约、流程、成本和指标口径、迁移方法与协作约束 |

数据文件、锁定依赖、计算器 AST 沙箱及历史 `docs/review-decisions.md` 保留。API 与评测均调用同一个 `run_review`。

## 取舍与澄清

1. 无模型检查时，条件路由显式返回 `aggregate`，对应设计说明的空分支。LangGraph 返回空 Send 列表本身不会继续聚合。主图测试包含这条边。
2. `verify_flags` 对外保持单节点；内部使用 `verify_dispatch → verify_dimension → verify_merge` 子图与 reducer，符合方案允许的拆分方式。
3. 标题 Unit 用 `pN` 保存身份，模型正文显示 `[secK]`；无标题和前言增加 `[sec0]`。列表标记保留在原文 span 内，句间与段内空白不规范化；未加句末标点的残余文本也作为一个句子。
4. 对“未说明业务问题”等缺失型 section/document flag，没有可引用原文时 `quote=""`，不编造引文。字段仍遵守方案的 `str` 类型；claim flag 必须有 anchor 引文。此约定补足设计中缺失判断与必填 quote 的冲突。
5. `Flag` 额外保存 `quotes` 字典，供复核看到各问题的引文；`ReviewRecord` 额外保存 `verification_error_code`，避免混用审阅失败与复核失败。其余诊断不靠自然语言常量重复堆入记录。
6. 保留请求字段 `rubric_id`，默认改为 `program_evaluation`；记录的 `analysis_type` 来自 rubric，加载时验证两者一致。CLI 使用 `--analysis-type`。不增加环境变量或改变既有变量名。
7. 冻结全部 `data/synthetic/`。旧固定输入、旧答案不兼容新契约；CLI 默认改为 end_to_end，不自动加载或转换它们，显式加载会说明迁移方式。新合成内容和答案仍需用户决定。
8. specific/vague 比较只忽略 description、ask、examples 中的文字；例子数量和 flag 值仍须一致。附录没有指定工具许可，草案为 `overclaim_small_diff`（两型）和 `comparison_fair`（KPI）保留 calculator 许可，工具仍受请求开关和两阶段限制。
9. 贪心匹配先按段落、首句、check_id、target_id 排序，从左侧逐条选第一个未使用的右侧匹配；这是方案要求的确定性贪心，不是最大二分匹配，复杂重叠时可能低估匹配数。聚合代表值来自 run_index 最小的完整运行；F 编号按 start、check_id、target_id 排序。
10. 复核标签分布按独立记录内聚合 flag 的观察逐条统计，使用 `review_flag_observations` 标识；跨端到端记录同一目标的不同标签都保留，不由最后一条覆盖。
11. 保留最多 80 个抽取 claims 的既有上限；撤销旧段落数上限，原文仍限制 30,000 字符。未引入按 claim 拆分维度调用，以保持本轮规定的调用结构。
12. `run_review` 拒绝复用已发生调用的 LLM 实例（`llm_already_used`），落实删除用量差值计算所依赖的“每次新实例”前提。
13. 实施采用单一共享契约切换后并行完成各责任范围，先保留旧版离线基线，再跑各模块检查及完整集成验收；没有为了让每个中间阶段同时兼容旧契约而引入临时适配层。这与 §13 逐阶段全套测试的执行顺序不同，交付按最终统一契约验收。

## 设计 §2 的非目标

| 非目标 | 本轮遵守情况 |
|---|---|
| 不做中文分句 | 只实现指定英文规则；不声称支持中文语义切分 |
| 不引入 embedding、向量库、外部检索、新依赖、YAML | 全部没有引入，候选只在当前报告内产生；依赖版本保持 |
| 不做多轮反思/自修订，复核不修改或删除 flag | 单轮子图，无回边；验收逐字段对比复核前后 flags |
| 不改 `.env` / 变量名，不做真实模型调用 | `.env` 未读取或修改；只按明确要求改 `.env.example` 的默认预算值；测试为 fake 或注入 SDK |
| 不改历史 review-decisions | 原样保留历史记录 |
| 暂不做数值口径冲突 | 仅算式、单句百分比变化、引用存在性三类 detector |

## 已知限制

- rubric、严重度依据和新合成答案都未经过用户内容审定，不能当作正式业务规则。尚无新数据集上的正式召回结论。
- 算式严格遵守方案的容差“或”条件。例如 `12.4 + 13.4 = 26` 会触发相对误差门槛，尽管可解释为整数舍入；没有擅自把“或”改成“且”。
- 单句 `from 2024 to 2025` 这样的年份区间仍可能被百分比 detector 误当数值端点。这是当前规则的内容歧义，尚未加入未经设计确认的年份排除规则。模糊多百分比、多区间和双方向句子会跳过。同句中的唯一百分比即使描述的是其他量（例如 margin）也可能被当作声称的变化率，当前规则不做语义关系识别。
- 题注仅做确定性语法识别，不能理解任意 Markdown 风格。英文缩写、列表及表格只覆盖本轮约定，未实现完整 Markdown 解析器；代码块和分隔线仍会作为普通文本参与切分，弯引号不在本轮 ASCII 句末规则内。标题按方案不能作为证据/引文，因此仅在标题里表达业务问题可能导致该项引用失败；允许引用标题属于后续设计变更。
- 输出 token 上限仍是既有配置，长报告的全覆盖答案可能被真实模型截断；这会显式修复一次后失败，不当作无 flag 成功。未做真实服务兼容性、质量或输出容量验证。
- 原生 Send 可同时发出多个请求，没有新增并发限速配置。预算分配和上游限速可能影响失败维度；测试不依赖真实网络完成顺序。并行任务在 await 前预留物理调用额度，总预算不突破。
- 附录现有 code/model check_id 不重叠，`also_reported_by_model` 通常为 false；通用去重机制保留，未新增非规范的语义 check 映射。
- 端到端唯一 flag 数与人工裁定按精确 (check_id,target_id) 去重；不同范围的 flags 可在重叠 Jaccard 中匹配，但仍是不同裁定对象。复核分布 unverified 包括代码 flag、关闭复核和复核失败，应结合 origin 与记录 verification_status 区分。
- 五次运行、每维度都走工具和一次格式修复的最坏情形约需 92 次调用，超过默认 80 会明确失败；并不承诺所有合法请求都可在默认预算内完成。
- 检测器仍不理解全部上下文：数值括号前缀 `(2025) 60 + 50 = 100` 和链式等式可能保守跳过；`appendix I prepared` 中的代词 I 可能误认作罗马数字标签。这些歧义边界见 Claude 报告 R3–R5。百分比引文可能截到百分号而留下未闭合括号（R7），仍是精确原文子串，不影响判定。
- 按方案简化为普通 write_text 后，不提供原子落盘；没有生产认证、数据治理或恢复保证。

## 独立复审后的修正

- 拒绝将乘除/减法、单位后缀或其他较大表达式的尾部截取成纯加法；不增加新的算式或单位换算支持。针对边界收紧引入的 Markdown 强调和括号说明漏报，另补回归并修正。
- 混合明确币种或百分比/普通数字标记的算式跳过，不做单位换算；复合引用标签不截断成简单编号；普通 appendix below 不当作附录标签。
- 百分比 reason 使用可读定点数，判定仍使用完整 Decimal 精度。
- assess payload 使用显式字段白名单，触发规则、reason 模板和严重度留在代码中；FakeLLM 使用固定协议夹具答案，不读取触发规则。
- 删除已没有调用方的旧 flag 错误码；新增定向回归，并在稳定后重新执行完整离线验收。

## 验证与独立审核

本轮只执行离线验证，所有模型行为来自同一个 FakeLLM 或注入的内存 SDK stub：

- 修改前基线：34 passed；首次集成完成为 190 passed，独立复审后补充反例并修复后为下列最终结果。
- 最终默认环境：`UV_CACHE_DIR=/private/tmp/charon-uv-cache uv run --locked --offline pytest -q`，**239 passed in 0.92s**。
- 最终显式离线环境：`CHARON_OFFLINE=true UV_CACHE_DIR=/private/tmp/charon-uv-cache uv run --locked --offline pytest -q`，**239 passed in 0.95s**。
- 默认三轮四格 CLI 比较全部完成，共 72 次 fake 调用；固定输入、端到端、关闭复核及旧快照拒绝路径均已验证。
- 范围重叠示例 Jaccard=1/3；纯内存模拟审批测试验证代码 flag 参与召回、一对一匹配、partial 抑制和裁定冲突。测试模拟不构成项目答案批准。
- 原生图结构、Send 空分支、运行失败、复核失败、工具阶段、预算和安全响应均有离线测试。
- 原文 ID、规则及评测源码的旧摘要命名检索为零匹配；清理本轮测试遗留的旧 Python 字节码缓存后执行全树检索。

Claude 的独立报告位于 `docs/claude-design-audit.md`。**最终代码复审及修复复核已完成，结论为“可以交付，没有阻断问题”。** Claude 逐项审查方案与当前实现，并用原反例确认 D1、D2、K1–K3、G1、S6、R1、R2、R6 均已修复且未退化；又独立运行默认与显式离线环境，各 239 passed。复核确认 data/synthetic、锁定依赖和历史记录保持不变。

本轮已完成代码实施、离线验收和独立复审。按方案保留的限制、低优先级歧义及新设计建议均列在上文与审核报告中；rubric/新合成内容仍待用户审定，真实模型兼容性与质量未验证。
