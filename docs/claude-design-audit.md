# 设计审查与验收清单：句子级 Claim 方案（Claude 独立只读审查）

> 审查对象：`design-claim-granularity.md`（已批准，2026-09-27）及实施前的工作区代码。
> 审查者：Claude（只读；本文件是唯一写入的文件）。日期：2026-09-27。
> 本文只做设计审查。实现审核在 Codex 通知完成后另行追加（见 §4）。

## 0. 范围、方法与证据等级

- **已读取**：设计文档；`README.md`、`AGENTS.md`、`docs/review-decisions.md`、`pyproject.toml`、`.env.example`、`.gitignore`；`app/`、`eval/`、`tests/` 的全部源码；`rubrics/synthetic.*.json`；`data/synthetic/*`（合成数据，只读）。读取时间约 00:40。
- **未读取、未执行**：`.env`、密钥、真实客户材料、项目简报；未调用任何模型；未运行项目测试；未修改任何项目文件。
- **证据等级**：
  - 【已验证】：在锁定依赖（langgraph 1.2.11、pydantic 2.13.5）下，用 scratchpad 中的探针脚本实际运行得到。
  - 【推断】：基于读代码或设计推理，未运行。
- **注意**：审查过程中实施已经开始。00:43 起陆续出现 `app/segment.py`、`app/matching.py`、`app/agent/rules.py`、`app/tools/cues.py`、`app/tools/detectors.py`、四个新 rubric、若干新测试，`schemas.py`、`config.py` 等也被修改。本文**没有**审查这些进行中的文件，只针对设计和实施前的代码。
- Codex 已确认处理的三点不再展开，只保留对应的验收项：空 `Send` 列表需显式路由到 `aggregate`；verify 可用内部子图 `Send`；标题 Unit 用 `pN`，渲染为 `[secK]`。

## 1. 已验证的 LangGraph 与 Pydantic 行为（直接约束实现）

| # | 行为【已验证】 | 对实现的要求 |
|---|---|---|
| V1 | `StateGraph(S, context_schema=Ctx)`、节点参数 `runtime: Runtime[Ctx]`、`ainvoke(..., context=Ctx(...))` 在 1.2.11 下可用，`Send` 派发的节点同样能读到 `runtime.context` | 无需升级依赖 |
| V2 | 条件边返回 `[]` 时，图在该节点之后直接结束，`aggregate` 不会运行 | Codex 已处理：显式返回 `"aggregate"` |
| V3 | 并行 `Send` 任务中只要一个抛出异常，整个 `ainvoke` 抛出，其他任务的结果丢失，`write_record` 不执行 | `assess_dimension` 和 verify 的并行节点**必须在节点内部捕获全部异常**（包括 `call_budget_exceeded`），写入“失败”结果条目。否则会没有审阅记录，API 直接返回 500 |
| V4 | 并行任务写入没有 reducer 的 state 键时，抛出 `InvalidUpdateError` | 由 `Send` 派发的节点只能写带 reducer 的字段 |
| V5 | `add_conditional_edges` 未提供 `path_map` 时，`get_graph()` 对这条条件边只画出 `→ __end__`，不画真实目标 | 所有条件边都要提供 `path_map`（或 `Literal` 返回注解），否则 §10 的图结构测试无法按 §11.9 断言 |
| V6 | 默认不限制并发：8 个 `Send` 同时运行。`config={'max_concurrency': 2}` 时峰值为 2 | 见 P1-2 |
| V7 | 探针里各任务按相反顺序完成，reducer 仍按 `Send` 的派发顺序合并 | 能观察到，但我没有在文档中确认这是承诺的契约。`aggregate` 应显式按 `(run_index, 维度顺序)` 排序，不依赖它 |
| V8 | Pydantic 默认宽松模式下，`dict[str, bool \| str]` 会把 JSON 的 `1` 转成 `True`、`0.0` 转成 `False`，而字符串 `"true"` 保持为字符串。另外 Python 中 `True in [1]` 为真 | 规则引擎要按 `question.type` 做严格类型检查：`bool` 字段要求 `type(v) is bool`，`enum` 字段要求是 `str` 且在 `values` 内。或者对答案模型用 `strict=True` |

## 2. 设计歧义与实现风险

优先级定义：
- **P0**：实现前需要决策或必须遵守，否则实现与设计冲突、测试无法按设计写，或运行必然出错。
- **P1**：会产生错误结果或不可复现的指标。
- **P2**：边界情况与文档。

### P0

**P0-1 冻结的合成数据与去 hash 冲突。**
- `FixedInput` 和 `AnswerKey` 删除 `report_hash` 后，由于 `Contract` 设置了 `extra='forbid'`，现有的 `data/synthetic/fixed_input.json`、`answers.draft.json`、`adjudications.pending.json` 都会加载失败：它们含 `report_hash`，也含旧的 hash 目标 ID（如 `c3c7170cb1eb3a83:claim:34:100`）。
- `report.txt` 不是带标题的 markdown，新 rubric 的 `analysis_type` 中也没有 `synthetic`。
- 第 1 步完成后，`tests/test_evaluation.py` 和 eval CLI 中读取随附数据的部分会立即失败。
- 需要明确过渡方案：
  - (a) 测试改用测试内构造的合成夹具，不再读 `data/synthetic`；
  - (b) 随附数据更新前，eval CLI 以明确的错误码失败，**不得**自动转换或改写答案；
  - (c) `fixed_input.json` 和裁定模板是否属于冻结范围，需要用户或 Codex 明确。
- 验收时核对 `report.txt` 和 `answers.draft.json` 与基线逐字节一致。

**P0-2 document 级“缺失类”检查与 `Flag.quote: str` 冲突。**
- `question_stated`、`data_disclosed`、`assumptions_disclosed` 的典型触发条件是“找不到”，没有原文可以引用。
- 但 §11.8 的 `Flag.quote` 是必填 `str`，§11.2 又规定 anchor 引文就是 flag 的 quote。
- 需要二选一：
  - `quote` 改为 `str | None`，只在 section/document scope 且 anchor 答案表示“缺失”时允许 null（延续旧 README 的规则）；
  - 或者这些检查不设 `anchor_field`，并给出对应的 quote 规则。
- 同时，`reason_template` 不能引用可能为空的 `{x.quote}`。

