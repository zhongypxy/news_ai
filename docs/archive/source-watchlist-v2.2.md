# 每日账号来源清单（V2.2）

用户于2026-10-19要求加入国内大账号及海外AI业内人士。名单是每日必查起点，不是内容入选配额，也不是封闭白名单。配置入口在 `config/sources.yaml`，执行与事实边界在 `prompts/daily_research.md`。优先采用现有Work实时搜索扩充两个独立搜索组，避免未经授权的社交平台抓取代理和另一套付费API。现有发布程序会要求两个组与其他启用搜索组一起出现在研究包中；账号级记录由Work按本手册实际完成。

## 国内逐一检查

| 名称 | 入口/检索身份 | 处理方式 |
| --- | --- | --- |
| ai-hot / AIHOT | https://aihot.news/ ，[官方接入说明](https://aihot.news/agent) | 当前将用户的ai-hot理解为AIHOT；精选、全部动态、热点与原文链接用于发现，不把聚合时间当首发。旧域名aihot.virxact.com不是新配置入口。 |
| 赛博禅心 | 微信同名公众号；精确搜索“赛博禅心”，必要时查作者公开转载 | 尚未核得本人官网确认的固定X账号，不猜handle、不与其他同名Cyber Zen账号合并；第三方目录只提供线索，每篇核对署名和原文。 |
| 机器之心 | https://www.jiqizhixin.com/ 及同名公众号 | 公开文章、原创访谈和技术解读；PRO或登录受限内容只记录可公开核实信息。 |
| 新智元 | https://www.aiera.com.cn/ 、[关于页](https://www.aiera.com.cn/about.html)及同名公众号 | 核对文章署名和原始引用，不将营销展示页的动态数字当新闻发布。 |
| 量子位 | https://www.qbitai.com/ 、[关于页](https://www.qbitai.com/关于我们)及同名公众号 | 同一稿在多个平台出现时合并；具体文章日期和事件日期分开。 |

## X逐一检查

| 方向 | 账号主页 | 身份依据与边界 |
| --- | --- | --- |
| OpenAI官方 | https://x.com/OpenAI / https://x.com/OpenAIDevs / https://x.com/ChatGPTapp / https://x.com/OpenAINewsroom | [OpenAI官方账号列表](https://help.openai.com/en/articles/11725090-verifying-communications-from-openai)。 |
| Sam Altman | https://x.com/sama | 核对主页与[本人博客](https://blog.samaltman.com/)关联；个人观点、预告不能写成正式产品交付。 |
| Greg Brockman | https://x.com/gdb | [本人主页](https://gregbrockman.com/)直接链接@gdb。 |
| Anthropic / Claude | https://x.com/AnthropicAI / https://x.com/ClaudeAI / https://x.com/ClaudeDevs | 账号须与[Anthropic](https://www.anthropic.com/)及[Claude开发者博客](https://claude.dev/)交叉核对；注意Anthropic_ai等相似拼写不是同一账号。每篇仍须验证原帖和身份。 |
| Boris Cherny | https://x.com/bcherny | [本人介绍页](https://borischerny.com/about/)直接出链，介绍Claude Code工作；不能把他的个人用法当所有用户默认能力。 |
| Grok及公司官方 | https://x.com/SpaceXSI / https://x.com/grok | 2026-10-19读取的[x.ai官网](https://x.ai/)直接出链这两个账号；xAI/xai为旧称线索，后续以官网最新出链为准。@grok自动回复和AI生成的X趋势摘要不能作为官方公告。 |
| Elon Musk | https://x.com/elonmusk | 核对本人主页及原帖关联；只筛AI/Grok相关内容，预告、玩笑与已交付分开。 |
| Andrej Karpathy | https://x.com/karpathy | [本人主页](https://karpathy.ai/)直接链接其Twitter账号；按研究者/教育者观点归因，不自动视为任何实验室现任官方发言。 |

不凭记忆给Dario Amodei等人猜handle；先验证本人公开账号或用本人博客、官方采访原文补充。发现其他高价值研究者、产品负责人、创业者可扩展，核对当时任职关系。

## 每次执行与日志

1. 读取本表与配置，对国内每家、X每个handle逐一查询；查询加上bundle实际日期，不把搜索引擎“最近抓取”当原帖时间。
2. 每个账号记录名称/handle、主页、实际查询、UTC检查时间、原帖/文章URL、可核实发布时间、是否读到正文及范围、核验/入选结果。明细保存在本次 `logs/work-.../account-checks.json`，不存文章全文。
3. `status`以明确文本区分：取得内容、检索完成但无合格新增、访问/时间核验失败、身份待确认。没有搜索结果只能说明该检索未发现，不能证明账号没发帖。日志只在真实执行后填写，不预置成功。
4. `search_checks`按启用搜索源共九项记录，实际查询和结果URL必须可回溯；个别账号失败写进组级note及limitations，整组搜索不可用标failed。
5. AIHOT、媒体、公众号是发现入口；追到原始公告/论文/仓库/完整线程。入选时保留有价值的发现文章及一手引用，合并重复事件与翻译稿，记录转载链。
6. 重要观点可按“某人公开提出/报告了什么”入选，正文归因并区分分析；亲测注明环境和未独立复测。无法核实的爆料只留研究日志，不进入FACT日报。
7. 单篇正文、时间或身份不合格时保留诊断，继续找其他合法公开原始来源。不得用截图、搜索摘要、聚合收录时间或自动回答补造证据；不得绕过访问限制。

## 维护与验收

配置变动后运行 `python -m ainews.cli check` 和 `python -m pytest -q`，核对全部启用搜索源能进入Work研究包及验收。此次只扩充配置和编辑规则，不修改报告Schema或程序质量门；今天及历史日报不改写。两套已有定时任务均读取本手册和最新配置，下一次实际研究需记录逐账号结果，不能将名单身份核对冒充当天完整采集。
