# -*- coding: utf-8 -*-
"""
video_engine.py — 短视频批改引擎（2026-07-20 常年化改版）
════════════════════════════════════════════════════
- 按任务快照的评分标准(rubric)动态批改：
    text / text_speech 维度 → DeepSeek 读转写稿+指标
    frames 维度            → GLM-4V 看关键帧拼贴图（单图，flash 兼容）
    manual 维度            → AI 不评，标"请老师手评"进覆核
- parse_rubric_text()：DeepSeek 把老师粘贴的评分表解析成结构化模板
- 两个客户端 180 秒硬超时；HTTP 报错带 API 名和响应体详情
- 拍摄/画面维度批改失败降级不拖垮整份（2026-07-20 修复保留）
- review_flags(item, rubric)：覆核标黄规则按模板配置通用化
"""

import base64
import io
import json
import re
import urllib.request
import urllib.error

from video_rubric import (dims, dim_by_key, dims_of_judge, total_max,
                          level_of, normalize_rubric, validate_rubric,
                          SPEECH_RATE_COMFORT, PAUSE_MANY, FILLER_MANY)
from video_prompts import (build_text_grading_system, build_text_grading_user,
                           build_frames_prompt, RUBRIC_PARSE_SYSTEM)

ENGINE_VERSION = "video-2.0-20260720"
TIMEOUT_SEC = 180  # 硬超时，防止单份卡死整条队列

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
        try:
            body = e.read().decode("utf-8", errors="ignore")[:300]
        except Exception:
            body = ""
        raise RuntimeError(f"{label} HTTP {e.code}: {body}") from None


def _extract_json(text):
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


# ─────────────────────────────────────────────────────────────
# 评分表解析（新建模板用）
# ─────────────────────────────────────────────────────────────

def parse_rubric_text(deepseek_key, raw_text):
    """老师粘贴的评分表文字 → 结构化模板。返回 (rubric, 校验问题清单)。"""
    payload = {
        "model": "deepseek-chat", "temperature": 0.2, "max_tokens": 4000,
        "messages": [
            {"role": "system", "content": RUBRIC_PARSE_SYSTEM},
            {"role": "user", "content": raw_text[:12000]},
        ],
    }
    data = _post_json(DEEPSEEK_URL, deepseek_key, payload, label="DeepSeek")
    rubric = _extract_json(data["choices"][0]["message"]["content"])
    normalize_rubric(rubric)
    return rubric, validate_rubric(rubric)


# ─────────────────────────────────────────────────────────────
# 批改
# ─────────────────────────────────────────────────────────────

def grade_text(deepseek_key, rubric, requirements, topic, student_ids,
               segments, metrics):
    payload = {
        "model": "deepseek-chat",
        "temperature": 0.2,
        "max_tokens": 4000,
        "messages": [
            {"role": "system",
             "content": build_text_grading_system(rubric, requirements)},
            {"role": "user",
             "content": build_text_grading_user(topic, student_ids,
                                                segments, metrics)},
        ],
    }
    data = _post_json(DEEPSEEK_URL, deepseek_key, payload, label="DeepSeek")
    return _extract_json(data["choices"][0]["message"]["content"])


def _contact_sheet(frame_paths):
    """glm-4v-flash 单图限制：8 帧拼成 2×4 拼贴图。返回 jpeg bytes。"""
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


