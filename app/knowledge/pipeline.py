"""
知识库入库流水线：
1. 从 Tushare 拉取三张财务报表 → SQLite（结构化指标）
2. 从巨潮下载年报 PDF → Chroma（语义检索）
"""
from dataclasses import dataclass
from app.knowledge.downloader import fetch_report_list, download_reports, AnnReport
from app.knowledge.store import is_indexed, save_report_index, index_document
from app.knowledge.financial_fetcher import fetch_financials
from app.parsers.pdf_parser import parse_pdf


@dataclass
class IndexResult:
    stock_code: str
    total: int
    skipped: int
    indexed: int
    failed: int
    financial_years: list[str]   # Tushare 拉到的财务数据年份
    details: list[str]


def build_knowledge_base(
    stock_code: str,
    report_types: list[str] | None = None,
    start_year: int = 2020,
    end_year: int = 2024,
) -> IndexResult:
    if report_types is None:
        report_types = ["annual"]

    # ── Step 1：从 Tushare 拉取三张财务报表存 SQLite ──────────────────
    fin_result = fetch_financials(stock_code, start_year=start_year, end_year=end_year)
    fin_detail = (
        f"[财务数据] 从 Tushare 拉取 {fin_result['saved_count']} 条指标"
        f"，覆盖年份：{', '.join(fin_result['years'])}"
    )

    # ── Step 2：从巨潮下载 PDF → 向量化存 Chroma ─────────────────────
    all_reports: list[AnnReport] = []
    for rt in report_types:
        reports = fetch_report_list(
            stock_code, report_type=rt,
            start_year=start_year, end_year=end_year,
        )
        all_reports.extend(reports)

    result = IndexResult(
        stock_code=stock_code,
        total=len(all_reports),
        skipped=0, indexed=0, failed=0,
        financial_years=fin_result["years"],
        details=[fin_detail],
    )

    for report in all_reports:
        if is_indexed(stock_code, report.report_type, report.ann_date):
            result.skipped += 1
            result.details.append(f"[跳过] {report.title}")
            continue

        downloaded = download_reports(stock_code, [report])
        if not downloaded or not downloaded[0].local_path:
            result.failed += 1
            result.details.append(f"[失败] 下载失败：{report.title}")
            continue

        local_path = downloaded[0].local_path

        try:
            doc = parse_pdf(local_path)
        except Exception as e:
            result.failed += 1
            result.details.append(f"[失败] 解析失败：{report.title} — {e}")
            continue

        try:
            chunk_count = index_document(
                doc=doc,
                stock_code=stock_code,
                report_type=report.report_type,
                ann_date=report.ann_date,
                title=report.title,
            )
        except Exception as e:
            result.failed += 1
            result.details.append(f"[失败] 向量化失败：{report.title} — {e}")
            continue

        save_report_index(stock_code, report.report_type, report.ann_date,
                          report.title, local_path)
        result.indexed += 1
        result.details.append(f"[成功] {report.title}（{chunk_count} 个段落）")

    return result
