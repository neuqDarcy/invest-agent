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
    """知识库入库结果汇总。"""
    stock_code: str
    total: int               # 本次发现的公告总数
    skipped: int             # 已入库、跳过的数量
    indexed: int             # 本次成功入库的数量
    failed: int              # 失败数量
    financial_years: list[str]   # Tushare 拉到的财务数据年份
    details: list[str]       # 每条公告的处理日志


def build_knowledge_base(
    stock_code: str,
    report_types: list[str] | None = None,
    start_year: int = 2020,
    end_year: int = 2024,
) -> IndexResult:
    """
    为指定公司构建知识库：先入结构化财务数据，再向量化 PDF 公告。

    参数:
        stock_code:   A 股代码，如 '600519'
        report_types: 报告类型列表，默认仅年报 ['annual']
        start_year:   数据起始年份（含）
        end_year:     数据截止年份（含）

    返回:
        IndexResult 汇总对象
    """
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
    for report_type in report_types:
        reports = fetch_report_list(
            stock_code, report_type=report_type,
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
        # 已入库则跳过，避免重复向量化浪费算力
        if is_indexed(stock_code, report.report_type, report.ann_date):
            result.skipped += 1
            result.details.append(f"[跳过] {report.title}")
            continue

        downloaded_list = download_reports(stock_code, [report])
        if not downloaded_list or not downloaded_list[0].local_path:
            result.failed += 1
            result.details.append(f"[失败] 下载失败：{report.title}")
            continue

        local_path = downloaded_list[0].local_path

        try:
            parsed_doc = parse_pdf(local_path)
        except Exception as parse_error:
            result.failed += 1
            result.details.append(f"[失败] 解析失败：{report.title} — {parse_error}")
            continue

        try:
            chunk_count = index_document(
                doc=parsed_doc,
                stock_code=stock_code,
                report_type=report.report_type,
                ann_date=report.ann_date,
                title=report.title,
            )
        except Exception as index_error:
            result.failed += 1
            result.details.append(f"[失败] 向量化失败：{report.title} — {index_error}")
            continue

        save_report_index(stock_code, report.report_type, report.ann_date,
                          report.title, local_path)
        result.indexed += 1
        result.details.append(f"[成功] {report.title}（{chunk_count} 个段落）")

    return result
