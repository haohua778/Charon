# Charon 后续待做清单

整理自 2026-09-27/28 的代码审查、演示页面搭建和架构讨论。按优先级分层；每项写明动机、做什么、涉及文件和前置条件。状态列留给后续更新。

优先级含义：**P0** 影响真实文档能否跑通；**P1** 决定产品形态；**P2** 代码质量与可维护性；**P3** 值得做但不急。

## P0 真实运行的可靠性

| # | 事项 | 动机 | 做什么 | 涉及 | 状态 |
|---|---|---|---|---|---|
| 0.1 | 首次真实调用验证 | 现有全部记录来自假模型，Kimi 输出能否通过校验从未验证 | 用一份英文报告跑 runs=1，逐阶段看修复重试次数和失败码；据此决定 0.2、0.3 是否必须 | `.env`、`records/` | 待授权 |
| 0.2 | 评估阶段分批 | 每维度一次调用要求「每个 check × 每个 claim」都返回一行，几十个 claim 就会撞输出 token 上限 | `check_dimension` 按 check 或按 claim 分组多次调用，同一维度内合并结果；fake 的 `demo_response` 同步 | `app/agent/nodes.py`、`app/llm/fake.py`、`tests/test_review_pipeline.py` | 视 0.1 结果 |
| 0.3 | 长文档策略 | 10 页 PDF 解析后约 45k 字符，超过 30,000 上限 | 二选一：页面上按章节勾选送审（推荐，保留精确偏移）；或提高上限并接受成本和 0.2 的风险。不做静默截断 | `app/static/index.html`、`app/schemas.py` | 待定 |
| 0.4 | 引文精确匹配的容错 | 模型引文必须是原文精确子串；换行、弯引号、行内 `**` 都会导致校验失败 | 校验时允许「空白折叠后相等」的宽松匹配，但仍以原文偏移落点；记录归一化前后差异 | `app/agent/rules.py`、`app/agent/nodes.py` | 视 0.1 结果 |
| 0.5 | MinerU 常驻 | 每次解析约 10 秒模型初始化 | 用 MinerU 自带的 `mineru-api` 常驻进程，`parsing.py` 改为 HTTP 调用，子进程方式保留为回退 | `app/parsing.py`、`scripts/` | |
| 0.6 | 模型供应商与数据出境 | 美国客户材料经 Moonshot 中国节点处理存在合规风险 | `JsonLLM` 已是 OpenAI 兼容接口，评估替换为美国节点的供应商；`.env` 只改 base_url/model 即可，需实测输出格式 | `app/llm/client.py`、`app/core/config.py` | 需客户确认 |

## P1 产品形态（对应五点讨论）

| # | 事项 | 动机 | 做什么 | 涉及 | 状态 |
|---|---|---|---|---|---|
| 1.1 | rubric 加深 | 「模型参与少」的真实原因是每个 check 只有 1–2 个 bool/enum 问题 | 每个 check 补充依据类问题（例如 `evidence_type`、`magnitude`），加 `rationale` 自由文本字段；仍由代码按 `flag_when` 出 flag | `rubrics/*.json`、`app/schemas.py`（Question 类型）、`app/agent/rules.py` | 内容需用户审定 |
| 1.2 | 展示模型的 note 和依据 | `note` 目前不进 reason，页面也不显示 | reason 模板可引用 `{rationale}`；页面 flag 卡片显示 note、答案和全部引文 | `app/agent/rules.py`、`app/static/index.html` | |
| 1.3 | 复核覆盖代码 flag | 代码 flag 的 `verification` 永远为 null，检测器显得死板 | `dispatch_verifications` 不再过滤 `origin == 'model'`；复核 payload 对代码 flag 提供 reason 和 quote；指标口径同步 | `app/agent/nodes.py`、`eval/metrics.py`、README 指标章节 | |
| 1.4 | 维度级汇总 | 客户想看每个维度的整体情况 | 优先做派生汇总：按维度统计 flag 数、最高严重度、复核分布，页面展示；如需模型判断，给每个维度加 `overall_concern` 枚举问题，仍不出报告结论 | `app/static/index.html`；可选 `rubrics/`、`app/agent/nodes.py` | 二选一待定 |
| 1.5 | 待人工队列 | 人工介入的信号已存在，缺流程 | 页面新增「待人工」区块，聚合四类信号：`pending_standards`、confidence < 1、复核标签为 partial/counter、校验失败的维度 | `app/static/index.html`、`app/api/documents.py` | |
| 1.6 | 中断与恢复 | 真正的人在环 | 引入 LangGraph checkpointer；在聚合后按 1.5 的结构性条件 `interrupt`；新增恢复接口接收人工决定并写入 `decisions`；`human_status` 走 pending → reviewed。不由模型自行决定何时打断 | `app/agent/graph.py`、`app/agent/state.py`、`app/api/`、`app/schemas.py` | 依赖 1.5 |
| 1.7 | 重命名 fixed 模式 | `fixed` 被误解为固定规则 | 改为 `frozen_preparation` 或类似；仅评测 CLI 使用，README 明确它是实验对照 | `eval/run.py`、`app/agent/graph.py`、README | |
| 1.8 | 人工裁定写回记录 | 裁定目前只能通过 CLI 传 JSON | 页面上对 flag 做 confirmed_error / confirmed_valid，写回 `records/*.json` 的 `decisions`；评测读取记录内裁定 | `app/api/`、`eval/run.py`、`eval/metrics.py` | 依赖 1.5 |

