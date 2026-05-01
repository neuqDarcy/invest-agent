from abc import ABC, abstractmethod
from dataclasses import dataclass
from app.parsers.pdf_parser import ParsedDocument


@dataclass
class AgentResult:
    dimension: str       # 分析维度名称
    summary: str         # 核心结论
    details: str         # 详细分析
    score: int | None    # 1-5 评分，None 表示不适用


class BaseAgent(ABC):
    @property
    @abstractmethod
    def dimension(self) -> str:
        pass

    @abstractmethod
    def analyze(self, doc: ParsedDocument) -> AgentResult:
        pass
