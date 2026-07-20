# -*- coding: utf-8 -*-
"""
app.py — 华文通·短视频批改 主页（上传 + 队列）
════════════════════════════════════════════
2026-07-20 改版：学校电脑装不了 Python → 老师【直接上传视频】，
预处理（抽音频/关键帧/转写/指标）全部搬到服务器上跑，零本地安装。

- 单次上传总量 ≤ 1GB，超出会拦下提示分批；分批上传互不覆盖。
- 【视频删除承诺】每个视频提取完音频和关键帧后立刻从服务器删除，
  不等批改流程走完——留存时间最短。
- 队列两阶段，每次脚本运行只推进一步 → st.rerun() 续跑（沿用作文
  平台 2026-07 队列修复方案）：
    阶段一 预处理：uploaded → pending（视频→音频/帧/转写稿，删视频）
    阶段二 批改：  pending → graded（DeepSeek + GLM-4V）
- 转写稿等文本入库；音频/关键帧只存会话临时目录（PDPA：不入库）。
  断线后转写稿还在，批改可续；仅覆核抽听和拍摄维度需要的媒体文件
  会丢失——重传对应视频即可补。
- 备用通道：仍支持上传本地工具生成的批改包 zip（家里电脑可装 Python 时）。
"""

import json
import shutil
import tempfile
import zipfile
from pathlib import Path

import streamlit as st

import video_db as db
import video_ingest as ingest
from video_engine import grade_one_item, ENGINE_VERSION

st.set_page_config(page_title="华文通·短视频批改", page_icon="🎬",
                   layout="wide")

MAX_BATCH_BYTES = 1024 * 1024 * 1024  # 单次上传总量 1GB

# ── 简易门禁 ─────────────────────────────────────────────────
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

# ── 侧栏 ─────────────────────────────────────────────────────
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
    st.divider()
    st.session_state.setdefault("whisper_size", "tiny")
    st.session_state.whisper_size = st.selectbox(
        "转写模型", ["tiny", "small"],
        index=["tiny", "small"].index(st.session_state.whisper_size),
        help="tiny：快，云端默认；small：更准但明显更慢。"
             "先用 tiny 试批 3-5 份看转写质量，不够再换。")

st.title("🎬 华文通 · 短视频批改")
st.caption("直接上传视频 → 服务器转写 → 队列批改 → 覆核 → 下载。"
           "每个视频提取完音频和画面后**立刻从服务器删除**。")


# ── 会话临时目录 ─────────────────────────────────────────────
def media_root():
    if "media_root" not in st.session_state:
        st.session_state.media_root = tempfile.mkdtemp(prefix="dsp_media_")
    return Path(st.session_state.media_root)


def video_inbox(job_id):
    """待预处理视频的临时暂存目录（处理完即删）。"""
    p = media_root() / str(job_id) / "_inbox"
    p.mkdir(parents=True, exist_ok=True)
    return p


# ── 第一步：任务 + 上传视频 ──────────────────────────────────
st.subheader("① 上传视频")
col1, col2 = st.columns(2)
with col1:
    class_name = st.text_input("班级",
                               value=st.session_state.get("class_name", ""))
with col2:
    topic = st.text_input("本次技能主题（如：教一项你会的技能）",
                          value=st.session_state.get("topic", ""))

ups = st.file_uploader(
    "选择学生视频（可多选；命名：个人 05.mp4，两人组 05_12.mp4）",
    type=["mp4", "mov", "m4v", "avi", "mkv", "webm"],
    accept_multiple_files=True)

if ups:
    total_bytes = sum(u.size for u in ups)
    st.write(f"已选 {len(ups)} 个视频，共 {total_bytes/1024/1024:.0f} MB")
    if total_bytes > MAX_BATCH_BYTES:
        st.error(f"本次总量超过 1GB 上限（{total_bytes/1024/1024/1024:.1f} GB）。"
                 f"请减少选择、分几次上传——分批上传互不覆盖，放心分。")
    elif st.button("📥 入队开始处理", type="primary", disabled=not class_name):
        job_id = st.session_state.get("job_id")
        if not job_id:
            job_id = db.create_job(class_name, topic)
            st.session_state.job_id = job_id
        st.session_state.class_name = class_name
        st.session_state.topic = topic
        inbox = video_inbox(job_id)
        n_ok, n_skip = 0, []
        for u in ups:
            key = "_".join(ingest.parse_student_ids(Path(u.name).stem))
            if db.create_uploaded_item(job_id, key,
                                       ingest.parse_student_ids(Path(u.name).stem),
                                       u.name):
                (inbox / f"{key}{Path(u.name).suffix.lower()}").write_bytes(
                    u.getbuffer())
                n_ok += 1
            else:
                n_skip.append(key)
        msg = f"入队 {n_ok} 个视频。"
        if n_skip:
            msg += f" 跳过 {len(n_skip)} 个已批改的学号（不覆盖）：{'、'.join(n_skip)}"
        st.success(msg)
        st.session_state.queue_running = True
        st.rerun()

