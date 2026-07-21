# -*- coding: utf-8 -*-
"""
pages/1_覆核.py — 覆核总表（P0 环节）
════════════════════════════════════
原则：宁可老师多看一眼，不能带错发出去。
- 总表一行一份，异常自动标黄（准入异常/口语矛盾/极端总分/缺评/失败）
- 点开单份：改各维度分、写老师批注、勾人工准入项、可打回
- 批量确认带弹窗拦截：有标黄未处理时要求二次确认
- 口语可抽听：会话目录里有 audio.mp3 时提供播放器（不入库）
"""

import json
from pathlib import Path

import streamlit as st

import video_db as db
from video_rubric import dims, level_of, JUDGE_LABELS
from video_engine import review_flags, total_of

st.set_page_config(page_title="覆核 · 短视频批改", page_icon="✅",
                   layout="wide")
if not st.session_state.get("authed"):
    st.warning("请先在主页输入平台口令")
    st.stop()

job_id = st.session_state.get("job_id")
if not job_id:
    st.info("请先在主页选择或创建批改任务")
    st.stop()

job = db.get_job(job_id)
RUBRIC = job["rubric"] if not isinstance(job["rubric"], str) else json.loads(job["rubric"])
DIMS = dims(RUBRIC)
PRE_CFG = RUBRIC.get("precheck", {})
st.title(f"✅ 覆核 — {job['class_name']}《{job['topic']}》")
st.caption(f"评分标准：{RUBRIC.get('name','')}")

items = [dict(i) for i in db.list_items(job_id)]
for it in items:
    for f in ("student_ids", "transcript", "metrics", "precheck",
              "ai_result", "final_scores"):
        if isinstance(it.get(f), str):
            try:
                it[f] = json.loads(it[f])
            except Exception:
                pass

flagged = []
st.subheader("覆核总表")
hdr = st.columns([1.2, 1, 1, 3.2, 3.2, 1.4])
for c, t in zip(hdr, ["学号", "总分", "状态", "主要问题", "覆核提示", ""]):
    c.markdown(f"**{t}**")

for it in items:
    flags = review_flags(it, RUBRIC)
    if flags and it["status"] in ("graded", "failed"):
        flagged.append(it["item_key"])
    ai = it.get("ai_result") or {}
    cols = st.columns([1.2, 1, 1, 3.2, 3.2, 1.4])
    mark = "🟡 " if flags else ""
    status_zh = {"pending": "待批", "graded": "已批待确认",
                 "confirmed": "已确认", "rejected": "已打回",
                 "failed": "失败"}.get(it["status"], it["status"])
    cols[0].write(f"{mark}{it['item_key']}")
    cols[1].write(str(total_of(it.get("final_scores"))))
    cols[2].write(status_zh)
    cols[3].write(ai.get("top_issue", "—"))
    cols[4].write("；".join(flags) if flags else "—")
    if cols[5].button("查看/微调", key=f"open_{it['id']}"):
        st.session_state.open_item = it["id"]

