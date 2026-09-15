"""Build the v12 Word reference guide from the maintained Markdown manual."""

from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path

# The builder is intentionally executable both as ``python -m`` and as a
# standalone file from the release/test harness.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

from utils.app_info import APP_NAME, APP_VERSION


SOURCE = PROJECT_ROOT / "docs" / f"{APP_NAME}-v{APP_VERSION}-使用说明.md"
DEFAULT_OUTPUT = PROJECT_ROOT / "发布" / f"{APP_NAME}-v{APP_VERSION}-使用手册.docx"

# ``compact_reference_guide`` preset, with Microsoft YaHei only for CJK runs.
INK = "000000"
ACCENT = "000000"
MUTED = "5E6B7A"
SOFT = "E8EEF5"
TABLE_WIDTH = 9360


def set_run_font(run, *, size: float | None = None, color: str | None = None, bold: bool | None = None) -> None:
    run.font.name = "Microsoft YaHei"
    run._element.rPr.rFonts.set(qn("w:ascii"), "Calibri")
    run._element.rPr.rFonts.set(qn("w:hAnsi"), "Calibri")
    run._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
    if size is not None:
        run.font.size = Pt(size)
    if color is not None:
        run.font.color.rgb = RGBColor.from_string(color)
    if bold is not None:
        run.bold = bold


def shade(cell, fill: str) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:fill"), fill)
    tc_pr.append(shd)


def set_cell_widths(table, widths: list[int]) -> None:
    table.autofit = False
    table.alignment = 0
    table_pr = table._tbl.tblPr
    table_width = table_pr.first_child_found_in("w:tblW")
    if table_width is None:
        table_width = OxmlElement("w:tblW")
        table_pr.append(table_width)
    table_width.set(qn("w:w"), str(TABLE_WIDTH))
    table_width.set(qn("w:type"), "dxa")
    table_indent = table_pr.first_child_found_in("w:tblInd")
    if table_indent is None:
        table_indent = OxmlElement("w:tblInd")
        table_pr.append(table_indent)
    table_indent.set(qn("w:w"), "120")
    table_indent.set(qn("w:type"), "dxa")
    grid = table._tbl.tblGrid
    for grid_col, width in zip(grid.gridCol_lst, widths):
        grid_col.set(qn("w:w"), str(width))
    for row in table.rows:
        for cell, width in zip(row.cells, widths):
            tc_pr = cell._tc.get_or_add_tcPr()
            tc_w = tc_pr.find(qn("w:tcW"))
            if tc_w is None:
                tc_w = OxmlElement("w:tcW")
                tc_pr.append(tc_w)
            tc_w.set(qn("w:w"), str(width))
            tc_w.set(qn("w:type"), "dxa")
            cell.width = Inches(width / 1440)
            tc_margins = tc_pr.first_child_found_in("w:tcMar")
            if tc_margins is None:
                tc_margins = OxmlElement("w:tcMar")
                tc_pr.append(tc_margins)
            for side, value in (("top", 80), ("bottom", 80), ("start", 120), ("end", 120)):
                node = tc_margins.find(qn(f"w:{side}"))
                if node is None:
                    node = OxmlElement(f"w:{side}")
                    tc_margins.append(node)
                node.set(qn("w:w"), str(value))
                node.set(qn("w:type"), "dxa")


def configure_styles(document: Document) -> None:
    section = document.sections[0]
    section.top_margin = Inches(1.0)
    section.bottom_margin = Inches(1.0)
    section.left_margin = Inches(1.0)
    section.right_margin = Inches(1.0)
    section.header_distance = Inches(0.492)
    section.footer_distance = Inches(0.492)

    normal = document.styles["Normal"]
    normal.font.name = "Microsoft YaHei"
    normal._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
    normal.font.size = Pt(11)
    normal.font.color.rgb = RGBColor.from_string(INK)
    normal.paragraph_format.space_after = Pt(6)
    normal.paragraph_format.line_spacing = 1.25
    for name, size, color, before, after in (
        ("Heading 1", 16, ACCENT, 18, 10),
        ("Heading 2", 13, ACCENT, 14, 7),
        ("Heading 3", 12, INK, 10, 5),
    ):
        style = document.styles[name]
        style.font.name = "Microsoft YaHei"
        style._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
        style.font.size = Pt(size)
        style.font.bold = True
        style.font.color.rgb = RGBColor.from_string(color)
        style.paragraph_format.space_before = Pt(before)
        style.paragraph_format.space_after = Pt(after)
        style.paragraph_format.keep_with_next = True

    header = section.header.paragraphs[0]
    header.alignment = WD_ALIGN_PARAGRAPH.LEFT
    set_run_font(header.add_run("科研助手 · 本地个人科研工作台"), size=8.5, color=MUTED)
    footer = section.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    set_run_font(footer.add_run(f"{APP_NAME} v{APP_VERSION}"), size=8.5, color=MUTED)


