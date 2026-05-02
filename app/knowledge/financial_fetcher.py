"""
从 Tushare 拉取三张财务报表，存入 SQLite。
替代 PDF 表格解析，数据更准确可靠。
"""
import tushare as ts
import pandas as pd
from app.core.config import settings
from app.knowledge.store import _get_db


def _get_pro():
    """初始化并返回 Tushare Pro API 客户端。"""
    ts.set_token(settings.tushare_token)
    return ts.pro_api()


def _to_ts_code(stock_code: str) -> str:
    """
    将普通股票代码转换为 Tushare 格式（含交易所后缀）。

    参数:
        stock_code: 如 '600519' 或已含后缀的 '600519.SH'

    返回:
        Tushare 格式代码，如 '600519.SH' 或 '000858.SZ'
    """
    if "." in stock_code:
        return stock_code
    # 沪市股票以 6 开头，其余为深市
    return f"{stock_code}.SH" if stock_code.startswith("6") else f"{stock_code}.SZ"


# 利润表字段映射：Tushare 字段名 → 标准中文指标名
INCOME_FIELDS = {
    "revenue":          "营业收入",
    "total_cogs":       "营业成本",
    "operate_profit":   "营业利润",
    "n_income_attr_p":  "净利润",
    "sell_exp":         "销售费用",
    "admin_exp":        "管理费用",
    "fin_exp":          "财务费用",
    "rd_exp":           "研发费用",
}

# 资产负债表字段映射
BALANCE_FIELDS = {
    "total_assets":              "资产总计",
    "total_liab":                "负债合计",
    "total_hldr_eqy_exc_min_int": "股东权益合计",
    "money_cap":                 "货币资金",
    "accounts_receiv":           "应收账款",
    "inventories":               "存货",
    "fix_assets":                "固定资产",
}

# 现金流量表字段映射
CASHFLOW_FIELDS = {
    "n_cashflow_act":          "经营活动现金流",
    "n_cashflow_inv_act":      "投资活动现金流",
    "n_cash_flows_fnc_act":    "筹资活动现金流",
    "c_pay_acq_const_fiolta":  "资本支出",
}


def _safe_float(raw_value) -> float | None:
    """
    安全地将原始值转为 float，无法转换或为 NaN 时返回 None。

    参数:
        raw_value: 来自 DataFrame 的原始值

    返回:
        有效的 float 数值，或 None
    """
    try:
        # 复数类型无法映射到财务指标，直接排除
        if isinstance(raw_value, complex):
            return None
        converted = float(raw_value)
        return None if pd.isna(converted) else converted
    except (TypeError, ValueError):
        return None


def _fetch_and_save(
    stock_code: str,
    ts_code: str,
    start_date: str,
    end_date: str,
    report_type: str = "1",   # 1=合并报表（默认），2=母公司报表
):
    """
    从 Tushare 拉取三张财务报表并持久化到 SQLite。
    只保留合并报表中的年报数据（end_date 末尾为 1231）。

    参数:
        stock_code:  内部使用的股票代码（不含后缀）
        ts_code:     Tushare 格式代码，如 '600519.SH'
        start_date:  查询起始日期，格式 YYYYMMDD
        end_date:    查询截止日期，格式 YYYYMMDD
        report_type: 报表类型，'1' 为合并报表

    返回:
        成功写入 SQLite 的指标条数
    """
    pro = _get_pro()
    saved_count = 0

    # 三张表的 (API函数, 字段映射, 非空校验字段) 元组列表
    financial_tables = [
        (pro.income,       INCOME_FIELDS,   "revenue"),
        (pro.balancesheet, BALANCE_FIELDS,  "total_assets"),
        (pro.cashflow,     CASHFLOW_FIELDS, "n_cashflow_act"),
    ]

    with _get_db() as conn:
        for api_func, field_map, check_field in financial_tables:
            fields = "ts_code,ann_date,end_date,report_type," + ",".join(field_map.keys())
            try:
                df = api_func(
                    ts_code=ts_code,
                    start_date=start_date,
                    end_date=end_date,
                    fields=fields,
                )
            except Exception as error:
                print(f"拉取失败：{api_func.__name__} — {error}")
                continue

            if df is None or df.empty:
                continue

            # 只保留合并报表 + 年报（end_date 以 1231 结尾），排除季报和半年报
            df = df[df["report_type"] == report_type]
            df = df[df["end_date"].str.endswith("1231")]
            # 同一报告期可能有多次更新公告，取最新一次（ann_date 最大）
            df = df.sort_values("ann_date", ascending=False)
            df = df.drop_duplicates(subset=["end_date"])

            for _, row in df.iterrows():
                year = row["end_date"][:4]
                # 统一用年末日期作为存储 key，便于跨表关联
                period_key = f"{year}-12-31"
                for ts_field, metric_name in field_map.items():
                    metric_value = _safe_float(row.get(ts_field))
                    if metric_value is None:
                        continue
                    conn.execute("""
                        INSERT OR REPLACE INTO financial_metrics
                            (stock_code, ann_date, metric_name, value, unit)
                        VALUES (?, ?, ?, ?, ?)
                    """, (stock_code, period_key, metric_name, metric_value, "元"))
                    saved_count += 1

        conn.commit()

    return saved_count


def fetch_financials(
    stock_code: str,
    start_year: int = 2019,
    end_year: int = 2024,
) -> dict:
    """
    为指定公司拉取三张财务报表并存入 SQLite。
    会先清除该公司的旧数据，再重新写入，保证数据最新。

    参数:
        stock_code: A 股代码，如 '600519'
        start_year: 起始年份（含）
        end_year:   结束年份（含）

    返回:
        {stock_code, saved_count, years} 字典，years 为已入库的年份列表
    """
    ts_code = _to_ts_code(stock_code)
    start_date = f"{start_year}0101"
    end_date = f"{end_year}1231"

    # 先清除旧数据，避免历史脏数据干扰后续分析
    with _get_db() as conn:
        conn.execute(
            "DELETE FROM financial_metrics WHERE stock_code=?",
            (stock_code,)
        )
        conn.commit()

    saved_count = _fetch_and_save(stock_code, ts_code, start_date, end_date)

    # 回查已入库年份，用于调用方展示覆盖范围
    with _get_db() as conn:
        year_rows = conn.execute(
            "SELECT DISTINCT ann_date FROM financial_metrics WHERE stock_code=? ORDER BY ann_date DESC",
            (stock_code,)
        ).fetchall()
    years = [row[0][:4] for row in year_rows]

    return {
        "stock_code": stock_code,
        "saved_count": saved_count,
        "years": years,
    }
