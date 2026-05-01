"""
每日定时任务：同步所有已建立知识库的股票的最新公告。
运行方式：python -m app.tasks.daily_sync
或通过 cron：0 8 * * * cd /path/to/project && python -m app.tasks.daily_sync
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import sqlite3
from app.core.config import settings
from app.knowledge.announcement_sync import sync_announcements
from app.core.logger import setup_logger, get_logger

setup_logger()
logger = get_logger("daily_sync")


def get_tracked_stocks() -> list[str]:
    """获取所有已建立知识库的股票代码"""
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT DISTINCT stock_code FROM report_index ORDER BY stock_code"
    ).fetchall()
    conn.close()
    return [r["stock_code"] for r in rows]


def run():
    stocks = get_tracked_stocks()
    if not stocks:
        logger.info("没有需要同步的股票")
        return

    logger.info(f"开始每日公告同步，共 {len(stocks)} 只股票：{stocks}")
    total_indexed = 0

    for code in stocks:
        result = sync_announcements(code, force=True)
        if result.get("indexed", 0) > 0:
            logger.info(f"{code}: 新入库 {result['indexed']} 条公告")
            total_indexed += result["indexed"]
        else:
            logger.debug(f"{code}: 无新公告")

    logger.info(f"每日同步完成，共新入库 {total_indexed} 条公告")


if __name__ == "__main__":
    run()
