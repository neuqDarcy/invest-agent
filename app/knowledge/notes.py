"""
投资笔记：SQLite 存结构化 + Chroma 存向量，支持语义检索。
"""
import json
import uuid
from app.knowledge.store import _get_db, _get_collection


# ── SQLite ────────────────────────────────────────────────────────────────────

def _ensure_table():
    with _get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS notes (
                id          TEXT PRIMARY KEY,
                user_id     TEXT,
                stock_code  TEXT,
                title       TEXT,
                content     TEXT NOT NULL,
                created_at  TEXT DEFAULT (datetime('now','localtime')),
                updated_at  TEXT DEFAULT (datetime('now','localtime'))
            )
        """)
        conn.commit()


def save_note(content: str, stock_code: str = "", title: str = "", user_id: str = "") -> dict:
    """新建笔记，同时入向量库"""
    _ensure_table()
    note_id = str(uuid.uuid4())
    title = title or content[:30].replace("\n", " ")

    with _get_db() as conn:
        conn.execute(
            "INSERT INTO notes (id, user_id, stock_code, title, content) VALUES (?,?,?,?,?)",
            (note_id, user_id or None, stock_code or None, title, content),
        )
        conn.commit()

    # 入向量库
    _index_note(note_id, content, stock_code, title)

    return get_note(note_id)


def update_note(note_id: str, content: str, title: str = "") -> dict:
    """更新笔记内容，同步更新向量"""
    _ensure_table()
    title = title or content[:30].replace("\n", " ")
    with _get_db() as conn:
        conn.execute(
            "UPDATE notes SET content=?, title=?, updated_at=datetime('now','localtime') WHERE id=?",
            (content, title, note_id),
        )
        conn.commit()

    # 更新向量（先删再插）
    col = _get_collection()
    try:
        col.delete(ids=[f"note_{note_id}"])
    except Exception:
        pass
    note = get_note(note_id)
    _index_note(note_id, content, note.get("stock_code", ""), title)
    return note


def delete_note(note_id: str):
    _ensure_table()
    with _get_db() as conn:
        conn.execute("DELETE FROM notes WHERE id=?", (note_id,))
        conn.commit()
    try:
        _get_collection().delete(ids=[f"note_{note_id}"])
    except Exception:
        pass


def get_note(note_id: str) -> dict:
    _ensure_table()
    with _get_db() as conn:
        row = conn.execute("SELECT * FROM notes WHERE id=?", (note_id,)).fetchone()
        return dict(row) if row else {}


def list_notes(stock_code: str | None = None, limit: int = 50, user_id: str = "") -> list[dict]:
    """列出笔记，可按股票代码过滤，并按用户隔离"""
    _ensure_table()
    with _get_db() as conn:
        if user_id and stock_code:
            rows = conn.execute(
                "SELECT * FROM notes WHERE user_id=? AND stock_code=? ORDER BY created_at DESC LIMIT ?",
                (user_id, stock_code, limit),
            ).fetchall()
        elif user_id:
            rows = conn.execute(
                "SELECT * FROM notes WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
        elif stock_code:
            rows = conn.execute(
                "SELECT * FROM notes WHERE stock_code=? ORDER BY created_at DESC LIMIT ?",
                (stock_code, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM notes ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
    return [dict(r) for r in rows]


# ── Chroma ────────────────────────────────────────────────────────────────────

def _index_note(note_id: str, content: str, stock_code: str, title: str):
    col = _get_collection()
    chunk_id = f"note_{note_id}"
    existing = col.get(ids=[chunk_id])
    if not existing["ids"]:
        col.add(
            documents=[f"{title}\n{content}"],
            ids=[chunk_id],
            metadatas=[{
                "stock_code": stock_code or "",
                "report_type": "user_note",
                "ann_date": "",
                "title": title,
                "note_id": note_id,
                "section": "",
                "sub_section": "",
                "sub_sub_section": "",
                "chapter_index": 0,
                "chunk_index": 0,
            }],
        )


def search_notes(query: str, stock_code: str | None = None, top_k: int = 3) -> list[dict]:
    """语义检索相关笔记"""
    col = _get_collection()
    where = {"report_type": "user_note"}
    if stock_code:
        where = {"$and": [{"report_type": "user_note"}, {"stock_code": stock_code}]}
    try:
        results = col.query(query_texts=[query], n_results=top_k, where=where)
        hits = []
        for i, doc in enumerate(results["documents"][0]):
            meta = results["metadatas"][0][i]
            hits.append({"text": doc, "note_id": meta.get("note_id"), "title": meta.get("title")})
        return hits
    except Exception:
        return []