## P2 代码质量（对应审查发现）

| # | 事项 | 做什么 | 涉及 | 状态 |
|---|---|---|---|---|
| 2.1 | 合并两套聚合逻辑 | 从 `aggregate_node` 抽出纯函数（去重、计数、confidence、代码/模型合并），`compute_metrics` 复用；两边口径不再可能分叉 | `app/agent/nodes.py`、`eval/metrics.py` | 最高优先 |
| 2.2 | 去掉重复校验 | `_claims_from_output`、`validated_evidence` 各执行两次；`flags_from_assessments` 内重复调用 `validate_assessments` | `app/agent/nodes.py`、`app/agent/rules.py` | |
| 2.3 | 共享修复循环 | `JsonLLM.generate` 与 `FakeLLM.generate` 的 repair 循环抽成一个函数；删除循环后不可达的 `raise` | `app/llm/client.py`、`app/llm/fake.py` | |
| 2.4 | 复用已有判断 | `validate_assessments` 改用 `Question.accepts`；引文校验统一用 `validate_unit_quote`；`render_reason` 删除不可达的 `invalid_rubric` 分支 | `app/agent/rules.py`、`app/agent/nodes.py` | |
| 2.5 | 集中常量与正则 | claim ID 正则（3 处）、标识符模式（6 处）、数字正则（2 处）、标题正则（2 处）各收敛为一处 | `app/schemas.py`、`app/segment.py`、`app/matching.py`、`eval/metrics.py`、`app/tools/` | |
| 2.6 | 删除死代码 | `source_targets`、`evidence_candidates`、`_validate_comparison`、`verify_dispatch` 空节点、合并 `after_extract`/`after_evidence` | `app/segment.py`、`app/tools/cues.py`、`eval/run.py`、`app/agent/graph.py` | |
| 2.7 | 拆分 nodes.py | 提示词、校验、聚合、落盘分到独立模块；`aggregate_node` 拆成四个函数 | `app/agent/` | |
| 2.8 | 状态类型化 | `dimension_results`、`verification_batches` 改为 Pydantic 模型；`Send` 载荷与父图状态分开定义；统一 `dimension`/`dimension_id` | `app/agent/state.py`、`app/agent/nodes.py` | |
| 2.9 | 检测器注册表 | `run_detectors` 的 if/elif 改为字典，未知检测器显式报错 | `app/tools/detectors.py` | |
| 2.10 | 错误可诊断 | 节点 `except Exception` 保留完整堆栈到本地日志（不进记录、不进响应） | `app/agent/nodes.py` | |
| 2.11 | 引入 linter/formatter | 配置 ruff，行宽限制，消除 `dict()`/`{}` 混用和 150+ 字符的行 | `pyproject.toml` | |
| 2.12 | 系统提示词瘦身 | 每次调用都带四个阶段的全部规则，与 payload 的 `instructions` 重复 | `app/llm/client.py` | |
| 2.13 | `resolve_target` 不重复切分 | 评测循环里每项都重新切分整份报告 | `app/segment.py`、`eval/run.py` | |
| 2.14 | 去掉 `rubric_id`/`analysis_type` 二选一 | 加载时强制相等，只保留一个 | `app/core/rubrics.py`、`app/schemas.py` | |

## P3 解析与评测

| # | 事项 | 做什么 | 涉及 | 状态 |
|---|---|---|---|---|
| 3.1 | 检测器误报 | 年份区间、同句无关百分比、跨单元口径冲突；先收集真实文档上的误报样本再改规则 | `app/tools/detectors.py`、`tests/test_detectors.py` | 需样本 |
| 3.2 | 解析质量样本集 | 用 3–5 份真实格式的英文报告建立解析回归样本（标题层级、表格、脚注、页眉页脚残留） | `tests/`、`data/` | 内容需用户提供 |
| 3.3 | 页眉页脚与参考文献 | MinerU 输出可能含页码、页眉、参考文献列表；这些会被当作句子送审 | `app/parsing.py` | |
| 3.4 | 合成报告与答案 | 新的英文 Markdown 合成报告，埋入错误及答案由用户决定；答案批准流程 | `data/synthetic/` | 内容需用户决定 |
| 3.5 | 真实评测基线 | 有答案后用 `--live --compare` 跑 specific/vague × 工具开关，形成第一份有 provenance 的指标 | `eval/` | 依赖 3.4、0.1 |
| 3.6 | 记录目录治理 | `records/` 现在混着假模型和真实记录；按 provenance 分目录或加清理命令 | `app/agent/nodes.py`、`app/api/documents.py` | |
| 3.7 | 页面：切分预览与审阅联动 | 预览里点击单元跳到原文；审阅结果里显示模型看到的标注文本 | `app/static/index.html` | |
| 3.8 | 演示与生产边界 | 页面无认证、服务只绑本机；若要给客户远程访问需另立方案，不在当前范围 | README | 明确不做 |

## 明确不做

- 模型直接产出 flag、severity 或报告结论（破坏可追溯与一致性度量）
- 每个检测器命中单独调一次模型「重新评价」（1.3 的复核覆盖已足够）
- 由模型自行决定何时中断交人工（触发条件必须是结构性的）
- 中文切分（客户为美国本土公司）
- 静默截断超长文档
