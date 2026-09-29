# 协作与审查决策

## 方案与分工

- 基线没有首次 Git 提交，原 Eris Basic 全部未跟踪；未找到 AGENTS.md。用户确认按本次说明与合成草稿继续。原代码在外部临时目录保留基线副本，未将 .env 内容用于协作。原有运行依赖版本未升级。
- 与桌面 Claude 普通 Chat 实际讨论：[Contract details and testing priorities](https://claude.ai/chat/6e2a9c2f-0e4e-4b5a-8d07-5fef69209579)。提供目录、关键代码与边界，没有用 Claude API/Code 替代。
- 采纳确定性段落目标、模型只选 ID、固定 scope、延迟配置、物理调用预算、单一审阅入口、双空 Jaccard 单列。保留用户要求的 confidence/thread_id；选择整 run 有效性，部分失败不计算正式指标。无效引文使运行失败，不静默丢弃。
- 三名子 Agent：review_pipeline 负责 agent/tools；evaluation 负责 eval/rubrics/data；api_tests 负责 api/main/tests。主负责人负责 schemas/core/llm、依赖、文档、集成及 Claude 沟通；没有递归创建 Agent。

## 独立审查范围

真正的新桌面 Claude Chat：[Charon 项目代码独立审查](https://claude.ai/chat/54285c94-46f6-46d8-bbd6-a2cb5e4336f0)，与方案讨论会话不同。

- v1：42 文件完整逐行快照，SHA256 `9a77884bfe80af3181c7ae0670e1bcb3dea343f5de5f40870314c64421c7db5e`。包含全部 app/eval/tests 源码、rubrics、data/synthetic、README、AGENTS、pyproject、uv.lock、.env.example、.gitignore、.python-version。
- Claude 确认 42/42 内容哈希一致，逐行阅读除 uv.lock 外的 41 文件；锁文件审查包名、版本、来源和依赖声明，没有逐项审计 wheel 哈希。完成 Python 语法、计算工具恶意输入、合成目标一致性检查；其环境不能安装锁定依赖，未运行完整 pytest/API/DAG/CLI。完整运行验证由主负责人在本机执行。
- 排除 .env/密钥、内部材料、虚拟环境/缓存、生成记录。本日志为避免前序意见影响独立审查而不放入源码快照；README 引用的日志确实存在。
- v2：43 文件，SHA256 `4a12d169ce5497eb30296b19feeba0eff4c0e484fe09babf82c7c6134e43b7ea`。同一审查会话收到完整新 manifest 和全部 16 个修改/新增文件全文；27 个未改文件沿用 v1，覆盖 schema→LLM→API/DAG→评测→测试上下游。Claude 已读完全部变更及受影响上下游，确认主要修复完成；发现前段失败时 unchecked_check_ids 需与成功零 claims 区分。

- v3（当前）：SHA256 `f876c8483dae22556ceb72bb6f1a0b5cca3a9169721b504c1b179e18940ee15b`。追加 4 文件全文修复上述条件及测试隔离；Claude 重建并核对 43/43 文件哈希，检查全部变更，30 个 Python 文件语法通过，最终回复“没有新的阻塞问题，本次审查可以结束”。其未运行完整 pytest，35 项通过来自本机。

## 意见处理

| 意见 | 处理与理由 |
| --- | --- |
| `/chat` 绕过离线开关，测试替换了整个被测入口 | 修复：两个接口共用 get_llm；删除独立 chat_with_kimi 工厂；真实模型边界也检查 offline；测试实际依赖和 SDK 边界。保留原有 /chat 接口兼容性。 |
| 零 claims 隐藏检查覆盖缺口；API 不显示 fake 来源 | 修复：响应和记录带 provenance/unchecked_check_ids；跳过无目标检查与空证据调用。complete 指执行完成，覆盖缺口单列；端到端评测仍能把抽取漏检计为漏项，不能因排除失败而提高召回。 |
| README 的 pytest 命令可能导入失败 | 本机复现后增加 pytest pythonpath；原命令现已通过。 |
| 未使用 prepare_input、重复摘要、重复校验 | 删除未使用入口及导出；共用 fixed_input_digest、validate_adjudications。保留调用前校验位置，避免付费后才发现人工输入错误。 |
| 关闭工具仍传许可、负载重复原文、顺序/草稿标记不清 | 修复 tools=[]；抽取只发 claim 候选，后段 claims 不重复全文；按原文位置排序；匹配项标明答案状态。 |
| 可在模型未配置时拒绝保存失败原文 | 暂不采用：当前保留失败记录便于定位，明确 failed 且 README 说明含原文。rubric 无法加载时缺少记录必要契约，属于执行前错误；不为统一形式新增存储层。 |
| 前段失败被误标为零 claims 覆盖缺口（v2 复核发现） | 修复：preparation_error 时该覆盖字段为空，真实失败由 status/error_code 表示；成功零 claims 仍披露未覆盖检查。补充失败评测断言，测试显式隔离 shell 的 offline 设置。 |
| 全部真实尝试失败仍被标成实测（并行复核发现） | 修复：必须既有实际调用又有有效运行才能 measured_synthetic，否则 not_measured；注入超时验证计数、预算与失败状态。 |

没有增加 provider/storage 注册中心、多层 service/repository 或标注平台。保留简单固定图、AST 计算器和现有聚合实现。

## 实际验证与边界

- `UV_CACHE_DIR=/tmp/charon-uv-cache uv sync --locked --offline`：成功；原有依赖版本无变更。
- `UV_CACHE_DIR=/tmp/charon-uv-cache uv run --locked pytest -q`：35 passed（0.58 秒）；另在 shell 显式 CHARON_OFFLINE=true 下同样 35 passed。FakeLLM 或注入 SDK，无模型网络调用。
- `uv run --locked python -m eval.run --mode fixed --runs 3 --compare --output outputs/fixed-comparison.json`：四组合完整，各 9 次 fake 调用。
- `uv run --locked python -m eval.run --mode end-to-end --runs 3 --compare --output outputs/e2e-comparison.json`：四组合完整，各 15 次 fake 调用。两条实际命令另指定 UV_CACHE_DIR 和临时 RECORDS_DIR；输出明确 offline_fake，草稿召回/待裁定错误占比为空。
- 当前版本本地 uvicorn 绑定 127.0.0.1:8765，显式 offline：`/review` 和 `/chat` 均 HTTP 200；报告 3/3 有效运行、11 次 fake 调用、3 flags，保存 JSON 与响应匹配、人工 pending。验证后关闭服务。
- 未执行真实连通性或模型质量验证；合成答案仍待用户审定。未推送、发布或修改用户 .env。

后续仅在实际需要时处理：新增非缺失类 section/document 检查的引文许可、第二套 rubric 参数、全角空白分段、预算内输出重试，以及付费评测中途故障的逐格汇总保存。上线前仍需鉴权/限流、数据保留治理、持久化可靠性与部署监测；本次均未实现。

## 后续清理（2026-09-26）

上文 v1–v3 是当时的审查记录，保持原样；其中提到的 `/chat` 验证结果仅代表当时状态。

- 按用户要求移除超出 Architecture B 审阅原型范围的内容：旧项目 Eris 遗留的 `POST /chat` 接口（`app/api/chat.py`、`ChatMessage`/`ChatResponse`、`Settings.prompt` 中的通用助手提示词、FakeLLM 的 chat 分支），以及 `frontend/` 模拟星空对话网页和 `docs/frontend-demo.md`。`.gitignore` 中仅为前端服务的条目一并删除。
- 原先借用 chat stage 的模型边界测试改用 `ExtractionOutput`，覆盖不变；`/chat` 的离线开关测试改为在 `/review` 上验证。`/review` 的 503 安全错误已有测试覆盖，故删除重复的 `/chat` 用例。
- README 和 AGENTS.md 同步去掉前端与 `/chat` 内容并理顺结构；未改动 rubric、合成数据、评测逻辑、LLM 调用参数、依赖或环境变量名。清理前的完整副本（不含 `.env`）保存在会话临时目录。
- 验证：`uv run --locked --offline pytest -q` 在默认环境与 `CHARON_OFFLINE=true` 下均为 34 passed。
