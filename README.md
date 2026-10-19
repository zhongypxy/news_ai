# 全球 AI 每日情报系统 · V2

每天由 **ChatGPT Work 定时任务**采集和研究全球 AI 新闻，本地程序核验来源、校验数据和去重，生成可离线阅读的 HTML 与 JSON，保存至本地并归档到 GitHub。**默认模式无需 OpenAI API Key**。GitHub Actions + OpenAI API 路线保留为可选模式。

核心提示词是用户提供的完整 `prompts/daily_research.md`。RSS、JSON API、官方发布页和搜索查询全部在 `config/sources.yaml` 配置。Work 模型由应用选择，程序无法读取其 token/订阅消耗，日报明确标未知；可选 API 模式的模型名称从配置读取，一般候选 `gpt-5.4-mini`，重大事件 `gpt-5.4`。

V2.2每天额外逐一检查 **AIHOT、赛博禅心、机器之心、新智元、量子位**，以及 OpenAI、Anthropic/Claude、Grok 官方与业内人士的 X 动态。账号、原始身份依据和访问限制见 [账号来源清单](docs/source-watchlist.md)。两个专用搜索组由现有 Work 任务执行，无需 X API Key；保留发现链接并追溯原文，未核实爆料不作为事实新闻。

## 当前状态与首次启用

项目仓库：<https://github.com/zhongypxy/news_ai>，本机目录 `D:\code\AInewsV2`。沿用并升级原“每日 AI 新闻本地归档”Work 任务，每天 **09:00（UTC+8）**运行，另有 **10:17** Work 兜底检查。运行步骤详见 [Work 执行手册](docs/work-mode.md)。只发布真实研究且通过全部质量门的日报。

日常使用：保持电脑开机、联网和 Work/Codex 应用运行；打开本地 `index.html` 查看最新与历史日报。手动生成时在此 Work 聊天说“按 V2 生成今天 AI 日报并归档”。任务每天成功后将报告、JSON 与历史状态推送到仓库。原旧目录的内容不合并到 V2 事件历史，首次报告会注明无跨期记录。

## 可选 OpenAI API 云端模式

当前无需配置以下选项。用户曾配置的 secret 返回 AuthenticationError，尚未完成付费 API 端到端实测。`.github/workflows/daily.yml` 使用 `ENABLE_OPENAI_API` 仓库变量保护，默认跳过，不继续触发无效 API 调用。若以后启用：

1. 在仓库 Settings → Secrets and variables → Actions → New repository secret 添加 `OPENAI_API_KEY`。不要把 Key 发到聊天、写进配置或提交文件。
2. 添加 repository variable `ENABLE_OPENAI_API=true`；停用 Work 主任务/兜底，避免两种模式争抢同一天。确认 Actions 写权限及分支保护允许归档。
3. 打开 Actions → **Global AI Daily (optional API mode)** → Run workflow，日期留空。成功后会出现 `reports/YYYY-MM-DD.html`、`data/YYYY-MM-DD.json` 和更新的首页。
4. 在 Windows 执行一次 `scripts/sync-windows.ps1`，随后直接打开本地 `index.html`。

GitHub 云端运行需要 OpenAI API 账户及可用额度；ChatGPT 订阅不会自动提供 API Key。第一次真实运行之前，API 模型访问、Web Search/JSON Schema 联合调用和端到端新闻质量仍需实测。

## 目录

```text
config/       来源、模型、质量阈值、预算和报告 JSON Schema
prompts/      用户完整 V2 研究编辑提示词
src/ainews/   采集、网络、模型调用、编辑、核验、事件状态、渲染及发布
templates/    独立 HTML 模板、内嵌样式与交互
reports/      YYYY-MM-DD.html 日报（生成后不可直接覆盖）
data/         YYYY-MM-DD.json 结构化结果
state/        events.json 事件和报告完整性记录
logs/         运行诊断、token/费用和失败记录（忽略，不进 Git）
scripts/      Windows 自动同步与计划任务安装
tests/        不调用付费 API 的单元和离线集成测试
index.html    最新及历史日报首页
```

## 本地安装（Python 3.11+）

```powershell
git clone https://github.com/zhongypxy/news_ai.git
cd news_ai
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
.\.venv\Scripts\python.exe -m pip install --no-deps -e .
.\.venv\Scripts\python.exe -m ainews.cli check
```

