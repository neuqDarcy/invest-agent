"""
年报章节化拆分：按章节/子章节切块，每个 chunk 携带结构化 metadata。

A股年报标准结构：
  第一节  释义
  第二节  公司简介和主要财务指标
  第三节  管理层讨论与分析
  第四节  公司治理
  第五节  环境与社会责任
  第六节  重要事项
  第七节  股份变动及股东情况
  第八节  优先股相关情况
  第九节  债券相关情况
  第十节  财务报告（含会计政策、三表、附注）
"""
import re
from dataclasses import dataclass, field


@dataclass
class Chunk:
    """年报文本块，携带章节层级元数据，用于向量检索时精确定位来源。"""
    text: str
    section: str           # 主章节，如 "第三节 管理层讨论与分析"
    sub_section: str       # 一级子章节，如 "一、主营业务分析"
    sub_sub_section: str   # 二级子章节，如 "（一）收入构成"
    chapter_index: int     # 主章节序号（1-12）
    chunk_index: int       # 该章节内的分块序号


# ── 章节识别正则 ──────────────────────────────────────────────────────────────

# 主章节：匹配"第X节"格式，要求节名后紧跟非空内容（排除目录中的虚线行）
_CHAPTER_RE = re.compile(
    r'^第(一|二|三|四|五|六|七|八|九|十|十一|十二)节\s+\S'
)

# 一级子章节：匹配"一、""二、"等中文数字加顿号格式
_SUB1_RE = re.compile(
    r'^(一|二|三|四|五|六|七|八|九|十)[、．]\s*\S'
)

# 二级子章节：匹配"（一）（二）"括号格式，或"1. 2."阿拉伯数字格式
_SUB2_RE = re.compile(
    r'^[（(](一|二|三|四|五|六|七|八|九|十)[）)]\s*\S'
    r'|^\d+[\.、]\s{1,3}\S'
)

# 财务报告章节内的重要子节关键词（用于未来扩展精细化拆分）
_FINANCE_KEYWORDS = [
    '重要会计政策', '收入确认', '合并财务报表', '母公司财务报表',
    '关联交易', '重大合同', '审计报告', '利润分配',
]

# 中文数字 → 阿拉伯数字的映射，用于提取章节序号
_CHAPTER_NUM = {
    '一': 1, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6,
    '七': 7, '八': 8, '九': 9, '十': 10, '十一': 11, '十二': 12,
}


def _clean(line: str) -> str:
    """
    清理目录行中的点线填充和页码，保留纯标题文本。

    参数:
        line: 原始行文本

    返回:
        去除点线和页码后的标题文本
    """
    line = re.sub(r'[．。\.]{3,}.*$', '', line)   # 去掉 "……… 12" 这类目录填充
    line = re.sub(r'\s+\d+\s*$', '', line)         # 去掉行尾页码数字
    return line.strip()


def _is_chapter(line: str) -> tuple[bool, int, str]:
    """
    判断行是否为主章节标题。

    参数:
        line: 待检测的文本行

    返回:
        (是否主章节, 章节序号, 章节标题) 三元组
    """
    matched = _CHAPTER_RE.match(line.strip())
    if matched:
        num_str = matched.group(1)
        return True, _CHAPTER_NUM.get(num_str, 0), _clean(line.strip())
    return False, 0, ''


def _is_sub1(line: str) -> tuple[bool, str]:
    """
    判断行是否为一级子章节标题（如"一、主营业务分析"）。

    返回:
        (是否匹配, 清理后的标题)
    """
    matched = _SUB1_RE.match(line.strip())
    if matched:
        return True, _clean(line.strip())
    return False, ''


def _is_sub2(line: str) -> tuple[bool, str]:
    """
    判断行是否为二级子章节标题（如"（一）收入构成"或"1. 产品分类"）。

    返回:
        (是否匹配, 清理后的标题)
    """
    matched = _SUB2_RE.match(line.strip())
    if matched:
        return True, _clean(line.strip())
    return False, ''


