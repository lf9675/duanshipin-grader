# -*- coding: utf-8 -*-
"""
app.py — 华文通·短视频批改 主页（上传 + 队列）
════════════════════════════════════════════
极简四步的前两步：上传批改包 → 队列进度。
队列模式沿用作文平台 2026-07 修复方案：每次脚本运行只批一份 →
st.rerun() 续跑；进度持久化在 video_batch_items 表，断线可续。
关键帧/音频只存会话临时目录（PDPA：不入库）；断线重连后重新上传
同一个批改包即可恢复（已批份自动跳过）。
"""

import json
import shutil
import tempfile
import zipfile
from pathlib import Path

import streamlit as st

import video_db as db
from video_engine import grade_one_item, ENGINE_VERSION

st.set_page_config(page_title="华文通·短视频批改", page_icon="🎬",
                   layout="wide")

# ── 简易门禁（v1 单老师使用；密码放 Streamlit Secrets 的 APP_PASSWORD）──
if "authed" not in st.session_state:
    st.session_state.authed = False
if not st.session_state.authed:
    st.title("🎬 华文通 · 短视频批改")
    pw = st.text_input("平台口令", type="password")
    if st.button("进入"):
        if pw == st.secrets.get("APP_PASSWORD", ""):
            st.session_state.authed = True
            st.rerun()
        else:
            st.error("口令不正确")
    st.stop()

db.init_schema()

# ── 侧栏：API 钥匙（只存会话，不落库）──────────────────────
with st.sidebar:
    st.markdown(f"**引擎版本** `{ENGINE_VERSION}`")
    st.session_state.setdefault("deepseek_key", "")
    st.session_state.setdefault("glm_key", "")
    st.session_state.deepseek_key = st.text_input(
        "DeepSeek API Key", value=st.session_state.deepseek_key,
        type="password")
    st.session_state.glm_key = st.text_input(
        "GLM API Key（智谱）", value=st.session_state.glm_key,
        type="password")
    st.caption("钥匙只保存在本次会话内存，不写入数据库。")

st.title("🎬 华文通 · 短视频批改")
st.caption("上传批改包 → 队列批改 → 覆核 → 下载。视频原片不上传本平台。")


# ── 会话临时目录：存关键帧和音频 ─────────────────────────────
def media_root():
    if "media_root" not in st.session_state:
        st.session_state.media_root = tempfile.mkdtemp(prefix="dsp_media_")
    return Path(st.session_state.media_root)


def unpack_zip(uploaded, job_id):
    """解包批改包：文本入库，媒体进会话临时目录。返回 (成功数, 报错列表)。"""
    root = media_root() / str(job_id)
    root.mkdir(parents=True, exist_ok=True)
    ok, errs = 0, []
    with zipfile.ZipFile(uploaded) as zf:
        names = zf.namelist()
        item_dirs = sorted({n.split("/")[0] for n in names
                            if "/" in n and n.split("/")[0] != ""
                            and not n.split("/")[0].endswith(".json")})
        for key in item_dirs:
            try:
                tjson = json.loads(
                    zf.read(f"{key}/transcript.json").decode("utf-8"))
                summ = tjson["summary"]
                # 媒体解到临时目录
                for n in names:
                    if n.startswith(f"{key}/") and (
                            n.endswith(".jpg") or n.endswith(".mp3")):
                        target = root / n
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(zf.read(n))
                db.upsert_item(
                    job_id=job_id, item_key=key,
                    student_ids=summ["student_ids"],
                    source_file=summ.get("source_file", ""),
                    transcript=tjson["segments"],
                    metrics=summ.get("metrics", {}),
                    precheck=summ.get("precheck", {}))
                ok += 1
            except Exception as e:
                errs.append(f"{key}: {e}")
    return ok, errs


# ── 第一步：新建/选择批改任务并上传 ──────────────────────────
st.subheader("① 上传批改包")
col1, col2 = st.columns(2)
with col1:
    class_name = st.text_input("班级", value=st.session_state.get("class_name", ""))