# ── 单份详情 ─────────────────────────────────────────────────
open_id = st.session_state.get("open_item")
if open_id:
    it = next((x for x in items if x["id"] == open_id), None)
    if it:
        st.divider()
        st.subheader(f"学号 {it['item_key']} 详情")
        ai = it.get("ai_result") or {}
        dims = ai.get("dimensions", {})
        fs = dict(it.get("final_scores") or {})
        metrics = it.get("metrics") or {}

        # 口语抽听（媒体在会话目录才有；断线后需重传批改包）
        mp3 = (Path(st.session_state.get("media_root", "/nonexistent"))
               / str(job_id) / it["item_key"] / "audio.mp3")
        if mp3.exists():
            st.audio(str(mp3))
        else:
            st.caption("（音频不在本会话中——如需抽听请在主页重传批改包）")

        st.write(f"**一句话总评**：{ai.get('one_line_comment', '—')}")
        st.write(f"**口语指标**：语速 {metrics.get('speech_rate_cpm', '?')} 字/分 ｜ "
                 f"停顿 {metrics.get('long_pause_count', '?')} 次 ｜ "
                 f"填充词 {metrics.get('filler_count', '?')} 次 ｜ "
                 f"“然后” {metrics.get('ranhou_count', '?')} 次")

        new_scores = {}
        sc_cols = st.columns(max(len(DIMS), 1))
        for i, d in enumerate(DIMS):
            k = d["key"]
            info = dims.get(k, {})
            with sc_cols[i]:
                new_scores[k] = st.number_input(
                    f"{d['name']} /{d['max']}",
                    min_value=0, max_value=d["max"],
                    value=int(fs.get(k, 0)), key=f"sc_{it['id']}_{k}")
                lv = level_of(d, new_scores[k])
                conf = info.get("confidence", "")
                tail = " ⚠AI仅参考" if conf in ("low", "none") else ""
                st.caption(f"{lv['grade']} {lv['label']}{tail}")
                if info.get("comment"):
                    st.caption(info["comment"])
                if info.get("note"):
                    st.caption(f"📌 {info['note']}")

        with st.expander("逐维度证据与问题（AI 原始输出）"):
            for d in DIMS:
                info = dims.get(d["key"], {})
                if not info:
                    continue
                st.markdown(f"**{d['name']}**")
                for ev in info.get("evidence", []):
                    st.write(f"[{ev.get('t', '')}] “{ev.get('quote', '')}” — "
                             f"{ev.get('point', '')}")
                for iss in info.get("issues", []):
                    st.write(f"❗ {iss.get('problem', '')}｜为什么：{iss.get('why', '')}"
                             f"｜怎么改：{iss.get('how', '')}")

        with st.expander("完整转写稿"):
            for s in (it.get("transcript") or []):
                st.write(f"[{int(s['start']) // 60}:{int(s['start']) % 60:02d}] "
                         f"{s['text']}")

        pre = dict(it.get("precheck") or {})
        auto_items = []
        if PRE_CFG.get("max_duration_sec"):
            auto_items.append(("duration_ok",
                               f"时长 ≤ {PRE_CFG['max_duration_sec']}秒"))
        if PRE_CFG.get("en_ratio_limit", 1.0) < 1.0:
            auto_items.append(("chinese_ok",
                f"外语夹杂 ≤ {round(PRE_CFG['en_ratio_limit']*100)}%"))
        manual_items = [(f"m_{i}", label) for i, label in
                        enumerate(PRE_CFG.get("manual_items", []))]
        all_items = auto_items + manual_items
        if all_items:
            st.markdown("**准入检查**（自动预判项可覆盖，人工项请勾选）")
            pc_cols = st.columns(len(all_items))
            for i, (k, label) in enumerate(all_items):
                with pc_cols[i]:
                    pre[k] = st.checkbox(label, value=bool(pre.get(k, True)),
                                         key=f"pc_{it['id']}_{k}")

        comment = st.text_area("老师批注（会进学生 PDF 的「老师的话」）",
                               value=it.get("teacher_comment", ""),
                               key=f"tc_{it['id']}")

        b1, b2, b3 = st.columns(3)
        with b1:
            if st.button("💾 保存并确认此份", type="primary",
                         key=f"ok_{it['id']}"):
                db.save_review(it["id"], new_scores, comment, pre, "confirmed")
                st.session_state.open_item = None
                st.rerun()
        with b2:
            if st.button("💾 仅保存（不确认）", key=f"sv_{it['id']}"):
                db.save_review(it["id"], new_scores, comment, pre, "graded")
                st.rerun()
        with b3:
            if st.button("⛔ 打回（不符准入要求）", key=f"rj_{it['id']}"):
                db.save_review(it["id"], new_scores, comment, pre, "rejected")
                st.session_state.open_item = None
                st.rerun()

# ── 批量确认（标黄拦截弹窗）─────────────────────────────────
st.divider()
n_graded = sum(1 for i in items if i["status"] == "graded")
if n_graded:
    if st.button(f"✅ 一键确认其余 {n_graded} 份"):
        st.session_state.show_confirm_pop = True
    if st.session_state.get("show_confirm_pop"):
        with st.container(border=True):
            if flagged:
                st.warning(f"仍有 {len(flagged)} 份标黄未逐一查看："
                           f"{'、'.join(flagged)}。确定全部按 AI 结果确认？")
            else:
                st.info("没有标黄项。确认全部？")
            cc1, cc2 = st.columns(2)
            with cc1:
                if st.button("确定，全部确认", type="primary"):
                    n = db.confirm_all_graded(job_id)
                    db.set_job_status(job_id, "done")
                    st.session_state.show_confirm_pop = False
                    st.success(f"已确认 {n} 份。请到「下载」页生成最终文件。")
            with cc2:
                if st.button("取消"):
                    st.session_state.show_confirm_pop = False
                    st.rerun()
else:
    n_conf = sum(1 for i in items if i["status"] == "confirmed")
    if n_conf:
        st.success(f"已确认 {n_conf} 份。请到「下载」页生成最终文件。")
