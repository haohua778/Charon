# Charon

Charon 是课程 Architecture B 的英文 Markdown 报告审阅原型。固定 LangGraph DAG 输出供人检查的 flags，不给报告通过或拒绝的结论。

```text
START → load_rubric → segment → extract_claims → link_evidence
                           ↘ load_fixed_input ↗
       → code_checks → assess_dimension × (run, dimension)
       → aggregate → [verify_flags] → write_record → END
```

抽取、证据或固定输入校验失败直接写失败记录。`assess_dimension` 由 LangGraph `Send` 并行派发；没有模型检查时直接聚合。复核是一个普通节点，内部子图按维度 `Send`，没有回边。每次审阅要求新的 LLM 实例，重复使用会报 `llm_already_used`。运行时依赖通过 `ReviewContext` 注入，state 只保存可序列化数据。`review_id` 同时作为 LangGraph 的 `thread_id`；没有 checkpointer、对话记忆或人工中断功能。

## 运行

需要 Python 3.12 和 uv，保持 `uv.lock` 中的依赖版本。

```bash
uv sync --locked
uv run --locked --offline pytest -q
CHARON_OFFLINE=true uv run --locked --offline pytest -q
CHARON_OFFLINE=true uv run --locked uvicorn app.main:app --host 127.0.0.1 --port 8000
```

若本机默认 uv 缓存不可写，可为上述命令设置 `UV_CACHE_DIR=/private/tmp/charon-uv-cache`。

```bash
curl http://127.0.0.1:8000/review \
  -H 'Content-Type: application/json' \
  -d '{"report":"# Results\n\nThe stated sum is 60 + 50 = 100.","rubric_id":"program_evaluation","runs":3,"verify_enabled":true}'
```

`POST /review` 成功时直接返回完整 `ReviewRecord`。请求字段：

| 字段 | 默认值 / 限制 |
|---|---|
| `report` | 必填，1–30,000 Unicode 字符，不允许全空白 |
| `rubric_id` | `program_evaluation`，另有 `kpi_comparison` |
| `variant` | `specific` 或 `vague` |
| `runs` | 默认 1，范围 1–5 |
| `tools_enabled` | `true` |
| `verify_enabled` | `true` |