**P0-3 rubric 加载时需要校验“flag 必有引文”的不变量（设计未写）。**
- 以下任一条件不满足，运行时就会出现“flag 已触发但没有引文”或“模板填充失败”。加载时应全部拒绝：
  - `anchor_field ∈ flag_when`；
  - `flag_when[anchor] ⊆ quote_required_when[anchor]`；
  - `reason_template` 中每个 `{f.quote}` 的字段 f 都满足 `flag_when[f] ⊆ quote_required_when[f]`；
  - `flag_when` 和 `quote_required_when` 的取值都在该字段的取值域内（`bool` 字段为 `{true, false}`）；
  - 模板占位符只能是 `{field}` 或 `{field.quote}`。建议用正则替换实现，不用 `str.format`：后者对 dict 做属性访问会出错，也允许任意属性访问。
- §10 只列了前三种拒绝情形，建议补测试。与 P0-2 的例外规则需要一起定。

**P0-4 没有模型检查可跑时，要按请求补齐运行结果。**
- 以下情况 `Send` 列表为空：零 claims 且没有 document/section 级模型检查；或 rubric 中没有 `model_assessed`。此时 `aggregate` 收不到任何 assess 结果。
- `aggregate` 必须按 `request.runs` 构造 `run_index` 为 0 到 runs−1 的 `RunResult`。判断方式是比较“期望的（运行, 维度）集合”与实际结果，否则 `valid_runs=0`，状态会被判成 `failed`。
- 同理，某次运行的某个维度因为没有目标而被跳过时，不能当作“缺结果”判为失败。
- 验收测试：零 claims、只有 claim 级检查的维度 → `status=complete`，`unchecked_check_ids` 正确，`pending_standards` 单独列出。

**P0-5 LangGraph 实现的硬要求**（来自 V3、V4、V5）：
- 并行节点内部捕获全部异常；
- 并行节点只写 reducer 字段；
- 所有条件边提供 `path_map`，包括 `code_checks → aggregate`。

### P1

**P1-1 输出 token 上限与“全覆盖”冲突【推断，未实测】。**
- 覆盖检查要求每个（检查项, 目标）恰好一行。`interpretation` 维度在 K 和 P 下各有 3 个 claim 级 `model_assessed` 检查。
- 粗估每行（含 answers 和 quotes）80–150 tokens。claims 达到 15–20 个时，一次调用有 45–60 行，超过默认 `CHARON_MAX_OUTPUT_TOKENS=4096`。结果是 `finish_reason=length`，报 `invalid_model_output`；修复调用同样会被截断，该次运行失败。
- 离线 fake 暴露不出这个问题。
- 需要决定：按 claim 分批（会改变 §11.7 的调用估算）、提高上限（最大 8192），或者限制 claim 数量。
- 另外，原来“80 个段落”的上限已不适用，设计没有给出段落、单元、claim 数量的新上限。

**P1-2 并行执行加共享预算后，失败模式不可复现。**
- 现在是顺序执行（README 写明“各维度与重复运行顺序执行”）。改为 `Send` 后，最多有 runs×维度 = 5×4 = 20 个并发的真实请求（V6）。
- 后果一：429 的概率上升，而客户端只重试 1 次，间隔 0.1 秒。
- 后果二：预算耗尽时，哪个（运行, 维度）失败取决于完成顺序。结果更可能是“每次运行都有维度失败”，状态从以前的 `partial` 变成 `failed`，而且每次不同。
- 建议：`run_review` 传入 `config={'max_concurrency': N}`，N 可配置、默认取小值；README 同步修改执行方式的描述。
- 用 `FakeLLM(max_calls=k)` 断言状态的测试不能依赖并行完成顺序。

**P1-3 `also_reported_by_model` 在现有规则下永远为 false。**
- §7 的“同一处问题”要求 `check_id` 相同。但 A.2 中 `arithmetic_mismatch`、`percent_change_mismatch`、`missing_reference` 都是 code 检查，不会发给模型，模型不可能产出相同 `check_id` 的 flag。
- 要么删除这个字段和规则，要么定义 code 检查与 model 检查之间的 `check_id` 对应关系。A.2 中目前没有语义重叠的 model 检查。

**P1-4 `missing_reference` 会被引用句自己满足。**
- 规则是“全文没有以该标签开头的标题或题注行”。但引用句本身若以标签开头（如“Table 3 shows …”），就会被当作题注，这个检查永远不出 flag。
- 另外，纯字符串前缀匹配会让 “Table 10: …” 满足 “Table 1”。
- 需要定义题注行：标题行去掉 `#` 后的文本，或者独立成行、标签后紧跟 `:`、`.`、`—` 或行尾的行。标签后要求词边界，并排除引用句所在的单元。大小写规则也需要写明。

**P1-5 `arithmetic_equation` 的容差用“或”，会把合法的四舍五入判为错误。**
- 设计原文是“与 c 的差超过 c 的 0.5%，**或**超过 c 最后一位有效数字的半个单位，即出 flag”，等于取两个容差中较小的一个。
- 反例：`12.4 + 13.4 = 26`。差为 0.2，未超过半个单位 0.5；但 26 的 0.5% 是 0.13，被超过，于是出 flag，属于误报。
- 若本意是“两者都超过才报”（取较大容差），应改为“且”。
- 还需定义：
  - `100` 这类数的“最后一位有效数字”按最后一位书写的数字计；
  - 是否只支持加法；
  - 操作数的单位或后缀（`$`、`%`、M、K、bn）不一致时跳过。

**P1-6 `percent_change` 会把年份当成起止值，百分比计数也不明确。**
- 例：“Revenue grew 12% from 2024 to 2025”会以 X=2024、Y=2025 计算，变化率 0.05%，与声称的 12% 不符，出 flag，属于误报。建议排除 1900–2100 之间、无千分位也无货币或单位的整数。
- “恰好一个百分比”没有说明 X、Y 本身是百分数时怎么计数。例：“from 10% to 12%, a 20% increase”里有三个 `%`。设计也没有区分百分比与百分点。
- 建议：X 或 Y 是百分数时直接跳过，与 §11.4“不猜测”的原则一致。

**P1-7 渲染文本与引文校验必须逐字一致。**
- 模型只看到行内标注后的文本。如果渲染时规范化了段内换行、连续空白或表格行，模型复制出的 quote 就不再是原文单元的精确子串，会大量出现 invalid_quote 失败。
- 要求：渲染中每个单元的文本必须是 `report[start:end]` 原样，只在单元之间插入标记。
- 验收：对含软换行的段落，fake 从渲染文本复制的引文能通过校验。

