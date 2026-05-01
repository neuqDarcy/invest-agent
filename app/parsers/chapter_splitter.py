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
    text: str
    section: str        # 如 "第三节 管理层讨论与分析"
    sub_section: str    # 如 "一、主营业务分析"
    sub_sub_section: str  # 如 "（一）收入构成"
    chapter_index: int  # 第几节（1-12）
    chunk_index: int    # 该章节内第几块


# ── 章节识别正则 ──────────────────────────────────────────────────────────────

# 主章节：第X节
_CHAPTER_RE = re.compile(
    r'^第(一|二|三|四|五|六|七|八|九|十|十一|十二)节\s+\S'
)

# 一级子章节：一、二、三... 或 (一)(二)(三)...
_SUB1_RE = re.compile(
    r'^(一|二|三|四|五|六|七|八|九|十)[、．]\s*\S'
)

# 二级子章节：（一）（二）... 或 1. 2. ...
_SUB2_RE = re.compile(
    r'^[（(](一|二|三|四|五|六|七|八|九|十)[）)]\s*\S'
    r'|^\d+[\.、]\s{1,3}\S'
)

# 重要子节（财务报告内）
_FINANCE_KEYWORDS = [
    '重要会计政策', '收入确认', '合并财务报表', '母公司财务报表',
    '关联交易', '重大合同', '审计报告', '利润分配',
]

_CHAPTER_NUM = {
    '一': 1, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6,
    '七': 7, '八': 8, '九': 9, '十': 10, '十一': 11, '十二': 12,
}


def _clean(line: str) -> str:
    """去掉目录中的点线和页码"""
    line = re.sub(r'[．。\.]{3,}.*$', '', line)
    line = re.sub(r'\s+\d+\s*$', '', line)
    return line.strip()


def _is_chapter(line: str) -> tuple[bool, int, str]:
    """返回 (是否主章节, 章节序号, 章节标题)"""
    m = _CHAPTER_RE.match(line.strip())
    if m:
        num_str = m.group(1)
        return True, _CHAPTER_NUM.get(num_str, 0), _clean(line.strip())
    return False, 0, ''


def _is_sub1(line: str) -> tuple[bool, str]:
    m = _SUB1_RE.match(line.strip())
    if m:
        return True, _clean(line.strip())
    return False, ''


def _is_sub2(line: str) -> tuple[bool, str]:
    m = _SUB2_RE.match(line.strip())
    if m:
        return True, _clean(line.strip())
    return False, ''


def split_by_chapter(
    text: str,
    chunk_size: int = 600,
    overlap: int = 60,
) -> list[Chunk]:
    """
    把年报全文按章节结构切块。
    同一子章节内若文本过长，再按 chunk_size 滑窗切分。
    """
    lines = text.split('\n')

    # ── 第一步：识别章节边界 ──────────────────────────────────────────
    segments = []   # [(chapter_idx, section, sub1, sub2, lines[])]
    cur_chapter_idx = 0
    cur_section = '前言'
    cur_sub1 = ''
    cur_sub2 = ''
    cur_lines = []

    def flush():
        if cur_lines:
            segments.append((cur_chapter_idx, cur_section, cur_sub1, cur_sub2, list(cur_lines)))

    for line in lines:
        stripped = line.strip()
        if not stripped:
            cur_lines.append(line)
            continue

        is_ch, ch_idx, ch_title = _is_chapter(stripped)
        if is_ch and ch_idx > 0:
            flush()
            cur_lines = []
            cur_chapter_idx = ch_idx
            cur_section = ch_title
            cur_sub1 = ''
            cur_sub2 = ''
            cur_lines.append(line)
            continue

        is_s1, s1_title = _is_sub1(stripped)
        if is_s1 and cur_chapter_idx > 0:
            flush()
            cur_lines = []
            cur_sub1 = s1_title
            cur_sub2 = ''
            cur_lines.append(line)
            continue

        is_s2, s2_title = _is_sub2(stripped)
        if is_s2 and cur_chapter_idx > 0:
            flush()
            cur_lines = []
            cur_sub2 = s2_title
            cur_lines.append(line)
            continue

        cur_lines.append(line)

    flush()

    # ── 第二步：把每个 segment 按 chunk_size 切块 ──────────────────────
    chunks: list[Chunk] = []
    chapter_chunk_counters: dict[int, int] = {}

    for ch_idx, section, sub1, sub2, seg_lines in segments:
        seg_text = '\n'.join(seg_lines).strip()
        if len(seg_text) < 30:   # 太短的段落跳过
            continue

        sub_chunks = _split_text(seg_text, chunk_size, overlap)
        for text_chunk in sub_chunks:
            counter = chapter_chunk_counters.get(ch_idx, 0)
            chapter_chunk_counters[ch_idx] = counter + 1
            chunks.append(Chunk(
                text=text_chunk,
                section=section,
                sub_section=sub1,
                sub_sub_section=sub2,
                chapter_index=ch_idx,
                chunk_index=counter,
            ))

    return chunks


def _split_text(text: str, chunk_size: int, overlap: int) -> list[str]:
    """按段落优先切分，超长时滑窗"""
    if len(text) <= chunk_size:
        return [text]

    paragraphs = [p.strip() for p in text.split('\n') if p.strip()]
    chunks, buf = [], ''
    for para in paragraphs:
        if len(buf) + len(para) + 1 <= chunk_size:
            buf = buf + '\n' + para if buf else para
        else:
            if buf:
                chunks.append(buf)
            # 段落本身超长，强制切分
            if len(para) > chunk_size:
                for i in range(0, len(para), chunk_size - overlap):
                    chunks.append(para[i:i + chunk_size])
            else:
                buf = para
    if buf:
        chunks.append(buf)
    return chunks or [text]
