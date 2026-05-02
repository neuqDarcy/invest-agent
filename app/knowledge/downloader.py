import os
import time
import requests
from dataclasses import dataclass
from app.core.config import settings

# 巨潮资讯网基础 URL
CNINFO_BASE = "https://static.cninfo.com.cn/"
# 股票信息查询接口（用于获取 orgId 和市场）
CNINFO_QUERY_URL = "https://www.cninfo.com.cn/new/data/query"
# 历史公告查询接口
CNINFO_ANN_URL = "https://www.cninfo.com.cn/new/hisAnnouncement/query"

# 模拟浏览器请求头，避免被反爬拦截
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Referer": "https://www.cninfo.com.cn",
}

# 报告类型映射：业务简称 → 巨潮分类代码
REPORT_CATEGORIES = {
    "annual":    "category_ndbg_szsh",   # 年度报告
    "semi":      "category_bndbg_szsh",  # 半年度报告
    "q1":        "category_yjdbg_szsh",  # 一季报
    "q3":        "category_sjdbg_szsh",  # 三季报
    "dividend":  "category_fhgg_szsh",   # 分红送股公告
    "important": "category_lshgg_szsh",  # 临时重要公告
}

# 交易所代码映射：市场标识 → 巨潮列名
MARKET_COLUMN = {
    "sh": "sse",   # 上交所
    "sz": "szse",  # 深交所
}


@dataclass
class AnnReport:
    """单份公告报告的元数据。"""
    title: str
    report_type: str       # annual / semi / q1 / q3
    ann_date: str          # 公告日期 YYYY-MM-DD
    pdf_url: str           # 完整下载链接
    local_path: str | None = None


def _get_org_info(stock_code: str) -> tuple[str, str]:
    """
    通过股票代码查询巨潮机构信息。

    参数:
        stock_code: A 股代码，如 '600519'

    返回:
        (orgId, market) 元组，如 ('gssh0600519', 'sh')
    """
    response = requests.post(
        CNINFO_QUERY_URL,
        data=f"keyWord={stock_code}&timeout=10000",
        headers={**HEADERS, "Content-Type": "application/x-www-form-urlencoded"},
        timeout=10,
    )
    items = response.json()
    if not items:
        raise ValueError(f"巨潮未找到股票：{stock_code}")
    first_item = items[0]
    return first_item["orgId"], first_item["market"]


def fetch_report_list(
    stock_code: str,
    report_type: str = "annual",
    start_year: int = 2019,
    end_year: int = 2024,
) -> list[AnnReport]:
    """
    获取指定公司在给定年份范围内的公告列表（只查询，不下载文件）。

    参数:
        stock_code:  A 股代码，如 '600519'
        report_type: 报告类型，见 REPORT_CATEGORIES 键名
        start_year:  起始年份（含）
        end_year:    结束年份（含）

    返回:
        AnnReport 列表，每项包含标题、日期和 PDF 链接
    """
    if report_type not in REPORT_CATEGORIES:
        raise ValueError(f"不支持的报告类型：{report_type}，可选：{list(REPORT_CATEGORIES)}")

    org_id, market = _get_org_info(stock_code)
    category = REPORT_CATEGORIES[report_type]
    column = MARKET_COLUMN.get(market, "sse")

    payload = {
        "stock": f"{stock_code},{org_id}",
        "category": category,
        "pageNum": 1,
        "pageSize": 50,
        "tabName": "fulltext",
        "column": column,
        "seDate": f"{start_year}-01-01~{end_year}-12-31",
        "isHLtitle": True,
    }
    # 分红公告需额外加关键词，否则会混入其他股东大会公告
    if report_type == "dividend":
        payload["searchkey"] = "利润分配"

    response = requests.post(
        CNINFO_ANN_URL,
        data=payload,
        headers={**HEADERS, "Content-Type": "application/x-www-form-urlencoded"},
        timeout=15,
    )
    announcements = response.json().get("announcements") or []

    results = []
    for ann in announcements:
        title = ann.get("announcementTitle", "")
        adj_url = ann.get("adjunctUrl", "")
        timestamp = ann.get("announcementTime")

        # 将时间戳统一转为 YYYY-MM-DD 字符串
        if isinstance(timestamp, str):
            ann_date = str(timestamp)[:10]
        elif timestamp:
            ann_date = __import__("datetime").datetime.fromtimestamp(
                timestamp / 1000
            ).strftime("%Y-%m-%d")
        else:
            ann_date = ""

        # 年/季/半年报：过滤摘要、英文版、更新版，避免重复入库
        is_periodic_report = report_type in ("annual", "semi", "q1", "q3")
        if is_periodic_report and any(kw in title for kw in ["摘要", "英文", "更新"]):
            continue
        # 只处理 PDF 附件，跳过 HTML 公告
        if not adj_url.upper().endswith(".PDF"):
            continue

        results.append(AnnReport(
            title=title,
            report_type=report_type,
            ann_date=ann_date,
            pdf_url=CNINFO_BASE + adj_url,
        ))

    return results


def download_reports(
    stock_code: str,
    reports: list[AnnReport],
) -> list[AnnReport]:
    """
    将公告 PDF 下载到本地，已存在则跳过。

    参数:
        stock_code: A 股代码，用于构建本地存储目录
        reports:    待下载的 AnnReport 列表

    返回:
        成功下载（或已存在）的 AnnReport 列表，每项 local_path 已填充
    """
    save_dir = os.path.join(settings.upload_dir, "reports", stock_code)
    os.makedirs(save_dir, exist_ok=True)

    downloaded = []
    for report in reports:
        filename = f"{stock_code}_{report.report_type}_{report.ann_date}.pdf"
        local_path = os.path.join(save_dir, filename)

        # 文件已存在则直接复用，避免重复下载
        if os.path.exists(local_path):
            report.local_path = local_path
            downloaded.append(report)
            continue

        try:
            response = requests.get(report.pdf_url, headers=HEADERS, timeout=60)
            response.raise_for_status()
            with open(local_path, "wb") as file_handle:
                file_handle.write(response.content)
            report.local_path = local_path
            downloaded.append(report)
            # 礼貌性延迟，防止请求过于密集触发反爬
            time.sleep(0.5)
        except Exception as error:
            print(f"下载失败：{report.title} — {error}")

    return downloaded
