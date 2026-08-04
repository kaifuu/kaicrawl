# -*- coding: utf-8 -*-
"""按《爬虫数据来源.xlsx》格式规范生成 WORD 文件。

规范：
  标题：加粗 / 宋体 / 五号(10.5pt) / 居中 / 单倍行距
  正文：首行空两格 / 宋体 / 五号 / 左对齐 / 单倍行距
  作者：单位动态 -> 「各单位」；其他用原作者或无
  图片：png/jpg，按原文位置插入，并单独保存到当天目录的 images 子目录
"""
import os

from docx import Document
from docx.shared import Pt, Inches
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

from config import OUTPUT_DIR, FONT_NAME, FONT_SIZE_PT
from .utils import safe_filename, unique_path, ParserError
from .utils import http_get

IMG_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}

# 图片文件头魔数，用于校验下载内容确实是图片（防止把 404/HTML 错误页误存为图片）
_IMG_MAGICS = (b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n", b"GIF87a", b"GIF89a", b"BM")


def _is_image_bytes(data, ctype=""):
    """下载到的字节是否真的是图片：优先校验魔数，Content-Type 作兜底。"""
    if not data:
        return False
    if data.startswith(_IMG_MAGICS):
        return True
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return True
    return "image/" in (ctype or "").lower() and len(data) > 64


def _set_run_font(run, size_pt=FONT_SIZE_PT, bold=False):
    """设置 run 字体为宋体（含中文 eastAsia）、字号、加粗。"""
    run.font.name = FONT_NAME
    run.font.size = Pt(size_pt)
    run.bold = bold
    rPr = run._element.get_or_add_rPr()
    rFonts = rPr.find(qn("w:rFonts"))
    if rFonts is None:
        rFonts = OxmlElement("w:rFonts")
        rPr.append(rFonts)
    rFonts.set(qn("w:eastAsia"), FONT_NAME)
    rFonts.set(qn("w:ascii"), FONT_NAME)
    rFonts.set(qn("w:hAnsi"), FONT_NAME)


def _set_single_spacing(paragraph):
    pf = paragraph.paragraph_format
    pf.line_spacing_rule = WD_LINE_SPACING.SINGLE
    pf.line_spacing = 1.0
    pf.space_before = Pt(0)
    pf.space_after = Pt(0)


def _set_first_line_indent_2chars(paragraph):
    """首行缩进 2 个中文字符（OOXML firstLineChars=200）。"""
    pPr = paragraph._p.get_or_add_pPr()
    ind = pPr.find(qn("w:ind"))
    if ind is None:
        ind = OxmlElement("w:ind")
        pPr.append(ind)
    ind.set(qn("w:firstLineChars"), "200")
    # 回退值（twips），2 字符 × 五号(10.5pt) × 20 ≈ 420
    ind.set(qn("w:firstLine"), str(int(2 * FONT_SIZE_PT * 20)))


def _add_title(doc, text):
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _set_single_spacing(p)
    run = p.add_run(text)
    _set_run_font(run, bold=True)
    return p


def _add_meta(doc, text):
    """作者/日期行：右对齐，宋体五号。"""
    if not text:
        return
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    _set_single_spacing(p)
    run = p.add_run(text)
    _set_run_font(run)
    return p


def _add_body_text(doc, text):
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.LEFT
    _set_single_spacing(p)
    _set_first_line_indent_2chars(p)
    run = p.add_run(text)
    _set_run_font(run)
    return p


def _add_image(doc, local_path):
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _set_single_spacing(p)
    run = p.add_run()
    try:
        run.add_picture(local_path, width=Inches(5.2))
    except Exception:
        run.add_text("[图片插入失败]")
    return p


def _download_image(url, images_dir, seq, name_prefix=""):
    """下载单张图片到 images_dir，返回本地路径或 None。

    文件名：<name_prefix>_<seq>.<ext>。name_prefix 取自文章 docx 文件名，使每篇文章
    的图片在共享的 images/ 目录里互不撞名（旧实现用 001/002 会被同分类同日的其它文章覆盖）。
    下载后校验字节确为图片（魔数），非图片（如 404/HTML 错误页）返回 None。
    """
    try:
        resp = http_get(url)
        if resp.status_code >= 400:
            return None
        data = resp.content
        ctype = resp.headers.get("Content-Type", "")
        if not _is_image_bytes(data, ctype):
            return None
        # 由 Content-Type 或 URL 推断扩展名
        ctype_l = ctype.lower()
        ext = ".jpg"
        if "png" in ctype_l:
            ext = ".png"
        elif "jpeg" in ctype_l or "jpg" in ctype_l:
            ext = ".jpg"
        elif "webp" in ctype_l:
            ext = ".webp"
        elif "gif" in ctype_l:
            ext = ".gif"
        else:
            low = url.lower().split("?", 1)[0]
            for e in IMG_EXTS:
                if low.endswith(e):
                    ext = e
                    break
        os.makedirs(images_dir, exist_ok=True)
        prefix = safe_filename(name_prefix) if name_prefix else f"{seq:03d}"
        path = os.path.join(images_dir, f"{prefix}_{seq}{ext}")
        with open(path, "wb") as f:
            f.write(data)
        return path
    except Exception:
        return None


def _resolve_author(source, detail):
    """作者规则：单位动态 -> 各单位；其他 -> 原作者或空。"""
    if source.author_policy:
        return source.author_policy
    return (detail.get("author") or "").strip()


def generate(source, detail, date_str):
    """生成单篇文章的 WORD 文件。

    返回 (docx_abs_path, images_abs_dir)。
    落盘路径：OUTPUT_DIR/<分类>/<date>/<标题>.docx，图片存于同目录 images/。
    """
    category_dir = safe_filename(source.category) or "未分类"
    out_dir = os.path.join(OUTPUT_DIR, category_dir, date_str)
    images_dir = os.path.join(out_dir, "images")
    os.makedirs(out_dir, exist_ok=True)

    title = (detail.get("title") or "无标题").strip()
    base_name = safe_filename(title)
    # 先定下 docx 文件名（含可能的 _N 去重后缀）；图片以其为前缀命名，避免共享 images/ 目录撞名
    docx_path = unique_path(out_dir, base_name, ".docx")
    img_prefix = os.path.splitext(os.path.basename(docx_path))[0]

    doc = Document()
    # 默认正文字体也设为宋体五号，兜底
    normal = doc.styles["Normal"]
    normal.font.name = FONT_NAME
    normal.font.size = Pt(FONT_SIZE_PT)
    normal.element.rPr.rFonts.set(qn("w:eastAsia"), FONT_NAME)

    _add_title(doc, title)

    author = _resolve_author(source, detail)
    meta_parts = []
    if author:
        meta_parts.append(f"作者：{author}")
    if detail.get("publish_date"):
        meta_parts.append(detail["publish_date"])
    _add_meta(doc, "  ".join(meta_parts))

    # 按原文顺序写入正文块
    img_seq = 0
    for block in detail.get("blocks", []):
        if block["type"] == "text":
            data = (block.get("data") or "").strip()
            if data:
                _add_body_text(doc, data)
        elif block["type"] == "image":
            src = block.get("src") or ""
            if not src:
                continue
            img_seq += 1
            local = _download_image(src, images_dir, img_seq, name_prefix=img_prefix)
            if local:
                _add_image(doc, local)
            else:
                _add_body_text(doc, "[图片加载失败]")

    doc.save(docx_path)
    return docx_path, images_dir
