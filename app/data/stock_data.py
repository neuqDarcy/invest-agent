import tushare as ts
import pandas as pd
from dataclasses import dataclass
from functools import lru_cache
from app.core.config import settings

# 初始化 Tushare Pro 接口
def _get_pro():
    """
    获取 Tushare Pro API 实例。

    每次调用都重新设置 token 并返回新实例，
    调用方负责控制调用频率，避免超出接口限额。
    """
    ts.set_token(settings.tushare_token)
    return ts.pro_api()


# 股票列表进程级缓存（首次调用时从 Tushare 加载，进程重启后失效）
_stock_list_cache: pd.DataFrame | None = None


def _get_stock_list() -> pd.DataFrame:
    """
    获取全量上市股票列表（含代码、名称、行业、市场）。

    使用进程级缓存，首次调用耗时约 1~2 秒，后续直接返回内存数据。

    返回：
        DataFrame，包含列：ts_code / name / industry / market / code（纯数字代码）
    """
    global _stock_list_cache
    if _stock_list_cache is None:
        pro = _get_pro()
        stock_df = pro.stock_basic(
            fields="ts_code,name,industry,market",
            list_status="L",  # 只取在市股票
        )
        # 从 "600519.SH" 中拆分出纯数字代码 "600519"，方便前端按代码前缀搜索
        stock_df["code"] = stock_df["ts_code"].str.split(".").str[0]
        _stock_list_cache = stock_df
    return _stock_list_cache


def search_stocks(keyword: str, limit: int = 10) -> list[dict]:
    """
    模糊搜索股票：支持代码前缀、名称包含。

    搜索策略：代码前缀精确匹配排在名称模糊匹配之前，
    保证输入纯数字时优先返回对应股票。

    参数：
        keyword: 搜索关键词（代码前缀或名称片段）
        limit:   最多返回条数，默认 10

    返回：
        列表，每项包含 code / ts_code / name / industry
    """
    if not keyword.strip():
        return []

    stock_df = _get_stock_list()
    kw = keyword.strip()

    # 代码精确前缀匹配优先（如输入 "600" 先匹配所有 600 开头的股票）
    code_match = stock_df[stock_df["code"].str.startswith(kw)]
    # 名称包含匹配（如输入 "茅台"）
    name_match = stock_df[stock_df["name"].str.contains(kw, na=False)]
    # 合并去重，代码匹配行排在前面
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
    """个股基础行情与估值快照（对应单日数据）。"""
    code: str
    ts_code: str
    name: str
    current_price: float
    market_cap: float       # 总市值（亿元）
    circ_mv: float | None   # 流通市值（亿元）
    pe: float | None        # 市盈率（动态）
    pe_ttm: float | None    # 市盈率（TTM）
    pb: float | None        # 市净率
    dv_ratio: float | None  # 股息率（%）
    total_share: float | None  # 总股本（万股）
    float_share: float | None  # 流通股（万股）
    eps: float | None       # 每股收益
    bps: float | None       # 每股净资产
    week52_high: float | None  # 近 52 周最高价
    week52_low: float | None   # 近 52 周最低价


@dataclass
class StockValuationHistory:
    """个股历史估值序列，用于计算历史分位。"""
    code: str
    pb_series: pd.Series    # 历史 PB，index 为交易日期
    pe_series: pd.Series    # 历史 PE TTM，index 为交易日期


@dataclass
class IndustryValuation:
    """行业估值均值（中位数），用于横向对比。"""
    industry_name: str
    avg_pb: float
    avg_pe: float


def _to_ts_code(code: str) -> str:
    """
    将纯数字股票代码转换为 Tushare 格式（含交易所后缀）。

    规则：6 开头为沪市（.SH），其余为深市（.SZ）。
    若已包含 "." 则原样返回，避免重复转换。

    示例：'600519' → '600519.SH'，'000001' → '000001.SZ'
    """
    if "." in code:
        return code
    return f"{code}.SH" if code.startswith("6") else f"{code}.SZ"


# 行情短时缓存：避免同一股票在 1 分钟内重复请求 Tushare，降低接口调用频率
_basic_cache: dict[str, tuple] = {}  # 格式：code -> (StockBasicInfo, 缓存时间戳)
_BASIC_CACHE_TTL = 60  # 缓存有效期（秒）


