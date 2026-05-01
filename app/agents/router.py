"""
对话路由器：判断用户问题应该由谁来回答。

三条路径：
  1. knowledge_qa  → 单公司问答（有明确股票代码 + 问财报/年报内容）
  2. fund_manager  → 基金经理 Agent（选股/策略/投资建议/跨公司分析）
  3. general       → 通用问题（直接 LLM 回答，无需工具）
"""
import re
from app.core.llm import chat
from app.core.logger import get_logger

logger = get_logger("router")

# 明确指向基金经理的关键词
_FM_KEYWORDS = [
    "选股", "选几只", "找几只", "推荐", "投资建议", "值不值得买",
    "值得投资", "交易策略", "买入策略", "卖出策略", "投资组合",
    "哪些公司", "哪只股票", "什么股票", "筛选", "机会",
    "行业", "赛道", "板块", "比较", "对比", "横向",
    "巴菲特", "价值投资", "成长股", "低估", "高估",
]

# 明确指向知识库问答的关键词
_KA_KEYWORDS = [
    "年报", "财报", "季报", "公告", "研报",
    "营收", "净利润", "现金流", "资产负债", "毛利率",
    "收入确认", "会计政策", "管理层", "护城河", "风险",
    "多少钱", "增速", "同比", "CAGR", "自由现金流",
]


def _extract_stock_code(text: str) -> str | None:
    """从文本中提取6位股票代码"""
    m = re.search(r'\b(6\d{5}|0\d{5}|3\d{5})\b', text)
    return m.group(1) if m else None


def route(question: str, current_stock_code: str | None = None) -> dict:
    """
    判断问题路由。
    返回 {route: 'knowledge_qa'|'fund_manager'|'general', stock_code, reason}
    """
    code = _extract_stock_code(question) or current_stock_code

    fm_score = sum(1 for kw in _FM_KEYWORDS if kw in question)
    ka_score = sum(1 for kw in _KA_KEYWORDS if kw in question)

    # 有股票代码 + 知识库关键词 → 走知识库问答
    if code and ka_score > 0:
        result = {"route": "knowledge_qa", "stock_code": code, "reason": "检测到股票代码和财报相关问题"}
    # 基金经理关键词多 → 走基金经理
    elif fm_score > 0 and fm_score >= ka_score:
        result = {"route": "fund_manager", "stock_code": code, "reason": "检测到选股/策略相关问题"}
    # 有股票代码但无明确关键词 → 走知识库
    elif code:
        result = {"route": "knowledge_qa", "stock_code": code, "reason": "检测到股票代码，默认走知识库问答"}
    else:
        result = {"route": "general", "stock_code": None, "reason": "通用问题"}

    logger.info(f"路由 [{result['route']}] stock={result['stock_code']} | {result['reason']} | 问题: {question[:50]}")
    return result
