"""
财务计算层：所有数学计算在此完成，结果作为已计算事实传给 LLM。
LLM 只负责语言组织，不做任何数值运算，确保财务数字准确可靠。
"""
from collections import defaultdict


def _yoy(current_value: float, previous_value: float) -> float | None:
    """
    计算同比增速（Year-over-Year，%）。

    参数：
        current_value:  本期数值
        previous_value: 上期数值（分母，不能为零）

    返回：同比增速百分比，上期为零时返回 None。
    """
    if previous_value and previous_value != 0:
        return round((current_value - previous_value) / abs(previous_value) * 100, 2)
    return None


def _cagr(start_value: float, end_value: float, num_years: int) -> float | None:
    """
    计算复合年均增长率（Compound Annual Growth Rate，%）。

    参数：
        start_value: 起始期数值（必须为正数）
        end_value:   结束期数值
        num_years:   跨越年数（必须大于零）

    返回：CAGR 百分比，起始值非正或年数为零时返回 None。
    """
    if start_value and start_value > 0 and num_years > 0:
        return round(((end_value / start_value) ** (1 / num_years) - 1) * 100, 2)
    return None


def _cumulative_growth(start_value: float, end_value: float) -> float | None:
    """
    计算累计增幅（%）。

    参数：
        start_value: 起始期数值（不能为零）
        end_value:   结束期数值

    返回：累计增幅百分比，起始值为零时返回 None。
    """
    if start_value and start_value != 0:
        return round((end_value - start_value) / abs(start_value) * 100, 2)
    return None


def _margin(numerator: float | None, denominator: float | None) -> float | None:
    """
    计算利润率（%）= numerator / denominator * 100。

    参数：
        numerator:   分子（利润等）
        denominator: 分母（营收等，不能为零）

    返回：利润率百分比，任一参数为 None 或分母为零时返回 None。
    """
    if numerator is not None and denominator and denominator != 0:
        return round(numerator / denominator * 100, 2)
    return None


