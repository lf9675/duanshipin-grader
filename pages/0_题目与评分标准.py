# -*- coding: utf-8 -*-
"""
pages/0_题目与评分标准.py — 作业模板管理（2026-07-20 常年化）
════════════════════════════════════════════════════════
换年级、换题目、换评分标准都在这里：
  粘贴评分表文字（或上传 docx）→ DeepSeek 解析成结构化标准 →
  老师逐项确认（判定方式可改）→ 保存为模板 → 主页建任务时选择。
原则：AI 解析结果必须过老师的眼才能保存——复核原则同样适用于规则本身。
"""

import json

import streamlit as st

import video_db as db
from video_rubric import (JUDGE_LABELS, dims, total_max, validate_rubric,
                          normalize_rubric)
from video_engine import parse_rubric_text

st.set_page_config(page_title="题目与评分标准", page_icon="📋", layout="wide")
if not st.session_state.get("authed"):
    st.warning("请先在主页输入平台口令")
    st.stop()

db.init_schema()
st.title("📋 题目与评分标准（作业模板）")
st.caption("每学期换题目/换标准：新建一个模板即可，历史批改记录不受影响。")

# ── 现有模板 ─────────────────────────────────────────────────
st.subheader("现有模板")
rubrics = db.list_rubrics()
for r in rubrics:
    rb = r["rubric"]
    if isinstance(rb, str):
        rb = json.loads(rb)
    dim_line = "、".join(f"{d['name']}{d['max']}分" for d in dims(rb))
    with st.expander(f"#{r['id']} {r['name']} ｜ 总分 {total_max(rb)} ｜ {dim_line}"):
        st.write(f"**题目要求默认文本**：{r['requirements'] or '（空）'}")
        for d in dims(rb):
            st.markdown(f"**{d['name']}（{d['max']}分）** — "
                        f"{JUDGE_LABELS.get(d.get('judge'), '?')}")
            for lv in d.get("levels", []):
                st.caption(f"{lv['grade']} {lv['label']}"
                           f"（{lv['lo']}–{lv['hi']}）：{lv['desc']}")
        pre = rb.get("precheck", {})
        if rb.get("stance"):
            st.write(f"**宽严指引**：{rb['stance']}")
        st.write(f"准入：时长≤{pre.get('max_duration_sec') or '不限'}秒 ｜ "
                 f"英文夹杂≤{round(pre.get('en_ratio_limit', 1.0)*100)}% ｜ "
                 f"出镜要求：{'是' if pre.get('face_required') else '否'} ｜ "
                 f"人工勾选：{'、'.join(pre.get('manual_items', [])) or '无'}")
        if len(rubrics) > 1 and st.button("🗑 删除此模板", key=f"del_{r['id']}"):
            db.delete_rubric(r["id"])
            st.rerun()

# ── 新建模板 ─────────────────────────────────────────────────
st.divider()
st.subheader("新建模板")
st.markdown("把新评分表**粘贴到下面**（从 Word 直接复制即可，表格格式乱没关系），"
            "或上传 docx 文件。解析需要 DeepSeek Key（主页侧栏填）。")

up_docx = st.file_uploader("上传评分表 docx（可选）", type=["docx"])
raw_default = ""
if up_docx is not None:
    try:
        from docx import Document
        doc = Document(up_docx)
        parts = [p.text for p in doc.paragraphs if p.text.strip()]
        for t in doc.tables:
            for row in t.rows:
                parts.append(" | ".join(c.text.strip() for c in row.cells))
        raw_default = "\n".join(parts)
        st.success(f"已读取 docx，共 {len(raw_default)} 字，"
                   f"内容已填入下方文本框，可先检查再解析。")
    except Exception as e:
        st.error(f"docx 读取失败：{e}，请改用粘贴方式。")

raw = st.text_area("评分表原文", value=raw_default, height=260,
                   key="rubric_raw",
                   placeholder="例：\n一、内容讲述（25分）\n优秀 21-25 步骤清晰完整…\n良好 16-20 …")
req_text = st.text_area("题目要求（会写进批改提示词，让 AI 知道作业要求什么）",
                        height=100,
                        placeholder="例：拍一个不超过3分钟的短视频，用华语介绍一本书…")

if st.button("🔍 AI 解析评分表", disabled=not raw.strip()):
    if not st.session_state.get("deepseek_key"):
        st.error("请先在主页侧栏填入 DeepSeek Key")
    else:
        with st.spinner("正在解析…"):
            try:
                rubric, errs = parse_rubric_text(
                    st.session_state.deepseek_key, raw)
                st.session_state.parsed_rubric = rubric
                st.session_state.parsed_errs = errs
            except Exception as e:
                st.error(f"解析失败：{e}")

# ── 解析结果确认 ─────────────────────────────────────────────
if st.session_state.get("parsed_rubric"):
    rubric = st.session_state.parsed_rubric
    st.divider()
    st.subheader("解析结果——请逐项核对后保存")
    for e in st.session_state.get("parsed_errs", []):
        st.error(f"体检未过：{e}（请修改上方原文重新解析）")

    name = st.text_input("模板名", value=rubric.get("name", ""))
    st.markdown(f"**总分 {total_max(rubric)} 分**，各维度如下。"
                f"请特别确认每个维度的**判定方式**（AI 预判，可改）：")
    judge_opts = list(JUDGE_LABELS.keys())
    for i, d in enumerate(dims(rubric)):
        c1, c2 = st.columns([3, 2])
        with c1:
            st.markdown(f"**{d['name']}（{d['max']}分）**"
                        + ("（重点）" if d.get("star") else ""))
            for lv in d.get("levels", []):
                st.caption(f"{lv['grade']} {lv['label']}"
                           f"（{lv['lo']}–{lv['hi']}）：{lv['desc']}")
        with c2:
            d["judge"] = st.selectbox(
                "判定方式", judge_opts,
                index=judge_opts.index(d.get("judge", "text")),
                format_func=lambda k: JUDGE_LABELS[k],
                key=f"judge_{i}")

    pre = rubric.setdefault("precheck", {})
    st.markdown("**准入检查配置**")
    p1, p2, p3 = st.columns(3)
    with p1:
        pre["max_duration_sec"] = st.number_input(
            "时长上限（秒，0=不限）", min_value=0, value=int(pre.get("max_duration_sec", 0)))
    with p2:
        pre["en_ratio_limit"] = st.number_input(
            "英文夹杂上限（%，100=不限）", min_value=0, max_value=100,
            value=int(round(pre.get("en_ratio_limit", 1.0) * 100))) / 100.0
    with p3:
        pre["face_required"] = st.checkbox(
            "要求面容出镜", value=bool(pre.get("face_required")))
    rubric["stance"] = st.text_area(
        "宽严指引（写给 AI 的评分基调，如：中一学生从宽、拍摄清楚即3分起步）",
        value=rubric.get("stance", ""), height=110)
    manual_str = st.text_input(
        "人工勾选项（老师复核时勾，逗号分隔）",
        value="，".join(pre.get("manual_items", [])))
    pre["manual_items"] = [s.strip() for s in
                           manual_str.replace(",", "，").split("，")
                           if s.strip()]

    if st.button("💾 保存为模板", type="primary", disabled=not name.strip()):
        normalize_rubric(rubric)
        rubric["name"] = name.strip()
        errs = validate_rubric(rubric)
        if errs:
            for e in errs:
                st.error(e)
        else:
            db.create_rubric(name.strip(), rubric, req_text.strip())
            st.session_state.parsed_rubric = None
            st.success(f"模板「{name.strip()}」已保存。到主页建任务时即可选择。")
            st.rerun()
