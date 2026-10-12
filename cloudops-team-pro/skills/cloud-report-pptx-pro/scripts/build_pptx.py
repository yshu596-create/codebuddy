#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_pptx.py — 云运维报告 PPT 生成器

把 cloud-billing-pro / cloud-auditor-pro / cloud-inspector-pro / cloud-cost-optimizer-pro
四类专家产出的概览 + HTML 数据，渲染为 VCIC 模板风格的 .pptx。

用法：
  /usr/bin/python3 build_pptx.py --input <input.json> --output <out.pptx>

依赖：python-pptx（必须用 /usr/bin/python3，managed Python 的 lxml 有签名冲突）
"""

import argparse
import copy
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

# ── shared team i18n dictionary (single source of truth) ──
# Anchor by file path (parents[2]: scripts -> skill -> skills root) so import is
# robust regardless of the cwd this skill is invoked from. Used only as an
# enum fallback in en mode; business/tag names are never translated.
_LIB = Path(__file__).resolve().parents[2] / "cloudops-accounts-pro" / "scripts" / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
try:
    from i18n import tr as _tr, set_llm_hook as _set_llm_hook, create_llm_hook as _create_llm_hook, _flush_llm_cache as _flush_llm_cache
except Exception:
    def _tr(s, lang="zh"):
        return s
    _set_llm_hook = lambda hook: None
    _create_llm_hook = lambda: None
    _flush_llm_cache = lambda: None

try:
    from pptx import Presentation
    from pptx.util import Inches, Pt, Emu
    from pptx.dml.color import RGBColor
    from pptx.enum.text import PP_ALIGN
    from pptx.enum.shapes import MSO_SHAPE
    from pptx.oxml.ns import qn
except ImportError:
    print("❌ 缺少 python-pptx。请先安装：/usr/bin/python3 -m pip install --user python-pptx", file=sys.stderr)
    sys.exit(2)

# ============== 常量 ==============

SCRIPT_DIR = Path(__file__).parent.resolve()
TEMPLATE_PATH = SCRIPT_DIR.parent / "templates" / "cloud-report-template.pptx"

# 模板色板
COLOR_DEEP_SPACE_BLUE = RGBColor(0x00, 0x1E, 0x50)
COLOR_VIVID_GREEN = RGBColor(0x00, 0xB0, 0xB9)
COLOR_WHITE = RGBColor(0xFF, 0xFF, 0xFF)
COLOR_LIGHT_GREY = RGBColor(0xDF, 0xE4, 0xE8)
COLOR_DARK_GREY = RGBColor(0x55, 0x55, 0x55)
COLOR_RED = RGBColor(0xD9, 0x37, 0x3E)
COLOR_ORANGE = RGBColor(0xE8, 0x8E, 0x32)
COLOR_BLUE = RGBColor(0x1E, 0x6F, 0xB0)
COLOR_GREY = RGBColor(0x88, 0x88, 0x88)

PRIORITY_COLOR = {
    "P0": COLOR_RED,
    "P1": COLOR_ORANGE,
    "P2": COLOR_BLUE,
    "P3": COLOR_GREY,
}

# 报告类型映射（按 lang 取词；key 同时用于 report_type 合法性校验）
REPORT_TYPE_I18N = {
    "zh": {
        "billing": "云账单分析报告",
        "audit": "云审计风险报告",
        "inspect": "云安全巡检报告",
        "costopt": "云降本优化报告",
    },
    "en": {
        "billing": "Cloud Billing Analysis Report",
        "audit": "Cloud Audit Risk Report",
        "inspect": "Cloud Security Inspection Report",
        "costopt": "Cloud Cost Optimization Report",
    },
}

# i18n 固定骨架文案字典 + 取词函数（团队统一模式）
I18N = {
    "zh": {
        "cover_default": "云运维报告",
        "agenda_title": "目录",
        "summary_title": "AIOps 智能概览",
        "summary_sub": "Executive Summary",
        "highlights_title": "核心亮点",
        "highlights_sub": "Key Highlights",
        "details": "明细",
        "actions_title": "行动建议",
        "actions_sub": "Action Items",
        "chart_metric_label": "指标",
        "chart_metric_default": "数值",
        "chart_note": "图表数据来源于已生成 HTML 看板，数值经表格化兜底渲染",
    },
    "en": {
        "cover_default": "Cloud Ops Report",
        "agenda_title": "Agenda",
        "summary_title": "AIOps Overview",
        "summary_sub": "Executive Summary",
        "highlights_title": "Key Highlights",
        "highlights_sub": "Key Highlights",
        "details": "Details",
        "actions_title": "Action Items",
        "actions_sub": "Action Items",
        "chart_metric_label": "Metric",
        "chart_metric_default": "Value",
        "chart_note": "Chart data sourced from the generated HTML dashboard; values rendered via tabular fallback",
    },
}


def L(lang, key):
    return I18N.get(lang, I18N["zh"]).get(key, I18N["zh"].get(key, key))


def report_type_label(lang, report_type):
    """按 lang 取报告类型名；缺失时回落到封面默认名"""
    table = REPORT_TYPE_I18N.get(lang, REPORT_TYPE_I18N["zh"])
    return table.get(report_type, L(lang, "cover_default"))

# layout 名称（来自模板）
L_COVER = "Titelfolie"
L_AGENDA = "5x Agenda Deep Space Blue"
L_SECTION = "Abschnitts-\nüberschrift"  # 模板里这个布局名带换行
L_3BOX = "Title and 3 Text Boxes, Deep Space Blue"
L_TITLE_CONTENT = "Title and Content Deep Space Blue"
L_TITLE_ONLY = "Nur Titel"


# ============== 工具函数 ==============

def find_layout(prs, name):
    """按 name 找 layout；同名不存在时返回第一个仅标题型布局兜底"""
    for sl in prs.slide_layouts:
        if sl.name == name:
            return sl
    # 兜底：找包含关键词
    for sl in prs.slide_layouts:
        if name.replace("\n", "") in sl.name.replace("\n", ""):
            return sl
    print(f"⚠️ layout '{name}' 未找到，使用首个布局兜底", file=sys.stderr)
    return prs.slide_layouts[0]


def set_placeholder_text(slide, idx, text, *, bold=None, size=None, color=None, align=None):
    """按 idx 设置 placeholder 文本，保留模板默认样式；只在显式传参时覆盖"""
    if text is None:
        return False
    for ph in slide.placeholders:
        if ph.placeholder_format.idx == idx:
            tf = ph.text_frame
            tf.clear()
            # 多行支持
            lines = str(text).split("\n")
            for i, line in enumerate(lines):
                p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
                run = p.add_run()
                run.text = line
                if bold is not None:
                    run.font.bold = bold
                if size is not None:
                    run.font.size = Pt(size)
                if color is not None:
                    run.font.color.rgb = color
                if align is not None:
                    p.alignment = align
            return True
    return False


def remove_placeholder(slide, idx):
    """删除指定 placeholder，避免示例文本残留"""
    for ph in list(slide.placeholders):
        if ph.placeholder_format.idx == idx:
            sp = ph._element
            sp.getparent().remove(sp)
            return True
    return False


def md_clean(text):
    """简单清洗 markdown 标记，保留纯文本（PPT 不渲染 md）"""
    if not text:
        return ""
    s = str(text)
    s = re.sub(r"^#+\s*", "", s, flags=re.MULTILINE)
    s = re.sub(r"\*\*(.+?)\*\*", r"\1", s)
    s = re.sub(r"\*(.+?)\*", r"\1", s)
    s = re.sub(r"`([^`]+)`", r"\1", s)
    s = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", s)
    s = re.sub(r"^[\-\*]\s+", "• ", s, flags=re.MULTILINE)
    s = re.sub(r"^\d+\.\s+", "", s, flags=re.MULTILINE)
    return s.strip()


def truncate(text, n):
    s = str(text or "")
    if len(s) <= n:
        return s
    return s[: n - 1] + "…"


# ============== 各类 Slide 生成 ==============

def add_cover(prs, title, subtitle, report_type, lang="zh"):
    """封面页"""
    layout = find_layout(prs, L_COVER)
    slide = prs.slides.add_slide(layout)
    set_placeholder_text(slide, 0, title or report_type_label(lang, report_type))
    if subtitle:
        set_placeholder_text(slide, 1, subtitle)
    # idx=18 是 Classification，保留默认 INTERNAL
    return slide


def add_agenda(prs, items, lang="zh"):
    """目录页（5 项）"""
    if not items:
        return None
    layout = find_layout(prs, L_AGENDA)
    slide = prs.slides.add_slide(layout)
    set_placeholder_text(slide, 0, L(lang, "agenda_title"))
    item_idx = [(13, 14), (25, 26), (27, 28), (29, 30), (31, 32)]
    for i, (num_idx, item_idx_) in enumerate(item_idx):
        if i < len(items):
            set_placeholder_text(slide, num_idx, str(i + 1))
            set_placeholder_text(slide, item_idx_, truncate(items[i], 40))
        else:
            remove_placeholder(slide, num_idx)
            remove_placeholder(slide, item_idx_)
    return slide


def add_section_divider(prs, number, title, subheader="", lang="zh"):
    """章节封面"""
    layout = find_layout(prs, L_SECTION)
    slide = prs.slides.add_slide(layout)
    set_placeholder_text(slide, 36, str(number))
    set_placeholder_text(slide, 0, title)
    if subheader:
        set_placeholder_text(slide, 1, subheader)
    return slide


def add_summary_page(prs, summary_md, lang="zh"):
    """AIOps 智能概览页（标题+正文）"""
    layout = find_layout(prs, L_TITLE_CONTENT)
    slide = prs.slides.add_slide(layout)
    set_placeholder_text(slide, 0, L(lang, "summary_title"))
    set_placeholder_text(slide, 14, L(lang, "summary_sub"))
    text = md_clean(summary_md)
    # 分段填入 content placeholder（idx=1）
    for ph in slide.placeholders:
        if ph.placeholder_format.idx == 1:
            tf = ph.text_frame
            tf.clear()
            tf.word_wrap = True
            paragraphs = [p.strip() for p in text.split("\n") if p.strip()]
            for i, line in enumerate(paragraphs):
                p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
                run = p.add_run()
                run.text = line
                run.font.size = Pt(12)
                run.font.color.rgb = COLOR_WHITE
                # 设为顶级段落
                p.level = 0
            break
    return slide


def add_highlights_page(prs, highlights, lang="zh"):
    """三栏要点页（取前 3 条 highlights）"""
    if not highlights:
        return None
    layout = find_layout(prs, L_3BOX)
    slide = prs.slides.add_slide(layout)
    set_placeholder_text(slide, 0, L(lang, "highlights_title"))
    set_placeholder_text(slide, 14, L(lang, "highlights_sub"))

    box_idx = [39, 40, 41]
    for i, idx in enumerate(box_idx):
        if i < len(highlights):
            h = highlights[i]
            title = h.get("title", "")
            value = h.get("value", "")
            delta = h.get("delta", "")
            note = h.get("note", "")
            for ph in slide.placeholders:
                if ph.placeholder_format.idx == idx:
                    tf = ph.text_frame
                    tf.clear()
                    tf.word_wrap = True
                    # 第 1 段：value（大字）
                    p1 = tf.paragraphs[0]
                    r1 = p1.add_run()
                    r1.text = str(value) or "-"
                    r1.font.size = Pt(28)
                    r1.font.bold = True
                    r1.font.color.rgb = COLOR_WHITE
                    # 第 2 段：title + delta
                    p2 = tf.add_paragraph()
                    r2a = p2.add_run()
                    r2a.text = title + ("  " if delta else "")
                    r2a.font.size = Pt(13)
                    r2a.font.color.rgb = COLOR_VIVID_GREEN
                    if delta:
                        r2b = p2.add_run()
                        r2b.text = delta
                        r2b.font.size = Pt(13)
                        r2b.font.color.rgb = COLOR_VIVID_GREEN
                        r2b.font.bold = True
                    # 第 3 段：note
                    if note:
                        p3 = tf.add_paragraph()
                        r3 = p3.add_run()
                        r3.text = truncate(note, 80)
                        r3.font.size = Pt(11)
                        r3.font.color.rgb = COLOR_LIGHT_GREY
                    break
        else:
            remove_placeholder(slide, idx)
    return slide


def add_table_section(prs, heading, subheading, table_data, page_idx=None, total_pages=None, lang="zh"):
    """表格页"""
    if not table_data or not table_data.get("rows"):
        return None
    layout = find_layout(prs, L_TITLE_CONTENT)
    slide = prs.slides.add_slide(layout)
    title_text = heading
    if total_pages and total_pages > 1:
        title_text = f"{heading} ({page_idx}/{total_pages})"
    set_placeholder_text(slide, 0, title_text)
    if subheading:
        set_placeholder_text(slide, 14, truncate(subheading, 60))
    else:
        remove_placeholder(slide, 14)

    # 删掉 content placeholder，自己加表格
    remove_placeholder(slide, 1)

    headers = table_data.get("headers", [])
    rows = table_data.get("rows", [])
    if not headers or not rows:
        return slide

    # 限制 6 列
    headers = headers[:6]
    rows = [r[:6] for r in rows]

    n_rows = len(rows) + 1  # 含表头
    n_cols = len(headers)
    left = Inches(0.6)
    top = Inches(1.7)
    width = Inches(12.1)
    height = Inches(0.4) + Inches(0.35) * len(rows)
    if height > Inches(5.2):
        height = Inches(5.2)

    table_shape = slide.shapes.add_table(n_rows, n_cols, left, top, width, height)
    table = table_shape.table

    # 表头（en 模式下对列名做枚举兜底翻译；业务数据单元格不翻，避免误翻客户/owner 名）
    for c, h in enumerate(headers):
        cell = table.cell(0, c)
        cell.fill.solid()
        cell.fill.fore_color.rgb = COLOR_VIVID_GREEN
        cell.text = ""
        tf = cell.text_frame
        p = tf.paragraphs[0]
        run = p.add_run()
        run.text = _tr(str(h), lang)
        run.font.bold = True
        run.font.size = Pt(12)
        run.font.color.rgb = COLOR_WHITE

    # 行
    for r, row in enumerate(rows, start=1):
        for c in range(n_cols):
            val = row[c] if c < len(row) else ""
            cell = table.cell(r, c)
            cell.fill.solid()
            # 斑马纹
            cell.fill.fore_color.rgb = RGBColor(0xF5, 0xF7, 0xFA) if r % 2 == 1 else COLOR_WHITE
            cell.text = ""
            tf = cell.text_frame
            p = tf.paragraphs[0]
            run = p.add_run()
            run.text = truncate(str(val), 50)
            run.font.size = Pt(11)
            run.font.color.rgb = COLOR_DEEP_SPACE_BLUE

    return slide


def add_chart_section(prs, heading, subheading, chart_data, lang="zh"):
    """图表页（用形状条 + 数值兜底）"""
    if not chart_data or not chart_data.get("series"):
        return None
    layout = find_layout(prs, L_TITLE_ONLY)
    slide = prs.slides.add_slide(layout)
    set_placeholder_text(slide, 0, heading)
    if subheading:
        set_placeholder_text(slide, 14, truncate(subheading, 60))
    else:
        remove_placeholder(slide, 14)

    chart_type = chart_data.get("type", "bar")
    categories = chart_data.get("categories", [])
    series_list = chart_data.get("series", [])
    if not categories or not series_list:
        return slide

    # 只取第一条 series（兜底实现）
    series = series_list[0]
    values = series.get("values", [])
    if not values:
        return slide

    max_val = max(values) if values else 1.0
    if max_val <= 0:
        max_val = 1.0

    # 画布范围（用 float inches 计算，最后再转 Inches）
    plot_left_in = 1.0
    plot_top_in = 1.8
    plot_width_in = 11.3
    plot_height_in = 5.0

    n = len(categories)
    if n == 0:
        return slide
    bar_h_in = min(0.5, plot_height_in / max(n, 1) * 0.8)
    label_col_w_in = 2.0
    bar_col_left_in = plot_left_in + label_col_w_in
    bar_col_w_in = plot_width_in - label_col_w_in - 1.5

    for i, cat in enumerate(categories):
        v = values[i] if i < len(values) else 0
        y_in = plot_top_in + plot_height_in / n * i + 0.1
        # 类目标签
        tb = slide.shapes.add_textbox(Inches(plot_left_in), Inches(y_in), Inches(label_col_w_in), Inches(bar_h_in))
        tf = tb.text_frame
        tf.margin_left = 0
        p = tf.paragraphs[0]
        p.alignment = PP_ALIGN.RIGHT
        run = p.add_run()
        run.text = truncate(str(cat), 18)
        run.font.size = Pt(11)
        run.font.color.rgb = COLOR_DEEP_SPACE_BLUE
        run.font.bold = True

        # 条
        ratio = max(0.02, min(1.0, float(v) / max_val))
        bw_in = bar_col_w_in * ratio
        bar = slide.shapes.add_shape(
            MSO_SHAPE.RECTANGLE,
            Inches(bar_col_left_in), Inches(y_in + 0.05),
            Inches(bw_in), Inches(max(0.05, bar_h_in - 0.1))
        )
        bar.fill.solid()
        bar.fill.fore_color.rgb = COLOR_VIVID_GREEN if i % 2 == 0 else COLOR_DEEP_SPACE_BLUE
        bar.line.fill.background()
        try:
            bar.shadow.inherit = False
        except Exception:
            pass

        # 数值标签
        val_tb = slide.shapes.add_textbox(
            Inches(bar_col_left_in + bw_in + 0.05), Inches(y_in),
            Inches(1.4), Inches(bar_h_in)
        )
        vtf = val_tb.text_frame
        vtf.margin_left = 0
        vp = vtf.paragraphs[0]
        vrun = vp.add_run()
        vrun.text = format_value(v, lang)
        vrun.font.size = Pt(11)
        vrun.font.color.rgb = COLOR_DARK_GREY

    # 坐标系标识
    note_tb = slide.shapes.add_textbox(
        Inches(plot_left_in),
        Inches(plot_top_in + plot_height_in + 0.1),
        Inches(plot_width_in), Inches(0.3)
    )
    nrun = note_tb.text_frame.paragraphs[0].add_run()
    metric_name = series.get('name') or L(lang, "chart_metric_default")
    nrun.text = f"{L(lang, 'chart_metric_label')}: {metric_name} - {L(lang, 'chart_note')}"
    nrun.font.size = Pt(9)
    nrun.font.color.rgb = COLOR_GREY
    nrun.font.italic = True

    return slide


def format_value(v, lang="zh"):
    """智能格式化数值（按 lang 选用中文万/亿或英文 K/M/B 量级）"""
    try:
        f = float(v)
        if lang == "en":
            if abs(f) >= 1000000000:
                return f"{f/1000000000:.2f}B"
            if abs(f) >= 1000000:
                return f"{f/1000000:.2f}M"
            if abs(f) >= 1000:
                return f"{f/1000:.2f}K"
        else:
            if abs(f) >= 100000000:
                return f"{f/100000000:.2f}亿"
            if abs(f) >= 10000:
                return f"{f/10000:.2f}万"
        if isinstance(v, int) or f == int(f):
            return f"{int(f):,}"
        return f"{f:,.2f}"
    except (ValueError, TypeError):
        return str(v)


def add_actions_page(prs, actions, lang="zh"):
    """行动建议页"""
    if not actions:
        return None
    layout = find_layout(prs, L_TITLE_CONTENT)
    slide = prs.slides.add_slide(layout)
    set_placeholder_text(slide, 0, L(lang, "actions_title"))
    set_placeholder_text(slide, 14, L(lang, "actions_sub"))

    for ph in slide.placeholders:
        if ph.placeholder_format.idx == 1:
            tf = ph.text_frame
            tf.clear()
            tf.word_wrap = True
            # 排序：P0 > P1 > P2 > P3
            pri_order = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
            sorted_actions = sorted(actions, key=lambda a: pri_order.get(a.get("priority", "P3"), 99))
            for i, a in enumerate(sorted_actions[:8]):
                p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
                pri = a.get("priority", "P2")
                color = PRIORITY_COLOR.get(pri, COLOR_BLUE)
                # 优先级 chip
                r1 = p.add_run()
                r1.text = f"[{pri}] "
                r1.font.bold = True
                r1.font.size = Pt(13)
                r1.font.color.rgb = color
                # 内容
                r2 = p.add_run()
                r2.text = truncate(a.get("text", ""), 200)
                r2.font.size = Pt(13)
                r2.font.color.rgb = COLOR_WHITE
                p.level = 0
            break
    return slide


# ============== 主流程 ==============

def _purge_template_slides(prs):
    """彻底删除模板内置示例幻灯片：1) sldIdLst 移除 2) drop part + relationship"""
    sldIdLst = prs.slides._sldIdLst
    slide_ids = list(sldIdLst)
    pres_part = prs.part
    for sldId in slide_ids:
        rId = sldId.get(qn("r:id"))
        # drop relationship + part
        if rId in pres_part.rels:
            rel = pres_part.rels[rId]
            target = rel.target_part
            try:
                pres_part.drop_rel(rId)
            except Exception:
                pass
            # 从 package 里移除 part
            try:
                prs.part.package._drop_part(target.partname)
            except (AttributeError, KeyError):
                pass
        sldIdLst.remove(sldId)


def chunk_table(table_data, max_rows=12):
    """长表分页"""
    if not table_data or not table_data.get("rows"):
        return [table_data]
    rows = table_data["rows"]
    if len(rows) <= max_rows:
        return [table_data]
    chunks = []
    for i in range(0, len(rows), max_rows):
        chunks.append({
            "headers": table_data["headers"],
            "rows": rows[i: i + max_rows],
        })
    return chunks


def build(input_data, output_path, lang="zh"):
    """主入口"""
    if not TEMPLATE_PATH.exists():
        raise FileNotFoundError(f"模板不存在：{TEMPLATE_PATH}")

    # 必填校验
    for k in ("report_type", "title", "summary_md"):
        if not input_data.get(k):
            raise ValueError(f"缺少必填字段：{k}")

    rt = input_data["report_type"]
    if rt not in REPORT_TYPE_I18N["zh"]:
        raise ValueError(f"未知 report_type：{rt}")

    prs = Presentation(str(TEMPLATE_PATH))

    # 彻底删除模板里 11 张示例幻灯片（连同 part 和 rel）
    # 否则 zipfile 会报 Duplicate name 警告，且打开时残留示例
    _purge_template_slides(prs)

    # 1) 封面
    add_cover(prs, input_data["title"], input_data.get("subtitle"), rt, lang=lang)

    # 2) 目录（基于 sections + 固定项）
    agenda_items = [L(lang, "summary_title")]
    if input_data.get("highlights"):
        agenda_items.append(L(lang, "highlights_title"))
    for s in input_data.get("sections", []):
        agenda_items.append(s.get("heading", L(lang, "details")))
    if input_data.get("actions"):
        agenda_items.append(L(lang, "actions_title"))
    add_agenda(prs, agenda_items[:5], lang=lang)

    # 3) AIOps 概览
    add_summary_page(prs, input_data["summary_md"], lang=lang)

    # 4) 核心亮点（如有）
    if input_data.get("highlights"):
        add_highlights_page(prs, input_data["highlights"], lang=lang)

    # 5) 各章节
    sec_no = 1
    for section in input_data.get("sections", []):
        heading = section.get("heading", L(lang, "details"))
        subheading = section.get("subheading", "")

        # 章节封面（>1 个 section 才加）
        if len(input_data.get("sections", [])) > 1:
            add_section_divider(prs, sec_no, heading, subheading, lang=lang)
            sec_no += 1

        # 优先 chart，再 table
        if section.get("chart"):
            add_chart_section(prs, heading, subheading, section["chart"], lang=lang)
        if section.get("table"):
            chunks = chunk_table(section["table"], max_rows=12)
            for i, ch in enumerate(chunks, start=1):
                add_table_section(prs, heading, subheading, ch,
                                  page_idx=i, total_pages=len(chunks), lang=lang)

    # 6) 行动建议
    if input_data.get("actions"):
        add_actions_page(prs, input_data["actions"], lang=lang)

    # 落盘
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(output_path))
    return output_path


def main():
    parser = argparse.ArgumentParser(description="云运维报告 PPT 生成器")
    parser.add_argument("--input", required=True, help="输入 JSON 路径")
    parser.add_argument("--output", required=True, help="输出 .pptx 路径")
    parser.add_argument("--lang", choices=["zh", "en"], default="zh", help="report skeleton language")
    args = parser.parse_args()

    # 在任何 _tr() 调用之前注册 LLM 兜底钩子
    if args.lang == "en":
        _set_llm_hook(_create_llm_hook())

    with open(args.input, "r", encoding="utf-8") as f:
        data = json.load(f)

    out = build(data, args.output, lang=args.lang)
    print(f"✅ PPT 已生成：{out}")
    print(f"   报告类型: {data.get('report_type')}")
    print(f"   章节数:   {len(data.get('sections', []))}")
    print(f"   文件大小: {out.stat().st_size / 1024:.1f} KB")

    # 持久化 LLM 翻译缓存
    if args.lang == "en":
        _flush_llm_cache()


if __name__ == "__main__":
    main()