不必激活虚拟环境。GitHub 工作流使用锁定的依赖版本。更改依赖后重新安装并更新 `requirements.lock`，再在 Windows 和 Linux CI 上测试。

仅可选 API 模式需要本地 Key；设置时用隐藏输入，避免进入 PowerShell 历史：

```powershell
$taskKey = Read-Host 'OpenAI API Key' -AsSecureString
$env:OPENAI_API_KEY = [System.Net.NetworkCredential]::new('', $taskKey).Password
.\.venv\Scripts\python.exe -m ainews.cli run
Remove-Item Env:OPENAI_API_KEY
```

程序只读取环境变量，不自动加载 `.env`，避免凭据写入文件。GitHub Actions 从 repository secret 注入凭据，日志不输出请求头、Key、API 原始错误文本或带凭据 URL。

## 手动运行与真实采集检查

```powershell
# 检查配置和归档完整性，并更新历史首页；不调用付费 API
.\.venv\Scripts\python.exe -m ainews.cli check

# Work 默认模式：公开采集并生成研究上下文（后续由 Work 搜索和编辑）
.\.venv\Scripts\python.exe -m ainews.cli work-prepare
# 完整步骤：docs/work-mode.md；输入结构：config/work.schema.json

# 真实公开入口探测；不使用 Web Search、不编辑新闻、不生成日报
.\.venv\Scripts\python.exe -m ainews.cli collect
.\.venv\Scripts\python.exe -m ainews.cli collect --source openai

# 可选 API 完整日报，需要 Key；已成功的日期重跑不调用 API
.\.venv\Scripts\python.exe -m ainews.cli run

# 历史补报：该日 09:00 UTC+8 为截止；不能在已有新日报之前倒插
.\.venv\Scripts\python.exe -m ainews.cli run --date 2026-10-13

# 导出报告 JSON Schema
.\.venv\Scripts\python.exe -m ainews.cli schema
```

从其他目录运行时把项目位置放在子命令之前：`python -m ainews.cli --root D:\code\AInewsV2 check`。缺失 Key 返回非零退出码并保存诊断。`collect` 的成功只说明至少一个公开入口可用，绝不表示完成了六领域检索或生成了合格日报。

## 时间窗口与三轮研究

- 每天 **Asia/Singapore 09:00** 主执行，**10:17** 同日兜底，均为 UTC+8。
- 主窗口是“上次成功报告的截止时刻（含）→本次运行时刻（不含）”，首次为最近 24 小时；兜底成功后，第二天从兜底的实际截止接续。
- 补漏窗口为最近 72 小时。主窗口长于 72 小时时扩大采集资格范围，并明确记录历史入口保留长度导致的漏报风险。
- 模型收到最近 30 天事件上下文。更老事件的 URL、事件别名和进展指纹仍在状态中用于程序去重。
- 第一轮分别检索模型、Agent、开源、商业、教育/垂直、基础设施/政策；第三轮使用 watchlist、候选概况和历史再次检索重要遗漏。第二轮是入选重大事件的原始证据读取、再次编辑与事实支持核验（Work 当前会话；API 可选模式为 GPT-5.4）。
- 原文发布时间、实际发生时间、采集时间、生成时间分开保存。原文时间不能用搜索索引时间、HTTP Date 或 updated time 替代。
- 只有日期的来源按“来源当地整天”的不确定区间处理；跨越窗口边界时拒绝，不假定具体发表时分。事件发生时间未知为 `null`。

