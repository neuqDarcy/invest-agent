"""
单公司问答：数字类 → SQLite + Python计算层，语义类 → Chroma + LLM
LLM 只负责语言组织，所有数学计算由 calculator.py 完成。
"""
import re
from app.knowledge.store import semantic_search, get_indexed_reports
from app.knowledge.extractor import get_metrics
from app.knowledge.calculator import compute_analytics, format_analytics_for_llm
from app.knowledge.notes import search_notes
from app.core.llm import chat, chat_stream

SYSTEM_PROMPT = """你是一位专业的A股研究员。回答问题时：
1. 财务数字和计算结果直接引用【已计算财务数据】中的数值，不要自行重新计算
2. 补充【原文片段】中的定性信息（战略、风险、业务描述等）
3. 如果数据中找不到答案，明确说明"数据中未包含相关信息"
4. 回答简洁专业，数字保留2位小数"""

_NUMERIC_KEYWORDS = [
    # 财务指标
    "营业收入", "净利润", "毛利润", "营业成本", "现金流", "资产", "负债",
    "股东权益", "研发", "销售费用", "管理费用", "财务费用", "利润率", "毛利率",
    "自由现金流", "资本支出", "ROE", "ROA", "负债率",
    # 数量词
    "多少", "几亿", "增长", "下降", "同比", "增速", "趋势", "CAGR", "复合",
    "累计", "翻了", "增幅",
    # 综合分析类——这类问题也应该带上财务数据
    "基本面", "财务", "业绩", "经营", "盈利", "分析", "情况怎么样",
    "表现如何", "怎么样", "如何", "好不好", "值不值",
]

# 关键词 → 优先检索章节映射
_SECTION_ROUTING = [
    (["收入确认", "会计政策", "收入结算", "确认标准", "确认时点",
      "经销商货款", "结算方式", "会计准则"],        "第十节"),
    (["管理层", "经营分析", "业务分析", "主营业务", "成本分析",
      "竞争力", "核心竞争", "战略", "业务模式"],    "第三节"),
    (["风险", "风险提示", "面临风险", "主要风险"],  "第六节"),
    (["股东", "股权结构", "控股股东", "持股"],       "第七节"),
    (["公司治理", "董事会", "监事会", "独立董事"],   "第四节"),
    (["环境", "社会责任", "ESG", "碳排放"],          "第五节"),
]


def _is_numeric_question(question: str) -> bool:
    return any(kw in question for kw in _NUMERIC_KEYWORDS)


_ANNOUNCEMENT_KEYWORDS = [
    "分红", "派息", "股息", "利润分配", "分红方案",
    "半年报", "季报", "一季度", "三季度", "中期",
    "公告", "重大事项", "重组", "并购",
]


def _is_announcement_question(question: str) -> bool:
    return any(kw in question for kw in _ANNOUNCEMENT_KEYWORDS)


def _expand_query(question: str) -> str:
    """
    把口语化问题扩展为年报专业术语，提升向量检索召回率。
    原问题保留，追加同义专业术语。
    """
    expansions = [
        (["经销商货款", "经销商收款", "经销商打款", "卖给经销商"],
         "收入确认 会计政策 商品控制权转移 履约义务"),
        (["收入确认", "何时确认收入", "什么时候算收入"],
         "收入确认政策 会计政策 履约义务 控制权转移"),
        (["商业模式", "怎么赚钱", "盈利模式"],
         "主营业务 业务模式 收入来源 经营模式"),
        (["护城河", "竞争优势", "壁垒"],
         "核心竞争力 竞争优势 品牌 技术壁垒"),
        (["管理层", "管理团队", "高管"],
         "董事会 总裁 管理层讨论 经营决策"),
    ]
    extra = []
    for keywords, expansion in expansions:
        if any(kw in question for kw in keywords):
            extra.append(expansion)
    if extra:
        return question + " " + " ".join(extra)
    return question


def _route_section(question: str) -> str | None:
    """根据问题关键词，返回应优先检索的章节，None 表示不限制"""
    for keywords, section in _SECTION_ROUTING:
        if any(kw in question for kw in keywords):
            return section
    return None


def _extract_year_range(question: str) -> tuple[str | None, str | None]:
    """从问题中提取起止年份，如 '2019年到2023年' → ('2019', '2023')"""
    years = re.findall(r"20\d{2}", question)
    if len(years) >= 2:
        return min(years), max(years)
    if len(years) == 1:
        return years[0], years[0]
    return None, None