def compute_analytics(metrics: list[dict]) -> dict:
    """
    输入 SQLite 查出的原始指标列表，输出包含所有预计算结果的结构化字典。

    计算内容包括：
    - raw:        各年原始数值（转为亿元）
    - fcf:        自由现金流及相关指标（FCF = 经营现金流 - 资本支出）
    - yoy:        关键指标同比增速（%）
    - margin:     净利率、营业利润率、毛利率（%）
    - cagr:       任意年份区间的复合增长率（%）
    - cumulative: 任意年份区间的累计增幅（%）

    参数：
        metrics: SQLite financial_metrics 表查询结果，每条含 ann_date/metric_name/value

    返回：包含上述六个维度的嵌套字典。
    """
    # 按年份分组整理原始数据，便于后续按年份查找指标值
    metrics_by_year: dict[str, dict[str, float]] = defaultdict(dict)
    for metric_row in metrics:
        year = metric_row["ann_date"][:4]  # 取年份前4位，如 "20231231" → "2023"
        metrics_by_year[year][metric_row["metric_name"]] = metric_row["value"]

    years_sorted = sorted(metrics_by_year.keys())  # 升序排列，方便相邻年份计算同比
    result = {
        "raw": {},           # 原始数值（亿元）
        "yoy": {},           # 同比增速（%）
        "margin": {},        # 利润率（%）
        "cagr": {},          # CAGR（%）
        "cumulative": {},    # 累计增幅（%）
    }

    # ── 原始数值：原始单位（元）转为亿元，保留2位小数 ─────────────────────
    for year in years_sorted:
        result["raw"][year] = {
            metric_name: round(value / 1e8, 2)
            for metric_name, value in metrics_by_year[year].items()
        }

    # ── 自由现金流（FCF = 经营现金流 - 资本支出）──────────────────────────
    # Tushare 中资本支出存为负值，需取绝对值后相减
    result["fcf"] = {}
    for year in years_sorted:
        year_data = metrics_by_year[year]
        operating_cash_flow = year_data.get("经营活动现金流")
        capital_expenditure = year_data.get("资本支出")
        if operating_cash_flow is not None and capital_expenditure is not None:
            # 资本支出在 Tushare 中为负值，取绝对值后从经营现金流中扣除
            free_cash_flow = operating_cash_flow - abs(capital_expenditure)
            result["fcf"][year] = {
                "fcf":              round(free_cash_flow / 1e8, 2),
                "ocf":              round(operating_cash_flow / 1e8, 2),
                "capex":            round(abs(capital_expenditure) / 1e8, 2),
                "fcf_margin":       _margin(free_cash_flow, year_data.get("营业收入")),   # FCF 利润率
                "fcf_to_net_profit": round(free_cash_flow / year_data["净利润"], 2)       # 现金含量（FCF/净利润）
                                    if year_data.get("净利润") else None,
            }

    # ── 同比增速：逐年计算关键指标的 YoY ─────────────────────────────────
    key_metrics_for_yoy = [
        "营业收入", "净利润", "经营活动现金流", "资本支出", "营业成本",
        "销售费用", "管理费用", "研发费用", "资产总计",
    ]
    for year_idx in range(1, len(years_sorted)):
        current_year = years_sorted[year_idx]
        previous_year = years_sorted[year_idx - 1]
        result["yoy"][current_year] = {}
        for metric_name in key_metrics_for_yoy:
            current_val = metrics_by_year[current_year].get(metric_name)
            previous_val = metrics_by_year[previous_year].get(metric_name)
            if current_val is not None and previous_val is not None:
                result["yoy"][current_year][metric_name] = _yoy(current_val, previous_val)

    # ── 利润率：按年计算净利率、营业利润率、毛利率 ────────────────────────
    for year in years_sorted:
        year_data = metrics_by_year[year]
        revenue = year_data.get("营业收入")
        result["margin"][year] = {}
        if revenue:
            for metric_name, label in [
                ("净利润",    "净利率"),
                ("营业利润",  "营业利润率"),
                ("营业成本",  "毛利率"),   # 毛利率 = 1 - 营业成本/营收（非直接利润/营收）
            ]:
                metric_value = year_data.get(metric_name)
                if metric_value is not None:
                    if metric_name == "营业成本":
                        # 毛利率需特殊处理：毛利率 = (营收 - 营业成本) / 营收
                        result["margin"][year]["毛利率"] = round((1 - metric_value / revenue) * 100, 2)
                    else:
                        result["margin"][year][label] = _margin(metric_value, revenue)

    # ── CAGR & 累计增幅：对所有年份对两两计算，供 LLM 按需引用 ─────────────
    # 遍历所有起止年份组合（i < j），计算每个区间的增长指标
    for start_idx in range(len(years_sorted)):
        for end_idx in range(start_idx + 1, len(years_sorted)):
            start_year = years_sorted[start_idx]
            end_year = years_sorted[end_idx]
            num_years = int(end_year) - int(start_year)
            period_key = f"{start_year}-{end_year}"  # 区间标识，如 "2019-2023"

            result["cagr"][period_key] = {
                "years": num_years,
                "start": start_year,
                "end": end_year,
            }
            result["cumulative"][period_key] = {}

            # 计算营收、净利润、经营现金流的 CAGR 和累计增幅
            for metric_name in ["营业收入", "净利润", "经营活动现金流"]:
                start_val = metrics_by_year[start_year].get(metric_name)
                end_val = metrics_by_year[end_year].get(metric_name)
                if start_val and end_val:
                    result["cagr"][period_key][metric_name] = _cagr(start_val, end_val, num_years)
                    result["cumulative"][period_key][metric_name] = _cumulative_growth(start_val, end_val)

            # 自由现金流的 CAGR（FCF 存储单位为亿元，需还原为元再计算）
            start_fcf = result["fcf"].get(start_year, {}).get("fcf")
            end_fcf   = result["fcf"].get(end_year,   {}).get("fcf")
            if start_fcf and end_fcf and start_fcf > 0:
                result["cagr"][period_key]["自由现金流"] = _cagr(
                    start_fcf * 1e8, end_fcf * 1e8, num_years
                )
                result["cumulative"][period_key]["自由现金流"] = _cumulative_growth(
                    start_fcf * 1e8, end_fcf * 1e8
                )

    return result


