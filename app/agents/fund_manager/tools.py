"""
基金经理可调用的工具集。
每个工具返回结构化结果，供 Agent 决策使用。
"""
import json
from app.core.logger import get_logger
from app.screener.screener import screen_stocks, ScreenerCriteria

logger = get_logger("fm.tools")
from app.knowledge.extractor import get_metrics
from app.knowledge.calculator import compute_analytics, format_analytics_for_llm
from app.knowledge.qa import ask
from app.valuation.engine import run_valuation


# ── 工具定义（供 LLM Tool Use 使用）─────────────────────────────────────────

TOOL_DEFINITIONS = [
    {
        "name": "deep_screen",
        "description": (
            "两阶段深度选股：先按市值/行业/PE/PB量化初筛，再拉取候选股的ROE/FCF/负债率等财务指标做二次过滤，"
            "输出综合评分排序的候选清单。这是主要的选股工具，用于自然语言描述的选股需求。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "market_cap_min":    {"type": "number",  "description": "市值下限（亿元）"},
                "market_cap_max":    {"type": "number",  "description": "市值上限（亿元）"},
                "industry":          {"type": "string",  "description": "行业关键词，如 '消费'、'医药'、'白酒'"},
                "exclude_industries":{"type": "array",   "items": {"type": "string"}, "description": "排除行业，如 ['银行','保险']"},
                "pb_max":            {"type": "number",  "description": "PB上限"},
                "pe_max":            {"type": "number",  "description": "PE上限（TTM）"},
                "pe_min":            {"type": "number",  "description": "PE下限（过滤亏损股）"},
                "roe_min":           {"type": "number",  "description": "ROE下限（%），如 15"},
                "debt_ratio_max":    {"type": "number",  "description": "资产负债率上限（%），如 60"},
                "fcf_positive":      {"type": "boolean", "description": "是否要求自由现金流为正，默认false"},
                "pb_percentile_max": {"type": "number",  "description": "PB历史分位上限（%），如 30 表示低于30%分位"},
                "top_n":             {"type": "integer", "description": "最终返回数量，默认20"},
            },
        },
    },
    {
        "name": "get_financials",
        "description": "获取指定单只股票近几年的财务数据，包含营收、净利润、自由现金流、利润率、同比增速等完整分析。",
        "input_schema": {
            "type": "object",
            "properties": {
                "stock_code": {"type": "string", "description": "股票代码，如 600519"},
                "years":      {"type": "integer", "description": "获取近几年数据，默认5"},
            },
            "required": ["stock_code"],
        },
    },
    {
        "name": "get_valuation",
        "description": "获取指定股票的PB历史分位估值，输出合理买入/卖出价格区间和当前高估/低估状态。",
        "input_schema": {
            "type": "object",
            "properties": {
                "stock_code":    {"type": "string", "description": "股票代码"},
                "industry_name": {"type": "string", "description": "所属行业（可选，用于行业对比）"},
            },
            "required": ["stock_code"],
        },
    },
    {
        "name": "ask_knowledge",
        "description": (
            "基于公司年报和研报回答定性问题。适合研究护城河、商业模式、竞争优势、"
            "收入确认政策、风险因素等无法从财务数字直接得出的问题。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "stock_code": {"type": "string", "description": "股票代码"},
                "question":   {"type": "string", "description": "问题内容"},
            },
            "required": ["stock_code", "question"],
        },
    },
]


# ── 工具执行 ──────────────────────────────────────────────────────────────────

def execute_tool(name: str, inputs: dict) -> str:
    logger.info(f"工具调用: {name}({json.dumps(inputs, ensure_ascii=False)[:100]})")
    try:
        if name == "deep_screen":
            return _run_deep_screen(inputs)
        elif name == "get_financials":
            return _run_financials(inputs)
        elif name == "get_valuation":
            return _run_valuation(inputs)
        elif name == "ask_knowledge":
            return _run_ask_knowledge(inputs)
        else:
            return f"未知工具：{name}"
    except Exception as e:
        return f"工具执行失败（{name}）：{str(e)}"


