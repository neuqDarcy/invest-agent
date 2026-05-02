import tushare as ts
import pandas as pd
from dataclasses import dataclass
from app.core.config import settings


def _get_pro():
    """初始化并返回 Tushare Pro API 客户端。"""
    ts.set_token(settings.tushare_token)
    return ts.pro_api()


def _latest_trade_date() -> str:
    """
    获取最近一个交易日的日期字符串（YYYYMMDD 格式）。
    通过查询交易日历确保日期有效，避免节假日或周末导致数据缺失。
    """
    pro = _get_pro()
    today = pd.Timestamp.now().strftime("%Y%m%d")
    # 往前取 10 天保证能覆盖到上一个交易日（最长连续休市约 7 天）
    ten_days_ago = (pd.Timestamp.now() - pd.DateOffset(days=10)).strftime("%Y%m%d")
    trade_calendar = pro.trade_cal(exchange="SSE", is_open="1", start_date=ten_days_ago, end_date=today)
    return trade_calendar.sort_values("cal_date").iloc[-1]["cal_date"]


@dataclass
class ScreenerCriteria:
    """选股筛选条件，所有字段均可选，None 表示不设限制。"""
    market_cap_min: float | None = None   # 市值下限（亿元）
    market_cap_max: float | None = None   # 市值上限（亿元）
    pb_max: float | None = None           # 市净率上限
    pb_min: float | None = None           # 市净率下限
    pe_max: float | None = None           # 市盈率上限（TTM）
    pe_min: float | None = None           # 市盈率下限（TTM）
    industry: str | None = None           # 行业关键词过滤，如 "白酒"、"医药"
    exclude_industries: list[str] | None = None  # 排除行业列表，如 ["银行", "保险"]
    top_n: int = 50                       # 返回评分最高的前 N 只股票


@dataclass
class ScreenerResult:
    """单只股票的选股结果。"""
    code: str
    name: str
    industry: str
    market_cap: float      # 市值（亿元）
    current_price: float   # 最新收盘价（元）
    pb: float | None       # 市净率
    pe: float | None       # 市盈率 TTM
    score: float           # 综合评分（0-1，越高越被低估）


def screen_stocks(criteria: ScreenerCriteria) -> list[ScreenerResult]:
    """
    按条件筛选 A 股并综合评分排序。

    参数:
        criteria: 筛选条件对象

    返回:
        按评分降序排列的 ScreenerResult 列表
    """
    market_df = _fetch_market_data()
    filtered_df = _apply_filters(market_df, criteria)
    scored_df = _score(filtered_df)
    top_df = scored_df.sort_values("score", ascending=False).head(criteria.top_n)
    return _to_results(top_df)


def _fetch_market_data() -> pd.DataFrame:
    """
    从 Tushare 获取全市场的估值和基本信息数据，合并为一张宽表。

    返回:
        包含 code/name/industry/close/pe_ttm/pb/market_cap 等列的 DataFrame
    """
    pro = _get_pro()
    trade_date = _latest_trade_date()

    # 获取当日估值数据（PE、PB、市值等）
    valuation_df = pro.daily_basic(
        trade_date=trade_date,
        fields="ts_code,close,pe_ttm,pb,total_mv",
    )
    # 获取股票基本信息（名称、行业），只取上市状态的股票
    basic_info_df = pro.stock_basic(
        fields="ts_code,name,industry",
        list_status="L",
    )

    # 左连接保留所有有估值数据的股票，部分退市股可能缺少基本信息
    merged_df = pd.merge(valuation_df, basic_info_df, on="ts_code", how="left")

    # 强制转换为数值类型，非数值填 NaN（避免字符串混入导致比较出错）
    for col in ["close", "pe_ttm", "pb", "total_mv"]:
        merged_df[col] = pd.to_numeric(merged_df[col], errors="coerce")

    # Tushare total_mv 单位为万元，转换为亿元便于业务理解
    merged_df["market_cap"] = merged_df["total_mv"] / 10000
    # 提取纯数字股票代码（去掉交易所后缀）
    merged_df["code"] = merged_df["ts_code"].str.split(".").str[0]

    # 没有收盘价或市值的数据无意义，直接丢弃
    return merged_df.dropna(subset=["close", "market_cap"])


def _apply_filters(df: pd.DataFrame, criteria: ScreenerCriteria) -> pd.DataFrame:
    """
    按筛选条件逐步过滤 DataFrame。

    参数:
        df:       全市场数据
        criteria: 筛选条件

    返回:
        过滤后的 DataFrame
    """
    if criteria.market_cap_min is not None:
        df = df[df["market_cap"] >= criteria.market_cap_min]
    if criteria.market_cap_max is not None:
        df = df[df["market_cap"] <= criteria.market_cap_max]
    if criteria.pb_max is not None:
        df = df[df["pb"] <= criteria.pb_max]
    if criteria.pb_min is not None:
        df = df[df["pb"] >= criteria.pb_min]
    if criteria.pe_max is not None:
        # PE 为负表示亏损，排除负值避免亏损股混入"低PE"筛选结果
        df = df[(df["pe_ttm"] > 0) & (df["pe_ttm"] <= criteria.pe_max)]
    if criteria.pe_min is not None:
        df = df[df["pe_ttm"] >= criteria.pe_min]
    if criteria.industry is not None:
        df = df[df["industry"].str.contains(criteria.industry, na=False)]
    if criteria.exclude_industries:
        # 构建多行业排除的正则模式
        exclude_pattern = "|".join(criteria.exclude_industries)
        df = df[~df["industry"].str.contains(exclude_pattern, na=False)]
    return df


def _score(df: pd.DataFrame) -> pd.DataFrame:
    """
    对筛选后的股票进行综合评分。
    评分公式：低PB得分 * 50% + 低PE得分 * 50%
    使用 Min-Max 归一化后取反，使低估值股票得分更高。

    参数:
        df: 过滤后的股票 DataFrame

    返回:
        新增 score 列的 DataFrame
    """
    scored_df = df.copy()

    def normalize_descending(series):
        """Min-Max 归一化后取反，使值越小得分越高（适用于 PB/PE）。"""
        numeric_series = pd.to_numeric(series, errors="coerce")
        valid_values = numeric_series.dropna()
        # 所有值相同时无法区分，统一给 0.5 分
        if valid_values.max() == valid_values.min():
            return pd.Series(0.5, index=series.index)
        return 1 - (numeric_series - valid_values.min()) / (valid_values.max() - valid_values.min())

    pb_score = normalize_descending(scored_df["pb"]).fillna(0)
    pe_score = normalize_descending(scored_df["pe_ttm"]).fillna(0)
    scored_df["score"] = pb_score * 0.5 + pe_score * 0.5
    return scored_df


def _to_results(df: pd.DataFrame) -> list[ScreenerResult]:
    """
    将 DataFrame 行转换为 ScreenerResult 数据类列表。

    参数:
        df: 已评分排序的 DataFrame

    返回:
        ScreenerResult 列表
    """
    results = []
    for _, row in df.iterrows():
        results.append(ScreenerResult(
            code=str(row["code"]),
            name=str(row.get("name", "")),
            industry=str(row.get("industry", "")),
            market_cap=round(float(row["market_cap"]), 1),
            current_price=round(float(row["close"]), 2),
            pb=round(float(row["pb"]), 2) if pd.notna(row.get("pb")) else None,
            pe=round(float(row["pe_ttm"]), 2) if pd.notna(row.get("pe_ttm")) else None,
            score=round(float(row["score"]), 4),
        ))
    return results