def get_stock_basic(code: str) -> StockBasicInfo:
    """
    获取个股完整行情与估值数据（含 1 分钟本地缓存）。

    并行请求三类数据以缩短响应时间：
      1. 当日行情与估值（PE/PB/市值等）
      2. 近 52 周最高/最低价
      3. 最新年报 EPS/BPS

    参数：
        code: 纯数字股票代码，如 "600519"

    返回：
        StockBasicInfo 实例

    异常：
        ValueError: 当 Tushare 未返回任何行情数据时
    """
    import time as _time
    current_time = _time.time()

    # 命中缓存则直接返回，避免重复 I/O
    if code in _basic_cache:
        cached_info, cache_timestamp = _basic_cache[code]
        if current_time - cache_timestamp < _BASIC_CACHE_TTL:
            return cached_info

    pro = _get_pro()
    ts_code = _to_ts_code(code)
    today = pd.Timestamp.now().strftime("%Y%m%d")
    week52_start = (pd.Timestamp.now() - pd.DateOffset(weeks=52)).strftime("%Y%m%d")

    from concurrent.futures import ThreadPoolExecutor

    def _fetch_daily_basic():
        """拉取今年以来的日行情估值数据，取最新一条。"""
        return pro.daily_basic(
            ts_code=ts_code, start_date="20250101", end_date=today,
            fields="ts_code,trade_date,close,pe,pe_ttm,pb,dv_ratio,total_mv,circ_mv,total_share,float_share",
        )

    def _fetch_week52_range():
        """拉取近 52 周日线，计算最高价和最低价。"""
        try:
            week52_df = pro.daily(ts_code=ts_code, start_date=week52_start, end_date=today,
                                  fields="trade_date,high,low")
            if week52_df is not None and not week52_df.empty:
                return round(float(week52_df["high"].max()), 2), round(float(week52_df["low"].min()), 2)
        except Exception:
            pass
        return None, None

    def _fetch_eps_bps():
        """从最新年报财务指标中拉取 EPS 和 BPS。"""
        try:
            annual_period = _latest_annual_period()
            fina_df = pro.fina_indicator(ts_code=ts_code, period=annual_period,
                                         fields="ts_code,end_date,eps,bps")
            if fina_df is not None and not fina_df.empty:
                return _safe_float(fina_df.iloc[0].get("eps")), _safe_float(fina_df.iloc[0].get("bps"))
        except Exception:
            pass
        return None, None

    # 三类数据并行拉取，总耗时取决于最慢的一个
    with ThreadPoolExecutor(max_workers=3) as executor:
        future_basic   = executor.submit(_fetch_daily_basic)
        future_week52  = executor.submit(_fetch_week52_range)
        future_eps_bps = executor.submit(_fetch_eps_bps)
        daily_basic_df          = future_basic.result()
        week52_high, week52_low = future_week52.result()
        eps, bps                = future_eps_bps.result()

    if daily_basic_df is None or daily_basic_df.empty:
        raise ValueError(f"未找到行情数据：{code}")

    # 按交易日排序后取最后一行，确保拿到最新数据
    latest_row = daily_basic_df.sort_values("trade_date").iloc[-1]

    # 股票名称从本地缓存取，避免额外请求
    stock_name = code  # 兜底：若本地缓存无数据则显示代码
    try:
        stock_list_df = _get_stock_list()
        matched_rows = stock_list_df[stock_list_df["code"] == code]
        if not matched_rows.empty:
            stock_name = matched_rows.iloc[0]["name"]
    except Exception:
        pass

    result = StockBasicInfo(
        code=code,
        ts_code=ts_code,
        name=stock_name,
        current_price=float(latest_row["close"]),
        # Tushare 返回的 total_mv 单位为万元，除以 10000 转换为亿元
        market_cap=round(float(latest_row["total_mv"]) / 10000, 2),
        circ_mv=round(float(latest_row["circ_mv"]) / 10000, 2) if _safe_float(latest_row.get("circ_mv")) else None,
        pe=_safe_float(latest_row.get("pe")),
        pe_ttm=_safe_float(latest_row.get("pe_ttm")),
        pb=_safe_float(latest_row.get("pb")),
        dv_ratio=_safe_float(latest_row.get("dv_ratio")),
        total_share=round(float(latest_row["total_share"]), 2) if _safe_float(latest_row.get("total_share")) else None,
        float_share=round(float(latest_row["float_share"]), 2) if _safe_float(latest_row.get("float_share")) else None,
        eps=eps,
        bps=bps,
        week52_high=week52_high,
        week52_low=week52_low,
    )
    _basic_cache[code] = (result, current_time)
    return result


def _latest_annual_period() -> str:
    """
    返回最近已可用的年报财务期（格式：YYYYMMDD）。

    年报披露截止日为次年 4 月 30 日，因此：
    - 4 月 30 日前：使用前年年报（当年年报尚未全部披露）
    - 5 月 1 日起：使用去年年报（已全部披露）

    返回：
        如 "20231231"
    """
    now = pd.Timestamp.now()
    # 4 月底前年报未全部披露，往前多取一年以确保数据完整性
    if now.month <= 4:
        return f"{now.year - 2}1231"
    return f"{now.year - 1}1231"


_history_cache: dict[str, tuple] = {}  # 格式："{code}_{years}" -> (StockValuationHistory, 缓存时间戳)
_HISTORY_CACHE_TTL = 300  # 历史数据变化慢，缓存 5 分钟


