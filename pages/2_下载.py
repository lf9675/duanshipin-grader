# -*- coding: utf-8 -*-
"""
pages/2_下载.py — 生成并下载最终文件
════════════════════════════════════
- 只用「已确认」的份数生成（打回的不进包）
- 讲评聚合走 DeepSeek 单次调用（REVIEW_AGG，含人名匿名化）
- 产出一个总 ZIP：学生报告/学号.pdf ×N + 班级Excel + 讲评PPT
- 老师拿到 ZIP 后在谷歌课室逐个私下退还 PDF
"""

import json
from datetime import datetime

import streamlit as st

import video_db as db
from video_engine import _post_json, _extract_json, DEEPSEEK_URL
from video_export import (build_student_zip, build_teacher_zip,
                          grade_dist_of, EXPORT_VERSION)
from video_prompts import REVIEW_AGG_SYSTEM, build_review_agg_user

st.set_page_config(page_title="下载 · 短视频批改", page_icon="📦",
                   layout="wide")
if not st.session_state.get("authed"):
    st.warning("请先在主页输入平台口令")
    st.stop()

job_id = st.session_state.get("job_id")
if not job_id:
    st.info("请先在主页选择批改任务")
    st.stop()

job = db.get_job(job_id)
RUBRIC = job["rubric"] if not isinstance(job["rubric"], str) else json.loads(job["rubric"])
st.title(f"📦 下载 — {job['class_name']}《{job['topic']}》")
st.caption(f"导出版本 {EXPORT_VERSION}")

items = [dict(i) for i in db.list_items(job_id)]
for it in items:
    for f in ("student_ids", "transcript", "metrics", "precheck",
              "ai_result", "final_scores"):
        if isinstance(it.get(f), str):
            try:
                it[f] = json.loads(it[f])
            except Exception:
                pass

confirmed = [i for i in items if i["status"] == "confirmed"]
rejected = [i for i in items if i["status"] == "rejected"]
st.write(f"已确认 {len(confirmed)} 份"
         + (f"；已打回 {len(rejected)} 份（不进包）：" +
            "、".join(i["item_key"] for i in rejected) if rejected else ""))

if not confirmed:
    st.info("还没有已确认的份数。请先到「复核」页确认。")
    st.stop()

with_ppt = st.toggle("生成讲评 PPT（需调用一次 DeepSeek 做全班聚合+匿名化）",
                     value=True)

if st.button("🏗 生成全部文件", type="primary"):
    agg = None
    if with_ppt:
        if not st.session_state.get("deepseek_key"):
            st.error("生成讲评 PPT 需要 DeepSeek Key，请在主页侧栏填入")
            st.stop()
        with st.spinner("正在聚合全班共性问题并匿名化…"):
            try:
                payload = {
                    "model": "deepseek-chat", "temperature": 0.2,
                    "max_tokens": 4000,
                    "messages": [
                        {"role": "system", "content": REVIEW_AGG_SYSTEM},
                        {"role": "user",
                         "content": build_review_agg_user(
                             job["topic"],
                             [dict(i, final_total=sum(
                                 v for v in (i.get("final_scores") or {}).values()
                                 if isinstance(v, (int, float))))
                              for i in confirmed])},
                    ],
                }
                data = _post_json(DEEPSEEK_URL,
                                  st.session_state.deepseek_key, payload)
                agg = _extract_json(data["choices"][0]["message"]["content"])
            except Exception as e:
                st.warning(f"讲评聚合失败（{e}），本次 ZIP 不含 PPT，"
                           f"可稍后重试。")
    # 2026-08-04 决策：拆成两个包。原来 Excel（全班分数）和讲评 PPT 与
    # 学生 PDF 混在同一个 ZIP 里，老师手一滑整包传上谷歌课室，全班分数
    # 就公开了。这是防呆设计，不是洁癖。
    with st.spinner("正在生成 PDF / Excel / PPT 并打包…"):
        _stamp = datetime.now().strftime("%Y%m%d")
        try:
            st.session_state.stu_zip = build_student_zip(
                confirmed, job["class_name"], job["topic"], RUBRIC)
            st.session_state.stu_zip_name = (
                f"{job['class_name']}_发给学生_{_stamp}.zip")
        except Exception as e:
            st.error(f"学生包打包失败：{e}")
        try:
            st.session_state.tea_zip = build_teacher_zip(
                confirmed, job["class_name"], job["topic"], RUBRIC, agg)
            st.session_state.tea_zip_name = (
                f"{job['class_name']}_老师自留_{_stamp}.zip")
        except Exception as e:
            st.error(f"老师包打包失败：{e}")

if st.session_state.get("stu_zip") or st.session_state.get("tea_zip"):
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("#### 📤 发给学生的")
        st.caption("里面只有 PDF，一人一份。可以放心发。")
        if st.session_state.get("stu_zip"):
            st.download_button(
                "⬇️ 下载学生包", data=st.session_state.stu_zip,
                file_name=st.session_state.stu_zip_name,
                mime="application/zip", type="primary",
                key="dl_stu")
    with c2:
        st.markdown("#### 🔒 老师自留的")
        st.caption("全班成绩总表 + 讲评 PPT。**绝不可发给学生。**")
        if st.session_state.get("tea_zip"):
            st.download_button(
                "⬇️ 下载老师包", data=st.session_state.tea_zip,
                file_name=st.session_state.tea_zip_name,
                mime="application/zip", key="dl_tea")

    st.markdown("""**下一步（谷歌课室分发）**

1. 解压**学生包**，里面是 `组号_学号.pdf`（如 `01_05.pdf`）。
2. 在谷歌课室该作业下，进成绩簿 → 点该学生的格子 → 添加附件（传他那一份）
   → 退还。这份附件**只有他本人和你**看得到。
3. ❌ 不要把 PDF 放在课业说明区的附件——那是全班可见的。
4. ❌ 不要把**老师包**里的 Excel 或 PPT 发给学生。""")
