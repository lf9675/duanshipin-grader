# -*- coding: utf-8 -*-
"""
video_export.py — 短视频批改导出（每生 PDF / 全班 Excel / 讲评 PPT）
════════════════════════════════════════════════════════════════
- 字体：fonts/NotoSansSC-Regular.ttf + Bold（与教师版同一套，随仓库分发）
- 分数展示决策（2026-07-20）：短视频评分表是刘老师自订规则，非 SEAB
  校准资产，因此 PDF/Excel 直接展示各维度分与总分（与作文"只给等级"不同）。
- PPT 定位是"讲评底稿"，可编辑 pptx；素材来自 REVIEW_AGG 聚合结果
  （人名已在提示词层匿名化）。
- 生成失败绝不影响页面 — 调用处必须 try/except。
"""

import io
import os
import zipfile
from datetime import datetime, timezone, timedelta

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.colors import HexColor
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import (BaseDocTemplate, Frame, PageTemplate,
                                Paragraph, Spacer, Table, TableStyle)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from openpyxl import Workbook
from openpyxl.styles import Font as XFont, PatternFill, Alignment

from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor

from video_rubric import dims, total_max, level_of
from video_engine import total_of, review_flags

EXPORT_VERSION = "video-1.0-20260720"

NAVY = HexColor("#1F4E79")
GOLD = HexColor("#f0c27f")
TEXT = HexColor("#2c2c2a")
MUTED = HexColor("#888888")
GRAY_BG = HexColor("#f5f4f0")
RED = HexColor("#c62828")
GREEN = HexColor("#2e7d32")

_FONTS_REGISTERED = False


def _register_fonts():
    global _FONTS_REGISTERED
    if _FONTS_REGISTERED:
        return
    base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
    reg = os.path.join(base, "NotoSansSC-Regular.ttf")
    bold = os.path.join(base, "NotoSansSC-Bold.ttf")
    if not os.path.exists(reg):
        raise RuntimeError(f"缺少中文字体文件: {reg}")
    pdfmetrics.registerFont(TTFont("NotoSC", reg))
    pdfmetrics.registerFont(TTFont("NotoSC-Bold",
                                   bold if os.path.exists(bold) else reg))
    _FONTS_REGISTERED = True


def _sg_today():
    return datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")


def _styles():
    def s(name, **kw):
        base = dict(fontName="NotoSC", fontSize=9.5, leading=16, textColor=TEXT)
        base.update(kw)
        return ParagraphStyle(name, **base)
    return {
        "title": s("title", fontName="NotoSC-Bold", fontSize=15, leading=22,
                   textColor=NAVY),
        "sub": s("sub", fontSize=8.5, leading=13, textColor=MUTED),
        "sec": s("sec", fontName="NotoSC-Bold", fontSize=11, leading=17,
                 textColor=NAVY, spaceBefore=8),
        "body": s("body"),
        "small": s("small", fontSize=8.6, leading=14),
        "quote": s("quote", fontSize=8.6, leading=14,
                   textColor=HexColor("#555555"), leftIndent=6),
    }


