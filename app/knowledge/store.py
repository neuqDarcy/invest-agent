"""
知识库存储层：
- SQLite：存结构化财务指标（精确查询）
- Chroma：存文本段落向量（语义检索）
"""
import os
import re
import sqlite3
from dataclasses import dataclass
import chromadb

# 确保 HuggingFace 使用国内镜像
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
from chromadb.utils import embedding_functions
from app.core.config import settings
from app.parsers.pdf_parser import ParsedDocument


# ─── SQLite ───────────────────────────────────────────────────────────────────

def _get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS qa_feedback (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_code  TEXT NOT NULL,
            question    TEXT NOT NULL,
            answer      TEXT NOT NULL,
            sources     TEXT,
            rating      INTEGER NOT NULL,  -- 1=好 0=差
            comment     TEXT,              -- 用户补充说明（可选）
            created_at  TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS report_index (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_code  TEXT NOT NULL,
            report_type TEXT NOT NULL,
            ann_date    TEXT NOT NULL,
            title       TEXT,
            file_path   TEXT,
            indexed_at  TEXT DEFAULT (datetime('now')),
            UNIQUE(stock_code, report_type, ann_date)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS financial_metrics (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_code  TEXT NOT NULL,
            ann_date    TEXT NOT NULL,
            metric_name TEXT NOT NULL,
            value       REAL,
            unit        TEXT,
            UNIQUE(stock_code, ann_date, metric_name)
        )
    """)
    conn.commit()
    return conn


def save_report_index(stock_code: str, report_type: str, ann_date: str,
                      title: str, file_path: str):
    with _get_db() as conn:
        conn.execute("""
            INSERT OR IGNORE INTO report_index(stock_code, report_type, ann_date, title, file_path)
            VALUES (?, ?, ?, ?, ?)
        """, (stock_code, report_type, ann_date, title, file_path))


def is_indexed(stock_code: str, report_type: str, ann_date: str) -> bool:
    with _get_db() as conn:
        row = conn.execute("""
            SELECT id FROM report_index
            WHERE stock_code=? AND report_type=? AND ann_date=?
        """, (stock_code, report_type, ann_date)).fetchone()
        return row is not None


def get_indexed_reports(stock_code: str) -> list[dict]:
    with _get_db() as conn:
        rows = conn.execute("""
            SELECT * FROM report_index WHERE stock_code=?
            ORDER BY ann_date DESC
        """, (stock_code,)).fetchall()
        return [dict(r) for r in rows]


def save_feedback(
    stock_code: str,
    question: str,
    answer: str,
    sources: list[str],
    rating: int,
    comment: str = "",
) -> int:
    import json
    with _get_db() as conn:
        cur = conn.execute("""
            INSERT INTO qa_feedback (stock_code, question, answer, sources, rating, comment)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (stock_code, question, answer, json.dumps(sources, ensure_ascii=False), rating, comment))
        conn.commit()
        return cur.lastrowid


def get_feedback_stats(stock_code: str | None = None) -> dict:
    """返回反馈统计：总数、好评数、差评数、差评问题列表"""
    with _get_db() as conn:
        base = "WHERE stock_code=?" if stock_code else ""
        params = (stock_code,) if stock_code else ()
        row = conn.execute(f"""
            SELECT COUNT(*) total,
                   SUM(CASE WHEN rating=1 THEN 1 ELSE 0 END) good,
                   SUM(CASE WHEN rating=0 THEN 1 ELSE 0 END) bad
            FROM qa_feedback {base}
        """, params).fetchone()
        bad_rows = conn.execute(f"""
            SELECT question, comment, created_at
            FROM qa_feedback
            {base + (' AND' if base else 'WHERE')} rating=0
            ORDER BY created_at DESC LIMIT 20
        """, params).fetchall()
    return {
        "total": row["total"] or 0,
        "good": row["good"] or 0,
        "bad": row["bad"] or 0,
        "bad_questions": [dict(r) for r in bad_rows],
    }


# ─── Chroma ───────────────────────────────────────────────────────────────────

_chroma_client = None
_collection = None


def _get_collection():
    global _chroma_client, _collection
    if _collection is not None:
        return _collection

    db_dir = os.path.join(os.path.dirname(settings.db_path), "chroma")
    os.makedirs(db_dir, exist_ok=True)
    _chroma_client = chromadb.PersistentClient(path=db_dir)

    ef = embedding_functions.SentenceTransformerEmbeddingFunction(
        model_name="paraphrase-multilingual-MiniLM-L12-v2"
    )
    _collection = _chroma_client.get_or_create_collection(
        name="reports",
        embedding_function=ef,
        metadata={"hnsw:space": "cosine"},
    )
    return _collection


def _chunk_text(text: str, chunk_size: int = 500, overlap: int = 50) -> list[str]:
    """按段落优先切块，段落过长则按字数切"""
    paragraphs = [p.strip() for p in text.split("\n") if len(p.strip()) > 20]
    chunks, buf = [], ""
    for para in paragraphs:
        if len(buf) + len(para) <= chunk_size:
            buf += para + "\n"
        else:
            if buf:
                chunks.append(buf.strip())
            buf = para + "\n"
    if buf:
        chunks.append(buf.strip())
    return chunks


def index_document(
    doc: ParsedDocument,
    stock_code: str,
    report_type: str,
    ann_date: str,
    title: str,
):
    """将 ParsedDocument 按章节结构切块入向量库"""
    from app.parsers.chapter_splitter import split_by_chapter
    collection = _get_collection()

    # 年报用章节化拆分，其他类型用普通切块
    if report_type == "annual":
        chapter_chunks = split_by_chapter(doc.text)
        chunk_items = [
            (c.text, {
                "stock_code": stock_code,
                "report_type": report_type,
                "ann_date": ann_date,
                "title": title,
                "section": c.section,
                "sub_section": c.sub_section,
                "sub_sub_section": c.sub_sub_section,
                "chapter_index": c.chapter_index,
                "chunk_index": c.chunk_index,
            })
            for c in chapter_chunks
        ]
    else:
        plain_chunks = _chunk_text(doc.text)
        chunk_items = [
            (text, {
                "stock_code": stock_code,
                "report_type": report_type,
                "ann_date": ann_date,
                "title": title,
                "section": "",
                "sub_section": "",
                "sub_sub_section": "",
                "chapter_index": 0,
                "chunk_index": i,
            })
            for i, text in enumerate(plain_chunks)
        ]

    ids, texts, metas = [], [], []
    for i, (text, meta) in enumerate(chunk_items):
        chunk_id = f"{stock_code}_{report_type}_{ann_date}_{i}"
        existing = collection.get(ids=[chunk_id])
        if existing["ids"]:
            continue
        ids.append(chunk_id)
        texts.append(text)
        metas.append(meta)

    if ids:
        collection.add(documents=texts, ids=ids, metadatas=metas)

    return len(ids)


_SECTION_NUM = {
    "第一节": 1, "第二节": 2, "第三节": 3, "第四节": 4,
    "第五节": 5, "第六节": 6, "第七节": 7, "第八节": 8,
    "第九节": 9, "第十节": 10, "第十一节": 11, "第十二节": 12,
}


def semantic_search(
    query: str,
    stock_code: str | None = None,
    report_type: str | None = None,
    section_filter: str | None = None,   # 按章节过滤，如 "第三节"
    top_k: int = 5,
) -> list[dict]:
    """语义检索，可按公司/报告类型/章节过滤"""
    collection = _get_collection()

    conditions = []
    if stock_code:
        conditions.append({"stock_code": stock_code})
    if report_type and report_type != "all":
        conditions.append({"report_type": report_type})
    if section_filter:
        ch_idx = _SECTION_NUM.get(section_filter)
        if ch_idx:
            conditions.append({"chapter_index": {"$eq": int(ch_idx)}})

    where = {}
    if len(conditions) == 1:
        where = conditions[0]
    elif len(conditions) > 1:
        where = {"$and": conditions}

    kwargs = {"query_texts": [query], "n_results": top_k}
    if where:
        kwargs["where"] = where

    results = collection.query(**kwargs)

    hits = []
    for i, doc in enumerate(results["documents"][0]):
        hits.append({
            "text": doc,
            "metadata": results["metadatas"][0][i],
            "distance": results["distances"][0][i],
        })
    return hits
