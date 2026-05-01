import tushare as ts
import pandas as pd
from dataclasses import dataclass
from functools import lru_cache
from app.core.config import settings

# 初始化 Tushare
def _get_pro():
    ts.set_token(settings.tushare_token)
    return ts.pro_api()


# 股票列表缓存（进程级，首次调用时加载）
_stock_list_cache: pd.DataFrame | None = None


def _get_stock_list() -> pd.DataFrame:
    global _stock_list_cache
    if _stock_list_cache is None:
        pro = _get_pro()
        df = pro.stock_basic(
            fields="ts_code,name,industry,market",
            list_status="L",
        )
        df["code"] = df["ts_code"].str.split(".").str[0]
        _stock_list_cache = df
    return _stock_list_cache


def search_stocks(keyword: str, limit: int = 10) -> list[dict]:
    """
    模糊搜索股票：支持代码前缀、名称包含、拼音首字母（简单实现）。
    """
    if not keyword.strip():
        return []

    df = _get_stock_list()
    kw = keyword.strip()

    # 代码精确前缀匹配优先
    code_match = df[df["code"].str.startswith(kw)]
    # 名称包含匹配
    name_match = df[df["name"].str.contains(kw, na=False)]
    # 合并去重，代码匹配排在前面
    combined = pd.concat([code_match, name_match]).drop_duplicates(subset="ts_code")
    combined = combined.head(limit)

    return [
        {
            "code": row["code"],
            "ts_code": row["ts_code"],
            "name": row["name"],
            "industry": row.get("industry", ""),
        }
        for _, row in combined.iterrows()
    ]


@dataclass
class StockBasicInfo:
    code: str
    ts_code: str
    name: str
    current_price: float
    market_cap: float       # 总市值（亿元）
    circ_mv: float | None   # 流通市值（亿元）
    pe: float | None        # 市盈率（动）
    pe_ttm: float | None    # 市盈率（TTM）
    pb: float | None        # 市净率
    dv_ratio: float | None  # 股息率（%）
    total_share: float | None  # 总股本（万股）
    float_share: float | None  # 流通股（万股）
    eps: float | None       # 每股收益
    bps: float | None       # 每股净资产
    week52_high: float | None
    week52_low: float | None


@dataclass
class StockValuationHistory:
    code: str
    pb_series: pd.Series    # 历史 PB，index 为日期
    pe_series: pd.Series    # 历史 PE TTM


@dataclass
class IndustryValuation:
    industry_name: str
    avg_pb: float
    avg_pe: float


def _to_ts_code(code: str) -> str:
    """'600519' → '600519.SH'，'000001' → '000001.SZ'"""
    if "." in code:
        return code
    return f"{code}.SH" if code.startswith("6") else f"{code}.SZ"


# 行情短时缓存：避免同一股票短时间内重复请求 Tushare
_basic_cache: dict[str, tuple] = {}  # code -> (StockBasicInfo, timestamp)
_BASIC_CACHE_TTL = 60  # 秒


def get_stock_basic(code: str) -> StockBasicInfo:
    """获取个股完整行情 + 估值数据（含 1 分钟缓存）"""
    import time as _time
    now = _time.time()
    if code in _basic_cache:
        cached, ts = _basic_cache[code]
        if now - ts < _BASIC_CACHE_TTL:
            return cached

    pro = _get_pro()
    ts_code = _to_ts_code(code)
    today = pd.Timestamp.now().strftime("%Y%m%d")
    week52_start = (pd.Timestamp.now() - pd.DateOffset(weeks=52)).strftime("%Y%m%d")

    from concurrent.futures import ThreadPoolExecutor

    def _fetch_basic():
        return pro.daily_basic(
            ts_code=ts_code, start_date="20250101", end_date=today,
            fields="ts_code,trade_date,close,pe,pe_ttm,pb,dv_ratio,total_mv,circ_mv,total_share,float_share",
        )

    def _fetch_week52():
        try:
            df = pro.daily(ts_code=ts_code, start_date=week52_start, end_date=today,
                           fields="trade_date,high,low")
            if df is not None and not df.empty:
                return round(float(df["high"].max()), 2), round(float(df["low"].min()), 2)
        except Exception:
            pass
        return None, None

    def _fetch_eps_bps():
        try:
            period = _latest_annual_period()
            fi = pro.fina_indicator(ts_code=ts_code, period=period,
                                    fields="ts_code,end_date,eps,bps")
            if fi is not None and not fi.empty:
                return _safe_float(fi.iloc[0].get("eps")), _safe_float(fi.iloc[0].get("bps"))
        except Exception:
            pass
        return None, None

    with ThreadPoolExecutor(max_workers=3) as ex:
        f_basic  = ex.submit(_fetch_basic)
        f_week52 = ex.submit(_fetch_week52)
        f_eps    = ex.submit(_fetch_eps_bps)
        val_df          = f_basic.result()
        week52_high, week52_low = f_week52.result()
        eps, bps        = f_eps.result()

    if val_df is None or val_df.empty:
        raise ValueError(f"未找到行情数据：{code}")
    val_latest = val_df.sort_values("trade_date").iloc[-1]

    # 名称从本地缓存取
    name = code
    try:
        stock_list = _get_stock_list()
        row = stock_list[stock_list["code"] == code]
        if not row.empty:
            name = row.iloc[0]["name"]
    except Exception:
        pass

    result = StockBasicInfo(
        code=code,
        ts_code=ts_code,
        name=name,
        current_price=float(val_latest["close"]),
        market_cap=round(float(val_latest["total_mv"]) / 10000, 2),
        circ_mv=round(float(val_latest["circ_mv"]) / 10000, 2) if _safe_float(val_latest.get("circ_mv")) else None,
        pe=_safe_float(val_latest.get("pe")),
        pe_ttm=_safe_float(val_latest.get("pe_ttm")),
        pb=_safe_float(val_latest.get("pb")),
        dv_ratio=_safe_float(val_latest.get("dv_ratio")),
        total_share=round(float(val_latest["total_share"]), 2) if _safe_float(val_latest.get("total_share")) else None,
        float_share=round(float(val_latest["float_share"]), 2) if _safe_float(val_latest.get("float_share")) else None,
        eps=eps,
        bps=bps,
        week52_high=week52_high,
        week52_low=week52_low,
    )
    _basic_cache[code] = (result, now)
    return result