def _esc(t):
    return (str(t or "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


# ─────────────────────────────────────────────────────────────
# 每生 PDF
# ─────────────────────────────────────────────────────────────

def build_student_pdf(item, class_name, topic, rubric):
    """item: video_batch_items 行（dict 化）。返回 bytes。"""
    _register_fonts()
    st = _styles()
    ai = item.get("ai_result") or {}
    dims_r = ai.get("dimensions", {})
    fs = item.get("final_scores") or {}
    metrics = item.get("metrics") or {}
    ids = item.get("student_ids") or []
    total = total_of(fs)
    DIMS = dims(rubric)
    TM = total_max(rubric)

    buf = io.BytesIO()
    doc = BaseDocTemplate(buf, pagesize=A4,
                          leftMargin=16 * mm, rightMargin=16 * mm,
                          topMargin=14 * mm, bottomMargin=14 * mm)
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height)
    doc.addPageTemplates([PageTemplate(frames=[frame])])
    story = []

    story.append(Paragraph("短视频作业批改报告", st["title"]))
    story.append(Paragraph(
        f"班级 {_esc(class_name)} ｜ 学号 {_esc('、'.join(ids))} ｜ "
        f"题目 {_esc(topic)} ｜ {_sg_today()}", st["sub"]))
    story.append(Spacer(1, 5 * mm))

    # 总评 + 总分
    story.append(Paragraph(f"<b>总分：{total} / {TM}</b>　　"
                           f"{_esc(ai.get('one_line_comment', ''))}", st["body"]))
    story.append(Spacer(1, 3 * mm))

    # 四维度分数表
    rows = [["评分维度", "等级", "得分", "评语"]]
    for d in DIMS:
        k = d["key"]
        sc = fs.get(k, 0)
        lv = level_of(d, sc)
        comment = dims_r.get(k, {}).get("comment", "")
        if d.get("judge") == "text_speech":
            comment = (comment + " ※此维度AI仅供参考，以老师听后判断为准").strip()
        rows.append([f"{d['name']}（{d['max']}分）",
                     f"{lv['grade']} {lv['label']}", f"{sc}",
                     Paragraph(_esc(comment), st["small"])])
    tbl = Table(rows, colWidths=[36 * mm, 20 * mm, 13 * mm, 95 * mm])
    tbl.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), "NotoSC"),
        ("FONTNAME", (0, 0), (-1, 0), "NotoSC-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.8),
        ("BACKGROUND", (0, 0), (-1, 0), GRAY_BG),
        ("GRID", (0, 0), (-1, -1), 0.4, HexColor("#cccccc")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(tbl)

    # 问题与改法（why_and_how 底线）
    story.append(Paragraph("需要改进的地方（为什么 + 怎么改）", st["sec"]))
    n = 0
    for d in DIMS:
        for iss in dims_r.get(d["key"], {}).get("issues", []):
            n += 1
            story.append(Paragraph(
                f"<b>{n}. [{d['name']}] {_esc(iss.get('problem', ''))}</b>",
                st["body"]))
            story.append(Paragraph(f"为什么：{_esc(iss.get('why', ''))}", st["small"]))
            story.append(Paragraph(f"怎么改：{_esc(iss.get('how', ''))}", st["small"]))
            story.append(Spacer(1, 1.5 * mm))
    if n == 0:
        story.append(Paragraph("本次没有需要特别指出的问题，继续保持！", st["body"]))

    # 亮点
    praise = ai.get("praise_quotes", [])
    if praise:
        story.append(Paragraph("你的亮点句", st["sec"]))
        for p in praise:
            story.append(Paragraph(
                f"[{_esc(p.get('t', ''))}] “{_esc(p.get('quote', ''))}” —— "
                f"{_esc(p.get('why', ''))}", st["quote"]))

    # 口语指标
    story.append(Paragraph("口语小数据（供参考）", st["sec"]))
    story.append(Paragraph(
        f"语速 {metrics.get('speech_rate_cpm', '?')} 字/分钟 ｜ "
        f"明显停顿 {metrics.get('long_pause_count', '?')} 次 ｜ "
        f"嗯呃填充词 {metrics.get('filler_count', '?')} 次 ｜ "
        f"“然后”出现 {metrics.get('ranhou_count', '?')} 次", st["small"]))

    # 距上一档建议 + 老师批注
    adv = ai.get("next_level_advice", "")
    if adv:
        story.append(Paragraph("想再上一个档位，最该做的一件事", st["sec"]))
        story.append(Paragraph(_esc(adv), st["body"]))
    if item.get("teacher_comment"):
        story.append(Paragraph("老师的话", st["sec"]))
        story.append(Paragraph(_esc(item["teacher_comment"]), st["body"]))

    story.append(Spacer(1, 4 * mm))
    story.append(Paragraph(
        f"华文通·短视频批改 {EXPORT_VERSION} ｜ AI 批改 + 老师覆核", st["sub"]))
    doc.build(story)
    return buf.getvalue()


# ─────────────────────────────────────────────────────────────
# 全班 Excel
# ─────────────────────────────────────────────────────────────

def build_class_excel(items, class_name, topic, rubric):
    """一行一个学生（两人组各占一行）。返回 bytes。"""
    wb = Workbook()
    ws = wb.active
    ws.title = "短视频成绩总表"
    DIMS = dims(rubric)
    TM = total_max(rubric)
    header = (["学号", "组"]
              + [f"{d['name']}/{d['max']}" for d in DIMS]
              + [f"总分/{TM}", "主要问题", "突出优点",
                 "距上一档建议", "覆核提示", "教师最终评分（手写录入）"])
    ws.append([f"{class_name} 短视频作业 ｜ 题目：{topic} ｜ {_sg_today()}"])
    ws.append(header)
    yellow = PatternFill("solid", start_color="FFF3CD")
    for c in ws[2]:
        c.font = XFont(bold=True)
    for it in items:
        ai = it.get("ai_result") or {}
        fs = it.get("final_scores") or {}
        flags = review_flags(it, rubric)
        ids = it.get("student_ids") or []
        group = "_".join(ids) if len(ids) > 1 else ""
        for sid in ids:
            row = ([sid, group]
                   + [fs.get(d["key"], "") for d in DIMS]
                   + [total_of(fs),
                      ai.get("top_issue", ""), ai.get("top_strength", ""),
                      ai.get("next_level_advice", ""),
                      "；".join(flags), ""])
            ws.append(row)
            if flags:
                for c in ws[ws.max_row]:
                    c.fill = yellow
    widths = [8, 8] + [12] * len(DIMS) + [9, 22, 22, 30, 26, 20]
    from openpyxl.utils import get_column_letter
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    for row in ws.iter_rows(min_row=3):
        for c in row:
            c.alignment = Alignment(vertical="top", wrap_text=True)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ─────────────────────────────────────────────────────────────
# 讲评 PPT（讲评底稿，可编辑）
# ─────────────────────────────────────────────────────────────

_PPT_NAVY = RGBColor(0x1F, 0x4E, 0x79)
_PPT_RED = RGBColor(0xC6, 0x28, 0x28)
_PPT_GREEN = RGBColor(0x2E, 0x7D, 0x32)
_PPT_DARK = RGBColor(0x2C, 0x2C, 0x2A)


def _slide(prs):
    return prs.slides.add_slide(prs.slide_layouts[6])  # 空白版式


def _txt(slide, x, y, w, h, text, size, bold=False, color=_PPT_DARK,
         align=None):
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = box.text_frame
    tf.word_wrap = True
    first = True
    for line in str(text).split("\n"):
        p = tf.paragraphs[0] if first else tf.add_paragraph()
        first = False
        r = p.add_run()
        r.text = line
        r.font.size = Pt(size)
        r.font.bold = bold
        r.font.color.rgb = color
        if align:
            p.alignment = align
    return box


def build_review_ppt(agg, class_name, topic, grade_dist):
    """agg: REVIEW_AGG 聚合 JSON；grade_dist: {'A':n,...} 按总分折算的分布。
    字号按课堂投影标准放大（沿用作文 PPT 经验）。返回 bytes。"""
    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)

    # 封面
    s = _slide(prs)
    _txt(s, 1.2, 2.2, 11, 1.2, f"《{topic}》短视频作业讲评", 44, True, _PPT_NAVY)
    _txt(s, 1.2, 3.6, 11, 0.8, f"{class_name} ｜ {_sg_today()} ｜ AI 辅助整理 · 老师覆核",
         20, False, _PPT_DARK)

    # 总览
    s = _slide(prs)
    _txt(s, 0.8, 0.5, 11, 0.9, "全班总体表现", 36, True, _PPT_NAVY)
    dist_line = "　".join(f"{g}档 {n} 份" for g, n in grade_dist.items() if n)
    _txt(s, 0.8, 1.7, 11.5, 1.0, dist_line, 24, True, _PPT_DARK)
    _txt(s, 0.8, 2.8, 11.5, 3.5, agg.get("overview", ""), 24)

    # 共性问题：问题 → 例子 → 为什么错 → 怎么改（一题一页）
    for i, iss in enumerate(agg.get("common_issues", [])[:4], 1):
        s = _slide(prs)
        _txt(s, 0.8, 0.4, 11.5, 0.9,
             f"共性问题 {i}：{iss.get('title', '')}（{iss.get('count_hint', '')}）",
             32, True, _PPT_RED)
        y = 1.5
        for ex in iss.get("examples", [])[:2]:
            _txt(s, 1.0, y, 11.2, 0.85,
                 f"“{ex.get('quote', '')}”  [{ex.get('t', '')}]", 22,
                 color=RGBColor(0x55, 0x55, 0x55))
            y += 0.95
        _txt(s, 0.8, y + 0.1, 11.5, 1.4,
             f"为什么错：{iss.get('why', '')}", 24)
        _txt(s, 0.8, y + 1.7, 11.5, 1.8,
             f"怎么改：{iss.get('how', '')}", 24, True, _PPT_GREEN)

    # 佳句欣赏
    s = _slide(prs)
    _txt(s, 0.8, 0.4, 11.5, 0.9, "本班佳句欣赏", 36, True, _PPT_GREEN)
    y = 1.5
    for p in agg.get("praise", [])[:6]:
        _txt(s, 1.0, y, 11.3, 0.9,
             f"“{p.get('quote', '')}” —— {p.get('why', '')}", 22)
        y += 0.95

    # 下次拍摄前检查清单
    s = _slide(prs)
    _txt(s, 0.8, 0.4, 11.5, 0.9, "下次拍摄前，检查这五件事", 36, True, _PPT_NAVY)
    y = 1.6
    for i, c in enumerate(agg.get("checklist", [])[:5], 1):
        _txt(s, 1.0, y, 11.3, 0.9, f"☑ {c}", 26)
        y += 0.95

    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


# ─────────────────────────────────────────────────────────────
# 打包
# ─────────────────────────────────────────────────────────────

def grade_dist_of(items, rubric):
    """按总分占满分比例折四档（A≥80% B≥60% C≥40% D<40%），仅讲评总览用。"""
    tm = total_max(rubric) or 1
    dist = {"A": 0, "B": 0, "C": 0, "D": 0}
    for it in items:
        p = total_of(it.get("final_scores")) / tm
        g = "A" if p >= 0.8 else "B" if p >= 0.6 else "C" if p >= 0.4 else "D"
        dist[g] += 1
    return dist


def build_all_zip(items, class_name, topic, rubric, agg=None):
    """总打包：每生 PDF + 全班 Excel + 讲评 PPT（agg 为空则不含 PPT）。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for it in items:
            ids = it.get("student_ids") or []
            pdf = build_student_pdf(it, class_name, topic, rubric)
            for sid in ids:      # 两人组：每人一份相同报告（2026-07-20 决策）
                zf.writestr(f"学生报告/{sid}.pdf", pdf)
        zf.writestr(f"{class_name}_短视频成绩总表.xlsx",
                    build_class_excel(items, class_name, topic, rubric))
        if agg:
            zf.writestr(f"{class_name}_短视频讲评.pptx",
                        build_review_ppt(agg, class_name, topic,
                                         grade_dist_of(items, rubric)))
    return buf.getvalue()
