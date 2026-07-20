# -*- coding: utf-8 -*-
"""
video_engine.py — 短视频批改引擎
════════════════════════════════
- DeepSeek 读转写稿+指标 → 步骤讲述 / 口语表达 / 内容设计
- GLM-4V 看关键帧 → 拍摄与呈现 + 面容出镜
- 两个客户端都带 180 秒硬超时（沿用作文平台 2026-07 队列卡死修复方案）
- grade_one_item() 每次脚本运行只批一份，由 app.py 配合 st.rerun() 续跑
- review_flags() 给覆核表算标黄项
"""

import base64
import io
import json
import re
import urllib.request
import urllib.error

from video_rubric import (DIM_BY_KEY, SPEECH_RATE_COMFORT, PAUSE_MANY,
                          FILLER_MANY, level_of)
from video_prompts import (TEXT_GRADING_SYSTEM, build_text_grading_user,
                           FRAMES_GRADING_PROMPT)

ENGINE_VERSION = "video-1.0-20260720"
TIMEOUT_SEC = 180  # 2026-07 决策：硬超时，防止单份卡死整条队列

DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
GLM_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"


def _post_json(url, api_key, payload, label="API"):
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {api_key}"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        # 2026-07-20 修复：400裸报错无法定位，读出API返回的具体原因
        try:
            body = e.read().decode("utf-8", errors="ignore")[:300]
        except Exception:
            body = ""
        raise RuntimeError(f"{label} HTTP {e.code}: {body}") from None


def _extract_json(text):
    """容错解析：剥掉可能的 markdown 围栏后取第一个 JSON 对象。"""
    text = re.sub(r"```(?:json)?", "", text).strip().strip("`")
    start = text.find("{")
    if start < 0:
        raise ValueError("AI 回复中没有 JSON")
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start:i + 1])
    raise ValueError("AI 回复 JSON 不完整")


def grade_text(deepseek_key, topic, student_ids, segments, metrics):
    payload = {
        "model": "deepseek-chat",
        "temperature": 0.2,
        "max_tokens": 4000,
        "messages": [
            {"role": "system", "content": TEXT_GRADING_SYSTEM},
            {"role": "user",
             "content": build_text_grading_user(topic, student_ids,
                                                segments, metrics)},
        ],
    }
    data = _post_json(DEEPSEEK_URL, deepseek_key, payload, label="DeepSeek")
    return _extract_json(data["choices"][0]["message"]["content"])


