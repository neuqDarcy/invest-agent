"""
财务指标结构化提取：从 PDF 表格中提取三表核心指标存入 SQLite
"""
import re
from app.parsers.pdf_parser import ParsedDocument
from app.knowledge.store import _get_db

# 指标名称归一化映射（处理换行、空格、同义词）
METRIC_ALIASES = {
    "营业收入":         ["营业收入"],
    "营业成本":         ["营业成本"],
    "毛利润":           ["毛利润"],
    "净利润":           ["净利润", "归属于上市公司股东的净利润", "归属于母公司所有者的净利润"],
    "扣非净利润":       ["扣除非经常性损益的净利润", "归属于上市公司股东的扣除非经常性损益的净利润"],
    "销售费用":         ["销售费用"],
    "管理费用":         ["管理费用"],
    "研发费用":         ["研发费用", "研发投入合计"],
    "财务费用":         ["财务费用"],
    "经营活动现金流":   ["经营活动产生的现金流量净额"],
    "投资活动现金流":   ["投资活动产生的现金流量净额"],
    "筹资活动现金流":   ["筹资活动产生的现金流量净额"],
    "资产总计":         ["资产总计", "资产合计"],
    "负债合计":         ["负债合计", "负债总计"],
    "股东权益合计":     ["股东权益合计", "所有者权益合计", "归属于母公司所有者权益合计"],
    "货币资金":         ["货币资金"],
    "应收账款":         ["应收账款"],
    "存货":             ["存货"],
    "固定资产":         ["固定资产"],
}

# 反向索引：别名 → 标准名
_ALIAS_MAP: dict[str, str] = {}
for std, aliases in METRIC_ALIASES.items():
    for alias in aliases:
        _ALIAS_MAP[alias] = std


def _normalize_cell(cell) -> str:
    if cell is None:
        return ""
    return re.sub(r"\s+", "", str(cell))


def _parse_number(s: str) -> float | None:
    """解析财务数字，处理逗号、括号负数等格式"""
    s = re.sub(r"\s+|,", "", str(s))
    s = re.sub(r"，", "", s)
    # 括号表示负数：(1,234) → -1234
    if s.startswith("(") and s.endswith(")"):
        s = "-" + s[1:-1]
    try:
        return float(s)
    except ValueError:
        return None


def _extract_year_from_header(header_cells: list[str]) -> list[str]:
    """从表头行提取年份列表，如 ['2023年', '2022年', '2021年']"""
    years = []
    for cell in header_cells:
        m = re.search(r"(20\d{2})", str(cell))
        if m:
            years.append(m.group(1))
    return years


def extract_metrics(
    doc: ParsedDocument,
    stock_code: str,
    ann_date: str,
) -> dict[str, dict[str, float]]:
    """
    从 ParsedDocument 的表格中提取财务指标。
    返回 {year: {metric_name: value}}
    """
    results: dict[str, dict[str, float]] = {}

    for table in doc.tables:
        if not table or len(table) < 2:
            continue

        # 检查表格是否包含财务指标
        flat = " ".join(_normalize_cell(c) for row in table for c in row)
        if not any(alias in flat for alias in _ALIAS_MAP):
            continue

        # 尝试解析：第一列为指标名，后续列为数值（可能带年份表头）
        header = [_normalize_cell(c) for c in table[0]]
        years = _extract_year_from_header(header)

        for row in table[1:]:
            if not row:
                continue
            row_cells = [_normalize_cell(c) for c in row]
            if not row_cells:
                continue

            metric_raw = row_cells[0]
            std_name = _ALIAS_MAP.get(metric_raw)
            if not std_name:
                continue

            # 有年份表头：按列对应年份
            if years:
                for i, year in enumerate(years):
                    col_idx = i + 1
                    if col_idx < len(row_cells):
                        val = _parse_number(row_cells[col_idx])
                        # 过滤零值和明显异常（报告年份不应超过公告年份）
                        if val is not None and val != 0 and abs(val) > 1 and int(year) <= int(ann_date[:4]):
                            results.setdefault(year, {})[std_name] = val
            else:
                # 无年份表头：第二列为本期数，对应报告年份（公告年份-1）
                year = str(int(ann_date[:4]) - 1)
                if len(row_cells) >= 2:
                    val = _parse_number(row_cells[1])
                    if val is not None and abs(val) > 1:
                        results.setdefault(year, {})[std_name] = val

    return results


def save_metrics(
    stock_code: str,
    ann_date: str,
    metrics_by_year: dict[str, dict[str, float]],
):
    """将提取的指标存入 SQLite，ann_date 为报告年份的年末日期（如 2023-12-31）"""
    with _get_db() as conn:
        for year, metrics in metrics_by_year.items():
            period = f"{year}-12-31"   # 报告年份，非公告日期
            for metric_name, value in metrics.items():
                conn.execute("""
                    INSERT OR REPLACE INTO financial_metrics
                        (stock_code, ann_date, metric_name, value, unit)
                    VALUES (?, ?, ?, ?, ?)
                """, (stock_code, period, metric_name, value, "元"))
        conn.commit()


def get_metrics(
    stock_code: str,
    metric_names: list[str] | None = None,
    years: int = 3,
) -> list[dict]:
    """查询某公司近N年财务指标"""
    with _get_db() as conn:
        if metric_names:
            placeholders = ",".join("?" * len(metric_names))
            rows = conn.execute(f"""
                SELECT ann_date, metric_name, value, unit
                FROM financial_metrics
                WHERE stock_code=? AND metric_name IN ({placeholders})
                ORDER BY ann_date DESC
                LIMIT ?
            """, [stock_code] + metric_names + [years * len(metric_names)]).fetchall()
        else:
            rows = conn.execute("""
                SELECT ann_date, metric_name, value, unit
                FROM financial_metrics
                WHERE stock_code=?
                ORDER BY ann_date DESC
                LIMIT ?
            """, (stock_code, years * 20)).fetchall()
        return [dict(r) for r in rows]
