"""
多实体对比问答：识别实体 → 分别检索 → LLM 综合对比
"""
from app.knowledge.entity_extractor import extract_entities
from app.knowledge.store import semantic_search, get_indexed_reports
from app.knowledge.extractor import get_metrics
from app.core.llm import chat

SYSTEM_PROMPT = """你是一位专业的A股研究员，基于提供的多家公司数据进行横向对比分析。
要求：
1. 只基于提供的原文内容回答，不编造数据
2. 对比时使用相同口径的指标
3. 明确指出哪家公司在哪个维度更优
4. 如某公司数据不足，明确说明"数据不足，无法对比"
5. 结论简洁，有数据支撑"""


def _collect_context(stock_code: str, name: str, question: str, top_k: int = 5) -> str:
    """为单个实体收集检索上下文"""
    label = name or stock_code
    parts = [f"=== {label} ==="]

    # 检查是否入库
    indexed = get_indexed_reports(stock_code) if stock_code else []
    if not indexed:
        parts.append(f"（{label} 尚未建立知识库，无法检索）")
        return "\n".join(parts)

    # 数字类指标
    metrics = get_metrics(stock_code, years=5)
    if metrics:
        from collections import defaultdict
        by_year: dict = defaultdict(dict)
        for m in metrics:
            year = m["ann_date"][:4]
            by_year[year][m["metric_name"]] = m["value"]
        lines = ["【财务指标】"]
        for year in sorted(by_year.keys(), reverse=True)[:3]:
            lines.append(f"{year}年：")
            for mname, val in by_year[year].items():
                val_str = f"{val/1e8:.2f}亿元" if abs(val) >= 1e8 else f"{val/1e4:.2f}万元"
                lines.append(f"  {mname}：{val_str}")
        parts.append("\n".join(lines))

    # 语义检索
    hits = semantic_search(query=question, stock_code=stock_code, top_k=top_k)
    for i, hit in enumerate(hits):
        meta = hit["metadata"]
        parts.append(f"【原文片段{i+1}】{meta['title']}（{meta['ann_date']}）\n{hit['text']}")

    return "\n".join(parts)


def compare(question: str, top_k: int = 5) -> dict:
    """
    多实体对比问答入口。
    返回 {"answer": str, "entities": list, "has_data": bool}
    """
    entities = extract_entities(question)

    # 过滤掉没有 stock_code 的实体（无法检索）
    queryable = [e for e in entities if e["stock_code"]]

    if len(queryable) < 2:
        return {
            "answer": "未能识别到至少两家公司，请在问题中包含股票代码（如：600926 和 601328）。",
            "entities": entities,
            "has_data": False,
        }

    # 分别检索每个实体
    context_blocks = []
    for e in queryable:
        block = _collect_context(e["stock_code"], e["name"], question, top_k)
        context_blocks.append(block)

    context = "\n\n".join(context_blocks)
    user_prompt = f"以下是多家公司的相关数据：\n\n{context}\n\n问题：{question}"
    answer = chat(system=SYSTEM_PROMPT, user=user_prompt)

    return {
        "answer": answer,
        "entities": queryable,
        "has_data": True,
    }