def add_cover(document: Document) -> None:
    for _ in range(5):
        spacer = document.add_paragraph()
        spacer.paragraph_format.space_after = Pt(5)
    kicker = document.add_paragraph()
    kicker.alignment = WD_ALIGN_PARAGRAPH.CENTER
    set_run_font(kicker.add_run("PERSONAL RESEARCH WORKSPACE"), size=10, color=ACCENT, bold=True)
    title = document.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    title.paragraph_format.space_before = Pt(14)
    title.paragraph_format.space_after = Pt(5)
    set_run_font(title.add_run(f"{APP_NAME} v{APP_VERSION}"), size=28, color=INK, bold=True)
    subtitle = document.add_paragraph()
    subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER
    subtitle.paragraph_format.space_after = Pt(18)
    set_run_font(subtitle.add_run("本地科研工作台 · 使用说明"), size=14, color=MUTED)
    note = document.add_paragraph()
    note.alignment = WD_ALIGN_PARAGRAPH.CENTER
    set_run_font(note.add_run("从今天的下一步，到长期的投稿与选刊经验。"), size=11, color=INK)
    document.add_page_break()


def add_inline_markdown(paragraph, text: str, *, size: float | None = None) -> None:
    """Render the restrained inline emphasis used by the maintained manual."""

    cursor = 0
    pattern = re.compile(r"(\*\*.+?\*\*|`.+?`)")
    for match in pattern.finditer(text):
        if match.start() > cursor:
            set_run_font(paragraph.add_run(text[cursor : match.start()]), size=size)
        token = match.group(0)
        if token.startswith("**"):
            set_run_font(paragraph.add_run(token[2:-2]), size=size, bold=True)
        else:
            run = paragraph.add_run(token[1:-1])
            set_run_font(run, size=size, color=ACCENT)
            run.font.name = "Consolas"
            run._element.rPr.rFonts.set(qn("w:ascii"), "Consolas")
            run._element.rPr.rFonts.set(qn("w:hAnsi"), "Consolas")
        cursor = match.end()
    if cursor < len(text):
        set_run_font(paragraph.add_run(text[cursor:]), size=size)


def restart_numbered_list(document: Document, paragraph, style_name: str = "List Number") -> None:
    """Give each Markdown ordered-list block an independent Word numId.

    Word otherwise continues a built-in ``List Number`` sequence across distant
    sections, which made the final daily routine start at 4 instead of 1.
    """

    style_ppr = document.styles[style_name]._element.find(qn("w:pPr"))
    style_num_pr = style_ppr.find(qn("w:numPr")) if style_ppr is not None else None
    style_num_id = style_num_pr.find(qn("w:numId")) if style_num_pr is not None else None
    if style_num_id is None:
        return
    numbering = document.part.numbering_part.element
    source_num = next(
        (
            node
            for node in numbering.findall(qn("w:num"))
            if node.get(qn("w:numId")) == style_num_id.get(qn("w:val"))
        ),
        None,
    )
    if source_num is None:
        return
    source_abstract = source_num.find(qn("w:abstractNumId"))
    if source_abstract is None:
        return
    next_num_id = max((int(node.get(qn("w:numId"))) for node in numbering.findall(qn("w:num"))), default=0) + 1
    num = OxmlElement("w:num")
    num.set(qn("w:numId"), str(next_num_id))
    abstract = OxmlElement("w:abstractNumId")
    abstract.set(qn("w:val"), source_abstract.get(qn("w:val")))
    num.append(abstract)
    override = OxmlElement("w:lvlOverride")
    override.set(qn("w:ilvl"), "0")
    start = OxmlElement("w:startOverride")
    start.set(qn("w:val"), "1")
    override.append(start)
    num.append(override)
    numbering.append(num)
    p_pr = paragraph._p.get_or_add_pPr()
    num_pr = OxmlElement("w:numPr")
    level = OxmlElement("w:ilvl")
    level.set(qn("w:val"), "0")
    num_id = OxmlElement("w:numId")
    num_id.set(qn("w:val"), str(next_num_id))
    num_pr.append(level)
    num_pr.append(num_id)
    p_pr.append(num_pr)


def add_paragraph(
    document: Document,
    text: str,
    *,
    style: str | None = None,
    restart_number: bool = False,
) -> None:
    paragraph = document.add_paragraph(style=style)
    paragraph.paragraph_format.space_after = Pt(5)
    add_inline_markdown(paragraph, text)
    if restart_number and style == "List Number":
        restart_numbered_list(document, paragraph)


