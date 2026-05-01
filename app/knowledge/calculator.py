"""
财务计算层：所有数学计算在此完成，结果作为已计算事实传给 LLM。
LLM 只负责语言组织，不做任何数值运算。
"""
from collections import defaultdict


def _yoy(current: float, previous: float) -> float | None:
    """同比增速（%）"""
    if previous and previous != 0:
        return round((current - previous) / abs(previous) * 100, 2)
    return None


def _cagr(start: float, end: float, years: int) -> float | None:
    """复合年均增长率 CAGR（%）"""
    if start and start > 0 and years > 0:
        return round(((end / start) ** (1 / years) - 1) * 100, 2)
    return None


def _cumulative_growth(start: float, end: float) -> float | None:
    """累计增幅（%）"""
    if start and start != 0:
        return round((end - start) / abs(start) * 100, 2)
    return None


def _margin(numerator: float | None, denominator: float | None) -> float | None:
    """利润率（%）"""
    if numerator is not None and denominator and denominator != 0:
        return round(numerator / denominator * 100, 2)
    return None


def compute_analytics(metrics: list[dict]) -> dict:
    """
    输入 SQLite 查出的原始指标列表，
    输出包含所有预计算结果的结构化字典。
    """
    # 按年份整理
    by_year: dict[str, dict[str, float]] = defaultdict(dict)
    for m in metrics:
        year = m["ann_date"][:4]
        by_year[year][m["metric_name"]] = m["value"]

    years_sorted = sorted(by_year.keys())  # 升序，方便计算同比
    result = {
        "raw": {},           # 原始数值（亿元）
        "yoy": {},           # 同比增速（%）
        "margin": {},        # 利润率（%）
        "cagr": {},          # CAGR（%）
        "cumulative": {},    # 累计增幅（%）
    }

    # ── 原始数值（转为亿元，保留2位）──────────────────────────────────
    for year in years_sorted:
        result["raw"][year] = {
            k: round(v / 1e8, 2)
            for k, v in by_year[year].items()
        }

    # ── 自由现金流（FCF = 经营现金流 - 资本支出）────────────────────
    result["fcf"] = {}
    for year in years_sorted:
        d = by_year[year]
        ocf = d.get("经营活动现金流")
        capex = d.get("资本支出")
        if ocf is not None and capex is not None:
            fcf = ocf - abs(capex)   # 资本支出 Tushare 存为负值，取绝对值
            result["fcf"][year] = {
                "fcf": round(fcf / 1e8, 2),
                "ocf": round(ocf / 1e8, 2),
                "capex": round(abs(capex) / 1e8, 2),
                "fcf_margin": _margin(fcf, d.get("营业收入")),   # FCF利润率
                "fcf_to_net_profit": round(fcf / d["净利润"], 2) if d.get("净利润") else None,  # 现金含量
            }

    # ── 同比增速 ────────────────────────────────────────────────────
    key_metrics = ["营业收入", "净利润", "经营活动现金流", "资本支出", "营业成本",
                   "销售费用", "管理费用", "研发费用", "资产总计"]
    for i in range(1, len(years_sorted)):
        cur_year = years_sorted[i]
        pre_year = years_sorted[i - 1]
        result["yoy"][cur_year] = {}
        for metric in key_metrics:
            cur = by_year[cur_year].get(metric)
            pre = by_year[pre_year].get(metric)
            if cur is not None and pre is not None:
                result["yoy"][cur_year][metric] = _yoy(cur, pre)

    # ── 利润率 ──────────────────────────────────────────────────────
    for year in years_sorted:
        d = by_year[year]
        rev = d.get("营业收入")
        result["margin"][year] = {}
        if rev:
            for metric, label in [
                ("净利润",    "净利率"),
                ("营业利润",  "营业利润率"),
                ("营业成本",  "毛利率"),   # 毛利率 = 1 - 营业成本/营收
            ]:
                val = d.get(metric)
                if val is not None:
                    if metric == "营业成本":
                        result["margin"][year]["毛利率"] = round((1 - val / rev) * 100, 2)
                    else:
                        result["margin"][year][label] = _margin(val, rev)

    # ── CAGR & 累计增幅（对所有相邻年份对都计算，供 LLM 按需引用）────
    for i in range(len(years_sorted)):
        for j in range(i + 1, len(years_sorted)):
            start_y = years_sorted[i]
            end_y = years_sorted[j]
            n_years = int(end_y) - int(start_y)
            key = f"{start_y}-{end_y}"
            result["cagr"][key] = {"years": n_years, "start": start_y, "end": end_y}
            result["cumulative"][key] = {}
            for metric in ["营业收入", "净利润", "经营活动现金流"]:
                start_val = by_year[start_y].get(metric)
                end_val = by_year[end_y].get(metric)
                if start_val and end_val:
                    result["cagr"][key][metric] = _cagr(start_val, end_val, n_years)
                    result["cumulative"][key][metric] = _cumulative_growth(start_val, end_val)
            # FCF CAGR
            start_fcf_val = result["fcf"].get(start_y, {}).get("fcf")
            end_fcf_val   = result["fcf"].get(end_y,   {}).get("fcf")
            if start_fcf_val and end_fcf_val and start_fcf_val > 0:
                result["cagr"][key]["自由现金流"] = _cagr(start_fcf_val * 1e8, end_fcf_val * 1e8, n_years)
                result["cumulative"][key]["自由现金流"] = _cumulative_growth(start_fcf_val * 1e8, end_fcf_val * 1e8)

    return result