def _latest_annual_period() -> str:
    """返回最近已披露的年报期。年报在次年4月底前披露，5月后才能用当年数据"""
    now = pd.Timestamp.now()
    # 4月30日前用前年数据，5月1日起用去年数据
    if now.month <= 4:
        return f"{now.year - 2}1231"
    return f"{now.year - 1}1231"


_history_cache: dict[str, tuple] = {}  # code -> (StockValuationHistory, timestamp)
_HISTORY_CACHE_TTL = 300  # 5分钟


def get_stock_valuation_history(code: str, years: int = 10) -> StockValuationHistory:
    """获取个股历史 PB/PE 数据（含 5 分钟缓存）"""
    import time as _time
    cache_key = f"{code}_{years}"
    now = _time.time()
    if cache_key in _history_cache:
        cached, ts = _history_cache[cache_key]
        if now - ts < _HISTORY_CACHE_TTL:
            return cached

    pro = _get_pro()
    ts_code = _to_ts_code(code)

    start = (pd.Timestamp.now() - pd.DateOffset(years=years)).strftime("%Y%m%d")
    end = pd.Timestamp.now().strftime("%Y%m%d")

    df = pro.daily_basic(
        ts_code=ts_code,
        start_date=start,
        end_date=end,
        fields="trade_date,pe_ttm,pb",
    )
    if df is None or df.empty:
        raise ValueError(f"历史估值数据不足：{code}")

    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df = df.sort_values("trade_date").set_index("trade_date")

    result = StockValuationHistory(
        code=code,
        pb_series=pd.to_numeric(df["pb"], errors="coerce").dropna(),
        pe_series=pd.to_numeric(df["pe_ttm"], errors="coerce").dropna(),
    )
    _history_cache[cache_key] = (result, now)
    return result


@lru_cache(maxsize=1)
def get_industry_valuation_map() -> dict[str, IndustryValuation]:
    """获取申万行业估值均值，结果缓存"""
    pro = _get_pro()
    today = pd.Timestamp.now().strftime("%Y%m%d")

    # 获取所有股票最新估值 + 行业分类
    val_df = pro.daily_basic(
        trade_date=today,
        fields="ts_code,pe_ttm,pb",
    )
    industry_df = pro.stock_basic(
        fields="ts_code,industry",
        list_status="L",
    )
    if val_df is None or industry_df is None:
        return {}

    merged = pd.merge(val_df, industry_df, on="ts_code")
    merged["pe_ttm"] = pd.to_numeric(merged["pe_ttm"], errors="coerce")
    merged["pb"] = pd.to_numeric(merged["pb"], errors="coerce")
    merged = merged[(merged["pe_ttm"] > 0) & (merged["pb"] > 0)]

    result = {}
    for industry, group in merged.groupby("industry"):
        result[industry] = IndustryValuation(
            industry_name=industry,
            avg_pb=round(group["pb"].median(), 2),
            avg_pe=round(group["pe_ttm"].median(), 2),
        )
    return result


def get_pb_percentile(history: StockValuationHistory) -> dict:
    """计算当前 PB 在历史分位"""
    pb = history.pb_series.dropna()
    if pb.empty:
        return {}
    current = pb.iloc[-1]
    percentile = (pb < current).sum() / len(pb) * 100
    return {
        "current_pb": round(current, 2),
        "pb_percentile": round(percentile, 1),
        "pb_10pct": round(pb.quantile(0.10), 2),
        "pb_30pct": round(pb.quantile(0.30), 2),
        "pb_70pct": round(pb.quantile(0.70), 2),
        "pb_90pct": round(pb.quantile(0.90), 2),
        "pb_min": round(pb.min(), 2),
        "pb_max": round(pb.max(), 2),
    }


def _safe_float(val) -> float | None:
    try:
        f = float(val)
        return None if pd.isna(f) else f
    except (TypeError, ValueError):
        return None