def _build_context(
    stock_code: str,
    question: str,
    report_type: str = "annual",
    top_k: int = 5,
) -> tuple[str | None, list[str], str]:
    """
    构建 LLM 上下文。
    返回 (user_prompt, sources, error_message)
    error_message 非空表示无法回答。
    """
    indexed = get_indexed_reports(stock_code)
    if not indexed:
        return None, [], f"股票 {stock_code} 尚未建立知识库，请先建立知识库。"

    context_parts = []
    sources = []

    # 用户笔记
    note_hits = search_notes(question, stock_code=stock_code, top_k=2)
    if note_hits:
        note_texts = "\n\n".join(f"【我的笔记】{h['title']}\n{h['text']}" for h in note_hits)
        context_parts.append(note_texts)
        sources.append("我的投资笔记")

    # 数字类问题
    if _is_numeric_question(question):
        metrics = get_metrics(stock_code, years=6)
        if metrics:
            analytics = compute_analytics(metrics)
            start_y, end_y = _extract_year_range(question)
            context_parts.append(
                format_analytics_for_llm(analytics, focus_start=start_y, focus_end=end_y)
            )
            sources.append("结构化财务指标（Tushare，Python计算）")

    # 语义检索：公告类问题不限制 report_type，覆盖公告和年报
    section_filter = _route_section(question)
    search_report_type = None if _is_announcement_question(question) else (
        report_type if report_type != "all" else None
    )
    hits = semantic_search(
        query=_expand_query(question),
        stock_code=stock_code,
        report_type=search_report_type,
        section_filter=section_filter,
        top_k=top_k,
    )
    if len(hits) < 2 or (hits and hits[0]["distance"] > 0.6):
        extra = semantic_search(query=_expand_query(question), stock_code=stock_code, top_k=top_k)
        seen = {(h["metadata"].get("stock_code"), h["metadata"].get("chunk_index")) for h in hits}
        for h in extra:
            key = (h["metadata"].get("stock_code"), h["metadata"].get("chunk_index"))
            if key not in seen:
                hits.append(h)
                seen.add(key)
        hits = hits[:top_k]

    for i, hit in enumerate(hits):
        meta = hit["metadata"]
        section = meta.get("section", "")
        sub = meta.get("sub_section", "")
        location = f"{section} {sub}".strip() or "正文"
        context_parts.append(
            f"【原文片段{i+1}】来源：{meta['title']}（{meta['ann_date']}）- {location}\n{hit['text']}"
        )
        source_key = f"{meta['title']} · {location}" if location else meta['title']
        if source_key not in sources:
            sources.append(source_key)

    if not context_parts:
        return None, [], "在知识库中未检索到相关内容，请尝试换个问法。"

    context = "\n\n".join(context_parts)
    user_prompt = f"以下是公司财务数据：\n\n{context}\n\n问题：{question}"
    return user_prompt, sources, ""


def ask(
    stock_code: str,
    question: str,
    report_type: str = "annual",
    top_k: int = 5,
) -> dict:
    user_prompt, sources, error = _build_context(stock_code, question, report_type, top_k)
    if error:
        return {"answer": error, "sources": [], "has_data": bool(get_indexed_reports(stock_code))}
    answer = chat(system=SYSTEM_PROMPT, user=user_prompt)
    return {"answer": answer, "sources": sources, "has_data": True}


def ask_stream(
    stock_code: str,
    question: str,
    report_type: str = "annual",
    top_k: int = 5,
    stock_context: str = "",
):
    """
    流式版本。yield JSON 字符串：
      {"type":"sources","sources":[...]}
      {"type":"token","content":"..."}
      {"type":"done"}
      {"type":"error","message":"..."}
    """
    import json
    user_prompt, sources, error = _build_context(stock_code, question, report_type, top_k)
    if error:
        yield json.dumps({"type": "error", "message": error})
        return
    system = SYSTEM_PROMPT + (f"\n\n{stock_context}" if stock_context else "")
    yield json.dumps({"type": "sources", "sources": sources})
    for token in chat_stream(system=system, user=user_prompt):
        yield json.dumps({"type": "token", "content": token})
    yield json.dumps({"type": "done"})