本地 Work 运行要求电脑开机、联网、应用运行且额度可用；休眠或离线可能漏执行。[官方 Work 调度说明](https://learn.chatgpt.com/docs/automations)。可选云端模式的 GitHub 可能延迟或漏掉定时任务，09:00 是计划触发时间，不能保证在 09:00 已读到日报。工作流只在默认分支定时执行；公开仓库长时间无活动可能被 GitHub 停用调度。系统正常每天提交日报时会持续有活动。

## 质量门与事实边界

采集入口成功比例、六领域有效实时检索、第三轮补漏、原文可读性、可验证候选数量和地区覆盖全部检查。默认至少 5 条原文核验候选，北美/亚洲/欧洲均有来源覆盖，原文核验失败率不超过 60%。某个来源失效不等于该领域没有新闻；失败记录进入日报覆盖表或失败诊断。

Web Search 的 URL 必须来自实际搜索结果、打开的网页或 API 响应出处，再读取原网页核对发布时间。Work 模式保存实际查询和结果链接，不会由程序声称代替 Work 执行了搜索。模型仅引用程序候选 ID；程序把 ID 转为已校验链接，拒绝编造的来源。P0/P1 原始证据需再次编辑和核验（Work 进行明确事实审查；API 模式由 GPT-5.4 执行）；重大事实核验失败会终止整期，避免静默漏报。其他不可靠新闻可以拒绝。

AI 不执行外部文章、README 或工具结果中的指令；系统指令与不可信 JSON 输入分开，无需把任何 Key 提供给研究编辑；Work 只使用本任务授权工具。HTML 自动转义文本，不能通过文章注入 HTML/JS。采集不会绕过登录、付费墙、robots 或拒绝访问。只在内存中读取有限网页文本，不归档新闻全文；最终保存编辑摘要、必要元数据、链接与事件进展。

`FACT` 表示经过原始来源读取与 AI 事实支持检查；`ANALYSIS` 为推断。重复转载不会算成独立证据。单一原始来源的重大事件明确提示交叉核验限制。程序不能证明报道中每个事实绝对正确，模型的事实支持与语义事件匹配仍会出错，重要决策应打开原始来源复核。

## 去重、发布与异常恢复

URL 去除跟踪参数、片段和重复路径尾斜线，保留有意义查询参数。事件使用稳定身份、别名、原始 URL 和进展指纹匹配。已报道事件再次出现，必须有明确的新事实、不同进展指纹、新的证据和核验支持；标题变化/换媒体转载不能自动成为新事件。

最终发布使用操作系统互斥锁和 `state/.pending.json` 可恢复事务。日报/JSON 文件独占创建；报告清单保存 SHA-256。重跑检查清单、文件散列和 Schema，成功直接跳过。文件损坏或无登记的历史文件会报错，绝不偷偷覆盖。

本机中断后重跑会完成相同待发布事务。云 runner 的磁盘是临时的，失败 artifact 保存 `logs/` 及 pending transaction；若只有生成失败，下一次重新生成即可；若推送失败，检查远端是否已成功保存日报，避免再次付费。必要时下载 artifact，在同一仓库副本中恢复 `state/.pending.json` 后重跑。不要把旧事务放进包含更新日报的状态中。

自动提交仅发生在整个生成命令成功后；只提交 `reports/`、`data/`、`state/events.json` 和 `index.html`。不提交失败日志或原始候选正文。分支推送冲突时工作流失败，保留诊断，不强推。

## 超时、重试与费用（API 部分仅适用于可选模式）

Work 不向程序提供 token/订阅消耗与确定模型，日志和页面明确显示未知，不能声称免费或零消耗。Work 不调用付费 API，但消耗应用订阅额度。HTTP 默认 20 秒/次，OpenAI 默认 180 秒/次，最多 3 次；永久性 HTTP/API 错误不会反复重试。GitHub 作业总上限 40 分钟。模型拒绝、截断和 JSON 错误均失败并记录已产生费用。

记录模型、用途、response ID、输入/输出/缓存 token、Web Search 次数及估算 USD。默认每期估算预算 **3 USD**，每次尝试先预留保守估算，返回后用 usage 替换；超时费用未知时保留预留，不按零费用继续无限重试。每次搜索最多 8 次工具调用，预留包含工具上下文输入估算。配置单价不是实时账单；预算是程序估算门，不是 OpenAI 账户的硬扣费上限。另在 OpenAI 项目设置额度和告警。

## GitHub 全领域项目雷达与同日修订

V2.1 的开源栏目覆盖 AI 之外的开发工具、逆向与安全、系统、数据库、自托管等项目。Work 每次分别尝试 GitHub Trending 日、周、月榜，结合中英文搜索、公开 API 与正式 Release 判断近期变化和工程价值；榜单不可用会注明限制。没有连续快照时，不编写 Star 增长数字。REA 的官方 Release 已加入配置，关注清单不限制其它项目进入候选池。

普通定时运行保持同日幂等。用户明确要求当天重新研究时，使用独立工作目录及上一期历史基线执行完整 Work 流程，再以 `YYYY-MM-DD-r2.html/json` 等独立文件归档；原版保持不变。首页链接最新修订与原版，`state/events.json` 的 `latest_revision` 保存修订路径、截止时间和散列。完整性检查同时验证修订文件，下一期从验证后的最新截止时间继续。

## Windows 自动同步

```powershell
# 手动同步；使用已登录的 GitHub/Git 凭据
powershell.exe -NoProfile -File .\scripts\sync-windows.ps1

# 安装当前用户计划任务（UTC+8）
powershell.exe -NoProfile -File .\scripts\install-sync-task.ps1

# 查看或移除
Get-ScheduledTask -TaskName GlobalAI-Daily-Sync
Unregister-ScheduledTask -TaskName GlobalAI-Daily-Sync -Confirm:$false
```

计划任务每天本机 **10:00 和 11:00** 同步，并在登录时补同步；电脑需联网且用户已登录。错过时间会在可用时补执行。后台窗口隐藏，不保存密码。安装器检查 Windows 为无夏令时的 UTC+8，已有同名任务时拒绝覆盖。

同步先检查本地工作区和 main 分支，只用 `git pull --ff-only`，本地修改或分支分歧时停止；不会删除或重置你的改动。同步日志为 `logs/windows-sync.log`。开发期间先保存并提交修改再同步。HTML 使用 `file://` 可以直接打开：没有 CDN、外部脚本或本地 fetch；原文链接联网访问。

## 测试

```powershell
.\.venv\Scripts\python.exe -m pytest --cov=ainews --cov-report=term-missing
.\.venv\Scripts\python.exe -m ruff check src tests
```

测试覆盖 RSS/API/页面、压缩和安全重定向、Schema、日期边界/时区、跨期去重/进展、源失败、编辑来源 ID、重大事实核验失败、token/费用/错误脱敏、HTML 转义与离线依赖、历史保护、发布中断恢复、互斥锁、完整离线管道与重跑、工作流和同步脚本。测试替身只存在于 `tests/`，不会作为真实日报输入。CI 在 Windows/Linux 运行这些测试，不使用 API Key。

## 故障排查

| 现象 | 处理 |
| --- | --- |
| Work 定时未执行 | 检查电脑开机、联网、应用运行、任务状态和额度；可在本聊天手动补报 |
| Work 研究导入失败 | 查看 work-* 目录的 diagnostics.json；核对结构、时间和原文，不降低质量门 |
| 可选 API 模式缺少 OPENAI_API_KEY | 配置 repository secret；Work 默认模式不需要 Key |
| API 401/403/模型不可用 | 检查 Key、API 项目权限和所选模型访问 |
| API 超时/429 | 查看脱敏诊断及额度；有限重试后次日或兜底补跑 |
| 某入口 403/404/410 或选择器不匹配 | 更新 sources.yaml；保留失败记录，不绕过访问限制 |
| 搜索出处或日期无法核实 | 更换可访问的官方证据；不降低为“搜索结果即事实” |
| 缺少六领域、地区或候选数 | 查看来源状态；扩大可靠入口和配置 watchlist，而非造数据 |
| 预算超出 | 查看 usage，调整配置模型/批次/输出上限/预算及账户额度 |
| 历史完整性失败 | 从 Git 历史恢复相关文件；禁止直接覆盖旧日报 |
| Git 推送权限/保护失败 | 检查 Actions 写权限、默认分支和保护策略；不强推 |
| Windows 同步失败 | 保存本地修改，检查 main、Git 凭据、网络和同步日志 |

Work 研究与诊断位于 `logs/work-日期-编号/`。可选 API 诊断位于 `logs/YYYY-MM-DD-时间-编号/diagnostics.json` 与 `run.log`，启用后的 GitHub 作业保留 14 天 artifact。依赖安装或强制终止时可能尚无应用日志，查看步骤日志。

## 官方接口依据

- [OpenAI Responses Web Search](https://developers.openai.com/api/docs/guides/tools-web-search)
- [OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)
- [GPT-5.4 Mini](https://developers.openai.com/api/docs/models/gpt-5.4-mini)、[GPT-5.4](https://developers.openai.com/api/docs/models/gpt-5.4)
- [GitHub timezone schedule 及调度限制](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows)

验证情况和未验证集成详见 `docs/validation.md`。