def get_stock_valuation_history(code: str, years: int = 10) -> StockValuationHistory:
    """
    获取个股历史 PB/PE TTM 序列（含 5 分钟缓存）。

    参数：
        code:  纯数字股票代码
        years: 获取近几年数据，默认 10 年

    返回：
        StockValuationHistory，包含按日期索引的 pb_series 和 pe_series

    异常：
        ValueError: 当 Tushare 未返回任何历史数据时
    """
    import time as _time
    cache_key = f"{code}_{years}"
    current_time = _time.time()

    if cache_key in _history_cache:
        cached_history, cache_timestamp = _history_cache[cache_key]
        if current_time - cache_timestamp < _HISTORY_CACHE_TTL:
            return cached_history

    pro = _get_pro()
    ts_code = _to_ts_code(code)

    start_date = (pd.Timestamp.now() - pd.DateOffset(years=years)).strftime("%Y%m%d")
    end_date = pd.Timestamp.now().strftime("%Y%m%d")

    history_df = pro.daily_basic(
        ts_code=ts_code,
        start_date=start_date,
        end_date=end_date,
        fields="trade_date,pe_ttm,pb",
    )
    if history_df is None or history_df.empty:
        raise ValueError(f"历史估值数据不足：{code}")

    history_df["trade_date"] = pd.to_datetime(history_df["trade_date"])
    history_df = history_df.sort_values("trade_date").set_index("trade_date")

    result = StockValuationHistory(
        code=code,
        pb_series=pd.to_numeric(history_df["pb"], errors="coerce").dropna(),
        pe_series=pd.to_numeric(history_df["pe_ttm"], errors="coerce").dropna(),
    )
    _history_cache[cache_key] = (result, current_time)
    return result


@lru_cache(maxsize=1)
def get_industry_valuation_map() -> dict[str, IndustryValuation]:
    """
    获取申万行业估值均值（中位数），结果进程级缓存。

    使用中位数而非均值，是为了排除极端高估/低估个股的干扰，
    更准确反映行业整体估值水平。

    返回：
        dict，key 为行业名称，value 为 IndustryValuation
    """
    pro = _get_pro()
    today = pd.Timestamp.now().strftime("%Y%m%d")

    # 获取全市场当日估值快照
    all_valuation_df = pro.daily_basic(
        trade_date=today,
        fields="ts_code,pe_ttm,pb",
    )
    industry_df = pro.stock_basic(
        fields="ts_code,industry",
        list_status="L",
    )
    if all_valuation_df is None or industry_df is None:
        return {}

    merged_df = pd.merge(all_valuation_df, industry_df, on="ts_code")
    merged_df["pe_ttm"] = pd.to_numeric(merged_df["pe_ttm"], errors="coerce")
    merged_df["pb"] = pd.to_numeric(merged_df["pb"], errors="coerce")
    # 过滤负值：负 PE/PB 通常为亏损股，纳入计算会严重拉低行业均值
    merged_df = merged_df[(merged_df["pe_ttm"] > 0) & (merged_df["pb"] > 0)]

    result = {}
    for industry_name, industry_group in merged_df.groupby("industry"):
        result[industry_name] = IndustryValuation(
            industry_name=industry_name,
            avg_pb=round(industry_group["pb"].median(), 2),
            avg_pe=round(industry_group["pe_ttm"].median(), 2),
        )
    return result


def get_pb_percentile(history: StockValuationHistory) -> dict:
    """
    计算当前 PB 在历史序列中的分位数及关键分位点。

    分位数越低说明当前估值越便宜（相对历史），常用于判断安全边际。

    参数：
        history: StockValuationHistory 实例（含历史 PB 序列）

    返回：
        dict，包含当前 PB、历史分位、10/30/70/90 分位点及极值
    """
    pb_series = history.pb_series.dropna()
    if pb_series.empty:
        return {}
    current_pb = pb_series.iloc[-1]
    # 计算当前 PB 处于历史所有数据点的百分比位置
    pb_percentile = (pb_series < current_pb).sum() / len(pb_series) * 100
    return {
        "current_pb": round(current_pb, 2),
        "pb_percentile": round(pb_percentile, 1),
        "pb_10pct": round(pb_series.quantile(0.10), 2),
        "pb_30pct": round(pb_series.quantile(0.30), 2),
        "pb_70pct": round(pb_series.quantile(0.70), 2),
        "pb_90pct": round(pb_series.quantile(0.90), 2),
        "pb_min": round(pb_series.min(), 2),
        "pb_max": round(pb_series.max(), 2),
    }


def _safe_float(val) -> float | None:
    """
    安全地将任意值转换为 float，处理 None / NaN / 非数值字符串。

    Tushare 接口偶尔返回空字符串或 NaN，直接 float() 会抛异常，
    此函数统一兜底，返回 None 表示数据缺失。

    参数：
        val: 任意输入值

    返回：
        float 或 None
    """
    try:
        float_val = float(val)
        return None if pd.isna(float_val) else float_val
    except (TypeError, ValueError):
        return None
