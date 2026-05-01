import pdfplumber
from dataclasses import dataclass


@dataclass
class ParsedDocument:
    file_name: str
    text: str                    # 全文文本
    tables: list[list]           # 所有识别出的表格
    page_count: int


def parse_pdf(file_path: str) -> ParsedDocument:
    text_parts = []
    all_tables = []

    with pdfplumber.open(file_path) as pdf:
        page_count = len(pdf.pages)
        for page in pdf.pages:
            page_text = page.extract_text() or ""
            text_parts.append(page_text)

            tables = page.extract_tables()
            for table in tables:
                if table:
                    all_tables.append(table)

    return ParsedDocument(
        file_name=file_path.split("/")[-1],
        text="\n".join(text_parts),
        tables=all_tables,
        page_count=page_count,
    )