def split_by_chapter(
    text: str,
    chunk_size: int = 600,
    overlap: int = 60,
) -> list[Chunk]:
    """
    将年报全文按章节结构切块，同一子章节内若文本过长再按滑窗切分。

    参数:
        text:       年报全文字符串
        chunk_size: 单个 chunk 的最大字符数
        overlap:    相邻 chunk 的重叠字符数（保留上下文连贯性）

    返回:
        Chunk 列表，每项携带章节层级 metadata
    """
    lines = text.split('\n')

    # ── 第一步：识别章节边界，将全文切成带层级标签的 segment ──────────────
    # 每个 segment 格式：(章节序号, 主章节标题, 一级子标题, 二级子标题, 行列表)
    segments = []
    current_chapter_idx = 0
    current_section = '前言'
    current_sub1 = ''
    current_sub2 = ''
    current_lines = []

    def flush_segment():
        """将当前积累的行作为一个 segment 提交，重置缓冲区。"""
        if current_lines:
            segments.append((
                current_chapter_idx, current_section,
                current_sub1, current_sub2,
                list(current_lines),
            ))

    for line in lines:
        stripped = line.strip()
        if not stripped:
            current_lines.append(line)
            continue

        is_chapter, chapter_idx, chapter_title = _is_chapter(stripped)
        if is_chapter and chapter_idx > 0:
            flush_segment()
            current_lines = []
            current_chapter_idx = chapter_idx
            current_section = chapter_title
            current_sub1 = ''
            current_sub2 = ''
            current_lines.append(line)
            continue

        is_sub1, sub1_title = _is_sub1(stripped)
        # 只在已进入正文章节后才识别子章节，避免目录误匹配
        if is_sub1 and current_chapter_idx > 0:
            flush_segment()
            current_lines = []
            current_sub1 = sub1_title
            current_sub2 = ''
            current_lines.append(line)
            continue

        is_sub2, sub2_title = _is_sub2(stripped)
        if is_sub2 and current_chapter_idx > 0:
            flush_segment()
            current_lines = []
            current_sub2 = sub2_title
            current_lines.append(line)
            continue

        current_lines.append(line)

    flush_segment()

    # ── 第二步：将每个 segment 按 chunk_size 滑窗切块 ──────────────────
    chunks: list[Chunk] = []
    # 每个章节独立计数，确保 chunk_index 在章节内连续
    chapter_chunk_counters: dict[int, int] = {}

    for chapter_idx, section, sub1, sub2, segment_lines in segments:
        segment_text = '\n'.join(segment_lines).strip()
        # 过短的段落（如空节标题）无检索价值，直接跳过
        if len(segment_text) < 30:
            continue

        text_chunks = _split_text(segment_text, chunk_size, overlap)
        for text_chunk in text_chunks:
            current_counter = chapter_chunk_counters.get(chapter_idx, 0)
            chapter_chunk_counters[chapter_idx] = current_counter + 1
            chunks.append(Chunk(
                text=text_chunk,
                section=section,
                sub_section=sub1,
                sub_sub_section=sub2,
                chapter_index=chapter_idx,
                chunk_index=current_counter,
            ))

    return chunks


def _split_text(text: str, chunk_size: int, overlap: int) -> list[str]:
    """
    按自然段落优先切分文本，段落超长时退化为滑窗切分。

    优先按段落边界切分的原因：段落内语义完整，强制截断会破坏语义连贯性。

    参数:
        text:       待切分的文本
        chunk_size: 每块最大字符数
        overlap:    滑窗重叠字符数

    返回:
        切分后的文本块列表
    """
    if len(text) <= chunk_size:
        return [text]

    paragraphs = [para.strip() for para in text.split('\n') if para.strip()]
    chunks = []
    buffer = ''

    for paragraph in paragraphs:
        if len(buffer) + len(paragraph) + 1 <= chunk_size:
            # 当前段落可以追加到缓冲区
            buffer = buffer + '\n' + paragraph if buffer else paragraph
        else:
            if buffer:
                chunks.append(buffer)
            # 单段落超过 chunk_size，强制滑窗切分（避免丢失内容）
            if len(paragraph) > chunk_size:
                for start_pos in range(0, len(paragraph), chunk_size - overlap):
                    chunks.append(paragraph[start_pos:start_pos + chunk_size])
            else:
                buffer = paragraph

    if buffer:
        chunks.append(buffer)

    return chunks or [text]