**P1-8 修复重试（§11.7）放在哪里，以及 FakeLLM 是否走同一路径。**
- Pydantic 校验在 client 内部，而覆盖检查和引文校验在节点内部，需要报告和 units。§8 却把重试写在 `client.py`。
- 建议做成一个共享入口：`generate` 接受一个校验函数，返回错误列表。`JsonLLM` 和 `FakeLLM` 都走这条重试路径，否则离线测试覆盖不到真实路径。
- 还需要明确：
  - 错误列表不得包含 Pydantic 错误中的 `input` 字段，它会回显原文；
  - `finish_reason=length` 是否进入修复；
  - 工具轮中 `mixed_flags_and_tools` 和工具请求能否在修复里出现；
  - 修复调用的 stage 名称和计数方式。

**P1-9 聚合时代表值的选择。**
- 同一 `(check_id, target_id)` 在不同运行中的 `answers`、`reason`、`quote`、`note` 可能不同。
- 应规定取 `run_index` 最小的完整运行中的那一条作为代表，复核也基于它。不能依赖 reducer 的合并顺序（V7），否则 reason 和复核结果不可复现。

**P1-10 贪心匹配需要写精确，而且可能低估。**
- “按原文位置排序后贪心匹配”没有说明从哪一侧迭代，也没有说明并列候选如何选择。
- 贪心不是最大匹配。例：flags 为 {s1-s3, s1}，issues 为 {s1, s3}。若 s1-s3 先匹配到 s1，单句 s1 的 flag 就没有可匹配的 issue，m=1，而最大匹配是 2。
- 两种处理：
  - 写清贪心细节，并在 README 注明“可能低估”；
  - 或者改用最大二分匹配。数据规模小，实现简单，结果也确定。
- 两种做法都必须让召回和 Jaccard 共用同一个函数（§7 已有此要求）。

**P1-11 eval 指标的输入需要重新设计。**
- 代码 flag 不在 `RunResult.flags` 里（每次审阅只运行一次），但召回要计入代码 flag，所以 `compute_metrics` 需要额外接收记录级的 code flags。
- 端到端模式下每条记录的 claim 范围不同，Jaccard 必须走重叠匹配。
- “准备失败的记录计为一次失败运行，不另外伪造对象”：这要求 `compute_metrics` 接受失败运行的计数，而不是 `RunResult` 列表，否则仍然在造对象。
- 裁定在端到端模式下无法唯一对应：各记录的 `target_id` 不同，而 `F` 编号每条记录都从 F1 开始。需要决定裁定按 `(check_id, target_id)` 加重叠匹配，还是按 `(review_id, flag_id)`。§7 的“复核标签分布”依赖这个关联。

**P1-12 答案清单中不可检测的 issue。** `needs_standard` 检查不产生 flag，答案里若含这类 issue，召回永远小于 1。加载时应拒绝，或把它们单独列为“不可测”。

**P1-13 请求字段与 rubric 的命名。**
- 新 rubric 按 `analysis_type` 选文件，但 `ReviewRequest` 仍然是 `rubric_id`，默认值 `'synthetic'`。
- 需要确定：请求字段名和默认值（K 还是 P）；加载时校验 `rubric_id == analysis_type`；旧 `synthetic.*.json` 何时删除（§13 第 2 步要求先保留）。

**P1-14 specific/vague 结构一致性的校验位置，以及允许差异的字段。**
- §11.2 说在“加载比较矩阵时”校验，§10 却说结构不一致应“拒绝加载”。单变体加载（API 路径）是否也要读取另一个变体来比较，没有说明。
- 建议实现为“允许差异字段白名单”：`description`、`ask`、`examples` 的文字，是否包括 `dimension.name` 需要定。其余字段逐一比较，包括 `reason_template`、`pending_question`、`standard`、`tools`。
- 还需明确 `examples` 的数量、以及其中的 `flag` 布尔值能否不同。

**P1-15 `--compare` 的工具维度可能变成空实验。** 算式检查已改为 code 检查，A.2 中没有任何 `model_assessed` 检查授权 calculator。如果所有检查的 `tools` 都为空，工具开和关两格结果相同。需要用户或 Agent 2 决定哪个检查保留 calculator，或在 README 中写明。

**P1-16 `temperature=0` 与提供方的兼容性【未验证】。**
- 默认模型是 `kimi-k2.6`。部分提供方或推理模型对 temperature 有固定取值要求，传 0 可能返回 400。这只能在授权的真实调用中确认。
- 实现上确保 400 映射为 `upstream_error`，且不重试。
- 另外，temperature=0 会让重复运行结果高度相同，confidence 和 Jaccard 更接近 1。README 应说明这不代表判断可靠。

### P2

