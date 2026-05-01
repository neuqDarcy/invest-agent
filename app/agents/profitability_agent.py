from app.agents.base_agent import BaseAgent, AgentResult
from app.parsers.pdf_parser import ParsedDocument
from app.core.llm import chat

SYSTEM_PROMPT = """你是一位资深A股研究员，专注于公司基本面分析。
分析时严格基于提供的文档内容，不编造数据。
每个数据结论必须能在原文中找到依据。
输出格式要求：
1. 核心结论（1-2句话）
2. 关键指标（列举具体数字）
3. 风险提示（如有）
评分标准（1-5分）：1=很差 2=较差 3=一般 4=较好 5=优秀"""

USER_PROMPT_TEMPLATE = """请基于以下公司文档，分析其盈利能力。

重点关注：
- 营业收入及增速
- 净利润及增速
- 毛利率、净利率变化趋势
- ROE、ROA水平
- 与行业均值对比（如文档有提及）

文档内容：
{text}

{tables_section}

请给出盈利能力评分（1-5分）和详细分析。"""


class ProfitabilityAgent(BaseAgent):
    @property
    def dimension(self) -> str:
        return "盈利能力"

    def analyze(self, doc: ParsedDocument) -> AgentResult:
        tables_section = ""
        if doc.tables:
            tables_text = "\n".join(
                "\n".join("\t".join(str(cell) for cell in row if cell) for row in table)
                for table in doc.tables[:10]  # 最多取前10张表
            )
            tables_section = f"识别出的表格数据：\n{tables_text}"

        # 文档过长时截断，避免超出上下文
        text = doc.text[:8000] if len(doc.text) > 8000 else doc.text

        user_prompt = USER_PROMPT_TEMPLATE.format(
            text=text,
            tables_section=tables_section,
        )

        result_text = chat(system=SYSTEM_PROMPT, user=user_prompt)

        # 简单解析评分
        score = self._extract_score(result_text)

        return AgentResult(
            dimension=self.dimension,
            summary=self._extract_summary(result_text),
            details=result_text,
            score=score,
        )

    def _extract_score(self, text: str) -> int | None:
        import re
        matches = re.findall(r"[评分：:]\s*([1-5])", text)
        if matches:
            return int(matches[0])
        return None

    def _extract_summary(self, text: str) -> str:
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        return lines[0] if lines else text[:100]