请求不再接受 `thread_id`。原文空白保留；偏移按 Python Unicode 字符计。模型最多抽取 80 个互不重叠的 claims。接口文档在 [本地 /docs](http://127.0.0.1:8000/docs)。

## 网页与文档解析

服务根路径 `GET /` 提供一个无构建步骤的审阅页面（`app/static/index.html`），围绕同一个 `/review` 工作：上传或粘贴文档、免费预览切分、发起审阅、在原文上按偏移高亮 flags，并读取历史记录。页面不做任何通过或拒绝的结论。

| 接口 | 作用 |
|---|---|
| `POST /parse?filename=x.pdf` | 请求体是文件本身。`.md`/`.txt` 直接解码；`.pdf`/`.docx`/`.png`/`.jpg` 通过外部 MinerU 命令解析，HTML 表格改写为管道表格、图片引用删除。返回 Markdown、字符数和是否在 30,000 字符限制内 |
| `POST /preview` | 本地切分，不调用模型；返回单元、目标、提示标签和模型看到的标注文本 |
| `GET /records`、`GET /records/{review_id}` | 列出和读取本地记录；审阅失败或部分完成时页面据此展示已持久化的记录 |

解析复用本机的 MinerU 源码检出（默认 `../MinerU/MinerU`），只用其文本部分：`.docx` 走 office 后端，不需要模型；PDF 走 pipeline 后端的 `txt` 模式，用版面和表格模型，不做 OCR 和公式识别。包装脚本 `scripts/mineru_text_parse.py` 由 MinerU 自己的环境执行，Charon 只通过子进程调用它，MinerU 及 torch 不进入 `uv.lock`：

```bash
cd ../MinerU/MinerU && uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -e ".[pipeline]"
uv pip install --python .venv/bin/python six   # 该检出的 OCR 模块用到 six，但 [pipeline] 未声明
# 首次解析 PDF 时自动下载 PDF-Extract-Kit 模型；国内网络可先 export MINERU_MODEL_SOURCE=modelscope
```

实测（M5 Pro，MPS）：`.docx` 约 1–2 秒；PDF 每次调用约 10 秒模型初始化加解析，一篇 10 页论文共 18 秒。规整时删除图片引用、把 HTML 表格改成管道表格、去掉 `<sup>`/`<strong>` 等行内标签并把 `[文字](链接)` 还原为文字，以便模型引文能精确匹配原文。超过 30,000 字符的文档需要在页面上裁剪后再审阅。

`MINERU_COMMAND`（默认 `<MinerU>/.venv/bin/python <Charon>/scripts/mineru_text_parse.py`）、`MINERU_BACKEND`、`MINERU_PARSE_METHOD`、`PARSE_TIMEOUT_SECONDS`、`MAX_DOCUMENT_BYTES` 可在 `.env` 调整。命令或脚本不存在返回 `parser_unavailable`（503），失败/超时返回 `parser_failed`/`parser_timeout`，不支持的类型返回 `unsupported_document`（415）。解析只保证切分器能读懂的 Markdown 子集；切分器本身仍只支持英文。

每次审阅写入 `records/<review_id>.json`，包含完整报告、rubric、claims、证据、运行结果、flags 和用量。写入采用普通文件写入，失败返回 `record_write_failed`；不承诺原子写入或崩溃恢复。该目录被 Git 忽略，记录应按本地原文的敏感程度管理。

## 句子与检查规则

`app/segment.py` 确定性扫描英文 Markdown，普通段落按句末标点切句；小数、缩写、人名首字母和省略号不会被当作普通句号。列表按行处理并继续分句；表格行可以作为证据，不能作为 claim；标题自成段落和章节。句内不按逗号或冒号细分，也不实现中文分句。

- 段落为 `p1`、`p2`；句子与表格行为 `p2.s1`；同段连续句子可构成 `p2.s1-s3`。
- 标题开启真实章节 `sec1`、`sec2`，范围直到下一个任意级别标题；首个标题前为 `sec0`。全文为 `doc`。
- 标题 Unit 的内部 ID 为 `pN`，模型正文显示 `[secK]`；其他单元显示 `[pN.sM | causal, quant, ...]`。模型不计算偏移。提示标签始终开启，不过滤句子。
- 证据候选来自同一报告内共享数字、时期或多词专名的其他单元，最多 5 个，只作为提示。没有 embedding、向量检索或 RAG。
- 引文必须是指定原文单元的精确子串；anchor 引文的单元必须完全落在目标内。缺失型 section/document 问题没有可引用内容时，flag 的 `quote` 为 `""`；claim flag 必须有 anchor 引文。引文校验只验证出处。

`rubrics/<analysis_type>.<variant>.json` 保存四个维度及其检查项，上限 20 项。目前全部版本为 **`0.1-draft`，内容和严重度仍待用户审定**。

| `kind` | 处理 |
|---|---|
| `model_assessed` | 模型回答必填 bool/enum 问题；代码验证检查项 × 目标覆盖、类型、引文，再对 `flag_when` 求逻辑与 |
| `code` | 每次审阅仅运行一次，检查所有句子的加法算式、百分比变化、缺失表/图/附录引用 |
| `needs_standard` | 不调用模型、不出 flag；把待提供的标准记入 `pending_standards` |

严重度只来自 rubric。评估 payload 只给模型检查问题、例子和必要的目标/引文信息，不发送 flag_when、reason_template 或严重度。模型 note 保留但不参与判断。reason 仅用模板中的 `{field}`、`{field.quote}` 填充。specific/vague 只改变 description、问题和例子的文字；比较矩阵加载时检查其余结构一致。

计算器保持两阶段机制：每个维度先请求最多两个有许可的表达式，再返回最终评估。计算器只接受有界数字四则运算和括号，保留 AST 沙箱。首版代码检查跳过无法唯一识别的百分比描述；跨单元数字口径冲突留到后续阶段。

## 复核、状态与预算

聚合后，默认对每个有模型 flag 的维度复核一次。模型寻找反证并判断批评前提，代码推导 `supported`、`partially_supported`、`premise_not_supported` 或 `counter_evidence_found`。复核不删 flag，不改数量、严重度、reason 或 confidence，不循环。代码 flag 的 `verification=null`。

复核失败公开 `verification_status=failed` 与 `verification_error_code`，该维度的标签为 null；已成功完成的审阅保持 `complete`。关掉复核或没有模型 flag 时为 `not_run`。复核标签不表示报告或批评已被证明正确。

- `complete`、`partial`、`failed` 区分完整执行和失败；任一维度失败会使该次运行失败。失败运行的局部 flags 保留诊断用途，不进入模型聚合。
- 零 claim 的模型 claim 检查列在 `unchecked_check_ids`，与缺公司标准分开；代码检查仍覆盖所有句子。`complete` 不保证检查覆盖了所有内容。
- 默认 `CHARON_ALLOW_LIVE=false`。离线使用唯一 `FakeLLM`，记录为 `offline_fake`，只验证技术路径。没有真实模型质量或费用验证。
- 保持原环境变量名与 `.env`。真实调用需要用户明确授权、`CHARON_ALLOW_LIVE=true`、`CHARON_OFFLINE=false` 和有效的 Kimi 配置；不会自动从失败的真实模型切换到 fake。
- `temperature=0`；格式、覆盖或引文校验失败至多修复一次，附前次输出和安全字段错误。传输错误最多重试一次。每次物理尝试都计入共享预算，并行不能绕过。
- 默认 `CHARON_MAX_CALLS=80`，配置上限 100；单次超时 45 秒，输出上限默认 4096 tokens。调用次数是预算边界，不是精确货币预算。本轮不做 token 优化。五次运行若每维度都用工具且都需要修复，最坏约需 92 次调用，会超过默认预算并显式失败。
- provider 未提供或失败调用不明确的 token 用量保留 null。fake 调用数与耗时不能当作真实模型成本。

审阅不完整返回安全 HTTP 错误；上游超时 504，其他模型执行失败通常 502，错误只公开固定错误码和可用的 review ID，不回显报告或堆栈。rubric 无法加载时不会生成记录。

## 评测与人工审定

```bash
# 默认 fake，每次从报告重新抽取和关联
uv run --locked --offline python -m eval.run --mode end_to_end --runs 3 --output outputs/e2e.json
# 两种分析类型均可选择
uv run --locked --offline python -m eval.run --analysis-type kpi_comparison --runs 2 --no-verify --output outputs/kpi.json
# 使用人工核对过的新格式固定输入
uv run --locked --offline python -m eval.run --mode fixed --fixed-input /path/to/fixed.json --runs 3 --output outputs/fixed.json
# 在相同固定输入上比较 specific/vague × 工具开关
uv run --locked --offline python -m eval.run --mode fixed --fixed-input /path/to/fixed.json --compare --runs 3 --output outputs/compare.json
```

`--no-verify` 不增加比较矩阵维度；比较复核开关时，使用同一份固定输入分别执行两个命令。整个命令共用总调用预算，四格矩阵也不能突破；预算耗尽会留下失败状态。

随附 `data/synthetic/` 本轮保持原样。旧固定快照和旧答案已不兼容句子级契约，CLI 不会默认加载它们；显式传入时给出迁移错误。原报告仍可作为离线管线样例。新的英文 Markdown 合成报告应包含多结论段落、跨句 claim、标题和表格，但埋入哪些错误及对应答案由用户决定。

新固定输入为 `{"claims": [...], "evidence": [...]}`，可从成功记录取这两个字段后人工核对。每个 claim 保存 `claim_id`、`target`、`text`、`section_id`、`unit_ids`；加载时按当前报告重新切分并逐项核对，包括完整原文。证据使用 `unit_id`。

答案文件示意（不是已批准答案）：

```json
{
  "status": "draft",
  "approval_note": null,
  "issues": [{
    "check_id": "arithmetic_mismatch",
    "target_id": "p2.s1",
    "description": "Human-authored issue description",
    "excerpt": "60 + 50 = 100"
  }]
}
```

claim/section 答案必填原文 excerpt，document 答案可以省略。检查项、目标与 excerpt 必须符合当前报告与 rubric。只有用户能把答案改为 `approved` 并填写 `approval_note`；然后通过 `--answers` 传入。`--adjudications` 接收 `(check_id,target_id,status,note)` 列表；`confirmed_error` 表示批评错误，`confirmed_valid` 表示批评成立，默认 `pending`。与已批准答案相矛盾的裁定会被拒绝。机器记录始终 `human_status=pending`、`decisions=[]`。

真实评测需显式 `--live` 且配置允许调用，报告字符串必须与随附合成报告完全一致。只有实际完成有效运行且发生调用才标为 `measured_synthetic`；全部失败为 `not_measured`。这只是来源标签，不批准旧答案或 rubric，也不证明检测能力。

## 指标口径

同一次完整运行按 `(check_id,target_id)` 去重，模型 flags 取完整运行并集。模型 `confidence=occurrences/valid_runs` 只表示复现程度；单次 `1/1` 不等于正确率。批次不完整时 confidence、正式一致性和召回均为 null。代码 flag 的 occurrences/confidence 始终为 null；与模型重复的同处问题保留代码 flag，并记 `also_reported_by_model=true`；现有草案的代码与模型 check_id 不重叠，因此当前 rubric 不会触发这个通用去重分支。

“同一处”要求 check_id 相同，claim 的句子范围在同段重叠，section/document 则 ID 完全一致。共享匹配函数按原文位置排序后一对一贪心匹配，一个大范围不能同时命中两个小范围。按排序后的左侧逐条选择首个可用右侧目标；这种贪心在复杂重叠中可能低估匹配数。

- 召回对所有完整运行的并集（含代码 flag）与用户批准答案进行匹配；答案未批准、不完整批次或空答案不输出正式召回。
- Jaccard 仅使用模型 flags；两次运行匹配数为 m 时，值为 `m/(|A|+|B|−m)`。双方为空的配对另计并排除；不足两次有效运行时为 null。
- 未匹配答案的 flag 等待人工裁定，不自动算误报。错误 flag 占比是人工确认错误数除以全部唯一 flags；全部裁定完成前不输出该比例，也不称为 false-positive rate。
- 复核标签分布按各记录中聚合后 flag 的复核观察统计，并按人工裁定分组；端到端重复运行对同一目标产生的多次标签观察均保留，分布总数可能大于唯一 flag 数。
- 固定模式成本包含代码检查、重复评估、复核和写记录，不含已固定的抽取/证据准备；端到端模式每次从原文执行完整 `run_review`。`wall_clock_seconds` 包含文件写入，记录 `elapsed_seconds` 截止写入前。

当前规则仍有限制：年份区间和同句中无关的百分比可能误触发百分比检查；仅在标题中陈述的业务问题无法提供允许的单元引文；弯引号、代码块和分隔线不属于完整 Markdown 解析支持范围。端到端唯一 flag 数按精确目标 ID 去重，范围不同但重叠的 flags 在 Jaccard 中可匹配，在人工裁定计数中仍分开。

这是新的句子级基线，不能与旧段落指标比较或宣称“召回提升”。小样本离线技术测试不证明模型质量、rubric 有效性或业务收益。

## 维护入口

| 位置 | 责任 |
|---|---|
| `app/schemas.py` / `app/segment.py` | 数据契约 / 确定性原文单元与标注 |
| `app/core/` / `app/llm/` | 配置、rubric 校验 / 调用、修复和 fake |
| `app/agent/` | 原生 LangGraph、规则求值、聚合与单轮复核 |
| `app/tools/` / `app/matching.py` | 线索、三种代码检查、计算器 / 共享匹配 |
| `app/api/` / `eval/` | 同一 run_review 的 API 与评测入口 |
| `rubrics/` | 两种分析类型的四份草案 |
| `tests/` | 离线机制和失败边界测试 |
| `docs/implementation-claim-granularity.md` | 本轮交付范围、取舍和验证 |
| `docs/claude-design-audit.md` | Claude 独立设计与代码审核 |

| `app/parsing.py` / `app/static/` | MinerU 子进程与 Markdown 规整 / 审阅页面 |

演示页面和文档解析在客户演示前加入；数据库、RAG、后端记忆、流式输出、多轮反思、生产认证、租户和插件框架仍不在范围内。历史 `docs/review-decisions.md` 原样保留。
