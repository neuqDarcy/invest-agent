"""
单公司问答模块：
- 数字类问题 → SQLite 结构化查询 + Python 计算层（calculator.py）
- 语义类问题 → Chroma 向量检索 + LLM 语言组织
LLM 只负责语言组织，所有数学计算由 calculator.py 完成。
"""
import re
from app.knowledge.store import semantic_search, get_indexed_reports
from app.knowledge.extractor import get_metrics
from app.knowledge.calculator import compute_analytics, format_analytics_for_llm
from app.knowledge.notes import search_notes
from app.core.llm import chat, chat_stream

# LLM 系统提示：明确分工，避免 LLM 自行重算财务数字
SYSTEM_PROMPT = """你是一位专业的A股研究员。回答问题时：
1. 财务数字和计算结果直接引用【已计算财务数据】中的数值，不要自行重新计算
2. 补充【原文片段】中的定性信息（战略、风险、业务描述等）
3. 如果数据中找不到答案，明确说明"数据中未包含相关信息"
4. 回答简洁专业，数字保留2位小数"""

# 触发财务数据检索的关键词列表（命中任意一个即视为"数字类问题"）
_NUMERIC_KEYWORDS = [
    # 财务指标
    "营业收入", "净利润", "毛利润", "营业成本", "现金流", "资产", "负债",
    "股东权益", "研发", "销售费用", "管理费用", "财务费用", "利润率", "毛利率",
    "自由现金流", "资本支出", "ROE", "ROA", "负债率",
    # 数量词和趋势词
    "多少", "几亿", "增长", "下降", "同比", "增速", "趋势", "CAGR", "复合",
    "累计", "翻了", "增幅",
    # 综合分析类——这类问题也应该带上财务数据，避免 LLM 凭空作答
    "基本面", "财务", "业绩", "经营", "盈利", "分析", "情况怎么样",
    "表现如何", "怎么样", "如何", "好不好", "值不值",
]

# 关键词 → 优先检索的年报章节映射（提升检索精准度）
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
    """判断问题是否涉及财务数字，决定是否需要拉取结构化指标数据"""
    return any(keyword in question for keyword in _NUMERIC_KEYWORDS)


# 触发"公告类检索"的关键词（公告类不限制 report_type，覆盖公告和年报）
_ANNOUNCEMENT_KEYWORDS = [
    "分红", "派息", "股息", "利润分配", "分红方案",
    "半年报", "季报", "一季度", "三季度", "中期",
    "公告", "重大事项", "重组", "并购",
]


def _is_announcement_question(question: str) -> bool:
    """判断问题是否属于公告类，公告类问题需跨越年报和公告两类文件检索"""
    return any(keyword in question for keyword in _ANNOUNCEMENT_KEYWORDS)