- **P2-1 分句细节未定义**：
  - 段末没有终止符的片段是否算一句；
  - 列表标记（`- `、`1. `）是否计入 span（建议不计入）；
  - 缩进或嵌套列表、`+ ` 和 `1) ` 形式、列表续行；
  - 段内“散文行 + 列表行”混排时，列表行应强制断句，且句号编号连续；
  - `> ` 引用、代码块、分隔线 `---`（会变成只有标点的“句子”）、setext 标题、没有前导 `|` 的表格；
  - 句末之后以 `$`、`*`、`[`、`` ` `` 开头的下一句不会被切开；
  - 缩写表的大小写（如 `E.g.`）。
- **P2-2** §4.3 只检查 claim 范围的首尾是 sentence。建议要求范围内**所有**单元都是 sentence，避免中间夹着 table_row。
- **P2-3** 单句 claim 的 ID 必须规范化为 `p3.s2`，不能是 `p3.s2-s2`。否则同一目标有两个 ID，去重和匹配都会失效。
- **P2-4** 证据能否指向 claim 自身范围内的单元？每个 claim 是否仍需至少一条链接（旧的 `incomplete_evidence` 规则）？
- **P2-5** 新增的错误码（覆盖、引文、复核、excerpt 等）必须登记到 `app/api/errors.py` 的 `ERROR_STATUS`，否则对外会显示为 `internal_error`。
- **P2-6** `record.error_code` 目前表示审阅失败。复核失败而 `status=complete` 时，需要单独的字段（如 `verification_error_code`）。部分维度复核成功、部分失败时，`verification_status` 取什么值也要明确。
- **P2-7** `thread_id = review_id` 要求在 `ainvoke` 之前生成 `review_id`。目前它在 `record_node` 里用 `uuid4()` 生成。
- **P2-8** 目前请求校验器会调用 `source_targets`，所以非法报告返回 422。新的切分如果只在图内执行，出错时会成为未捕获异常：返回 500，且没有记录。建议在请求校验阶段完成切分和上限检查。
- **P2-9** `ReviewRecord.thread_id` 是否删除？§12.1 只提到请求中的字段。README 示例和测试 `test_invalid_requests_never_echo_sensitive_input` 用到了 thread_id，都要同步修改。
- **P2-10** §11.9 说条件边“不依据模型输出”，但 `preparation_error` 和“存在模型 flag”都来自模型输出。建议改为：只依据结构性状态，不依据模型的判断内容选择路径，且没有回边。
- **P2-11** `grep` 验收的可移植性：本机的 grep 是 ugrep，默认跳过二进制文件；GNU grep 会对 `__pycache__/*.pyc` 报 “Binary file matches”。基线的 `app/__pycache__/schemas.cpython-312.pyc` 中有 4 处匹配，另外还有已删除模块留下的 `app/api/__pycache__/chat.cpython-312.pyc`。建议验收命令加上 `-I --include='*.py'`。
- **P2-12** 删除用量差值的前提是“每次 `run_review` 都用新的 LLM 实例”。建议在入口断言 `llm.usage.calls == 0`，防止实例被复用时用量虚高。
- **P2-13** `FakeLLM` 默认 `max_calls=40`，`tests/conftest.py` 也固定为 40，与新默认值 80 不一致，需要决定是否同步。
- **P2-14** `app/segment.py` 和 `app/matching.py`（设计文件表中没有后者）不在 AGENTS.md 的分工表中，需要指定唯一写入者。
- **P2-15** `F` 编号的排序键需要写明并列时的规则，建议按 `(start, end, check_id, origin)`。
- **P2-16** section span 包含标题行。文档以标题开头时不应生成空的 `sec0`。
- **P2-17** 反证候选按共享数字和专有名词查找，找不到“flag 说没有对照组，而其他句子提到对照组”这类反证。候选只是提示，payload 中已经有全文，不应把候选当作搜索范围的上限。

## 3. 验收清单（实现审核时逐项核对）

**A 切分（§3、§4.1）**
- [ ] A1 `app/segment.py` 不引入第三方库；`Unit`、`Target` 与 §3.5 一致；`claim_id == target_id`。
- [ ] A2 分句测试覆盖 §10 列出的全部情形，并保留旧测试的 CRLF 和空白行分隔输入。
- [ ] A3 每个单元的 `report[start:end]` 首尾没有空白，并且与渲染文本逐字一致（P1-7）。
- [ ] A4 ID 符合设计：p/s 编号；标题占段号；表格行 `kind='table_row'`；分隔行不编号；`secK`、`sec0`、`doc`；单句 claim 的 ID 已规范化（P2-3）。
- [ ] A5 同一输入重复切分结果相等。渲染文本不含 start/end/scope；关键词标签只作提示，不过滤句子。
- [ ] A6 定义了新的数量上限，并在请求校验（422）阶段检查（P1-1、P2-8）。

**B 去 hash（§6）**
- [ ] B1 `grep -rIn --include='*.py' -E "hash|digest|sha256" app eval tests` 为空。
- [ ] B2 固定输入用原文自校验（claim 的 text 和 target 都与重新切分的结果一致）；篡改后失败，报 `invalid_fixed_input`，且调用次数为 0。
- [ ] B3 AnswerKey 的 `excerpt` 对 claim/section 级 issue 必填，且是目标原文的子串；document 级可以省略。
- [ ] B4 eval 判断随附报告用字符串比较。
- [ ] B5 `docs/review-decisions.md` 中的历史 SHA 保持原样。
- [ ] B6 `data/synthetic/report.txt` 和 `answers.draft.json` 与基线逐字节一致（P0-1）。

**C 抽取、证据与校验（§4.3、§11.7）**
- [ ] C1 抽取输出跨段、倒序、重叠、非句子单元、未知 ID 都报 `invalid_claim_target`，各有测试。
- [ ] C2 证据的单元类型限制、引文子串、missing 的格式都有校验。
- [ ] C3 同一次调用的 payload 不重复原文。
- [ ] C4 各阶段校验失败都会修复一次，仍失败则按节点规则处理；修复调用计入预算；错误列表不含原文（P1-8）。

**D rubric 与规则引擎（§11.1–§11.3、§11.5）**
- [ ] D1 三种 kind 用判别联合区分；`needs_standard` 没有 severity；`code` 类只有 detector、scope、severity、description。
- [ ] D2 加载时拒绝：未知字段、取值越界、anchor_field 不存在、specific/vague 结构不一致，以及 P0-3 的全部不变量。
- [ ] D3 `flag_when` 是纯“与”，没有 `eval`；答案按类型严格检查（V8）。
- [ ] D4 `reason_template` 只允许 `{field}` 和 `{field.quote}`，加载时校验。
- [ ] D5 覆盖检查拒绝缺行、多行、重复行、错误的 target 和错误的 scope。
- [ ] D6 anchor 引文所在单元在目标范围内；答案落在 `quote_required_when` 时有引文，且是单元原文的子串。
- [ ] D7 严重度只来自 rubric；`note` 不参与判断；`pending_standards` 与 `unchecked_check_ids` 分开。
- [ ] D8 检查项上限 20；`version` 为 `0.1-draft`；文件名为 `<analysis_type>.<variant>.json`。

**E 代码检查（§11.4）**
- [ ] E1 三个 detector 都有正例、反例和容差边界测试；quote 是原文子串；覆盖全部句子，包括不属于任何 claim 的句子；`claim_id` 对应正确。
- [ ] E2 P1-4、P1-5、P1-6 已修复，或在交付说明中声明为已知限制。
- [ ] E3 代码 flag 为 `origin=code`，confidence 和 occurrences 为 null；不进 Jaccard，不进复核，计入召回；固定输入模式下也只运行一次。

**F LangGraph 与精简（§11.9、§12）**
- [ ] F1 `get_graph()` 的节点和边与 §11.9 一致，包括显式的 `code_checks → aggregate`；所有条件边都有 `path_map`（V5）。
- [ ] F2 `ReviewState` 中没有 llm、settings、initial_usage、fixed_input_hash；运行时依赖通过 `Runtime[ReviewContext]` 注入。
- [ ] F3 准备失败时直接进入 `write_record`，`assess_dimension` 不被调用；节点里不再逐个检查 `preparation_error`。
- [ ] F4 并行节点捕获全部异常（V3），只写 reducer 字段（V4）；任一维度失败，该次运行即失败。
- [ ] F5 零 claims 或没有模型检查时，按请求补齐运行结果（P0-4）。
- [ ] F6 `review_id` 作为 `thread_id` 传入 config；请求中带 `thread_id` 返回 422。
- [ ] F7 设置了并发上限，或在 README 中说明（P1-2）。
- [ ] F8 没有回边、没有 `Command` 跳转、没有 `interrupt`、没有 checkpointer。

**G 复核（§11.6）**
- [ ] G1 只复核 `origin=model` 的聚合后 flag；每个有 flag 的维度调用一次，不随运行次数重复；`stage='verify'`。
- [ ] G2 复核输出有覆盖检查，support 和 counter 的引文有校验；标签按设计顺序由代码推导，不使用表示正确性的词。
- [ ] G3 复核前后，flag 的数量、严重度、reason、confidence 逐字段相等。
- [ ] G4 复核失败时重试一次；仍失败则 `verification=null`、`verification_status='failed'`、错误码可见，审阅状态仍为 `complete`（P2-6）。
- [ ] G5 `verify_enabled=false` 时不产生复核调用；没有模型 flag 时状态为 `not_run`；代码 flag 不进入复核。

**H 聚合、记录与 API（§11.8、§12）**
- [ ] H1 `Flag`、`AggregatedFlag`、`Verification` 的字段与 §11.8 一致；`F` 编号按原文位置分配，并列规则确定（P2-15）；代表值选择确定（P1-9）。
- [ ] H2 `ReviewRecord` 增加了 analysis_type、verify_enabled、verification_status、pending_standards；删除了所有 hash 字段以及 aggregation_rule、cost_scope。
- [ ] H3 API 直接返回 `ReviewRecord`，`ReviewResponse` 已删除；错误路径和安全错误码不变；新错误码已登记（P2-5）。
- [ ] H4 `persist_record` 已简化，写入失败时仍报 `record_write_failed`。
- [ ] H5 `JsonLLM` 设置 `temperature=0` 并接受 `max_calls` 参数；`CHARON_MAX_CALLS` 默认 80（config 和 `.env.example`），上限仍为 100；环境变量名不变。
- [ ] H6 §12.1 的删除项全部完成，§12.4 的保留项全部仍在。

**I 指标与评测（§7）**
- [ ] I1 召回和 Jaccard 调用同一个匹配函数（检查调用点）。
- [ ] I2 §7 示例的 Jaccard 等于 1/3；不同段、不同 check 不匹配；匹配一对一（P1-10）。
- [ ] I3 Jaccard 只包含模型 flag，召回包含代码 flag（P1-11）。
- [ ] I4 批次不完整或有效运行少于两次时指标为 null；双空配对单独计数。
- [ ] I5 有 `--no-verify`；模式统一为 `end_to_end`；输出复核标签分布；裁定与 flag 的关联方式已定义。
- [ ] I6 删除 document_tokens 等常量字段以及 limitations、interpretation；预算用 `max_calls` 传入，不再复制 Settings。

**J 文档与约束**
- [ ] J1 README 和 AGENTS 按 §12.3 更新；声明新粒度下的指标是新基线；不宣称“召回提升”。
- [ ] J2 `uv run --locked --offline pytest -q` 在默认环境和 `CHARON_OFFLINE=true` 下各跑一次，全部通过。
- [ ] J3 `pyproject.toml` 和 `uv.lock` 的依赖版本未变。
- [ ] J4 没有真实调用，没有改动 `.env`，没有提交或推送。
- [ ] J5 交付说明包含：改动文件、§2 每条非目标的遵守情况、与设计不一致之处及理由。

## 4. 实现审核的方式（待 Codex 通知后执行）

- 按 §3 逐项标注以下之一：
  - 【已验证】：附命令、测试或探针；
  - 【仅读码】；
  - 【未验证】：附原因。
- 每个问题给出 `文件:行号`、可复现的离线步骤（纯函数输入或 FakeLLM），以及优先级 P0–P2。
- 基线对照：我在约 00:40 通读了原始文件。scratchpad 中 00:44 的快照已经包含部分进行中的改动，因此不作为纯基线；`data/synthetic/` 在快照中仍是原样。

## 5. 实现审核结果（2026-09-27，最终版；01:51 修复冻结后已完成复核）

**方法**：通读 app、eval、rubrics、tests 的当前代码及两份交付文档；运行离线测试；用 scratchpad 中的离线探针复现问题（FakeLLM 或纯函数，`PYTHONDONTWRITEBYTECODE=1`，不在项目里写文件）。命令前缀均为 `UV_CACHE_DIR=/private/tmp/charon-uv-cache uv run --locked --offline`。

**复核范围**：Codex 通知修复冻结后，我把当前文件与 01:48 的快照逐一比对。只有这些文件有改动：`app/tools/detectors.py`、`app/agent/nodes.py`、`app/llm/fake.py`、`app/api/errors.py`、`tests/test_detectors.py`、`tests/test_review_pipeline.py`、`README.md`、`docs/implementation-claim-granularity.md`。我逐行读了这些改动，用原先的复现重新验证，并新增了边界探针。下面各表的“状态”写的是复核后的结论。

### 5.1 真实代码缺陷

| # | 优先级 | 位置 | 问题 | 离线复现（【已验证】） |
|---|---|---|---|---|
| D1 | P1 | `app/tools/detectors.py:15`（`REFERENCE` 使用 `re.I`，且 `Appendix\s+[A-Za-z0-9]+`） | 小写的普通英文会被当成附录标签，产生 `missing_reference` 误报。设计原文是 `Appendix X`，这里的 X 是标签，不是任意单词 | `missing_reference('Details are in the appendix below.', 同一文本)` 返回 `[('appendix below', 'The report refers to appendix below, …')]`。建议：`Table`、`Figure`、`Exhibit`、`Appendix` 这些关键词区分大小写，或至少要求标识符为大写字母、数字或罗马数字。**不在 Codex 所列的 K1–K3 中** |
| D2 | P2 | `app/tools/detectors.py:56`（`actual.normalize()`） | percent_change 的 reason 用 `Decimal.normalize()` 输出科学计数法或超长小数，审阅人看到的 reason 不可读 | `percent_change('Revenue rose from 100 to 120 while margin was 15%.')` 的 reason 为 `… recalculation gives 2E+1%.`；年份例子输出 `0.04940711462450592885375494071%`。建议用 `quantize(Decimal('0.1'))` 或 `f'{actual:.1f}'` |

**D1 状态：已修复【已验证】。** `detectors.py:16-18` 中，`Appendix` 的标识符只接受大写单字母、大写罗马数字或数字，并加了 `REFERENCE_END` 边界。原复现 `appendix below` 现在返回 `[]`。`See appendix A/IV/XII/12` 仍能检出，有新增测试。

**D2 状态：已修复【已验证】。** `detectors.py:51-53,94-95` 改为最多 4 位的定点数：原复现输出 `recalculation gives 20%.`，年份例子输出 `0.0494%`。判定仍用未取整的 Decimal（第 92 行）。

Codex 自查发现的问题，现已修复并复核：

- **K1 已修复【已验证】**：`_partial_equation` 和 `_right_continues_expression`（`detectors.py:27-44`）会拒绝截取更大表达式的尾部。`60 - 50 + 10 = 20`、`1K + 200 + 300 = 1,500` 都返回 `[]`；`60 + 50 = 100` 的基线仍能检出。
- **K2 已修复【已验证】**：出现不同币种时跳过（第 47-48、59、80 行）。`$1 + €2 = $3.40` 返回 `[]`。
- **K3 已修复【已验证】**：`Table 3.1` 这类复合标签不再被截断成 `Table 3`，直接跳过；同一句里独立的 `Table 4` 仍能检出，有测试。

### 5.2 与设计不一致之处（实现偏差，不是新要求）

| # | 优先级 | 位置 | 说明 | 证据 |
|---|---|---|---|---|
| G1 | P2 | `app/agent/nodes.py:218-221`（`check.model_dump()` 整体下发） | assess 的 payload 把 `flag_when`、`reason_template`、`severity`、`kind`、`detector`、`pending_question` 都发给了模型。设计 §4.2 只要求发送“本维度检查项的问题与例子”。§11“模型回答小问题，代码下结论”的用意，是模型不知道哪些答案会触发 flag；现在模型能看到触发条件，可能有意迎合或回避 | 【已验证】探针截获的 assess payload 中，每个检查项的键为 `anchor_field, check_id, description, detector, examples, flag_when, kind, pending_question, questions, reason_template, scope, severity, standard, target_ids, tools`。注意 `app/llm/fake.py:74` 的 demo 依赖 `flag_when` 来挑选不触发的答案，修改 payload 时要同步改 fake（例如默认选 `False` 或第一个 enum 值） |

**G1 状态：已修复【已验证】。**
- `nodes.py:218-225` 改为显式白名单。探针截获的 assess 检查项字段只剩 `anchor_field, check_id, description, examples, questions, target_ids, tools`，`standard` 仅在存在时下发；问题字段为 `ask, field, quote_required_when, type, values`。
- `fake.py` 不再读取 `flag_when`、`severity`、`reason_template`（检查过源码），改为固定的夹具答案：document 目标答 `true`，其他答 `false` 或第一个 enum 值。
- 测试 `assert_assessment_check_fields` 同时覆盖 assess 和 assess_final。

### 5.3 按方案保留的限制（交付说明已声明或属于设计原文，我核实了表述是否属实）

| # | 内容 | 核实情况 |
|---|---|---|
| L1 | 算式容差用“或” | 【已验证】`12.4 + 13.4 = 26` 会出 flag，与交付说明一致 |
| L2 | percent_change 把年份当端点 | 【已验证】`Revenue grew 12% from 2024 to 2025.` 会出 flag，与交付说明一致 |
| L2b | 同一句里有无关的百分比时也会被当作声称的变化率 | 【已验证】`Revenue rose from 100 to 120 while margin was 15%.` 会出 flag。**交付说明未写这一情形**，建议补进“已知限制” |
| L3 | 并发没有上限 | 【已验证】`runs=5` 时同时在途的 `generate()` 峰值为 20，与交付说明一致 |
| L4 | `also_reported_by_model` 在现有 rubric 下不会为 true | 读码确认。README 第 126 行把它写成会生效的能力，建议注明“现有 rubric 中不会出现” |
| L5 | 贪心匹配可能低估 | 读码确认与 README 第 128 行一致 |
| L6 | 最坏情况下的调用次数 | 【推断】每维度两阶段且每次都修复时：`runs=3` 约 60 次，`runs=5` 约 92 次，超过默认的 80。`runs=5` 加多次修复会因预算失败，而且会显示为失败状态，不会被当作成功 |

**复核状态**：L2b、L4、L6 已补进 README（第 81、126、136 行附近）和交付说明的“已知限制”，表述与实际行为一致。

### 5.4 对设计的新建议（均未经用户批准，不作为本轮验收要求）

| # | 影响 | 位置 | 建议 | 证据 |
|---|---|---|---|---|
| S1 | P1 | `app/agent/rules.py:41`（引文只允许 `sentence` 和 `table_row`） | 标题不能作为引文来源。业务问题常常只写在标题里，例如 `# Should we expand …?`。此时模型如实回答 `question_stated=true`，但无法按 `quote_required_when=[true]` 提供引文：要么整次运行失败，要么模型被迫答 `false`，产生一条误报 flag。建议允许 document/section scope 的问题引用标题单元（标题仍不能作为 claim 或证据） | 【已验证】报告 `# Should we expand the loyalty program to all stores?\n\nRevenue grew 12% in Q3.`，模型引用 `p1` 的标题文字后，经一次修复仍报 `invalid_assessment_quote`，review 为 `status=failed`，共 7 次调用 |
| S2 | P2 | `app/segment.py:106-117` | 分隔线 `---`、`* * *` 和整个代码块都被当成可抽取、可被 detector 检查的句子。代码块里的 `a + b = c` 会被当作报告算式 | 【已验证】`Intro.\n\n---\n\n```\nx = 1\n```\n\n* * *` 切出的单元文本为 `['Intro.', '---', '```\nx = 1\n```', '* * *']` |
| S3 | P2 | `app/segment.py:34,48` | 弯引号（`”`、`’`）不在闭合引号和下一句开头的字符集中，Word 导出的英文报告常用弯引号，这些句子不会被切开 | 【已验证】`He said it was “good.” Next came review.` 只切出 1 个句子 |
| S4 | P2 | `eval/metrics.py:72-73,99` | 端到端模式下，不同记录对同一问题抽出不同范围（如 `p2.s1-s2` 和 `p2.s2`）。Jaccard 把它们算作同一处（=1.0），但唯一 flag 数、待裁定数和错误占比的分母把它们算成两条，口径不一致 | 【已验证】两条记录各有一条上述 flag 时：`unique_flag_count=2`，pending 为 2，Jaccard 为 1.0 |
| S5 | P3 | `eval/metrics.py:108-117` | 复核分布中的 `unverified` 混合了三种情况：代码 flag（按设计不复核）、关闭复核、复核失败。建议分开统计 | 读码 |
| S6 | P3 | `app/api/errors.py:26-28` | `invalid_check_reference`、`invalid_flag_target`、`invalid_flag_quote`、`mixed_flags_and_tools` 是旧契约遗留的错误码，全仓库已不再抛出 | 【已验证】对 `CharonError('…')` 的检索结果与 `ERROR_STATUS` 对比；另外没有发现“抛出了但未登记”的错误码 |
| S7 | P3 | `app/agent/nodes.py:146-148` | 准备阶段失败时 `pending_standards` 为空。它只取决于 rubric，可以不依赖 `code_checks` 节点 | 读码 |

**复核状态**：
- S1–S5 按“已知限制”写进了 README 和交付说明，行为没有改变，符合“新建议不作为本轮必做”的约定。
- S6 已处理：4 个旧错误码已删除。复核时所有被抛出的错误码都已登记，没有遗漏。
- S7 未处理，属于可选项。

### 5.4b 修复后新发现的残余问题（detector 复核探针，均为【已验证】）

K1 的修复采用保守跳过，把几种常见写法也一起跳过了，产生了**新的漏报**。这些不是误报，符合 §11.4“不猜测”的方向，但降低了代码检查的检出率，而且修复前这些写法是能检出的。

| # | 优先级 | 位置 | 复现（均返回 `[]`，修复前能检出） | 建议 |
|---|---|---|---|---|
| R1 | P2 | `detectors.py:19,31,41`（`*` 在 `ARITHMETIC_OPERATORS` 里） | `The total is **60 + 50 = 100**.`、`*60 + 50 = 100*`。Markdown 加粗或斜体的算式全部被跳过，而报告中加粗关键数字很常见 | 左右判断时先剥掉紧贴的 `*`、`_` 强调符，或者 `*` 只在两侧都是数字或空格加数字时才算运算符 |
| R2 | P2 | `detectors.py:31`（`(` 和 `-` 被当作表达式延续），在 `percent_change` 第 80 行调用 | `Revenue rose from 100 to 120 (25%).`、`… (a 25% increase).`、`… - a 25% increase.`、`60 + 50 = 100 (see note).` | 只有 `(` 或 `-` 后面紧跟数字或运算符时才算延续 |
| R3 | P3 | `detectors.py:39-40`（`numeric_parenthesis`） | `Costs (2025) 60 + 50 = 100.` 被跳过 | 可接受，或限制为括号紧贴数字的情况 |
| R4 | P3 | `detectors.py:41`（前缀是 `=`） | `The total = 60 + 50 = 100.` 被跳过 | 可接受（链式等式有歧义） |
| R5 | P3（误报） | `detectors.py:16-17`（关键词不区分大小写，标识符允许单个大写字母） | `In the appendix I prepared, costs rose.` 检出 `appendix I` | 罕见，可接受；或要求关键词首字母大写 |
| R6 | P3（误报） | `detectors.py:59` 只比较币种，不比较 `%` | `$60 + 50% = $100` 被当作同一单位计算并出 flag | 操作数与结果的 `%` 标记不一致时跳过 |

R1–R6 都不阻断交付，建议在交付说明的“已知限制”中补写 R1、R2 两项常见漏报，或者按上面的建议收窄判断。

**最终状态（01:57 定向复核，针对 `detectors.py` 01:56:45 版本，均为【已验证】）**：

- **R1 已修复。** `_partial_equation`（`detectors.py:48-66`）只在算式两侧成对出现 `*`、`**` 或 `***`，而且外侧不紧接单词字符或 `*` 时，才把它们当作强调符剥掉；引文仍用原文位置。以下写法现在都能检出 `60 + 50 = 100`：`**60 + 50 = 100**`、`*60 + 50 = 100*`、`***60 + 50 = 100***`、`- **60 + 50 = 100**`。以下乘法或幂运算写法仍然跳过：`2*60 + 50 = 100*3`、`2 * 60 + 50 = 100`、`2**60 + 50 = 100**2`。
- **R2 已修复。** `_right_continues_expression`（`detectors.py:27-45`）把括号分成两类：含两个以上字母、且不是量级词的算说明性括号；百分比专用的 `(25%)` 也允许。`- a/an/the …` 这种说明也允许。以下写法现在都能检出：`from 100 to 120 (25%)`、`(a 25% increase)`、`- a 25% increase`、`60 + 50 = 100 (see note)`。以下写法仍然跳过：`(thousand)`、`(M)`、`(x 2)`、`(rounded) + 5`、百分比句中的 `(x 2)`、`- 25% growth`（没有冠词）。
- **R6 已冻结并确认修复。** `detectors.py:85-87`：`10% + 0.2 = 0.3` 和 `$60 + 50% = $100` 跳过；`20% + 30% = 60%` 仍会检出。
- **之前的修复都没有退化。** K1 的五种写法、K2 的算式和百分比、K3、D1、R6 仍然返回 `[]`；D2 的 reason 仍是定点格式；基线正例仍能检出，反例仍不检出。
- **R3–R5**：Codex 决定保留为低优先级的歧义限制，已写入交付说明“已知限制”第 12 行。
- **R7（P3，新发现，外观问题）**：百分比 flag 的引文从区间起点截到百分号，可能留下不成对的括号，比如 `from 100 to 120 (25%`。它仍是原文的精确子串，不影响判定和校验，可以不改。

### 5.5 验收清单逐项结论（对照 §3；标记：✓ 通过 / △ 部分或按方案保留 / ⏳ 待复核）

证据来源：
- 离线测试：01:51 我自己重跑，默认环境和 `CHARON_OFFLINE=true` 下各 227 passed（修复前为 190）。R1、R2、R6 修复冻结后，01:58 最终重跑，两环境各 **239 passed**，与交付说明第 78-79 行一致；
- 逐文件读码；scratchpad 探针；
- 01:48 的快照比对：只有 §5 开头列出的那些文件有改动。

- **A 切分**
  - A1–A5 ✓：`segment.py` 只用 `re`；`Claim` 校验 `claim_id == target.target_id`（`schemas.py:54-58`）；单句 ID 已规范化（`segment.py:149`）；渲染只在单元起点插入标记，原文字符不变（`segment.py:171-192`）；CRLF、Unicode、确定性都有测试。
  - A6 △：只有 30,000 字符和 80 个 claims 的上限，没有单元数量上限（交付说明第 11 条已声明）。合法请求下切分不会抛异常。
- **B 去 hash**：B1–B6 ✓。
  - B1：`grep -rIn --include='*.py' -E "hash|digest|sha256" app eval tests` 为空。
  - B2：固定输入整体重新切分比对，篡改或改动原文都被拒绝，有测试。
  - B3：excerpt 必填（`eval/metrics.py:23-27`），且在评测开始前校验是原文子串（`eval/run.py:41-43`）。
  - B4：随附报告改为字符串比较（`eval/run.py:89`）。
  - B5：`docs/review-decisions.md` 与 00:44 快照一致，历史 SHA 保留。
  - B6：`data/synthetic/` 四个文件与 00:44 快照逐字节一致。
- **C 抽取、证据与校验**
  - C1、C2、C4 ✓：修复逻辑由 `JsonLLM` 和 `FakeLLM` 共用同一个 validator 回调（`client.py:98-139`、`fake.py:26-45`）；每次修复计入预算；错误列表不回显原文，有测试。
  - C3 △：全文每次调用只发一次；证据引文和修复时附带的上次输出会重复出现片段，属于必要引用。
- **D rubric 与规则**
  - D1–D8 ✓：答案用 `StrictBool | StrictStr`，按题型严格判定（`rules.py:62-69`）；reason 模板只允许 `{field}` 和 `{field.quote}`，拒绝格式说明和转换。
  - 预审 P0-3 的不变量没有在加载阶段校验，但运行时 claim 级 anchor 会校验（`rules.py:76-78`）。我用脚本检查了四份 rubric，模板和 anchor 引文的不变量没有违反。
  - rubric 内容与附录 A.2 的 16 个检查项逐项一致（kind、scope、严重度、适用类型），用脚本核对过。
  - specific/vague 结构只在 `--compare` 时校验（交付说明第 8 条已声明）。
  - 见 S1（标题不能被引用）、G1（规则下发给了模型）。
- **E 代码检查**
  - E1 ✓：三个 detector 的正例、反例、容差边界、引文子串都有测试；K1–K3、D1、D2 已修复并验证；K/M/bn 后缀、负数、列表项中的算式都正确。
  - E2 △：L1、L2、L2b 按方案保留并已写入文档。R1、R2、R6 已修复并复核；R3–R5 保留为已写入文档的低优先级歧义；R7 是外观问题（§5.4b）。
  - E3 ✓：代码 flag 的 `origin=code`，occurrences 和 confidence 为 null；不进复核、不进 Jaccard，计入召回；固定输入模式下也只运行一次，有测试。
- **F LangGraph**
  - F1–F6、F8 ✓：
    - 边集合有测试，条件边都有 `Literal` 注解或 `path_map`；
    - state 中没有 llm 和 settings；
    - 准备失败时不调用 assess；
    - 两个并行节点都在内部捕获全部异常，只写 reducer 字段（`nodes.py:260-270,321-345`）；
    - 空派发时显式进入 aggregate，并按请求构造全部运行结果（`nodes.py:189-195,273-285`）；
    - `thread_id = review_id`；
    - 没有 `Command`、`interrupt` 或 checkpointer（已检索）。
  - F7 △：没有并发上限（L3）。
- **G 复核**：G1–G5 ✓。有测试，另外我用探针验证了两种情况：
  - 复核阶段预算耗尽：`status=complete`、`verification_status=failed`、`call_budget_exceeded`；
  - 两个维度中一个复核失败：失败的那批为 null，成功的那批保留标签，状态为 `failed`，审阅仍为 `complete`。
- **H 聚合、记录与 API**
  - H1–H6 ✓：代表值取 `run_index` 最小的完整运行（`nodes.py:288-296`）；F 编号排序确定；API 直接返回 `ReviewRecord`；新错误码都已登记；`temperature=0` 有测试；默认调用上限 80；§12.1、§12.2 的删除和简化项、§12.4 的保留项都已逐一检索确认。
  - 额外字段 `quotes`、`verification_error_code` 已在交付说明中声明。
- **I 指标与评测**
  - I1–I6 ✓：召回和 Jaccard 调用同一个 `match_items`，测试跟踪了调用点；§7 示例 Jaccard=1/3。
  - 离线 CLI `--compare --runs 3`（记录目录指向 scratchpad）在修复前后各跑一次，都完整：四格各 18 次调用，合计 72 次，剩余 8。项目的 `records/` 目录没有被写入。
  - 口径问题见 S4、S5。
- **J 文档与约束**
  - J1–J5 ✓：
    - README 声明是新基线，没有宣称召回提升；L2b、L4、L6 和 S1–S5 已补进文档；
    - `pyproject.toml` 和 `uv.lock` 的修改时间仍是 2026-09-26，未改动；
    - `data/synthetic/` 四个文件在复核时仍逐字节一致；
    - 修复后仍然不存在未登记的错误码，`hash|digest|sha256` 检索仍为空；
    - 没有提交（`git log` 显示没有任何提交）；我没有读取 `.env`。

### 5.6 最终结论

**结论：可以交付，没有阻断问题。**

- **已修复并复核的真实缺陷**：D1、D2、K1、K2、K3、G1、S6、R1、R2、R6，全部用原复现验证过，之前的修复没有退化。
- **离线测试**：最终两个环境各 **239 passed**（01:58，我自己运行）。
- **最终变更范围**：与 01:48 快照相比，只改了 detectors、nodes、fake、errors、两份测试、README 和交付说明。nodes、fake、errors 在 01:49 复核后没有再改。`data/synthetic/`、`pyproject.toml`、`uv.lock` 不变，`hash|digest|sha256` 检索为空。
- **非 detector 范围没有发现功能性缺陷**：覆盖检查、引文、修复与调用预算、范围匹配、聚合、复核失败后仍为 complete、计算器两阶段机制和 AST 沙箱（相对基线没有改动），都有测试，并经探针验证符合方案和 Architecture B 的约束：固定 DAG、没有回边、严重度只来自 rubric、confidence 只表示复现、答案和裁定都要人工审定、fake 与 live 的来源分开。
- **剩余问题分三类，都不需要在本轮必做**：
  1. **按方案保留的限制**：L1、L2、L2b、L3、L4、L5、L6，已如实写入文档。
  2. **低优先级歧义**：R3–R5 已写入文档；R7 是引文括号不成对的外观问题（P3），可以不改。
- **没有剩余的、明确且未修复的功能性问题。**
  3. **新设计建议**：S1–S5 已作为限制写进文档，S7 可选。其中 S1（标题不能作为引文）对真实报告影响最大，是否采纳由用户决定。
- **仍然没有验证的内容**：真实模型的兼容性，包括 `temperature=0`、JSON 模式、输出容量（P1-1），以及 rubric 草案的内容质量。这需要用户授权真实调用，以及用户审定 rubric 和合成数据后才能进行。本审核没有进行任何真实调用。
