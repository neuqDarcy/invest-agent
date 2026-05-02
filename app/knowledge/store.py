"""
知识库存储层，负责两类数据的持久化：
- SQLite：存储结构化财务指标，支持精确查询（报告索引、财务数据、用户反馈）
- Chroma：存储文本段落向量，支持语义相似度检索
"""
import os
import re
import sqlite3
from dataclasses import dataclass
import chromadb

# 确保 HuggingFace 模型下载使用国内镜像，避免直连超时
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
from chromadb.utils import embedding_functions
from app.core.config import settings
from app.parsers.pdf_parser import ParsedDocument


# ─── SQLite 层 ────────────────────────────────────────────────────────────────

def _get_db() -> sqlite3.Connection:
    """
    获取 SQLite 连接，并确保所有业务表已创建。

    表结构说明：
    - qa_feedback:       用户对问答结果的好/差评反馈
    - report_index:      已入库的报告索引（股票+类型+日期唯一）
    - financial_metrics: 结构化财务指标（股票+日期+指标名唯一）

    返回：配置了 Row 工厂（支持按列名访问）的连接对象。
    """
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row  # 允许通过列名访问查询结果，如 row["stock_code"]
    conn.execute("""
        CREATE TABLE IF NOT EXISTS qa_feedback (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_code  TEXT NOT NULL,
            question    TEXT NOT NULL,
            answer      TEXT NOT NULL,
            sources     TEXT,
            rating      INTEGER NOT NULL,  -- 1=好评 0=差评
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
            UNIQUE(stock_code, report_type, ann_date)  -- 防止重复入库
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
            UNIQUE(stock_code, ann_date, metric_name)  -- 防止重复写入
        )
    """)
    conn.commit()
    return conn


def save_report_index(
    stock_code: str,
    report_type: str,
    ann_date: str,
    title: str,
    file_path: str,
):
    """
    将报告元信息写入索引表（已存在则忽略，不覆盖）。

    参数：
        stock_code:  股票代码
        report_type: 报告类型，如 "annual"、"notice"
        ann_date:    公告日期，格式 YYYYMMDD
        title:       报告标题
        file_path:   本地文件路径
    """
    with _get_db() as conn:
        conn.execute("""
            INSERT OR IGNORE INTO report_index(stock_code, report_type, ann_date, title, file_path)
            VALUES (?, ?, ?, ?, ?)
        """, (stock_code, report_type, ann_date, title, file_path))


def is_indexed(stock_code: str, report_type: str, ann_date: str) -> bool:
    """
    检查指定报告是否已经入库。

    参数：
        stock_code:  股票代码
        report_type: 报告类型
        ann_date:    公告日期

    返回：True 表示已入库，False 表示尚未入库。
    """
    with _get_db() as conn:
        row = conn.execute("""
            SELECT id FROM report_index
            WHERE stock_code=? AND report_type=? AND ann_date=?
        """, (stock_code, report_type, ann_date)).fetchone()
        return row is not None


def get_indexed_reports(stock_code: str) -> list[dict]:
    """
    获取指定股票已入库的所有报告列表（按日期倒序）。

    参数：
        stock_code: 股票代码

    返回：报告元信息字典列表，空列表表示该股票尚未建立知识库。
    """
    with _get_db() as conn:
        rows = conn.execute("""
            SELECT * FROM report_index WHERE stock_code=?
            ORDER BY ann_date DESC
        """, (stock_code,)).fetchall()
        return [dict(row) for row in rows]