def format_analytics_for_llm(
    analytics: dict,
    focus_start: str | None = None,
    focus_end: str | None = None,
) -> str:
    """
    将 compute_analytics() 的计算结果格式化为 LLM prompt 上下文。

    明确标注"已计算结果"，防止 LLM 对已有数据重复计算。
    若指定了 focus_start/focus_end，则只展示该年份区间内的数据，减少无关噪音。

    参数：
        analytics:   compute_analytics() 返回的结构化字典
        focus_start: 关注区间起始年份（如 "2019"），None 表示不限制
        focus_end:   关注区间结束年份（如 "2023"），None 表示不限制

    返回：格式化后的多行文本字符串。
    """
    lines = ["【已计算财务数据（Python 计算，请直接引用，勿自行重算）】\n"]

    def _in_focus_range(year: str) -> bool:
        """判断给定年份是否在关注区间内"""
        if focus_start and year < focus_start:
            return False
        if focus_end and year > focus_end:
            return False
        return True

    # ── 各年核心指标原始数值（亿元）────────────────────────────────────
    lines.append("▌ 各年核心指标（亿元）")
    for year in sorted(analytics["raw"].keys(), reverse=True):
        if not _in_focus_range(year):
            continue
        year_raw = analytics["raw"][year]
        metric_parts = []
        for metric_name in ["营业收入", "净利润", "经营活动现金流", "资产总计", "负债合计"]:
            if metric_name in year_raw:
                metric_parts.append(f"{metric_name}={year_raw[metric_name]:.2f}")
        if metric_parts:
            lines.append(f"  {year}年：{'  '.join(metric_parts)}")

    # ── 自由现金流分析 ───────────────────────────────────────────────
    fcf_by_year = analytics.get("fcf", {})
    if fcf_by_year:
        lines.append("\n▌ 自由现金流分析（亿元）")
        for year in sorted(fcf_by_year.keys(), reverse=True):
            if not _in_focus_range(year):
                continue
            fcf_data = fcf_by_year[year]
            metric_parts = [
                f"FCF={fcf_data['fcf']:.2f}",
                f"经营现金流={fcf_data['ocf']:.2f}",
                f"资本支出={fcf_data['capex']:.2f}",
            ]
            if fcf_data.get("fcf_margin") is not None:
                metric_parts.append(f"FCF利润率={fcf_data['fcf_margin']:.1f}%")
            if fcf_data.get("fcf_to_net_profit") is not None:
                metric_parts.append(f"现金含量={fcf_data['fcf_to_net_profit']:.2f}x")
            lines.append(f"  {year}年：{'  '.join(metric_parts)}")

    # ── 同比增速 ─────────────────────────────────────────────────────
    lines.append("\n▌ 同比增速（%）")
    for year in sorted(analytics["yoy"].keys(), reverse=True):
        if not _in_focus_range(year):
            continue
        yoy_data = analytics["yoy"][year]
        metric_parts = []
        for metric_name in ["营业收入", "净利润", "经营活动现金流"]:
            if metric_name in yoy_data and yoy_data[metric_name] is not None:
                metric_parts.append(f"{metric_name}={yoy_data[metric_name]:+.1f}%")
        if metric_parts:
            lines.append(f"  {year}年：{'  '.join(metric_parts)}")

    # ── 利润率 ────────────────────────────────────────────────────────
    lines.append("\n▌ 利润率（%）")
    for year in sorted(analytics["margin"].keys(), reverse=True):
        if not _in_focus_range(year):
            continue
        margin_data = analytics["margin"][year]
        metric_parts = [
            f"{label}={value:.1f}%"
            for label, value in margin_data.items()
            if value is not None
        ]
        if metric_parts:
            lines.append(f"  {year}年：{'  '.join(metric_parts)}")

    # ── CAGR & 累计增幅（只展示与问题年份匹配的区间，避免信息过载）────────
    cagr_data = analytics.get("cagr", {})
    cumulative_data = analytics.get("cumulative", {})
    if cagr_data:
        lines.append("\n▌ CAGR 及累计增幅")
        for period_key, period_info in sorted(cagr_data.items()):
            if not isinstance(period_info, dict) or "start" not in period_info:
                continue
            start_year = period_info["start"]
            end_year = period_info["end"]
            num_years = period_info["years"]

            # 当用户指定了年份范围时，只展示完全匹配的区间，避免无关数据干扰 LLM
            if focus_start and focus_end:
                if start_year != focus_start or end_year != focus_end:
                    continue

            metric_parts = []
            for metric_name in ["营业收入", "净利润", "经营活动现金流"]:
                cagr_val = period_info.get(metric_name)
                cumulative_val = cumulative_data.get(period_key, {}).get(metric_name)
                if cagr_val is not None and cumulative_val is not None:
                    metric_parts.append(
                        f"{metric_name} CAGR={cagr_val:+.1f}% 累计={cumulative_val:+.1f}%"
                    )
            if metric_parts:
                lines.append(f"  {start_year}-{end_year}年（{num_years}年）：{'  '.join(metric_parts)}")

    return "\n".join(lines)
