"""
自选列表：用户关注的股票，按用户隔离。
"""
from app.knowledge.store import _get_db


def _ensure_table():
    with _get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS watchlist (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     TEXT NOT NULL,
                stock_code  TEXT NOT NULL,
                stock_name  TEXT,
                added_at    TEXT DEFAULT (datetime('now','localtime')),
                UNIQUE(user_id, stock_code)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_watchlist_user ON watchlist(user_id)")
        conn.commit()


def add_to_watchlist(user_id: str, stock_code: str, stock_name: str = "") -> dict:
    _ensure_table()
    with _get_db() as conn:
        try:
            conn.execute(
                "INSERT INTO watchlist (user_id, stock_code, stock_name) VALUES (?,?,?)",
                (user_id, stock_code, stock_name or ""),
            )
            conn.commit()
        except Exception as e:
            if "UNIQUE" in str(e):
                pass  # 已存在，忽略
    return {"stock_code": stock_code, "stock_name": stock_name}


def remove_from_watchlist(user_id: str, stock_code: str):
    _ensure_table()
    with _get_db() as conn:
        conn.execute(
            "DELETE FROM watchlist WHERE user_id=? AND stock_code=?",
            (user_id, stock_code),
        )
        conn.commit()


def get_watchlist(user_id: str) -> list[dict]:
    _ensure_table()
    with _get_db() as conn:
        rows = conn.execute(
            "SELECT stock_code, stock_name, added_at FROM watchlist WHERE user_id=? ORDER BY added_at DESC",
            (user_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def is_in_watchlist(user_id: str, stock_code: str) -> bool:
    _ensure_table()
    with _get_db() as conn:
        row = conn.execute(
            "SELECT 1 FROM watchlist WHERE user_id=? AND stock_code=?",
            (user_id, stock_code),
        ).fetchone()
    return row is not None
