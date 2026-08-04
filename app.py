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
  断线后转写稿还在，批改可续；仅复核抽听和拍摄维度需要的媒体文件
  会丢失——重传对应视频即可补。
- 备用通道：仍支持上传本地工具生成的批改包 zip（家里电脑可装 Python 时）。
  2026-07-28 决策（刘老师）：备用通道改为【可多选】，一次能传好几个批改包。
  逐包容错——某个包坏了/传错了只跳过它，不影响其余包；跨包同学号会提示覆盖。
  总量仍受 1GB 闸门约束（Streamlit 把上传文件整个读进内存，云端容器约 1GB）。
"""

import json
import shutil
import tempfile
import zipfile
from pathlib import Path

import streamlit as st

import video_db as db
from video_rubric import member_mode as rubric_member_mode
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
    # 2026-07-28 安全修复：原写法是 st.secrets.get("APP_PASSWORD", "")，
    # Secrets 漏配时默认值为空字符串 → 空口令即可进入 = 公网无密码。
    # 改为 fail-closed：没配就谁也进不来，并提示管理员去配。
    _pw_expected = str(st.secrets.get("APP_PASSWORD", "") or "")
    if not _pw_expected:
        st.error("本平台尚未设置口令，暂时无法进入。")
        st.info("管理员请到 Streamlit Cloud → Manage app → Settings → "
                "Secrets，加一行：\n\n"
                '`APP_PASSWORD = "自定口令"`\n\n保存后应用会自动重启。')
        st.stop()
    pw = st.text_input("平台口令", type="password")
    if st.button("进入"):
        if pw == _pw_expected:
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
    st.session_state.setdefault("whisper_size", "small")
    st.session_state.whisper_size = st.selectbox(
        "转写模型", ["small", "tiny"],
        index=["small", "tiny"].index(st.session_state.whisper_size),
        help="small：准确优先，云端默认（2026-07-21 起）；"
             "tiny：快一倍但对口音和噪音抵抗力差，容易转写失败。")

st.title("🎬 华文通 · 短视频批改")
st.caption("直接上传视频 → 服务器转写 → 队列批改 → 复核 → 下载。"
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
    topic = st.text_input("题目（如：教一项你会的技能）",
                          value=st.session_state.get("topic", ""))

# ── 选择作业模板（2026-07-20 常年化）──
rubs = db.list_rubrics()
rub_labels = {f"{r['name']}": r for r in rubs}
sel_rub = st.selectbox("评分标准（作业模板）", list(rub_labels),
                       help="换题目/换标准请到「题目与评分标准」页新建模板")
_r = rub_labels[sel_rub]
_rubric = _r["rubric"] if not isinstance(_r["rubric"], str) else json.loads(_r["rubric"])
requirements = st.text_area("题目要求（写进批改提示词）",
                            value=_r["requirements"] or "", height=90)

# ── 组员名单（2026-08-04，仅小组视频模板显示）────────────────
# 一个视频对应 4-5 名组员，视频文件名里没有学号，必须靠这张表把
# 「视频编号 → 学号」对上，才能一人一份报告、一人一个分数。
_groups = {}
if rubric_member_mode(_rubric):
    st.markdown("**组员名单**（每行一组，格式 `视频编号: 学号,学号,…`）")
    _roster = st.text_area(
        "组员名单", height=110, label_visibility="collapsed",
        placeholder="01: 05,12,18,23\n02: 07,09,14,21,25",
        help="视频文件请命名成 01.mp4、02.mp4。这里的编号要和文件名对上。")
    _bad = []
    for _ln in (_roster or "").splitlines():
        _ln = _ln.strip().replace("：", ":").replace("，", ",")
        if not _ln:
            continue
        if ":" not in _ln:
            _bad.append(_ln)
            continue
        _k, _v = _ln.split(":", 1)
        _ids = [x.strip() for x in _v.split(",") if x.strip()]
        if not _k.strip() or not _ids:
            _bad.append(_ln)
            continue
        _groups[_k.strip()] = _ids
    if _bad:
        st.warning("这几行看不懂，已跳过：" + "；".join(_bad[:3]))
    if _groups:
        _dupe = {}
        for _k, _ids in _groups.items():
            for _i in _ids:
                _dupe.setdefault(_i, []).append(_k)
        _multi = {i: ks for i, ks in _dupe.items() if len(ks) > 1}
        st.caption(f"已登记 {len(_groups)} 组、"
                   f"{sum(len(v) for v in _groups.values())} 人")
        if _multi:
            st.error("同一个学号出现在多个组里："
                     + "；".join(f"{i}→{'、'.join(k)}" for i, k in _multi.items()))

ups = st.file_uploader(
    "选择学生视频（可多选；命名：个人 05.mp4，两人组 05_12.mp4）",
    type=["mp4", "mov", "m4v", "avi", "mkv", "webm"],
    accept_multiple_files=True)

if ups:
    total_bytes = sum(u.size for u in ups)
    st.write(f"已选 {len(ups)} 个视频，共 {total_bytes/1024/1024:.0f} MB")
    # 2026-08-04：iPhone 原始 .mov 体积是同长度 mp4 的 3-5 倍，5 分钟常有
    # 400-800MB，两三个就撞上 1GB 闸门。原来只报"总量超限"，老师会误以为
    # 平台不收 mov（其实白名单一直有 mov），所以这里点名说清楚。
    _big = [u.name for u in ups if u.size > 300 * 1024 * 1024]
    if _big:
        st.warning(
            f"这 {len(_big)} 个文件单个就超过 300MB："
            f"{'、'.join(_big[:3])}{'…' if len(_big) > 3 else ''}\n\n"
            f"多半是 iPhone 原始 .mov。平台【收】mov，只是它太占地方，"
            f"一次传不了几个。建议在手机上用「邮件／信息」分享一次"
            f"（会自动转成小体积 mp4），或一次只传一两个。")
    if total_bytes > MAX_BATCH_BYTES:
        st.error(f"本次总量超过 1GB 上限（{total_bytes/1024/1024/1024:.1f} GB）。"
                 f"请减少选择、分几次上传——分批上传互不覆盖，放心分。")
    elif st.button("📥 入队开始处理", type="primary", disabled=not class_name):
        job_id = st.session_state.get("job_id")
        if not job_id:
            job_id = db.create_job(class_name, topic, _rubric, requirements)
            st.session_state.job_id = job_id
        if _groups:
            db.set_job_groups(job_id, _groups)
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

def _unpack_one_zip(zf, root, job_id, zip_name):
    """解一个批改包。返回 (成功入队的 item_key 列表, 错误串列表)。"""
    names = zf.namelist()
    item_dirs = sorted({n.split("/")[0] for n in names
                        if "/" in n and n.split("/")[0]
                        and not n.split("/")[0].endswith(".json")})
    # 2026-07-28：误传检测——包里全是视频原片，说明传错通道了。
    # 原来这种情况静默显示"入队 0 份"，老师无从判断哪里出错。
    if not item_dirs:
        vids = [n for n in names if Path(n).suffix.lower() in
                (".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm")]
        if vids:
            return [], [f"{zip_name}：这个包里装的是 {len(vids)} 个视频原片，"
                        f"不是 yuchuli.py 生成的批改包。请改用上面的"
                        f"「选择学生视频」直接传视频（不要打包）。"]
        return [], [f"{zip_name}：包里没找到任何批改包目录（应形如 05/transcript.json）。"]

    ok_keys, errs = [], []
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
            ok_keys.append(key)
        except KeyError:
            errs.append(f"{zip_name} / {key}：缺 transcript.json 或字段不全，已跳过")
        except Exception as e:
            errs.append(f"{zip_name} / {key}：{e}")
    return ok_keys, errs


with st.expander("备用通道：上传本地工具生成的批改包 zip"):
    st.caption("家里电脑能装 Python 时，可用 local_tool/yuchuli.py 在本地"
               "预处理，云端零转写负担、速度最快。")
    # 2026-07-28：改为可多选（刘老师要求）。批改包本身只有一百多 MB，
    # 多选几个正常不会超限；总量闸门与视频上传共用 1GB，理由同——
    # Streamlit 把上传文件整个读进内存，云端容器约 1GB，超了会 OOM 崩溃。
    up_zips = st.file_uploader("批改包_*.zip（可多选）", type="zip",
                               key="zip_up", accept_multiple_files=True)
    if up_zips:
        zip_bytes = sum(z.size for z in up_zips)
        st.write(f"已选 {len(up_zips)} 个批改包，共 "
                 f"{zip_bytes / 1024 / 1024:.0f} MB")
        if zip_bytes > MAX_BATCH_BYTES:
            st.error(
                f"本次总量 {zip_bytes / 1024 / 1024 / 1024:.1f} GB 超过 1GB 上限。"
                f"请减少选择、分几次上传——分批上传互不覆盖，放心分。\n\n"
                f"（若单个包就有好几百 MB，多半是把视频原片打进去了；"
                f"yuchuli.py 生成的批改包通常只有一百多 MB。）")
        elif st.button("📥 批改包入队", type="primary", disabled=not class_name):
            job_id = st.session_state.get("job_id")
            if not job_id:
                job_id = db.create_job(class_name, topic, _rubric, requirements)
                st.session_state.job_id = job_id
            st.session_state.class_name = class_name
            st.session_state.topic = topic
            root = media_root() / str(job_id)
            root.mkdir(parents=True, exist_ok=True)

            all_errs, dup_msgs = [], []
            seen = {}          # item_key -> 最早出现的包名（跨包重复检测）
            n_ok = 0
            prog = st.progress(0.0, text="正在解包…")
            for i, uz in enumerate(up_zips):
                zname = uz.name
                prog.progress(i / len(up_zips), text=f"正在解包 {zname}…")
                try:
                    with zipfile.ZipFile(uz) as zf:
                        keys, errs = _unpack_one_zip(zf, root, job_id, zname)
                except zipfile.BadZipFile:
                    all_errs.append(f"{zname}：不是有效的 zip 文件（可能上传中断），已跳过")
                    continue
                except Exception as e:
                    all_errs.append(f"{zname}：打开失败 {e}，已跳过")
                    continue
                for k in keys:
                    if k in seen:
                        dup_msgs.append(f"学号 {k} 在《{seen[k]}》和《{zname}》"
                                        f"里都有，后者已覆盖前者")
                    else:
                        seen[k] = zname
                n_ok += len(keys)
                all_errs.extend(errs)
            prog.progress(1.0, text="解包完成")

            if n_ok:
                st.success(f"从 {len(up_zips)} 个批改包中入队 {n_ok} 份"
                           f"（去重后 {len(seen)} 个学号）。")
            else:
                st.error("没有任何一份成功入队，请看下面的原因。")
            for m in dup_msgs:
                st.warning(m)
            for e in all_errs:
                st.warning(e)
            if n_ok:
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
        n_regr = sum(1 for i in items if i["status"] in ("graded", "failed"))
        if n_regr and st.button(f"↻ 重批 {n_regr} 份（按最新指引）",
                                help="评分指引/模板更新后使用；已确认的不重批"):
            db.requeue_for_regrade(job_id)
            st.session_state.queue_running = True
            st.rerun()
    if done == total and total > 0:
        st.success("批改完成！请到左侧「复核」页逐项确认。")

    _job = db.get_job(job_id)
    _job_rubric = _job["rubric"] if not isinstance(_job["rubric"], str) else json.loads(_job["rubric"])
    _job_req = _job["requirements"] or ""
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
                            model_size=st.session_state.whisper_size,
                            pre_cfg=_job_rubric.get("precheck", {}))
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
                                _job["topic"], _job_rubric, _job_req)
                            db.save_ai_result(nxt["id"], ai, fs)
                        except Exception as e:
                            db.mark_failed(nxt["id"], e)
                    st.rerun()

    if failed:
        with st.expander("失败清单"):
            for i in failed:
                st.write(f"学号 {i['item_key']}：{i['error_msg']}")
