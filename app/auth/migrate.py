"""
数据库迁移：给现有表加 user_id 字段（如果尚未添加）
"""
import sqlite3
from app.core.config import settings


def run_migrations():
    conn = sqlite3.connect(settings.db_path)
    try:
        tables_columns = {
            "notes":        "user_id",
            "chat_history": "user_id",
            "qa_feedback":  "user_id",
        }
        for table, col in tables_columns.items():
            # 检查列是否已存在
            cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
            if col not in cols:
                try:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} TEXT")
                    print(f"已给 {table} 添加 {col} 列")
                except Exception as e:
                    print(f"跳过 {table}.{col}：{e}")
        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    run_migrations()
