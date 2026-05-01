from app.valuation.base import BaseValuation, ValuationResult
from app.data.stock_data import (
    StockBasicInfo, StockValuationHistory, IndustryValuation, get_pb_percentile
)


class PBValuation(BaseValuation):
    """
    基于历史 PB 分位的估值模型。
    买入区间：历史 PB 10%-30% 分位对应价格
    卖出区间：历史 PB 70%-90% 分位对应价格
    同时参考行业 PB 均值做修正。
    """

    @property
    def model_name(self) -> str:
        return "PB历史分位估值"

    def calc(
        self,
        basic: StockBasicInfo,
        history: StockValuationHistory,
        industry: IndustryValuation | None = None,
    ) -> ValuationResult:
        if basic.pb is None or basic.pb <= 0:
            raise ValueError("当前 PB 数据不可用")

        pb_stats = get_pb_percentile(history)
        if not pb_stats:
            raise ValueError("历史 PB 数据不足，无法计算分位")

        current_price = basic.current_price
        current_pb = pb_stats["current_pb"]

        # 每股净资产 = 当前价格 / 当前PB
        book_value_per_share = current_price / current_pb

        # 买入区间：历史 10%-30% 分位 PB 对应价格
        buy_low = round(pb_stats["pb_10pct"] * book_value_per_share, 2)
        buy_high = round(pb_stats["pb_30pct"] * book_value_per_share, 2)

        # 合理区间：历史 30%-70% 分位
        fair_low = buy_high
        fair_high = round(pb_stats["pb_70pct"] * book_value_per_share, 2)

        # 卖出区间：历史 70%-90% 分位
        sell_low = fair_high
        sell_high = round(pb_stats["pb_90pct"] * book_value_per_share, 2)

        status = self._judge_status(current_price, fair_low, fair_high)

        # 行业对比说明
        industry_note = ""
        if industry and industry.avg_pb > 0:
            pb_vs_industry = current_pb / industry.avg_pb
            if pb_vs_industry < 0.8:
                industry_note = f"当前PB（{current_pb}x）低于行业均值（{industry.avg_pb}x）{round((1-pb_vs_industry)*100)}%，相对行业低估。"
            elif pb_vs_industry > 1.2:
                industry_note = f"当前PB（{current_pb}x）高于行业均值（{industry.avg_pb}x）{round((pb_vs_industry-1)*100)}%，相对行业溢价。"
            else:
                industry_note = f"当前PB（{current_pb}x）与行业均值（{industry.avg_pb}x）基本持平。"

        reasoning = (
            f"基于近5年历史PB分位分析：\n"
            f"- 当前PB {current_pb}x，处于历史 {pb_stats['pb_percentile']}% 分位\n"
            f"- 历史PB区间：{pb_stats['pb_min']}x ～ {pb_stats['pb_max']}x\n"
            f"- 低估区（10%分位）：{pb_stats['pb_10pct']}x → 对应价格 {buy_low} 元\n"
            f"- 合理区（30%-70%分位）：{pb_stats['pb_30pct']}x ～ {pb_stats['pb_70pct']}x → {fair_low} ～ {fair_high} 元\n"
            f"- 高估区（90%分位）：{pb_stats['pb_90pct']}x → 对应价格 {sell_high} 元\n"
            f"{industry_note}"
        )

        return ValuationResult(
            model_name=self.model_name,
            current_price=current_price,
            fair_value_low=fair_low,
            fair_value_high=fair_high,
            buy_price=buy_high,       # 低于30%分位价格时值得关注
            sell_price=sell_low,      # 高于70%分位价格时需谨慎
            current_status=status,
            reasoning=reasoning,
        )