def add_markdown_table(document: Document, rows: list[list[str]]) -> None:
    if not rows:
        return
    column_count = max(len(row) for row in rows)
    normalized = [row + [""] * (column_count - len(row)) for row in rows]
    table = document.add_table(rows=1, cols=column_count)
    table.style = "Table Grid"
    widths = [TABLE_WIDTH // column_count] * column_count
    widths[-1] += TABLE_WIDTH - sum(widths)
    set_cell_widths(table, widths)
    header_properties = table.rows[0]._tr.get_or_add_trPr()
    repeat_header = OxmlElement("w:tblHeader")
    repeat_header.set(qn("w:val"), "true")
    header_properties.append(repeat_header)
    for index, row in enumerate(normalized):
        table_row = table.rows[0] if index == 0 else table.add_row()
        row_properties = table_row._tr.get_or_add_trPr()
        if row_properties.find(qn("w:cantSplit")) is None:
            row_properties.append(OxmlElement("w:cantSplit"))
        cells = table_row.cells
        for cell, value in zip(cells, row):
            if index == 0:
                shade(cell, SOFT)
            elif index % 2:
                shade(cell, SOFT)
            paragraph = cell.paragraphs[0]
            paragraph.paragraph_format.space_after = Pt(0)
            if index == 0:
                set_run_font(paragraph.add_run(value), size=9.4, color=INK, bold=True)
            else:
                add_inline_markdown(paragraph, value, size=9.4)
    document.add_paragraph().paragraph_format.space_after = Pt(2)


def _table_cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _is_separator(line: str) -> bool:
    return bool(re.fullmatch(r"\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*", line))


def render_markdown(document: Document, markdown: str) -> None:
    lines = markdown.splitlines()
    index = 0
    in_ordered_list = False
    while index < len(lines):
        line = lines[index].rstrip()
        stripped = line.strip()
        if not stripped:
            in_ordered_list = False
            index += 1
            continue
        if stripped.startswith("# "):
            # The document already has a cover; retain the first heading as a
            # compact lead instead of duplicating a giant second title.
            lead = document.add_paragraph()
            lead.paragraph_format.space_after = Pt(12)
            set_run_font(lead.add_run(stripped[2:]), size=15, color=ACCENT, bold=True)
            index += 1
            in_ordered_list = False
            continue
        if stripped.startswith("## "):
            document.add_heading(stripped[3:], level=1)
            index += 1
            in_ordered_list = False
            continue
        if stripped.startswith("### "):
            document.add_heading(stripped[4:], level=2)
            index += 1
            in_ordered_list = False
            continue
        if stripped.startswith("| ") and index + 1 < len(lines) and _is_separator(lines[index + 1]):
            table_rows = [_table_cells(stripped)]
            index += 2
            while index < len(lines) and lines[index].strip().startswith("|"):
                table_rows.append(_table_cells(lines[index]))
                index += 1
            add_markdown_table(document, table_rows)
            in_ordered_list = False
            continue
        if stripped.startswith("- "):
            add_paragraph(document, stripped[2:], style="List Bullet")
            index += 1
            in_ordered_list = False
            continue
        ordered = re.match(r"^\d+\.\s+(.*)$", stripped)
        if ordered:
            add_paragraph(
                document,
                ordered.group(1),
                style="List Number",
                restart_number=not in_ordered_list,
            )
            index += 1
            in_ordered_list = True
            continue
        add_paragraph(document, stripped)
        index += 1
        in_ordered_list = False


def build_manual(output: Path, source: Path = SOURCE) -> None:
    if not source.is_file():
        raise FileNotFoundError(f"找不到 v11 使用说明源文件：{source}")
    output.parent.mkdir(parents=True, exist_ok=True)
    document = Document()
    configure_styles(document)
    add_cover(document)
    source_text = source.read_text(encoding="utf-8")
    base_manual = PROJECT_ROOT / "docs" / f"{APP_NAME}-v12.0-使用说明.md"
    if APP_VERSION != "12.0" and source.resolve() == SOURCE.resolve() and base_manual.is_file():
        base_text = base_manual.read_text(encoding="utf-8")
        base_text = re.sub(r"^#\s+.*?\n", "", base_text, count=1)
        base_text = base_text.replace("v12.0", f"v{APP_VERSION}")
        source_text += "\n\n## 完整功能说明\n\n" + base_text
    render_markdown(document, source_text)
    document.save(output)


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUTPUT
    build_manual(target)
    print(f"已生成使用手册：{target}")