def _contact_sheet(frame_paths):
    """2026-07-20 修复：glm-4v-flash 单次请求只接受一张图片，
    把 8 张关键帧拼成一张 2 行×4 列的拼贴图（还省算力）。返回 jpeg bytes。"""
    from PIL import Image
    imgs = [Image.open(p).convert("RGB") for p in frame_paths[:8]]
    cell_w = 480
    cells = []
    for im in imgs:
        h = int(im.height * cell_w / im.width)
        cells.append(im.resize((cell_w, h)))
    cell_h = max(c.height for c in cells)
    cols, rows = 4, (len(cells) + 3) // 4
    sheet = Image.new("RGB", (cols * cell_w, rows * cell_h), (255, 255, 255))
    for i, c in enumerate(cells):
        sheet.paste(c, ((i % cols) * cell_w, (i // cols) * cell_h))
    buf = io.BytesIO()
    sheet.save(buf, format="JPEG", quality=80)
    return buf.getvalue()


def grade_frames(glm_key, frame_paths, n_students):
    """frame_paths: 会话临时目录里的 jpg 路径列表。拼成单图后送 GLM。"""
    b64 = base64.b64encode(_contact_sheet(frame_paths)).decode()
    prompt = FRAMES_GRADING_PROMPT.format(n=len(frame_paths[:8]),
                                          n_students=n_students)
    prompt = ("下面这张图是按时间顺序从左到右、从上到下排列的关键帧拼贴。\n"
              + prompt)
    content = [
        {"type": "image_url",
         "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
        {"type": "text", "text": prompt},
    ]
    payload = {
        "model": "glm-4v-flash",   # 帧判定任务较简单，先用 flash 省算力；不准再升 glm-4v
        "temperature": 0.2,
        "messages": [{"role": "user", "content": content}],
    }
    data = _post_json(GLM_URL, glm_key, payload, label="GLM(智谱)")
    return _extract_json(data["choices"][0]["message"]["content"])


def _clamp_dim(dim_key, score):
    """把 AI 建议分夹回该维度合法区间，并对齐档位。"""
    try:
        s = int(round(float(score)))
    except (TypeError, ValueError):
        s = 0
    return max(0, min(DIM_BY_KEY[dim_key]["max"], s))


def grade_one_item(item, frames_dir, deepseek_key, glm_key, topic):
    """批一份。frames_dir: 该生关键帧所在临时目录（Path），可能为 None
    （批改包缺帧/断线后未重传时，拍摄维度留待老师手评）。
    返回 (ai_result, final_scores)。异常直接抛给调用方记 failed。"""
    student_ids = item["student_ids"]
    if isinstance(student_ids, str):
        student_ids = json.loads(student_ids)
    segments = item["transcript"]
    if isinstance(segments, str):
        segments = json.loads(segments)
    if isinstance(segments, dict):
        segments = segments.get("segments", [])
    metrics = item["metrics"]
    if isinstance(metrics, str):
        metrics = json.loads(metrics)

    text_result = grade_text(deepseek_key, topic, student_ids,
                             segments, metrics)

    frames_result = None
    frames_error = ""
    if frames_dir is not None:
        frame_paths = sorted(frames_dir.glob("frame_*.jpg"))
        if frame_paths:
            # 2026-07-20 修复：拍摄维度失败不拖垮整份——DeepSeek 三维度
            # 照常保存，拍摄维度标"AI未评请手评"，走覆核标黄
            try:
                frames_result = grade_frames(glm_key, frame_paths,
                                             len(student_ids))
            except Exception as e:
                frames_error = str(e)[:200]

    dims = text_result.get("dimensions", {})
    final_scores = {}
    for key in ("content", "speaking", "design"):
        final_scores[key] = _clamp_dim(key, dims.get(key, {}).get("score"))
    if frames_result:
        final_scores["video"] = _clamp_dim("video", frames_result.get("score"))
        dims["video"] = {
            "score": final_scores["video"],
            "grade": level_of("video", final_scores["video"])["grade"],
            "confidence": "high" if frames_result.get("face_ok") else "low",
            "comment": frames_result.get("comment", ""),
            "note": frames_result.get("note", ""),
            "face_ok": bool(frames_result.get("face_ok")),
        }
    else:
        final_scores["video"] = 0
        reason = (f"GLM批改失败（{frames_error}）" if frames_error
                  else "缺关键帧，AI 未评")
        dims["video"] = {"score": 0, "grade": "?", "confidence": "none",
                         "comment": f"{reason}，请老师看视频后手评",
                         "face_ok": None}
    text_result["dimensions"] = dims
    text_result["engine_version"] = ENGINE_VERSION
    return text_result, final_scores


# ─────────────────────────────────────────────────────────────
# 覆核标黄规则
# ─────────────────────────────────────────────────────────────

def review_flags(item):
    """返回该条目需要老师多看一眼的原因列表（空列表=无异常）。
    原则：宁可老师多看一眼，不能带错发出去。"""
    flags = []
    pre = item.get("precheck") or {}
    if isinstance(pre, str):
        pre = json.loads(pre)
    metrics = item.get("metrics") or {}
    if isinstance(metrics, str):
        metrics = json.loads(metrics)
    ai = item.get("ai_result") or {}
    if isinstance(ai, str):
        ai = json.loads(ai)
    dims = ai.get("dimensions", {})
    fs = item.get("final_scores") or {}
    if isinstance(fs, str):
        fs = json.loads(fs)

    if item.get("status") == "failed":
        return ["批改失败：" + (item.get("error_msg") or "")]

    # 1. 准入检查异常
    if pre.get("duration_ok") is False:
        flags.append(f"超时长（{metrics.get('duration_sec', '?')}秒 > 240秒）")
    if pre.get("chinese_ok") is False:
        flags.append(f"英文夹杂约{round(metrics.get('en_char_ratio', 0)*100)}% > 10%")
    if dims.get("video", {}).get("face_ok") is False:
        flags.append("关键帧未见面容出镜（请亲眼确认）")

    # 2. 口语指标与 AI 建议档矛盾
    sp = dims.get("speaking", {})
    sp_grade = sp.get("grade")
    rate = metrics.get("speech_rate_cpm", 0)
    pauses = metrics.get("long_pause_count", 0)
    fillers = metrics.get("filler_count", 0)
    bad_signals = ((rate and not (SPEECH_RATE_COMFORT[0] <= rate <= SPEECH_RATE_COMFORT[1]))
                   + (pauses >= PAUSE_MANY) + (fillers >= FILLER_MANY))
    if sp_grade == "A" and bad_signals >= 1:
        flags.append("口语建议A档但客观指标有异常，请抽听")
    if sp_grade == "D" and bad_signals == 0:
        flags.append("口语建议D档但客观指标正常，请抽听")

    # 3. 总分贴档边界 / 极端值
    total = sum(v for v in fs.values() if isinstance(v, (int, float)))
    if total >= 54:
        flags.append(f"总分极高（{total}/60），请确认")
    if 0 < total <= 24:
        flags.append(f"总分极低（{total}/60），请确认")

    # 4. 拍摄维度缺评
    if dims.get("video", {}).get("confidence") == "none":
        flags.append("拍摄维度缺关键帧未评，请手评")
    return flags


def total_of(final_scores):
    if not final_scores:
        return 0
    if isinstance(final_scores, str):
        final_scores = json.loads(final_scores)
    return sum(v for v in final_scores.values() if isinstance(v, (int, float)))
