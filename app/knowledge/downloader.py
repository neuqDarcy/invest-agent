import os
import time
import requests
from dataclasses import dataclass
from app.core.config import settings

CNINFO_BASE = "https://static.cninfo.com.cn/"
CNINFO_QUERY_URL = "https://www.cninfo.com.cn/new/data/query"
CNINFO_ANN_URL = "https://www.cninfo.com.cn/new/hisAnnouncement/query"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Referer": "https://www.cninfo.com.cn",
}

# 报告类型映射
REPORT_CATEGORIES = {
    "annual":    "category_ndbg_szsh",   # 年度报告
    "semi":      "category_bndbg_szsh",  # 半年度报告
    "q1":        "category_yjdbg_szsh",  # 一季报
    "q3":        "category_sjdbg_szsh",  # 三季报
    "dividend":  "category_fhgg_szsh",   # 分红送股公告
    "important": "category_lshgg_szsh",  # 临时重要公告
}

MARKET_COLUMN = {
    "sh": "sse",
    "sz": "szse",
}


@dataclass
class AnnReport:
    title: str
    report_type: str       # annual / semi / q1 / q3
    ann_date: str          # 公告日期 YYYY-MM-DD
    pdf_url: str           # 完整下载链接
    local_path: str | None = None


def _get_org_info(code: str) -> tuple[str, str]:
    """返回 (orgId, market)，如 ('gssh0600519', 'sh')"""
    res = requests.post(
        CNINFO_QUERY_URL,
        data=f"keyWord={code}&timeout=10000",
        headers={**HEADERS, "Content-Type": "application/x-www-form-urlencoded"},
        timeout=10,
    )
    items = res.json()
    if not items:
        raise ValueError(f"巨潮未找到股票：{code}")
    item = items[0]
    return item["orgId"], item["market"]


def fetch_report_list(
    code: str,
    report_type: str = "annual",
    start_year: int = 2019,
    end_year: int = 2024,
) -> list[AnnReport]:
    """获取指定公司的报告列表（不下载）"""
    if report_type not in REPORT_CATEGORIES:
        raise ValueError(f"不支持的报告类型：{report_type}，可选：{list(REPORT_CATEGORIES)}")

    org_id, market = _get_org_info(code)
    category = REPORT_CATEGORIES[report_type]
    column = MARKET_COLUMN.get(market, "sse")

    payload = {
        "stock": f"{code},{org_id}",
        "category": category,
        "pageNum": 1,
        "pageSize": 50,
        "tabName": "fulltext",
        "column": column,
        "seDate": f"{start_year}-01-01~{end_year}-12-31",
        "isHLtitle": True,
    }
    # 分红公告额外加搜索关键词，精准定位利润分配公告
    if report_type == "dividend":
        payload["searchkey"] = "利润分配"
    res = requests.post(
        CNINFO_ANN_URL,
        data=payload,
        headers={**HEADERS, "Content-Type": "application/x-www-form-urlencoded"},
        timeout=15,
    )
    announcements = res.json().get("announcements") or []

    results = []
    for ann in announcements:
        title = ann.get("announcementTitle", "")
        adj_url = ann.get("adjunctUrl", "")
        ts = ann.get("announcementTime")
        ann_date = str(ts)[:10] if isinstance(ts, str) else (
            __import__("datetime").datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d") if ts else ""
        )

        # 年报过滤摘要/英文版；公告类不过滤（每条都有价值）
        is_report = report_type in ("annual", "semi", "q1", "q3")
        if is_report and any(kw in title for kw in ["摘要", "英文", "更新"]):
            continue
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
    code: str,
    reports: list[AnnReport],
) -> list[AnnReport]:
    """下载报告列表到本地，返回带 local_path 的列表"""
    save_dir = os.path.join(settings.upload_dir, "reports", code)
    os.makedirs(save_dir, exist_ok=True)

    downloaded = []
    for report in reports:
        filename = f"{code}_{report.report_type}_{report.ann_date}.pdf"
        local_path = os.path.join(save_dir, filename)

        if os.path.exists(local_path):
            report.local_path = local_path
            downloaded.append(report)
            continue

        try:
            res = requests.get(report.pdf_url, headers=HEADERS, timeout=60)
            res.raise_for_status()
            with open(local_path, "wb") as f:
                f.write(res.content)
            report.local_path = local_path
            downloaded.append(report)
            time.sleep(0.5)  # 避免请求过快
        except Exception as e:
            print(f"下载失败：{report.title} — {e}")

    return downloaded
