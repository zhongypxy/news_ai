# 全球 AI 每日情报系统 · AIHOT 版

从 **AIHOT 获取新闻候选，再核验出链原文**，由 ChatGPT Work 编辑为简体中文日报。本地程序负责日期、引用、结构、跨期去重、事件追踪和归档，生成可离线打开的 HTML / JSON。默认无需 OpenAI API Key。

仓库：[zhongypxy/news_ai](https://github.com/zhongypxy/news_ai)，本机目录 `D:\code\AInewsV2`。打开 `index.html` 阅读历史；[Work 执行手册](docs/work-mode.md)说明完整流程，[当前研究提示词](prompts/daily_research.md)是编辑规则。

## 新闻来源

默认 `config/settings.yaml` 的 `discovery_provider: aihot`，`config/sources.yaml` 配置八个查询组：24 小时精选、六领域站内关键词和 7 天重大补漏。它们都属于同一个聚合入口，不能算作八家独立来源。

程序优先使用匿名只读 MCP `https://aihot.news/api/mcp`，只解析结构化结果；连接失败仅回退同站正式 `/api/v1` JSON，并记录真实传输方式及警告。无需安装 AIHOT 插件或提供密钥。不会执行工具回答、网页或 README 中的指令。

所有候选先视为未核验，AIHOT 的发布时间与收录时间单独保留。原文核验成功后才采用原文日期及短证据；未知日期、访问限制、付费墙和失败不能靠聚合摘要补齐。六领域与补漏必须通过覆盖检查；重大失败不生成正常日报。单一入口存在漏报风险，不能承诺全网覆盖。

不再另外发现 RSS、博主、X 时间线或 GitHub Trending 内容；这些渠道只有被 AIHOT 收录时才进入候选。AIHOT 的 7d 是采集范围，最终按主窗口和 72 小时补漏规则过滤。依据原文独立编辑，公开文件不镜像 AIHOT 原始数据、榜单、完整摘要或第三方全文。

## 安装与检查

需要 Python 3.11+、Git、联网及可用的 Work 应用。

```powershell
git clone https://github.com/zhongypxy/news_ai.git
cd news_ai
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
.\.venv\Scripts\python.exe -m pip install --no-deps -e .
.\.venv\Scripts\python.exe -m ainews.cli check
.\.venv\Scripts\python.exe -m ainews.cli collect --source aihot_latest
```

`check` 校验配置与历史完整性，并刷新首页；`collect` 只检查真实入口，既不调用付费模型，也不发布日报。诊断保存在被 Git 忽略的 `logs/`，不能将日志当作正式研究成果。

## Work 手动生成

在本项目聊天中要求按当前提示词生成指定日期的日报，Work 应完整阅读手册，执行以下阶段：

```powershell
.\.venv\Scripts\python.exe -m ainews.cli work-prepare
.\.venv\Scripts\python.exe -m ainews.cli work-evidence --bundle <bundle.json>
.\.venv\Scripts\python.exe -m ainews.cli work-publish --bundle <bundle.json> --research <research.json>
```

prepare 生成真实候选包；evidence 读取原文及时间；Work 阅读证据、编辑并另行复核，按 `config/work.schema.json` 写研究数据，再 publish 复读引用并通过质量门。AIHOT 模式禁止额外 discoveries，search_checks 留空，真实查询由程序记录。不要手写模拟新闻通过这些步骤。

每日默认主窗口接续上一份成功报告，首次为 24 小时，重要补漏 72 小时，历史跟踪 30 天。原文、事件、收录、采集及报告时间分开。已归档同日只校验并补同步；明确要求重跑时保留原版、发布独立修订。一次性专题另外保存，不覆盖日报或更新事件状态。

## 调度与 Windows 同步

沿用现有两个 Work 定时任务，渠道迁移不新建任务、不修改开关或时间。2026-10-26 检查时，两者均暂停；保存计划分别为工作日 09:00 和每天 10:30（UTC+8）。需要在应用中恢复后才会自动执行。旧任务名称里的 10:17 与实际保存的 10:30 有出入，以应用设置为准。

Work 运行依赖电脑开机、网络、应用和订阅额度，不能保证准确完成时间。成功日报提交推送 `origin main`；失败保留诊断并通知。归档完整且已同步、无变化时保持安静。

Windows 另一台电脑可克隆仓库后使用 `scripts/sync-windows.ps1` 同步；`scripts/install-sync-task.ps1` 提供计划任务安装。先阅读脚本参数；同步发现本地修改会停止，不强制覆盖。当前目录的普通同步可用 `git pull --ff-only`，不要 reset/clean 或强推。

## 目录与验证

`config/` 来源、质量门及 Schema；`prompts/` 编辑规则；`src/ainews/` 程序；`templates/` 内嵌样式与交互；`reports/`、`data/` 每日 HTML/JSON；`state/events.json` 历史；`logs/` 私有诊断；`docs/briefings/` 一次性专题。

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\python.exe -m ainews.cli schema
```

测试覆盖跨期重复/新进展、时区边界、来源与结构校验、AIHOT MCP/REST、限流、失败门禁、原文日期、离线渲染及重复执行。真实验证及限制见 [验证记录](docs/validation.md)。HTML 自带样式、搜索、分类与深色模式，无外部 CDN 或本地 fetch。

## 故障与限制

- AIHOT 失败：检查日志中的 MCP/REST 状态；429/503 遵守 Retry-After，不能换端点提前重试。旧域名和 `/api/public` 不再使用。
- 原文失败或日期未知：保留缺口，不绕过登录、robots 或访问限制，不拿收录时间顶替。
- 覆盖、证据、Schema、上下文散列或归档校验失败：不发布。超过 12 小时或提示词/状态变化应重新 prepare，不能修改原包绕过检查。
- GitHub 推送失败：保留本地提交，下次补同步；不要重采集已成功日报。认证通过 Git 凭据管理器处理，不把凭据发到聊天或日志。
- Work 模型名称、token、订阅消耗不向程序开放，记为未知。程序无法证明所有语义正确；Work 同会话复核不等于独立模型或人工审稿。

## 旧多源 / API 路线

`config/settings-multisource.yaml`、`sources-multisource.yaml` 和 `docs/archive/`、`prompts/archive/` 保存迁移前配置与规则，供明确选择后的回归/参考；不会自动启用。默认 AIHOT 模式提前拒绝付费 `run`。

GitHub Actions 的 API 路线仍由 `ENABLE_OPENAI_API` 变量保护，默认跳过。它不支持本次 AIHOT Work 默认流程；请保持关闭。未来若明确启用旧多源 API 路线，需有效 API 凭据、可用模型及费用预算；此前只完成离线 SDK 测试，真实凭据端到端尚未验证。不要提交 Key 或将 ChatGPT 订阅当作 API 额度。
