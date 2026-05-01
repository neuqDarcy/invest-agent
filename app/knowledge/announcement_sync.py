"""
公告知识库同步服务。
触发时机：
  1. 用户查看股票详情时（后台异步）
  2. 每日定时任务
"""
import threading
import logging
from datetime import datetime, timedelta
from app.knowledge.downloader import fetch_report_list, download_reports
from app.knowledge.store import is_indexed, save_report_index, index_document
from app.parsers.pdf_parser import parse_pdf
from app.core.logger import get_logger

logger = get_logger("announcement_sync")

# 公告类型：只同步最近1年的重要公告
ANNOUNCEMENT_TYPES = [
    "dividend",   # 分红送股
    "semi",       # 半年报
    "q1",         # 一季报
    "q3",         # 三季报
]

# 防止同一股票短时间内重复触发
_syncing: set[str] = set()
_sync_lock = threading.Lock()


def sync_announcements(stock_code: str, force: bool = False) -> dict:
    """
    同步指定股票的公告到知识库。
    force=True 时忽略防重复锁（用于定时任务）。
    返回同步结果摘要。
    """
    with _sync_lock:
        if stock_code in _syncing and not force:
            return {"status": "skipped", "reason": "already_syncing"}
        _syncing.add(stock_code)

    try:
        return _do_sync(stock_code)
    finally:
        with _sync_lock:
            _syncing.discard(stock_code)


def _do_sync(stock_code: str) -> dict:
    now = datetime.now()
    start_year = now.year - 1  # 同步近2年
    end_year = now.year + 1

    total = indexed = skipped = failed = 0
    details = []

    for ann_type in ANNOUNCEMENT_TYPES:
        try:
            reports = fetch_report_list(
                stock_code,
                report_type=ann_type,
                start_year=start_year,
                end_year=end_year,
            )
        except Exception as e:
            logger.warning(f"获取公告列表失败 {stock_code}/{ann_type}: {e}")
            continue

        for report in reports:
            total += 1
            if is_indexed(stock_code, report.report_type, report.ann_date):
                skipped += 1
                continue

            downloaded = download_reports(stock_code, [report])
            if not downloaded or not downloaded[0].local_path:
                failed += 1
                logger.warning(f"下载失败: {report.title}")
                continue

            try:
                doc = parse_pdf(downloaded[0].local_path)
                chunk_count = index_document(
                    doc=doc,
                    stock_code=stock_code,
                    report_type=report.report_type,
                    ann_date=report.ann_date,
                    title=report.title,
                )
                save_report_index(
                    stock_code, report.report_type, report.ann_date,
                    report.title, downloaded[0].local_path,
                )
                indexed += 1
                details.append(f"[成功] {report.title}（{chunk_count}段）")
                logger.info(f"公告入库: {stock_code} {report.title}")
            except Exception as e:
                failed += 1
                logger.error(f"入库失败 {report.title}: {e}")

    return {
        "stock_code": stock_code,
        "total": total,
        "indexed": indexed,
        "skipped": skipped,
        "failed": failed,
        "details": details,
    }


def sync_in_background(stock_code: str):
    """后台线程异步同步，不阻塞主请求"""
    t = threading.Thread(target=sync_announcements, args=(stock_code,), daemon=True)
    t.start()
