"""
券商研报处理：
1. 用 LLM 从 PDF 文本中提取结构化信息（评级、目标价、核心观点、盈利预测）
2. 文本分块入 Chroma 向量库（与年报共用同一个 collection）
"""
import os
import re
from dataclasses import dataclass, asdict
from app.parsers.pdf_parser import parse_pdf, ParsedDocument
from app.knowledge.store import _get_collection, _get_db
from app.core.llm import chat

EXTRACT_SYSTEM = """你是一位专业的证券研究助手，擅长解析券商研究报告。
从给定的研报文本中提取关键信息，严格按 JSON 格式输出，不要添加任何额外说明。"""

EXTRACT_USER_TEMPLATE = """从以下券商研报文本中提取关键信息，输出 JSON：

{{
  "stock_name": "公司名称",
  "stock_code": "股票代码（6位数字，无后缀）",
  "broker": "券商名称",
  "analyst": "分析师姓名（多人用逗号分隔）",
  "rating": "投资评级（买入/增持/中性/减持/卖出）",
  "target_price": 目标价数字或null,
  "report_date": "报告日期 YYYY-MM-DD 或 null",
  "title": "研报标题",
  "core_views": ["核心观点1", "核心观点2", "核心观点3"],
  "profit_forecast": {{
    "2024": {{"revenue": 营收预测亿元或null, "net_profit": 净利润预测亿元或null, "eps": EPS预测或null}},
    "2025": {{"revenue": null, "net_profit": null, "eps": null}},
    "2026": {{"revenue": null, "net_profit": null, "eps": null}}
  }},
  "risk_warnings": ["风险提示1", "风险提示2"]
}}

研报文本（前5000字）：
{text}"""


@dataclass
class BrokerReportMeta:
    stock_name: str = ""
    stock_code: str = ""
    broker: str = ""
    analyst: str = ""
    rating: str = ""
    target_price: float | None = None
    report_date: str | None = None
    title: str = ""
    core_views: list = None
    profit_forecast: dict = None
    risk_warnings: list = None

    def __post_init__(self):
        if self.core_views is None:
            self.core_views = []
        if self.profit_forecast is None:
            self.profit_forecast = {}
        if self.risk_warnings is None:
            self.risk_warnings = []


def extract_broker_meta(doc: ParsedDocument) -> BrokerReportMeta:
    """用 LLM 从研报文本提取结构化元数据"""
    text = doc.text[:5000]
    user_prompt = EXTRACT_USER_TEMPLATE.format(text=text)
    raw = chat(system=EXTRACT_SYSTEM, user=user_prompt, temperature=0.1)

    # 解析 JSON
    import json
    try:
        # 提取 JSON 块（LLM 可能带 markdown 代码块）
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            return BrokerReportMeta()
        data = json.loads(m.group())
        return BrokerReportMeta(
            stock_name=data.get("stock_name", ""),
            stock_code=str(data.get("stock_code", "") or ""),
            broker=data.get("broker", ""),
            analyst=data.get("analyst", ""),
            rating=data.get("rating", ""),
            target_price=_safe_float(data.get("target_price")),
            report_date=data.get("report_date"),
            title=data.get("title", ""),
            core_views=data.get("core_views", []),
            profit_forecast=data.get("profit_forecast", {}),
            risk_warnings=data.get("risk_warnings", []),
        )
    except Exception:
        return BrokerReportMeta()


def _safe_float(val) -> float | None:
    try:
        return float(val) if val is not None else None
    except (TypeError, ValueError):
        return None


def _chunk_text(text: str, chunk_size: int = 500) -> list[str]:
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


def _save_broker_index(meta: BrokerReportMeta, file_path: str):
    """把研报元数据存入 SQLite"""
    with _get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS broker_report_index (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                stock_code   TEXT,
                stock_name   TEXT,
                broker       TEXT,
                analyst      TEXT,
                rating       TEXT,
                target_price REAL,
                report_date  TEXT,
                title        TEXT,
                core_views   TEXT,
                profit_forecast TEXT,
                risk_warnings TEXT,
                file_path    TEXT,
                indexed_at   TEXT DEFAULT (datetime('now'))
            )
        """)
        import json
        conn.execute("""
            INSERT INTO broker_report_index
                (stock_code, stock_name, broker, analyst, rating, target_price,
                 report_date, title, core_views, profit_forecast, risk_warnings, file_path)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            meta.stock_code, meta.stock_name, meta.broker, meta.analyst,
            meta.rating, meta.target_price, meta.report_date, meta.title,
            json.dumps(meta.core_views, ensure_ascii=False),
            json.dumps(meta.profit_forecast, ensure_ascii=False),
            json.dumps(meta.risk_warnings, ensure_ascii=False),
            file_path,
        ))
        conn.commit()


def index_broker_report(file_path: str) -> dict:
    """
    处理券商研报 PDF：
    1. LLM 提取结构化元数据
    2. 文本分块入 Chroma
    3. 元数据存 SQLite
    返回提取到的元数据摘要
    """
    doc = parse_pdf(file_path)
    meta = extract_broker_meta(doc)

    # 入向量库
    collection = _get_collection()
    chunks = _chunk_text(doc.text)
    ann_date = meta.report_date or "unknown"
    title = meta.title or os.path.basename(file_path)

    ids, texts, metas = [], [], []
    for i, chunk in enumerate(chunks):
        chunk_id = f"broker_{meta.stock_code}_{ann_date}_{i}"
        existing = collection.get(ids=[chunk_id])
        if existing["ids"]:
            continue
        ids.append(chunk_id)
        texts.append(chunk)
        metas.append({
            "stock_code": meta.stock_code,
            "report_type": "broker_report",
            "ann_date": ann_date,
            "title": title,
            "broker": meta.broker,
            "analyst": meta.analyst,
            "rating": meta.rating,
            "chunk_index": i,
        })

    if ids:
        collection.add(documents=texts, ids=ids, metadatas=metas)

    # 存 SQLite
    _save_broker_index(meta, file_path)

    return {
        "title": title,
        "stock_code": meta.stock_code,
        "stock_name": meta.stock_name,
        "broker": meta.broker,
        "analyst": meta.analyst,
        "rating": meta.rating,
        "target_price": meta.target_price,
        "report_date": ann_date,
        "core_views": meta.core_views,
        "profit_forecast": meta.profit_forecast,
        "risk_warnings": meta.risk_warnings,
        "chunk_count": len(ids),
    }


def get_broker_reports(stock_code: str | None = None) -> list[dict]:
    """查询已入库的券商研报列表"""
    import json
    with _get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS broker_report_index (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                stock_code TEXT, stock_name TEXT, broker TEXT, analyst TEXT,
                rating TEXT, target_price REAL, report_date TEXT, title TEXT,
                core_views TEXT, profit_forecast TEXT, risk_warnings TEXT,
                file_path TEXT, indexed_at TEXT DEFAULT (datetime('now'))
            )
        """)
        if stock_code:
            rows = conn.execute(
                "SELECT * FROM broker_report_index WHERE stock_code=? ORDER BY report_date DESC",
                (stock_code,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM broker_report_index ORDER BY indexed_at DESC LIMIT 50"
            ).fetchall()

    results = []
    for r in rows:
        d = dict(r)
        for field in ["core_views", "profit_forecast", "risk_warnings"]:
            try:
                d[field] = json.loads(d[field]) if d[field] else []
            except Exception:
                pass
        results.append(d)
    return results
