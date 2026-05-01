"""
对话记录持久化：按股票代码存储用户和 LLM 的对话，支持跨会话恢复。
"""
import json
from app.knowledge.store import _get_db


def _ensure_table():
    with _get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS chat_history (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                stock_code  TEXT,               -- 关联股票代码，可为空（通用对话）
                role        TEXT NOT NULL,       -- 'user' | 'assistant'
                content     TEXT NOT NULL,
                sources     TEXT,               -- JSON 数组，仅 assistant 有
                route       TEXT,               -- 路由类型
                created_at  TEXT DEFAULT (datetime('now','localtime'))
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_chat_stock ON chat_history(stock_code, created_at)")
        conn.commit()


def save_message(
    role: str,
    content: str,
    stock_code: str = "",
    sources: list[str] | None = None,
    route: str = "",
    user_id: str = "",
):
    _ensure_table()
    with _get_db() as conn:
        conn.execute(
            "INSERT INTO chat_history (user_id, stock_code, role, content, sources, route) VALUES (?,?,?,?,?,?)",
            (user_id or None, stock_code or None, role, content,
             json.dumps(sources, ensure_ascii=False) if sources else None,
             route or None),
        )
        conn.commit()


def get_history(stock_code: str | None = None, limit: int = 50, user_id: str = "") -> list[dict]:
    """获取对话历史，按用户和股票代码过滤"""
    _ensure_table()
    with _get_db() as conn:
        if user_id and stock_code:
            rows = conn.execute(
                "SELECT * FROM chat_history WHERE user_id=? AND stock_code=? ORDER BY created_at DESC LIMIT ?",
                (user_id, stock_code, limit),
            ).fetchall()
        elif user_id:
            rows = conn.execute(
                "SELECT * FROM chat_history WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
        elif stock_code:
            rows = conn.execute(
                "SELECT * FROM chat_history WHERE stock_code=? ORDER BY created_at DESC LIMIT ?",
                (stock_code, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM chat_history WHERE stock_code IS NULL ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()

    results = []
    for r in reversed(list(rows)):  # 反转为正序
        d = dict(r)
        if d.get("sources"):
            try:
                d["sources"] = json.loads(d["sources"])
            except Exception:
                d["sources"] = []
        results.append(d)
    return results


def clear_history(stock_code: str | None = None, user_id: str = ""):
    _ensure_table()
    with _get_db() as conn:
        if user_id and stock_code:
            conn.execute("DELETE FROM chat_history WHERE user_id=? AND stock_code=?", (user_id, stock_code))
        elif user_id:
            conn.execute("DELETE FROM chat_history WHERE user_id=?", (user_id,))
        elif stock_code:
            conn.execute("DELETE FROM chat_history WHERE stock_code=?", (stock_code,))
        else:
            conn.execute("DELETE FROM chat_history WHERE stock_code IS NULL")
        conn.commit()
