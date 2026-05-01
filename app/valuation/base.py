from abc import ABC, abstractmethod
from dataclasses import dataclass
from app.data.stock_data import StockBasicInfo, StockValuationHistory, IndustryValuation


@dataclass
class ValuationResult:
    model_name: str
    current_price: float
    fair_value_low: float     # 合理价值下限
    fair_value_high: float    # 合理价值上限
    buy_price: float          # 建议买入区间上限（低于此价格值得关注）
    sell_price: float         # 建议卖出区间下限（高于此价格需谨慎）
    current_status: str       # 低估 / 合理 / 高估
    reasoning: str            # 估值逻辑说明


class BaseValuation(ABC):
    @property
    @abstractmethod
    def model_name(self) -> str:
        pass

    @abstractmethod
    def calc(
        self,
        basic: StockBasicInfo,
        history: StockValuationHistory,
        industry: IndustryValuation | None = None,
    ) -> ValuationResult:
        pass

    def _judge_status(self, current_price: float, low: float, high: float) -> str:
        if current_price <= low:
            return "低估"
        elif current_price >= high:
            return "高估"
        return "合理"