def _expand_query(question: str) -> str:
    """
    将口语化问题扩展为年报专业术语，提升向量检索召回率。

    原问题保留，末尾追加同义专业术语。例如：
    "经销商货款怎么结算" → 原文 + "收入确认 会计政策 商品控制权转移 履约义务"
    """
    # 每条规则：(触发关键词列表, 追加的专业术语)
    expansion_rules = [
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
    matched_expansions = []
    for trigger_keywords, expansion_text in expansion_rules:
        if any(keyword in question for keyword in trigger_keywords):
            matched_expansions.append(expansion_text)
    if matched_expansions:
        return question + " " + " ".join(matched_expansions)
    return question


def _route_section(question: str) -> str | None:
    """
    根据问题关键词，返回应优先检索的年报章节编号。

    返回 None 表示不限制章节，全文检索。
    """
    for trigger_keywords, section_name in _SECTION_ROUTING:
        if any(keyword in question for keyword in trigger_keywords):
            return section_name
    return None


def _extract_year_range(question: str) -> tuple[str | None, str | None]:
    """
    从问题文本中提取起止年份。

    示例："2019年到2023年的营收" → ('2019', '2023')
    只有一个年份时起止相同，无年份时返回 (None, None)。
    """
    # 匹配所有 20xx 格式的年份
    found_years = re.findall(r"20\d{2}", question)
    if len(found_years) >= 2:
        return min(found_years), max(found_years)
    if len(found_years) == 1:
        return found_years[0], found_years[0]
    return None, None


def _build_context(
    stock_code: str,
    question: str,
    report_type: str = "annual",
    top_k: int = 5,
) -> tuple[str | None, list[str], str]:
    """
    构建发送给 LLM 的完整上下文（用户 prompt）。

    数据来源按优先级叠加：
    1. 用户投资笔记（最高优先级，个人视角）
    2. 结构化财务指标（数字类问题触发，Python 精确计算）
    3. 年报/公告语义检索片段（向量检索，覆盖定性信息）

    参数：
        stock_code: 股票代码
        question:   用户问题
        report_type: 检索范围，"annual"=年报，"all"=不限
        top_k:      语义检索返回的最大片段数

    返回：
        (user_prompt, sources, error_message)
        error_message 非空表示无法回答（知识库未建立或检索无结果）。
    """
    # 检查知识库是否已建立
    indexed_reports = get_indexed_reports(stock_code)
    if not indexed_reports:
        return None, [], f"股票 {stock_code} 尚未建立知识库，请先建立知识库。"

    context_parts = []  # 各数据源的文本片段，最终拼接为 LLM 上下文
    sources = []        # 来源标注列表，用于前端展示

    # ── 1. 用户投资笔记 ────────────────────────────────────────────────
    note_hits = search_notes(question, stock_code=stock_code, top_k=2)
    if note_hits:
        note_texts = "\n\n".join(
            f"【我的笔记】{hit['title']}\n{hit['text']}" for hit in note_hits
        )
        context_parts.append(note_texts)
        sources.append("我的投资笔记")

    # ── 2. 结构化财务指标（数字类问题才拉取，避免无关噪音）─────────────
    if _is_numeric_question(question):
        raw_metrics = get_metrics(stock_code, years=6)
        if raw_metrics:
            analytics = compute_analytics(raw_metrics)
            focus_start_year, focus_end_year = _extract_year_range(question)
            context_parts.append(
                format_analytics_for_llm(
                    analytics,
                    focus_start=focus_start_year,
                    focus_end=focus_end_year,
                )
            )
            sources.append("结构化财务指标（Tushare，Python计算）")

    # ── 3. 语义检索：公告类问题不限制 report_type，覆盖公告和年报 ────────
    section_filter = _route_section(question)
    # 公告类问题跨类型检索；否则按指定 report_type（"all" 等价于不过滤）
    search_report_type = None if _is_announcement_question(question) else (
        report_type if report_type != "all" else None
    )
    semantic_hits = semantic_search(
        query=_expand_query(question),
        stock_code=stock_code,
        report_type=search_report_type,
        section_filter=section_filter,
        top_k=top_k,
    )

    # 若检索结果太少或相关性太低（distance > 0.6），降级为全文无过滤检索补充
    if len(semantic_hits) < 2 or (semantic_hits and semantic_hits[0]["distance"] > 0.6):
        fallback_hits = semantic_search(
            query=_expand_query(question),
            stock_code=stock_code,
            top_k=top_k,
        )
        # 去重：避免同一文本块被重复加入
        seen_chunk_keys = {
            (h["metadata"].get("stock_code"), h["metadata"].get("chunk_index"))
            for h in semantic_hits
        }
        for fallback_hit in fallback_hits:
            chunk_key = (
                fallback_hit["metadata"].get("stock_code"),
                fallback_hit["metadata"].get("chunk_index"),
            )
            if chunk_key not in seen_chunk_keys:
                semantic_hits.append(fallback_hit)
                seen_chunk_keys.add(chunk_key)
        semantic_hits = semantic_hits[:top_k]

    # 将检索片段格式化为带来源标注的文本块
    for idx, hit in enumerate(semantic_hits):
        meta = hit["metadata"]
        section_name = meta.get("section", "")
        sub_section_name = meta.get("sub_section", "")
        location_label = f"{section_name} {sub_section_name}".strip() or "正文"
        context_parts.append(
            f"【原文片段{idx + 1}】来源：{meta['title']}（{meta['ann_date']}）- {location_label}\n{hit['text']}"
        )
        source_key = f"{meta['title']} · {location_label}" if location_label else meta['title']
        if source_key not in sources:
            sources.append(source_key)

    if not context_parts:
        return None, [], "在知识库中未检索到相关内容，请尝试换个问法。"

    # 拼接所有上下文，构造最终的用户 prompt
    full_context = "\n\n".join(context_parts)
    user_prompt = f"以下是公司财务数据：\n\n{full_context}\n\n问题：{question}"
    return user_prompt, sources, ""


def ask(
    stock_code: str,
    question: str,
    report_type: str = "annual",
    top_k: int = 5,
) -> dict:
    """
    同步问答接口，一次性返回完整答案。

    参数：
        stock_code:  股票代码，如 "600519"
        question:    用户问题
        report_type: 检索范围，"annual"=仅年报，"all"=不限
        top_k:       语义检索返回的最大片段数

    返回：
        {"answer": str, "sources": list[str], "has_data": bool}
    """
    user_prompt, sources, error = _build_context(stock_code, question, report_type, top_k)
    if error:
        return {
            "answer": error,
            "sources": [],
            "has_data": bool(get_indexed_reports(stock_code)),
        }
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
    流式问答接口，逐步 yield JSON 字符串，适合前端实时展示。

    yield 事件格式：
      {"type": "sources",  "sources": [...]}    # 先发来源列表
      {"type": "token",    "content": "..."}    # 逐 token 流式回答
      {"type": "done"}                          # 回答结束
      {"type": "error",    "message": "..."}    # 发生错误

    参数：
        stock_code:    股票代码
        question:      用户问题
        report_type:   检索范围
        top_k:         语义检索片段数
        stock_context: 附加到系统提示的公司背景信息（可选）
    """
    import json
    user_prompt, sources, error = _build_context(stock_code, question, report_type, top_k)
    if error:
        yield json.dumps({"type": "error", "message": error})
        return

    # 将公司背景信息追加到系统提示（如有）
    system_prompt = SYSTEM_PROMPT + (f"\n\n{stock_context}" if stock_context else "")

    yield json.dumps({"type": "sources", "sources": sources})
    for token in chat_stream(system=system_prompt, user=user_prompt):
        yield json.dumps({"type": "token", "content": token})
    yield json.dumps({"type": "done"})
