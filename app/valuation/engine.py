from app.valuation.base import BaseValuation, ValuationResult
from app.data.stock_data import (
    get_stock_basic, get_stock_valuation_history, get_industry_valuation_map,
    StockBasicInfo, StockValuationHistory,
)
from app.valuation.pb_model import PBValuation

_VALUATION_MODELS: dict[str, BaseValuation] = {
    "pb": PBValuation(),
}


def run_valuation(
    stock_code: str,
    industry_name: str | None = None,
    model: str = "pb",
    basic: StockBasicInfo | None = None,
    history: StockValuationHistory | None = None,
) -> ValuationResult:
    """
    执行估值分析。可传入已获取的 basic/history 避免重复请求。
    """
    if model not in _VALUATION_MODELS:
        raise ValueError(f"不支持的估值模型：{model}，可选：{list(_VALUATION_MODELS.keys())}")

    if basic is None:
        basic = get_stock_basic(stock_code)
    if history is None:
        history = get_stock_valuation_history(stock_code)

    industry = None
    if industry_name:
        industry_map = get_industry_valuation_map()
        industry = industry_map.get(industry_name)

    return _VALUATION_MODELS[model].calc(basic, history, industry)
