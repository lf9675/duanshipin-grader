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
from video_export import build_all_zip, grade_dist_of, EXPORT_VERSION
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
    st.info("还没有已确认的份数。请先到「覆核」页确认。")
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
    with st.spinner("正在生成 PDF / Excel / PPT 并打包…"):
        try:
            blob = build_all_zip(confirmed, job["class_name"],
                                 job["topic"], agg)
            st.session_state.final_zip = blob
            st.session_state.final_zip_name = (
                f"{job['class_name']}_短视频批改_"
                f"{datetime.now().strftime('%Y%m%d')}.zip")
        except Exception as e:
            st.error(f"打包失败：{e}")

if st.session_state.get("final_zip"):
    st.download_button(
        "⬇️ 下载总包（可多次下载）",
        data=st.session_state.final_zip,
        file_name=st.session_state.final_zip_name,
        mime="application/zip", type="primary")
    st.markdown("""**下一步（谷歌课室分发）**
1. 解压总包，`学生报告/` 里是每人一份 PDF（两人组各有一份）。
2. 在谷歌课室该作业下，逐个学生「退还」并附上对应 PDF。
3. Excel 总表留档；讲评 PPT 是底稿，上课前按需增删。""")
