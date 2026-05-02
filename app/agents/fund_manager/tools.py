"""
基金经理 Agent 可调用的工具集。
每个工具返回结构化文本结果，供 Agent 决策和回答使用。
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
# 每个工具定义包含名称、描述和参数 schema，LLM 根据描述决定何时调用哪个工具

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


# ── 工具执行入口 ──────────────────────────────────────────────────────────────

def execute_tool(tool_name: str, tool_inputs: dict) -> str:
    """
    工具调用统一入口，根据工具名称路由到对应的执行函数。

    参数：
        tool_name:   工具名称，须与 TOOL_DEFINITIONS 中的 name 一致
        tool_inputs: 工具参数字典

    返回：工具执行结果文本，供 LLM 作为观察结果继续推理。
    """
    logger.info(f"工具调用: {tool_name}({json.dumps(tool_inputs, ensure_ascii=False)[:100]})")
    try:
        from langsmith import traceable
        return traceable(name=f"tool_{tool_name}", run_type="tool")(
            lambda: _dispatch_tool(tool_name, tool_inputs)
        )()
    except Exception:
        return _dispatch_tool(tool_name, tool_inputs)


def _dispatch_tool(tool_name: str, tool_inputs: dict) -> str:
    """实际工具路由与执行。"""
    try:
        if tool_name == "deep_screen":
            return _run_deep_screen(tool_inputs)
        elif tool_name == "get_financials":
            return _run_financials(tool_inputs)
        elif tool_name == "get_valuation":
            return _run_valuation(tool_inputs)
        elif tool_name == "ask_knowledge":
            return _run_ask_knowledge(tool_inputs)
        else:
            return f"未知工具：{tool_name}"
    except Exception as error:
        return f"工具执行失败（{tool_name}）：{str(error)}"


def _run_deep_screen(inputs: dict) -> str:
    """
    两阶段深度选股工具的执行逻辑。

    阶段1：调用 screener 按市值/PE/PB/行业量化初筛，最多取100只候选股。
    阶段2：逐只拉取财务数据，按 ROE/负债率/FCF 做二次过滤。
    阶段3：综合评分排序，截断至 top_n 返回。

    评分加权逻辑：
    - 基础分来自 screener（市值、估值等维度）
    - ROE 越高加分越多（上限 +0.3）
    - PB 历史分位越低加分越多（低估值溢价）
    """
    import pandas as pd
    import tushare as ts
    from app.core.config import settings
    from app.knowledge.financial_fetcher import fetch_financials, _to_ts_code
    from app.knowledge.extractor import get_metrics
    from app.knowledge.calculator import compute_analytics
    from app.data.stock_data import get_stock_valuation_history, get_pb_percentile

    # ── 阶段1：量化初筛，拿100只候选用于二次过滤 ──────────────────────
    screen_criteria = ScreenerCriteria(
        market_cap_min=inputs.get("market_cap_min"),
        market_cap_max=inputs.get("market_cap_max"),
        pb_max=inputs.get("pb_max"),
        pe_max=inputs.get("pe_max"),
        pe_min=inputs.get("pe_min") or 0,  # 默认 pe_min=0，过滤亏损股（PE为负）
        industry=inputs.get("industry"),
        exclude_industries=inputs.get("exclude_industries"),
        top_n=100,  # 初筛多拿一些，二次过滤后再截断到 top_n
    )
    candidate_stocks = screen_stocks(screen_criteria)
    if not candidate_stocks:
        return "未找到符合初筛条件的股票，请放宽筛选条件。"

    # ── 阶段2：财务指标二次过滤 ────────────────────────────────────────
    # 从 inputs 中读取二次过滤参数
    roe_min_threshold      = inputs.get("roe_min")
    debt_ratio_max_threshold = inputs.get("debt_ratio_max")
    require_positive_fcf   = inputs.get("fcf_positive", False)
    pb_percentile_max      = inputs.get("pb_percentile_max")
    final_top_n            = inputs.get("top_n", 20)

    # 判断是否需要拉取财务数据和估值历史（避免不必要的网络请求）
    need_financials = any([roe_min_threshold, debt_ratio_max_threshold, require_positive_fcf])
    need_valuation  = pb_percentile_max is not None

    qualified_stocks = []   # 通过二次过滤的股票列表

    for stock in candidate_stocks:
        stock_code = stock.code
        composite_score = stock.score  # 初始分来自 screener
        financial_extras = {}          # 二次过滤中计算出的财务指标（用于结果展示）

        # 拉取财务数据并计算 ROE/负债率/FCF
        if need_financials:
            try:
                raw_metrics = get_metrics(stock_code, years=3)
                if not raw_metrics:
                    # 本地无数据时从 Tushare 拉取并缓存
                    current_year = pd.Timestamp.now().year
                    fetch_financials(stock_code, start_year=current_year - 3, end_year=current_year)
                    raw_metrics = get_metrics(stock_code, years=3)

                if raw_metrics:
                    analytics = compute_analytics(raw_metrics)
                    raw_by_year = analytics.get("raw", {})
                    latest_year = max(raw_by_year.keys()) if raw_by_year else None

                    if latest_year:
                        latest_data = raw_by_year[latest_year]

                        # 计算 ROE = 净利润 / 股东权益合计
                        roe_value = None
                        net_profit = latest_data.get("净利润")
                        shareholders_equity = latest_data.get("股东权益合计")
                        if net_profit and shareholders_equity and shareholders_equity != 0:
                            roe_value = round(net_profit / shareholders_equity * 100, 2)

                        # 计算资产负债率 = 负债合计 / 资产总计
                        debt_ratio_value = None
                        total_assets = latest_data.get("资产总计")
                        total_liabilities = latest_data.get("负债合计")
                        if total_assets and total_liabilities and total_assets != 0:
                            debt_ratio_value = round(total_liabilities / total_assets * 100, 2)

                        # 自由现金流（亿元）
                        fcf_year_data = analytics.get("fcf", {}).get(latest_year, {})
                        fcf_value = fcf_year_data.get("fcf")

                        financial_extras = {
                            "roe": roe_value,
                            "debt_ratio": debt_ratio_value,
                            "fcf": fcf_value,
                        }

                        # 按阈值过滤（不满足则跳过该股票）
                        if roe_min_threshold and (roe_value is None or roe_value < roe_min_threshold):
                            continue
                        if debt_ratio_max_threshold and (debt_ratio_value is None or debt_ratio_value > debt_ratio_max_threshold):
                            continue
                        if require_positive_fcf and (fcf_value is None or fcf_value <= 0):
                            continue

                        # ROE 越高，综合评分加成越大（最多 +0.3，避免单因子主导）
                        if roe_value and roe_value > 0:
                            composite_score += min(roe_value / 100, 0.3)

            except Exception:
                # 财务数据拉取失败时：有强制财务要求则跳过，否则保留
                if roe_min_threshold:
                    continue

        # 计算 PB 历史分位并过滤
        pb_percentile_value = None
        if need_valuation:
            try:
                valuation_history = get_stock_valuation_history(stock_code)
                pb_stats = get_pb_percentile(valuation_history)
                pb_percentile_value = pb_stats.get("pb_percentile")
                if pb_percentile_max and (pb_percentile_value is None or pb_percentile_value > pb_percentile_max):
                    continue
                if pb_percentile_value is not None:
                    # 分位越低（越低估）加分越多，最多 +0.1
                    composite_score += (100 - pb_percentile_value) / 1000
            except Exception:
                if pb_percentile_max:
                    continue

        qualified_stocks.append({
            "code":         stock_code,
            "name":         stock.name,
            "industry":     stock.industry,
            "market_cap":   stock.market_cap,
            "price":        stock.current_price,
            "pb":           stock.pb,
            "pe":           stock.pe,
            "roe":          financial_extras.get("roe"),
            "debt_ratio":   financial_extras.get("debt_ratio"),
            "fcf":          financial_extras.get("fcf"),
            "pb_percentile": pb_percentile_value,
            "score":        round(composite_score, 4),
        })

    if not qualified_stocks:
        return f"初筛得到 {len(candidate_stocks)} 只，财务二次过滤后无符合条件的股票，请放宽条件。"

    # ── 阶段3：按综合评分降序排序，截断至 top_n ────────────────────────
    qualified_stocks.sort(key=lambda stock_item: stock_item["score"], reverse=True)
    qualified_stocks = qualified_stocks[:final_top_n]

    # 格式化输出
    output_lines = [f"筛选结果（初筛{len(candidate_stocks)}只 → 财务过滤后{len(qualified_stocks)}只）：\n"]
    for rank, stock_item in enumerate(qualified_stocks, 1):
        roe_str        = f"ROE={stock_item['roe']:.1f}%"          if stock_item['roe']           else ""
        debt_ratio_str = f"负债率={stock_item['debt_ratio']:.1f}%" if stock_item['debt_ratio']    else ""
        fcf_str        = f"FCF={stock_item['fcf']:.1f}亿"          if stock_item['fcf']           else ""
        pb_pct_str     = f"PB分位={stock_item['pb_percentile']:.0f}%" if stock_item['pb_percentile'] is not None else ""
        detail_parts   = " ".join(filter(None, [roe_str, debt_ratio_str, fcf_str, pb_pct_str]))
        output_lines.append(
            f"{rank:2d}. {stock_item['code']} {stock_item['name']}（{stock_item['industry']}）"
            f"  市值={stock_item['market_cap']}亿  PB={stock_item['pb']}  PE={stock_item['pe']}"
            f"  {detail_parts}"
        )
    return "\n".join(output_lines)


def _run_financials(inputs: dict) -> str:
    """
    获取单只股票的完整财务分析。

    若本地无数据，自动从 Tushare 拉取并缓存后再计算。

    参数：
        inputs: {"stock_code": str, "years": int（可选，默认5）}

    返回：格式化的财务分析文本。
    """
    from app.knowledge.financial_fetcher import fetch_financials
    stock_code = inputs["stock_code"]
    num_years = inputs.get("years", 5)

    raw_metrics = get_metrics(stock_code, years=num_years)
    if not raw_metrics:
        # 本地无缓存，从 Tushare 拉取
        import datetime
        current_year = datetime.datetime.now().year
        fetch_financials(stock_code, start_year=current_year - num_years, end_year=current_year)
        raw_metrics = get_metrics(stock_code, years=num_years)

    if not raw_metrics:
        return f"无法获取 {stock_code} 的财务数据。"

    analytics = compute_analytics(raw_metrics)
    return format_analytics_for_llm(analytics)


def _run_valuation(inputs: dict) -> str:
    """
    获取股票的 PB 历史分位估值分析。

    参数：
        inputs: {"stock_code": str, "industry_name": str（可选）}

    返回：格式化的估值分析文本，含买入/卖出参考价格。
    """
    stock_code = inputs["stock_code"]
    industry_name = inputs.get("industry_name")
    valuation_result = run_valuation(stock_code, industry_name=industry_name)
    return (
        f"估值分析（{valuation_result.model_name}）：\n"
        f"  当前价格：¥{valuation_result.current_price}  状态：{valuation_result.current_status}\n"
        f"  买入参考：≤ ¥{valuation_result.buy_price}\n"
        f"  合理区间：¥{valuation_result.fair_value_low} ~ ¥{valuation_result.fair_value_high}\n"
        f"  卖出参考：≥ ¥{valuation_result.sell_price}\n"
        f"{valuation_result.reasoning}"
    )


def _run_ask_knowledge(inputs: dict) -> str:
    """
    基于公司知识库回答定性问题（年报/公告语义检索）。

    若该股票尚未建立知识库，返回提示信息而非报错。

    参数：
        inputs: {"stock_code": str, "question": str}

    返回：问答结果文本，附带来源标注。
    """
    stock_code = inputs["stock_code"]
    question = inputs["question"]
    qa_result = ask(stock_code=stock_code, question=question)
    if not qa_result.get("has_data"):
        return f"{stock_code} 尚未建立知识库，无法回答定性问题。"
    answer_text = qa_result.get("answer", "")
    source_list = qa_result.get("sources", [])
    source_annotation = "  来源：" + "、".join(source_list) if source_list else ""
    return f"{answer_text}\n{source_annotation}"
