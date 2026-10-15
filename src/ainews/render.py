from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

CATEGORY_LABELS = {
    "models": "前沿模型与研究", "agents": "Agent、AI Coding 与技术工程", "business": "AI 产品与公司动态",
    "opensource": "GitHub 热门与优秀开源项目（不限 AI）", "applications": "AI 教育与垂直应用",
    "infrastructure": "基础设施、研究与政策",
}


class Renderer:
    def __init__(self, template_dir):
        self.env = Environment(loader=FileSystemLoader(str(template_dir)), autoescape=select_autoescape(["html"]),
                               undefined=StrictUndefined, trim_blocks=True, lstrip_blocks=True)
        self.env.filters["localtime"] = lambda value, timezone="Asia/Singapore": (
            value.astimezone(ZoneInfo(timezone)).strftime("%Y-%m-%d %H:%M") if value else "未知")

    def report(self, report):
        return self.env.get_template("report.html").render(report=report, labels=CATEGORY_LABELS,
                                                          items=report.all_items)

    def index(self, reports):
        entries = [{"date": day, **record} for day, record in sorted(reports.items(), reverse=True)]
        return self.env.get_template("index.html").render(entries=entries)