def _run_deep_screen(inputs: dict) -> str:
    """
    两阶段选股：
    1. daily_basic 量化初筛 → 候选池（最多100只）
    2. 逐只拉财务数据 → ROE/FCF/负债率二次过滤
    3. 综合评分排序
    """
    import pandas as pd
    import tushare as ts
    from app.core.config import settings
    from app.knowledge.financial_fetcher import fetch_financials, _to_ts_code
    from app.knowledge.extractor import get_metrics
    from app.knowledge.calculator import compute_analytics
    from app.data.stock_data import get_stock_valuation_history, get_pb_percentile

    # ── 阶段1：量化初筛 ────────────────────────────────────────────────
    criteria = ScreenerCriteria(
        market_cap_min=inputs.get("market_cap_min"),
        market_cap_max=inputs.get("market_cap_max"),
        pb_max=inputs.get("pb_max"),
        pe_max=inputs.get("pe_max"),
        pe_min=inputs.get("pe_min") or 0,  # 默认过滤亏损股
        industry=inputs.get("industry"),
        exclude_industries=inputs.get("exclude_industries"),
        top_n=100,  # 初筛拿100只，二次过滤后再截断
    )
    candidates = screen_stocks(criteria)
    if not candidates:
        return "未找到符合初筛条件的股票，请放宽筛选条件。"

    # ── 阶段2：财务指标二次过滤 ────────────────────────────────────────
    roe_min       = inputs.get("roe_min")
    debt_max      = inputs.get("debt_ratio_max")
    fcf_positive  = inputs.get("fcf_positive", False)
    pb_pct_max    = inputs.get("pb_percentile_max")
    top_n         = inputs.get("top_n", 20)

    need_financials = any([roe_min, debt_max, fcf_positive])
    need_valuation  = pb_pct_max is not None

    results = []
    checked = 0

    for stock in candidates:
        code = stock.code
        score = stock.score  # 基础评分（来自 screener）
        extra = {}

        # 拉财务数据
        if need_financials:
            try:
                metrics = get_metrics(code, years=3)
                if not metrics:
                    # 本地没有则从 Tushare 拉
                    end_year = pd.Timestamp.now().year
                    fetch_financials(code, start_year=end_year - 3, end_year=end_year)
                    metrics = get_metrics(code, years=3)

                if metrics:
                    analytics = compute_analytics(metrics)
                    raw = analytics.get("raw", {})
                    latest_year = max(raw.keys()) if raw else None

                    if latest_year:
                        d = raw[latest_year]
                        roe_val = None
                        # ROE = 净利润 / 股东权益
                        np_val = d.get("净利润")
                        eq_val = d.get("股东权益合计")
                        if np_val and eq_val and eq_val != 0:
                            roe_val = round(np_val / eq_val * 100, 2)

                        debt_val = None
                        ta = d.get("资产总计")
                        tl = d.get("负债合计")
                        if ta and tl and ta != 0:
                            debt_val = round(tl / ta * 100, 2)

                        fcf_data = analytics.get("fcf", {}).get(latest_year, {})
                        fcf_val = fcf_data.get("fcf")

                        extra = {"roe": roe_val, "debt_ratio": debt_val, "fcf": fcf_val}

                        # 过滤
                        if roe_min and (roe_val is None or roe_val < roe_min):
                            continue
                        if debt_max and (debt_val is None or debt_val > debt_max):
                            continue
                        if fcf_positive and (fcf_val is None or fcf_val <= 0):
                            continue

                        # ROE 加权提升评分
                        if roe_val and roe_val > 0:
                            score += min(roe_val / 100, 0.3)
            except Exception:
                if roe_min:  # 有强制财务要求时跳过拉取失败的
                    continue

        # PB历史分位过滤
        pb_pct_val = None
        if need_valuation:
            try:
                h = get_stock_valuation_history(code)
                stats = get_pb_percentile(h)
                pb_pct_val = stats.get("pb_percentile")
                if pb_pct_max and (pb_pct_val is None or pb_pct_val > pb_pct_max):
                    continue
                if pb_pct_val is not None:
                    score += (100 - pb_pct_val) / 1000  # 低分位加分
            except Exception:
                if pb_pct_max:
                    continue

        results.append({
            "code": code,
            "name": stock.name,
            "industry": stock.industry,
            "market_cap": stock.market_cap,
            "price": stock.current_price,
            "pb": stock.pb,
            "pe": stock.pe,
            "roe": extra.get("roe"),
            "debt_ratio": extra.get("debt_ratio"),
            "fcf": extra.get("fcf"),
            "pb_percentile": pb_pct_val,
            "score": round(score, 4),
        })
        checked += 1

    if not results:
        return f"初筛得到 {len(candidates)} 只，财务二次过滤后无符合条件的股票，请放宽条件。"

    # 排序截断
    results.sort(key=lambda x: x["score"], reverse=True)
    results = results[:top_n]

    lines = [f"筛选结果（初筛{len(candidates)}只 → 财务过滤后{len(results)}只）：\n"]
    for i, r in enumerate(results, 1):
        roe_str   = f"ROE={r['roe']:.1f}%" if r['roe'] else ""
        debt_str  = f"负债率={r['debt_ratio']:.1f}%" if r['debt_ratio'] else ""
        fcf_str   = f"FCF={r['fcf']:.1f}亿" if r['fcf'] else ""
        pct_str   = f"PB分位={r['pb_percentile']:.0f}%" if r['pb_percentile'] is not None else ""
        details   = " ".join(filter(None, [roe_str, debt_str, fcf_str, pct_str]))
        lines.append(
            f"{i:2d}. {r['code']} {r['name']}（{r['industry']}）"
            f"  市值={r['market_cap']}亿  PB={r['pb']}  PE={r['pe']}"
            f"  {details}"
        )
    return "\n".join(lines)


def _run_financials(inputs: dict) -> str:
    from app.knowledge.financial_fetcher import fetch_financials
    code = inputs["stock_code"]
    years = inputs.get("years", 5)

    metrics = get_metrics(code, years=years)
    if not metrics:
        import datetime
        end_year = datetime.datetime.now().year
        fetch_financials(code, start_year=end_year - years, end_year=end_year)
        metrics = get_metrics(code, years=years)

    if not metrics:
        return f"无法获取 {code} 的财务数据。"

    analytics = compute_analytics(metrics)
    return format_analytics_for_llm(analytics)


def _run_valuation(inputs: dict) -> str:
    code = inputs["stock_code"]
    industry = inputs.get("industry_name")
    result = run_valuation(code, industry_name=industry)
    return (
        f"估值分析（{result.model_name}）：\n"
        f"  当前价格：¥{result.current_price}  状态：{result.current_status}\n"
        f"  买入参考：≤ ¥{result.buy_price}\n"
        f"  合理区间：¥{result.fair_value_low} ~ ¥{result.fair_value_high}\n"
        f"  卖出参考：≥ ¥{result.sell_price}\n"
        f"{result.reasoning}"
    )


def _run_ask_knowledge(inputs: dict) -> str:
    code = inputs["stock_code"]
    question = inputs["question"]
    result = ask(stock_code=code, question=question)
    if not result.get("has_data"):
        return f"{code} 尚未建立知识库，无法回答定性问题。"
    answer = result.get("answer", "")
    sources = result.get("sources", [])
    source_str = "  来源：" + "、".join(sources) if sources else ""
    return f"{answer}\n{source_str}"
