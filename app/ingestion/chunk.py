"""Structure-aware chunking.

Chunk boundaries follow legal structure: articles, numbered clauses (1, 1.1,
1.1.1), and headings. Lettered and roman sub-items stay with their clause
unless the chunk grows large. Each chunk records its section path
("3.1 > (b) > (ii)") and page range, which the citations use.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.ingestion.parse import Block

ARTICLE_RE = re.compile(r"^(ARTICLE|Article|SECTION|Section)\s+([0-9]+|[IVXLCivxlc]+)\b")
NUM_RE = re.compile(r"^(\d+(?:\.\d+){0,3})\.?\s+(?=[A-Z\"“(])")
ALPHA_RE = re.compile(r"^\(([a-z]{1,2})\)\s*")
ROMAN_RE = re.compile(r"^\(([ivxlc]{1,6})\)\s*")
HEADING_MAX_WORDS = 8
HEADING_MAX_CHARS = 80


def _is_content(text: str) -> bool:
    """A line that carries a sentence, not just a label such as '1.2 Term' or 'ARTICLE 1 DEFINITIONS'."""
    return len(text.split()) > HEADING_MAX_WORDS or len(text) > HEADING_MAX_CHARS


@dataclass(frozen=True)
class Chunk:
    content: str
    section_path: str
    page_start: int | None
    page_end: int | None
    view_label: str


@dataclass
class _PathState:
    clause: str = ""
    sub: str = ""
    subsub: str = ""

    def label(self) -> str:
        return " > ".join(p for p in (self.clause, self.sub, self.subsub) if p)


def _next_letter(sub: str) -> str:
    letter = sub.strip("()")
    return chr(ord(letter) + 1) if len(letter) == 1 and letter < "z" else ""


def _classify(block: Block, st: _PathState) -> str:
    """Update the path state and return 'boundary', 'sub', or 'body'."""
    text = block.text
    if m := ARTICLE_RE.match(text):
        st.clause, st.sub, st.subsub = f"{m.group(1)} {m.group(2)}", "", ""
        return "boundary"
    if m := NUM_RE.match(text):
        st.clause, st.sub, st.subsub = m.group(1), "", ""
        return "boundary"
    if m := ROMAN_RE.match(text):
        label = m.group(1)
        # (i) after (h) is the next letter, not a roman sub-sub-item.
        if st.sub and label != _next_letter(st.sub):
            st.subsub = f"({label})"
            return "sub"
    if m := ALPHA_RE.match(text):
        st.sub, st.subsub = f"({m.group(1)})", ""
        return "sub"
    if m := ROMAN_RE.match(text):
        st.sub, st.subsub = f"({m.group(1)})", ""
        return "sub"
    if block.is_heading and not _is_content(text):
        st.clause, st.sub, st.subsub = text, "", ""
        return "boundary"
    return "body"


def chunk_blocks(blocks: list[Block], view_label: str, max_chars: int = 1600) -> list[Chunk]:
    st = _PathState()
    out: list[Chunk] = []
    lines: list[str] = []
    pages: list[int] = []
    path = ""
    has_body = False
    size = 0

    def flush() -> None:
        nonlocal lines, pages, has_body, size
        if lines and has_body:
            out.append(Chunk(
                content="\n".join(lines),
                section_path=path,
                page_start=min(pages) if pages else None,
                page_end=max(pages) if pages else None,
                view_label=view_label,
            ))
        lines, pages, has_body, size = [], [], False, 0

    for block in blocks:
        text = block.text.replace("\x00", "")
        if not text:
            continue
        kind = _classify(Block(text, block.page, block.is_heading), st)
        if kind == "boundary" and has_body:
            flush()
        if kind == "sub" and has_body and size >= max_chars // 2:
            flush()
        # Sub-items and body lines always carry content. Boundary lines count only when they hold a sentence.
        is_body = kind != "boundary" or _is_content(text)
        if has_body and is_body and size + len(text) > max_chars:
            flush()
        if not has_body:
            path = st.label()
        lines.append(text)
        size += len(text) + 1
        if block.page is not None:
            pages.append(block.page)
        if is_body:
            has_body = True
    flush()
    return out