def format_analytics_for_llm(
    analytics: dict,
    focus_start: str | None = None,
    focus_end: str | None = None,
) -> str:
    """把计算结果格式化为 LLM prompt 上下文，明确标注「已计算结果」"""
    lines = ["【已计算财务数据（Python 计算，请直接引用，勿自行重算）】\n"]

    def _in_focus(year: str) -> bool:
        if focus_start and year < focus_start:
            return False
        if focus_end and year > focus_end:
            return False
        return True

    # 原始数值
    lines.append("▌ 各年核心指标（亿元）")
    for year in sorted(analytics["raw"].keys(), reverse=True):
        if not _in_focus(year):
            continue
        d = analytics["raw"][year]
        parts = []
        for k in ["营业收入", "净利润", "经营活动现金流", "资产总计", "负债合计"]:
            if k in d:
                parts.append(f"{k}={d[k]:.2f}")
        if parts:
            lines.append(f"  {year}年：{'  '.join(parts)}")

    # 自由现金流
    fcf_data = analytics.get("fcf", {})
    if fcf_data:
        lines.append("\n▌ 自由现金流分析（亿元）")
        for year in sorted(fcf_data.keys(), reverse=True):
            if not _in_focus(year):
                continue
            d = fcf_data[year]
            parts = [
                f"FCF={d['fcf']:.2f}",
                f"经营现金流={d['ocf']:.2f}",
                f"资本支出={d['capex']:.2f}",
            ]
            if d.get("fcf_margin") is not None:
                parts.append(f"FCF利润率={d['fcf_margin']:.1f}%")
            if d.get("fcf_to_net_profit") is not None:
                parts.append(f"现金含量={d['fcf_to_net_profit']:.2f}x")
            lines.append(f"  {year}年：{'  '.join(parts)}")

    # 同比增速
    lines.append("\n▌ 同比增速（%）")
    for year in sorted(analytics["yoy"].keys(), reverse=True):
        if not _in_focus(year):
            continue
        d = analytics["yoy"][year]
        parts = []
        for k in ["营业收入", "净利润", "经营活动现金流"]:
            if k in d and d[k] is not None:
                parts.append(f"{k}={d[k]:+.1f}%")
        if parts:
            lines.append(f"  {year}年：{'  '.join(parts)}")

    # 利润率
    lines.append("\n▌ 利润率（%）")
    for year in sorted(analytics["margin"].keys(), reverse=True):
        if not _in_focus(year):
            continue
        d = analytics["margin"][year]
        parts = [f"{k}={v:.1f}%" for k, v in d.items() if v is not None]
        if parts:
            lines.append(f"  {year}年：{'  '.join(parts)}")

    # CAGR & 累计增幅（只展示与问题年份匹配的区间）
    cagr = analytics.get("cagr", {})
    cum = analytics.get("cumulative", {})
    if cagr:
        lines.append("\n▌ CAGR 及累计增幅")
        for key, info in sorted(cagr.items()):
            if not isinstance(info, dict) or "start" not in info:
                continue
            sy, ey, n = info["start"], info["end"], info["years"]
            # 只展示起止年和问题年份匹配的区间
            if focus_start and focus_end:
                if sy != focus_start or ey != focus_end:
                    continue
            parts = []
            for metric in ["营业收入", "净利润", "经营活动现金流"]:
                c = info.get(metric)
                cu = cum.get(key, {}).get(metric)
                if c is not None and cu is not None:
                    parts.append(f"{metric} CAGR={c:+.1f}% 累计={cu:+.1f}%")
            if parts:
                lines.append(f"  {sy}-{ey}年（{n}年）：{'  '.join(parts)}")

    return "\n".join(lines)
