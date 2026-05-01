"""
从用户问题中识别多个股票实体（股票代码或公司名），
支持 A 股六位数字代码和常见公司简称。
"""
import re
from app.core.llm import chat

# 六位 A 股代码
_CODE_RE = re.compile(r"\b(\d{6})\b")

SYSTEM_PROMPT = """你是一个实体识别助手。从用户问题中提取所有提到的 A 股上市公司。
输出格式：每行一个公司，格式为"股票代码|公司名称"，股票代码未知则留空。
如果只提到一家公司，也按格式输出一行。
如果没有提到任何公司，输出"NONE"。
只输出结果，不要解释。"""


def extract_entities(question: str) -> list[dict]:
    """
    返回 [{"stock_code": "600926", "name": "杭州银行"}, ...]
    stock_code 可能为空字符串（仅识别到名称未知代码时）
    """
    codes_in_text = _CODE_RE.findall(question)

    result_text = chat(system=SYSTEM_PROMPT, user=question)

    if result_text.strip() == "NONE":
        return [{"stock_code": c, "name": ""} for c in codes_in_text]

    entities = []
    seen_codes = set()
    for line in result_text.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("|")
        code = parts[0].strip() if len(parts) >= 1 else ""
        name = parts[1].strip() if len(parts) >= 2 else ""
        if code and not re.fullmatch(r"\d{6}", code):
            code = ""
        if code and code in seen_codes:
            continue
        if code:
            seen_codes.add(code)
        entities.append({"stock_code": code, "name": name})

    # 补充正则发现但 LLM 漏掉的代码
    for c in codes_in_text:
        if c not in seen_codes:
            entities.append({"stock_code": c, "name": ""})

    return entities