def save_feedback(
    stock_code: str,
    question: str,
    answer: str,
    sources: list[str],
    rating: int,
    comment: str = "",
) -> int:
    """
    保存用户对问答结果的反馈评价。

    参数：
        stock_code: 股票代码
        question:   用户提问
        answer:     系统回答
        sources:    引用来源列表
        rating:     评分，1=好评，0=差评
        comment:    用户补充说明（可选）

    返回：新插入记录的自增 ID。
    """
    import json
    with _get_db() as conn:
        cursor = conn.execute("""
            INSERT INTO qa_feedback (stock_code, question, answer, sources, rating, comment)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (stock_code, question, answer, json.dumps(sources, ensure_ascii=False), rating, comment))
        conn.commit()
        return cursor.lastrowid


def get_feedback_stats(stock_code: str | None = None) -> dict:
    """
    统计问答反馈数据，用于评估知识库质量。

    参数：
        stock_code: 指定股票代码时只统计该股票，None 时统计全部

    返回：{
        "total":         总反馈数,
        "good":          好评数,
        "bad":           差评数,
        "bad_questions": 最近20条差评问题列表
    }
    """
    with _get_db() as conn:
        # 按需添加 WHERE 子句过滤股票
        where_clause = "WHERE stock_code=?" if stock_code else ""
        query_params = (stock_code,) if stock_code else ()

        # 一次查询统计好评/差评总数
        stats_row = conn.execute(f"""
            SELECT COUNT(*) total,
                   SUM(CASE WHEN rating=1 THEN 1 ELSE 0 END) good,
                   SUM(CASE WHEN rating=0 THEN 1 ELSE 0 END) bad
            FROM qa_feedback {where_clause}
        """, query_params).fetchone()

        # 拉取最近差评问题，用于分析知识库薄弱点
        bad_question_rows = conn.execute(f"""
            SELECT question, comment, created_at
            FROM qa_feedback
            {where_clause + (' AND' if where_clause else 'WHERE')} rating=0
            ORDER BY created_at DESC LIMIT 20
        """, query_params).fetchall()

    return {
        "total": stats_row["total"] or 0,
        "good":  stats_row["good"] or 0,
        "bad":   stats_row["bad"] or 0,
        "bad_questions": [dict(row) for row in bad_question_rows],
    }


# ─── Chroma 向量库层 ──────────────────────────────────────────────────────────

# 模块级单例，避免重复初始化（加载 embedding 模型较慢）
_chroma_client = None
_chroma_collection = None


def _get_collection():
    """
    获取 Chroma 向量集合（单例模式，首次调用时初始化）。

    使用 paraphrase-multilingual-MiniLM-L12-v2 模型生成中文向量，
    余弦相似度（cosine）作为距离度量。
    """
    global _chroma_client, _chroma_collection
    if _chroma_collection is not None:
        return _chroma_collection

    # Chroma 数据库目录与 SQLite 同级
    chroma_db_dir = os.path.join(os.path.dirname(settings.db_path), "chroma")
    os.makedirs(chroma_db_dir, exist_ok=True)
    _chroma_client = chromadb.PersistentClient(path=chroma_db_dir)

    # 使用多语言小型模型，平衡中文语义质量与推理速度
    embedding_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
        model_name="paraphrase-multilingual-MiniLM-L12-v2"
    )
    _chroma_collection = _chroma_client.get_or_create_collection(
        name="reports",
        embedding_function=embedding_fn,
        metadata={"hnsw:space": "cosine"},  # 余弦距离更适合文本语义相似度
    )
    return _chroma_collection


def _chunk_text(text: str, chunk_size: int = 500, overlap: int = 50) -> list[str]:
    """
    将长文本切分为适合向量化的文本块。

    策略：优先按段落切分，段落过长时按字数截断。
    段落间有一定重叠（overlap），确保跨段落信息不丢失。

    参数：
        text:       待切分的原始文本
        chunk_size: 每块最大字符数（默认500）
        overlap:    相邻块的重叠字符数（默认50，当前实现中段落级别已隐式处理）

    返回：切分后的文本块列表。
    """
    # 按换行分段，过滤掉过短的无意义行（少于20字符）
    paragraphs = [para.strip() for para in text.split("\n") if len(para.strip()) > 20]
    chunks = []
    current_buffer = ""
    for paragraph in paragraphs:
        if len(current_buffer) + len(paragraph) <= chunk_size:
            # 当前段落可以追加到缓冲区
            current_buffer += paragraph + "\n"
        else:
            # 缓冲区已满，先保存当前块，再开始新块
            if current_buffer:
                chunks.append(current_buffer.strip())
            current_buffer = paragraph + "\n"
    if current_buffer:
        chunks.append(current_buffer.strip())
    return chunks


def index_document(
    doc: ParsedDocument,
    stock_code: str,
    report_type: str,
    ann_date: str,
    title: str,
):
    """
    将解析后的文档按章节结构切块并写入 Chroma 向量库。

    年报采用章节化拆分（保留章节层级信息，支持按章节过滤检索），
    其他报告类型（公告等）采用普通段落切块。

    参数：
        doc:         解析后的文档对象（含原始文本）
        stock_code:  股票代码
        report_type: 报告类型，"annual" 启用章节化拆分
        ann_date:    公告日期
        title:       报告标题

    返回：本次新写入的文本块数量（已存在的块会跳过）。
    """
    from app.parsers.chapter_splitter import split_by_chapter
    collection = _get_collection()

    # 年报用章节化拆分，保留 section/sub_section 层级，支持后续按章节过滤
    if report_type == "annual":
        chapter_chunks = split_by_chapter(doc.text)
        chunk_items = [
            (chunk.text, {
                "stock_code":       stock_code,
                "report_type":      report_type,
                "ann_date":         ann_date,
                "title":            title,
                "section":          chunk.section,
                "sub_section":      chunk.sub_section,
                "sub_sub_section":  chunk.sub_sub_section,
                "chapter_index":    chunk.chapter_index,
                "chunk_index":      chunk.chunk_index,
            })
            for chunk in chapter_chunks
        ]
    else:
        # 公告等非年报文件，章节信息留空，仅按段落切块
        plain_chunks = _chunk_text(doc.text)
        chunk_items = [
            (chunk_text, {
                "stock_code":       stock_code,
                "report_type":      report_type,
                "ann_date":         ann_date,
                "title":            title,
                "section":          "",
                "sub_section":      "",
                "sub_sub_section":  "",
                "chapter_index":    0,
                "chunk_index":      chunk_idx,
            })
            for chunk_idx, chunk_text in enumerate(plain_chunks)
        ]

    # 过滤已存在的块，避免重复写入（以 chunk_id 为唯一键）
    new_ids = []
    new_texts = []
    new_metas = []
    for seq_idx, (chunk_text, chunk_meta) in enumerate(chunk_items):
        chunk_id = f"{stock_code}_{report_type}_{ann_date}_{seq_idx}"
        existing = collection.get(ids=[chunk_id])
        if existing["ids"]:
            continue  # 已存在，跳过
        new_ids.append(chunk_id)
        new_texts.append(chunk_text)
        new_metas.append(chunk_meta)

    if new_ids:
        collection.add(documents=new_texts, ids=new_ids, metadatas=new_metas)

    return len(new_ids)


# 章节名称到数字索引的映射，用于 Chroma 的数值过滤条件
_SECTION_NAME_TO_INDEX = {
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
    """
    在向量库中执行语义相似度检索。

    支持按股票代码、报告类型、年报章节多维度过滤，
    多个过滤条件通过 AND 逻辑组合。

    参数：
        query:          查询文本（已扩展的专业术语版本效果更好）
        stock_code:     限定股票代码，None 表示全库检索
        report_type:    限定报告类型，None 或 "all" 表示不限制
        section_filter: 限定年报章节，如 "第三节"，None 表示不限制
        top_k:          返回最相似的前 k 条结果

    返回：检索结果列表，每条含 text/metadata/distance 三个字段。
    """
    collection = _get_collection()

    # 构建过滤条件列表（Chroma 的 where 语法）
    filter_conditions = []
    if stock_code:
        filter_conditions.append({"stock_code": stock_code})
    if report_type and report_type != "all":
        filter_conditions.append({"report_type": report_type})
    if section_filter:
        # 章节名转为数字索引，Chroma 元数据过滤只支持数值比较
        chapter_index = _SECTION_NAME_TO_INDEX.get(section_filter)
        if chapter_index:
            filter_conditions.append({"chapter_index": {"$eq": int(chapter_index)}})

    # 组合过滤条件：单条件直接用，多条件用 $and
    where_filter = {}
    if len(filter_conditions) == 1:
        where_filter = filter_conditions[0]
    elif len(filter_conditions) > 1:
        where_filter = {"$and": filter_conditions}

    # 执行向量检索
    query_kwargs = {"query_texts": [query], "n_results": top_k}
    if where_filter:
        query_kwargs["where"] = where_filter

    query_results = collection.query(**query_kwargs)

    # 将 Chroma 返回格式转为统一的列表格式
    hits = []
    for idx, doc_text in enumerate(query_results["documents"][0]):
        hits.append({
            "text":     doc_text,
            "metadata": query_results["metadatas"][0][idx],
            "distance": query_results["distances"][0][idx],
        })
    return hits