with st.expander("备用通道：上传本地工具生成的批改包 zip"):
    st.caption("家里电脑能装 Python 时，可用 local_tool/yuchuli.py 在本地"
               "预处理，云端零转写负担、速度最快。")
    up_zip = st.file_uploader("批改包_*.zip", type="zip", key="zip_up")
    if up_zip is not None and st.button("📥 批改包入队", disabled=not class_name):
        job_id = st.session_state.get("job_id")
        if not job_id:
            job_id = db.create_job(class_name, topic)
            st.session_state.job_id = job_id
        st.session_state.class_name = class_name
        st.session_state.topic = topic
        root = media_root() / str(job_id)
        root.mkdir(parents=True, exist_ok=True)
        ok, errs = 0, []
        with zipfile.ZipFile(up_zip) as zf:
            names = zf.namelist()
            item_dirs = sorted({n.split("/")[0] for n in names
                                if "/" in n and n.split("/")[0]
                                and not n.split("/")[0].endswith(".json")})
            for key in item_dirs:
                try:
                    tjson = json.loads(
                        zf.read(f"{key}/transcript.json").decode("utf-8"))
                    summ = tjson["summary"]
                    for n in names:
                        if n.startswith(f"{key}/") and (
                                n.endswith(".jpg") or n.endswith(".mp3")):
                            target = root / n
                            target.parent.mkdir(parents=True, exist_ok=True)
                            target.write_bytes(zf.read(n))
                    db.upsert_item(job_id, key, summ["student_ids"],
                                   summ.get("source_file", ""),
                                   tjson["segments"],
                                   summ.get("metrics", {}),
                                   summ.get("precheck", {}))
                    ok += 1
                except Exception as e:
                    errs.append(f"{key}: {e}")
        st.success(f"入队 {ok} 份。")
        for e in errs:
            st.warning(e)
        st.session_state.queue_running = True
        st.rerun()

# 历史任务恢复
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


# ── 第二步：两阶段队列 ───────────────────────────────────────
def _delete_video(path):
    """视频删除承诺的执行点。"""
    try:
        Path(path).unlink(missing_ok=True)
    except Exception:
        pass


job_id = st.session_state.get("job_id")
if job_id:
    st.subheader("② 队列进度")
    items = db.list_items(job_id)
    total = len(items)
    n_upl = sum(1 for i in items if i["status"] == "uploaded")
    done = sum(1 for i in items
               if i["status"] in ("graded", "confirmed", "rejected"))
    failed = [i for i in items if i["status"] == "failed"]
    label = f"{done}/{total} 已批改"
    if n_upl:
        label += f"，{n_upl} 份待转写"
    if failed:
        label += f"，{len(failed)} 份失败"
    st.progress(done / total if total else 0.0, text=label)

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
        # ── 阶段一：预处理（视频→音频/帧/转写，随即删视频）──
        upl = db.next_uploaded_item(job_id)
        if upl is not None:
            key = upl["item_key"]
            inbox = video_inbox(job_id)
            vids = [p for p in inbox.iterdir()
                    if p.stem == key and p.suffix.lower() in ingest.VIDEO_EXTS] \
                if inbox.exists() else []
            if not vids:
                db.mark_failed(upl["id"],
                               "视频不在本会话中（可能断线丢失），请重新上传该视频")
                st.rerun()
            else:
                with st.spinner(f"正在转写 学号 {key} 的视频…"
                                f"（每份约 1–3 分钟，请保持本页签打开）"):
                    out_dir = media_root() / str(job_id) / key
                    try:
                        result = ingest.preprocess_video(
                            vids[0], out_dir,
                            model_size=st.session_state.whisper_size)
                        db.save_preprocessed(upl["id"], result["segments"],
                                             result["metrics"],
                                             result["precheck"])
                    except Exception as e:
                        db.mark_failed(upl["id"], f"预处理失败：{e}")
                    finally:
                        _delete_video(vids[0])   # 承诺：处理完立刻删除视频
                st.rerun()
        else:
            # ── 阶段二：AI 批改 ──
            nxt = db.next_pending_item(job_id)
            if nxt is None:
                st.session_state.queue_running = False
                db.set_job_status(job_id, "reviewing")
                st.rerun()
            else:
                if not (st.session_state.deepseek_key
                        and st.session_state.glm_key):
                    st.error("请先在左侧填入 DeepSeek 和 GLM 的 API Key")
                    st.session_state.queue_running = False
                else:
                    key = nxt["item_key"]
                    with st.spinner(f"正在批改 学号 {key} …"
                                    f"（每份约 1–2 分钟，请保持本页签打开）"):
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