def grade_frames(glm_key, rubric, requirements, frame_paths, n_students):
    b64 = base64.b64encode(_contact_sheet(frame_paths)).decode()
    prompt = build_frames_prompt(rubric, len(frame_paths[:8]), n_students,
                                 requirements)
    payload = {
        "model": "glm-4v-flash",
        "temperature": 0.2,
        "messages": [{"role": "user", "content": [
            {"type": "image_url",
             "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
            {"type": "text", "text": prompt},
        ]}],
    }
    data = _post_json(GLM_URL, glm_key, payload, label="GLM(智谱)")
    return _extract_json(data["choices"][0]["message"]["content"])


def _clamp_dim(dim, score):
    try:
        s = int(round(float(score)))
    except (TypeError, ValueError):
        s = 0
    return max(0, min(dim["max"], s))


def grade_one_item(item, frames_dir, deepseek_key, glm_key, topic,
                   rubric, requirements):
    """批一份。返回 (ai_result, final_scores)。异常抛给调用方记 failed。"""
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

    text_dims = dims_of_judge(rubric, ("text", "text_speech"))
    frame_dims = dims_of_judge(rubric, ("frames",))
    manual_dims = dims_of_judge(rubric, ("manual",))

    result = {"dimensions": {}}
    final_scores = {}

    # 文本类维度（主力）
    if text_dims:
        text_result = grade_text(deepseek_key, rubric, requirements, topic,
                                 student_ids, segments, metrics)
        result.update({k: v for k, v in text_result.items()
                       if k != "dimensions"})
        for d in text_dims:
            info = text_result.get("dimensions", {}).get(d["key"], {}) or {}
            sc = _clamp_dim(d, info.get("score"))
            info["score"] = sc
            info["grade"] = level_of(d, sc)["grade"]
            result["dimensions"][d["key"]] = info
            final_scores[d["key"]] = sc

    # 画面类维度（失败降级不拖垮整份）
    if frame_dims:
        frames_error = ""
        frames_result = None
        if frames_dir is not None:
            frame_paths = sorted(frames_dir.glob("frame_*.jpg"))
            if frame_paths:
                try:
                    frames_result = grade_frames(glm_key, rubric,
                                                 requirements, frame_paths,
                                                 len(student_ids))
                except Exception as e:
                    frames_error = str(e)[:200]
        for d in frame_dims:
            if frames_result:
                info = (frames_result.get("dimensions", {})
                        .get(d["key"], {}) or {})
                sc = _clamp_dim(d, info.get("score"))
                result["dimensions"][d["key"]] = {
                    "score": sc, "grade": level_of(d, sc)["grade"],
                    "confidence": ("high"
                                   if frames_result.get("face_ok", True)
                                   else "low"),
                    "comment": frames_result.get("comment", ""),
                    "note": frames_result.get("note", ""),
                    "face_ok": frames_result.get("face_ok"),
                }
                final_scores[d["key"]] = sc
            else:
                reason = (f"GLM批改失败（{frames_error}）" if frames_error
                          else "缺关键帧，AI 未评")
                result["dimensions"][d["key"]] = {
                    "score": 0, "grade": "?", "confidence": "none",
                    "comment": f"{reason}，请老师看视频后手评",
                    "face_ok": None}
                final_scores[d["key"]] = 0

    # 老师手评维度
    for d in manual_dims:
        result["dimensions"][d["key"]] = {
            "score": 0, "grade": "?", "confidence": "none",
            "comment": "此维度设定为老师手评，AI 不评"}
        final_scores[d["key"]] = 0

    result["engine_version"] = ENGINE_VERSION
    return result, final_scores


# ─────────────────────────────────────────────────────────────
# 覆核标黄规则（按模板配置通用化）
# ─────────────────────────────────────────────────────────────

def review_flags(item, rubric):
    """返回需要老师多看一眼的原因列表。宁可老师多看一眼，不能带错发出去。"""
    flags = []
    pre_cfg = rubric.get("precheck", {})
    pre = item.get("precheck") or {}
    if isinstance(pre, str):
        pre = json.loads(pre)
    metrics = item.get("metrics") or {}
    if isinstance(metrics, str):
        metrics = json.loads(metrics)
    ai = item.get("ai_result") or {}
    if isinstance(ai, str):
        ai = json.loads(ai)
    dims_r = ai.get("dimensions", {})
    fs = item.get("final_scores") or {}
    if isinstance(fs, str):
        fs = json.loads(fs)

    if item.get("status") == "failed":
        return ["批改失败：" + (item.get("error_msg") or "")]

    # 1. 准入检查异常（按模板配置）
    if pre_cfg.get("max_duration_sec") and pre.get("duration_ok") is False:
        flags.append(f"超时长（{metrics.get('duration_sec', '?')}秒 > "
                     f"{pre_cfg['max_duration_sec']}秒）")
    if pre_cfg.get("en_ratio_limit", 1.0) < 1.0 and pre.get("chinese_ok") is False:
        flags.append(f"英文夹杂约{round(metrics.get('en_char_ratio', 0)*100)}% "
                     f"> {round(pre_cfg['en_ratio_limit']*100)}%")
    if pre_cfg.get("face_required"):
        for k, info in dims_r.items():
            if info.get("face_ok") is False:
                flags.append("关键帧未见面容出镜（请亲眼确认）")
                break

    # 2. 口语类维度：指标与 AI 建议档矛盾
    speech_keys = [d["key"] for d in dims_of_judge(rubric, ("text_speech",))]
    rate = metrics.get("speech_rate_cpm", 0)
    pauses = metrics.get("long_pause_count", 0)
    fillers = metrics.get("filler_count", 0)
    bad_signals = ((1 if rate and not (SPEECH_RATE_COMFORT[0] <= rate
                                       <= SPEECH_RATE_COMFORT[1]) else 0)
                   + (1 if pauses >= PAUSE_MANY else 0)
                   + (1 if fillers >= FILLER_MANY else 0))
    for k in speech_keys:
        d = dim_by_key(rubric, k)
        g = dims_r.get(k, {}).get("grade")
        top_grade = d["levels"][0]["grade"] if d and d.get("levels") else "A"
        low_grade = d["levels"][-1]["grade"] if d and d.get("levels") else "D"
        if g == top_grade and bad_signals >= 1:
            flags.append(f"「{d['name']}」建议最高档但客观指标有异常，请抽听")
        if g == low_grade and bad_signals == 0:
            flags.append(f"「{d['name']}」建议最低档但客观指标正常，请抽听")

    # 3. 总分极端（按满分比例：≥90% 或 ≤40%）
    tm = total_max(rubric)
    total = sum(v for v in fs.values() if isinstance(v, (int, float)))
    if tm and total >= tm * 0.9:
        flags.append(f"总分极高（{total}/{tm}），请确认")
    if tm and 0 < total <= tm * 0.4:
        flags.append(f"总分极低（{total}/{tm}），请确认")

    # 4. AI 未评的维度（画面缺帧 / 手评类）
    for k, info in dims_r.items():
        if info.get("confidence") == "none":
            d = dim_by_key(rubric, k)
            flags.append(f"「{d['name'] if d else k}」AI 未评，请手评")
    return flags


def total_of(final_scores):
    if not final_scores:
        return 0
    if isinstance(final_scores, str):
        final_scores = json.loads(final_scores)
    return sum(v for v in final_scores.values() if isinstance(v, (int, float)))
