import tushare as ts
import pandas as pd
from dataclasses import dataclass
from app.core.config import settings


def _get_pro():
    ts.set_token(settings.tushare_token)
    return ts.pro_api()


def _latest_trade_date() -> str:
    """获取最近一个交易日"""
    pro = _get_pro()
    today = pd.Timestamp.now().strftime("%Y%m%d")
    start = (pd.Timestamp.now() - pd.DateOffset(days=10)).strftime("%Y%m%d")
    df = pro.trade_cal(exchange="SSE", is_open="1", start_date=start, end_date=today)
    return df.sort_values("cal_date").iloc[-1]["cal_date"]


@dataclass
class ScreenerCriteria:
    market_cap_min: float | None = None   # 市值下限（亿元）
    market_cap_max: float | None = None   # 市值上限（亿元）
    pb_max: float | None = None
    pb_min: float | None = None
    pe_max: float | None = None
    pe_min: float | None = None
    industry: str | None = None           # 行业过滤，如 "白酒"、"医药"
    exclude_industries: list[str] | None = None  # 排除行业，如 ["银行", "保险"]
    top_n: int = 50


@dataclass
class ScreenerResult:
    code: str
    name: str
    industry: str
    market_cap: float
    current_price: float
    pb: float | None
    pe: float | None
    score: float


def screen_stocks(criteria: ScreenerCriteria) -> list[ScreenerResult]:
    df = _fetch_market_data()
    df = _apply_filters(df, criteria)
    df = _score(df)
    df = df.sort_values("score", ascending=False).head(criteria.top_n)
    return _to_results(df)


def _fetch_market_data() -> pd.DataFrame:
    pro = _get_pro()
    trade_date = _latest_trade_date()

    val_df = pro.daily_basic(
        trade_date=trade_date,
        fields="ts_code,close,pe_ttm,pb,total_mv",
    )
    info_df = pro.stock_basic(
        fields="ts_code,name,industry",
        list_status="L",
    )

    df = pd.merge(val_df, info_df, on="ts_code", how="left")

    for col in ["close", "pe_ttm", "pb", "total_mv"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df["market_cap"] = df["total_mv"] / 10000  # 万元→亿元
    df["code"] = df["ts_code"].str.split(".").str[0]
    return df.dropna(subset=["close", "market_cap"])


def _apply_filters(df: pd.DataFrame, c: ScreenerCriteria) -> pd.DataFrame:
    if c.market_cap_min is not None:
        df = df[df["market_cap"] >= c.market_cap_min]
    if c.market_cap_max is not None:
        df = df[df["market_cap"] <= c.market_cap_max]
    if c.pb_max is not None:
        df = df[df["pb"] <= c.pb_max]
    if c.pb_min is not None:
        df = df[df["pb"] >= c.pb_min]
    if c.pe_max is not None:
        df = df[(df["pe_ttm"] > 0) & (df["pe_ttm"] <= c.pe_max)]
    if c.pe_min is not None:
        df = df[df["pe_ttm"] >= c.pe_min]
    if c.industry is not None:
        df = df[df["industry"].str.contains(c.industry, na=False)]
    if c.exclude_industries:
        pattern = "|".join(c.exclude_industries)
        df = df[~df["industry"].str.contains(pattern, na=False)]
    return df


def _score(df: pd.DataFrame) -> pd.DataFrame:
    """综合评分：低PB 50% + 低PE 50%"""
    d = df.copy()

    def norm_desc(series):
        s = pd.to_numeric(series, errors="coerce")
        valid = s.dropna()
        if valid.max() == valid.min():
            return pd.Series(0.5, index=series.index)
        return 1 - (s - valid.min()) / (valid.max() - valid.min())

    pb_score = norm_desc(d["pb"]).fillna(0)
    pe_score = norm_desc(d["pe_ttm"]).fillna(0)
    d["score"] = pb_score * 0.5 + pe_score * 0.5
    return d


def _to_results(df: pd.DataFrame) -> list[ScreenerResult]:
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
