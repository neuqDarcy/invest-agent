import hashlib
import os
from app.parsers.pdf_parser import ParsedDocument, parse_pdf
from app.agents.profitability_agent import ProfitabilityAgent
from app.agents.base_agent import AgentResult
from app.core.config import settings
from dataclasses import dataclass


@dataclass
class AnalysisReport:
    file_name: str
    results: list[AgentResult]
    from_cache: bool = False

    def to_markdown(self) -> str:
        lines = [f"# 基本面分析报告\n", f"**文件：** {self.file_name}\n"]
        for r in self.results:
            score_str = f"（评分：{'⭐' * r.score}）" if r.score else ""
            lines.append(f"## {r.dimension} {score_str}\n")
            lines.append(f"**核心结论：** {r.summary}\n")
            lines.append(f"{r.details}\n")
            lines.append("---\n")
        return "\n".join(lines)


# 注册所有分析 Agent
AGENTS = [
    ProfitabilityAgent(),
    # 后续添加：SolvencyAgent, GrowthAgent, CashFlowAgent, RiskAgent
]


def _file_md5(file_path: str) -> str:
    h = hashlib.md5()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _cache_path(md5: str) -> str:
    os.makedirs(settings.output_dir, exist_ok=True)
    return os.path.join(settings.output_dir, f"{md5}.md")


def run_analysis(file_path: str) -> tuple[AnalysisReport | None, str]:
    """
    返回 (report, markdown)。
    命中缓存时 report.from_cache=True，直接返回历史报告，不调用 LLM。
    """
    md5 = _file_md5(file_path)
    cache_file = _cache_path(md5)

    if os.path.exists(cache_file):
        with open(cache_file, "r", encoding="utf-8") as f:
            markdown = f.read()
        report = AnalysisReport(
            file_name=os.path.basename(file_path),
            results=[],
            from_cache=True,
        )
        return report, markdown

    doc = parse_pdf(file_path)
    results = [agent.analyze(doc) for agent in AGENTS]
    report = AnalysisReport(file_name=doc.file_name, results=results, from_cache=False)
    markdown = report.to_markdown()

    with open(cache_file, "w", encoding="utf-8") as f:
        f.write(markdown)

    return report, markdown