with col2:
    topic = st.text_input("本次技能主题（如：教一项你会的技能）",
                          value=st.session_state.get("topic", ""))

up = st.file_uploader("上传本地预处理工具生成的 批改包_*.zip", type="zip")
if up is not None and st.button("📥 入队开始批改", type="primary",
                                disabled=not class_name):
    job_id = st.session_state.get("job_id")
    if not job_id:
        job_id = db.create_job(class_name, topic)
        st.session_state.job_id = job_id
    st.session_state.class_name = class_name
    st.session_state.topic = topic
    ok, errs = unpack_zip(up, job_id)
    st.success(f"入队 {ok} 份。已批改过的自动保留结果不重批。")
    for e in errs:
        st.warning(e)
    st.session_state.queue_running = True
    st.rerun()

# 历史任务恢复（断线续跑：选任务 + 重传批改包补媒体文件）
jobs = db.list_jobs()
if jobs:
    labels = {f"#{j['id']} {j['class_name']} {j['topic']} "
              f"({j['done']}/{j['total']}) {j['status']}": j["id"]
              for j in jobs}
    sel = st.selectbox("或继续历史任务", ["（新任务）"] + list(labels))
    if sel != "（新任务）" and st.session_state.get("job_id") != labels[sel]:
        if st.button("载入该任务"):
            st.session_state.job_id = labels[sel]
            j = db.get_job(labels[sel])
            st.session_state.class_name = j["class_name"]
            st.session_state.topic = j["topic"]
            st.rerun()

# ── 第二步：队列进度（每次运行批一份 → rerun 续跑）──────────
job_id = st.session_state.get("job_id")
if job_id:
    st.subheader("② 队列进度")
    items = db.list_items(job_id)
    total = len(items)
    done = sum(1 for i in items
               if i["status"] in ("graded", "confirmed", "rejected"))
    failed = [i for i in items if i["status"] == "failed"]
    st.progress(done / total if total else 0.0,
                text=f"{done}/{total} 已批改" +
                     (f"，{len(failed)} 份失败" if failed else ""))

    running = st.session_state.get("queue_running", False)
    c1, c2, c3 = st.columns(3)
    with c1:
        if running and st.button("⏸ 暂停队列"):
            st.session_state.queue_running = False
            st.rerun()
        if not running and done < total and st.button("▶ 继续批改"):
            st.session_state.queue_running = True
            st.rerun()
    with c2:
        if failed and st.button(f"🔁 重试 {len(failed)} 份失败"):
            db.retry_failed(job_id)
            st.session_state.queue_running = True
            st.rerun()
    with c3:
        if done == total and total > 0:
            st.success("批改完成！请到左侧「覆核」页逐项确认。")

    if running:
        nxt = db.next_pending_item(job_id)
        if nxt is None:
            st.session_state.queue_running = False
            db.set_job_status(job_id, "reviewing")
            st.rerun()
        else:
            if not (st.session_state.deepseek_key and st.session_state.glm_key):
                st.error("请先在左侧填入 DeepSeek 和 GLM 的 API Key")
                st.session_state.queue_running = False
            else:
                key = nxt["item_key"]
                with st.spinner(f"正在批改 学号 {key} …（每份约 1–2 分钟，"
                                f"请保持本页签打开）"):
                    fdir = media_root() / str(job_id) / key
                    fdir = fdir if fdir.exists() else None
                    try:
                        ai, fs = grade_one_item(
                            dict(nxt), fdir,
                            st.session_state.deepseek_key,
                            st.session_state.glm_key,
                            st.session_state.get("topic", ""))
                        db.save_ai_result(nxt["id"], ai, fs)
                    except Exception as e:
                        db.mark_failed(nxt["id"], e)
                st.rerun()

    if failed:
        with st.expander("失败清单"):
            for i in failed:
                st.write(f"学号 {i['item_key']}：{i['error_msg']}")
