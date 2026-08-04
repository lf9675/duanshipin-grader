# -*- coding: utf-8 -*-
"""
video_engine.py — 短视频批改引擎（2026-07-20 常年化改版）
════════════════════════════════════════════════════
- 按任务快照的评分标准(rubric)动态批改：
    text / text_speech 维度 → DeepSeek 读转写稿+指标
    frames 维度            → GLM-4V 看关键帧拼贴图（单图，flash 兼容）
    manual 维度            → AI 不评，标"请老师手评"进复核
- parse_rubric_text()：DeepSeek 把老师粘贴的评分表解析成结构化模板
- 两个客户端 180 秒硬超时；HTTP 报错带 API 名和响应体详情
- 拍摄/画面维度批改失败降级不拖垮整份（2026-07-20 修复保留）
- review_flags(item, rubric)：复核标黄规则按模板配置通用化

# 2026-07-28 决策（刘老师）：新增「照读封顶」——明显捧着 iPad/手机/讲稿照念的，
# 口语类维度（judge="text_speech"）封顶到该维度「及格」档上限
# （默认《短视频评分表》= 12/20）。
# 实现要点（三条，改动前务必读）：
#   1. 封顶在 finalize_video_scores() 里由代码执行，不交给 AI 自行减分——
#      作文平台已实证 AI 会"嘴上承认问题、手上照给高分"。
#   2. 判定采【甲案】：画面证据是必要条件。只有文本迹象、画面拿不到实锤的，
#      一律只标黄请老师抽听，不自动扣分（宁可漏判，不误伤背熟讲稿的学生）。
#   3. 防误伤硬闸：本作业是"讲解技能"，教电子绘画/剪辑/App/照食谱做菜的学生
#      手里本来就该拿设备。设备若是演示对象（device_is_demo_subject=true），
#      一律不算照读。
# 安全降级：画面维度批改失败/缺关键帧/模板无 frames 维度 → 拿不到画面证据
#           → 永不封顶，只标黄。
"""

import base64
import io
import json
import re
import urllib.request
import urllib.error

from video_rubric import (dims, dim_by_key, dims_of_judge, total_max,
                          level_of, normalize_rubric, validate_rubric,
                          pass_ceiling, total_level_of, member_mode,
                          reading_cap_total,
                          SPEECH_RATE_COMFORT, PAUSE_MANY, FILLER_MANY)
from video_prompts import (build_text_grading_system, build_text_grading_user,
                           build_frames_prompt, RUBRIC_PARSE_SYSTEM)

ENGINE_VERSION = "video-2.2-20260804"
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
                blob = text[start:i + 1]
                # 2026-07-21 修复：GLM 偶发返回带尾逗号的 JSON，容错清理
                blob = re.sub(r",\s*([}\]])", r"\1", blob)
                return json.loads(blob)
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


# ─────────────────────────────────────────────────────────────
# 照读判定与封顶（2026-07-28 刘老师裁定 · 甲案）
# ─────────────────────────────────────────────────────────────

DELIVERY_OBVIOUS = "明显照读"
DELIVERY_SUSPECT = "疑似照读"
DELIVERY_NATURAL = "自然讲述"


def _as_bool(v):
    """AI 可能回 true/false/"true"/"是"/None，统一成布尔。存疑一律 False。"""
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in ("true", "yes", "1", "是", "有")
    return False


def judge_reading(text_gate, frames_gate, frames_available):
    """合并文本与画面证据，判定照读程度。

    返回 dict：{delivery_mode, reason, text_signal, visual, evidence}

    甲案判定表（画面证据是封顶的必要条件）：
      画面实锤(手持非演示设备 + 视线全程) + 文本有迹象  → 明显照读（封顶）
      画面实锤 + 文本无迹象                              → 疑似（只标黄）
      视线间歇                                           → 疑似（只标黄）
      无画面证据 + 文本迹象强                            → 疑似（只标黄）
      其余                                               → 自然讲述
    """
    text_gate = text_gate or {}
    frames_gate = frames_gate or {}

    text_signal = str(text_gate.get("text_signal", "无")).strip() or "无"
    if text_signal not in ("强", "弱", "无"):
        text_signal = "无"

    in_hand = _as_bool(frames_gate.get("device_in_hand"))
    is_demo = _as_bool(frames_gate.get("device_is_demo_subject"))
    gaze = str(frames_gate.get("gaze_on_device", "无")).strip() or "无"
    if gaze not in ("全程", "间歇", "无"):
        gaze = "无"

    # 防误伤硬闸：设备本身就是被讲解的对象 → 一律不算照读
    device_is_script = in_hand and not is_demo

    visual_hard = frames_available and device_is_script and gaze == "全程"
    visual_soft = frames_available and device_is_script and gaze == "间歇"

    ev_parts = []
    if frames_gate.get("evidence"):
        ev_parts.append(f"画面：{frames_gate['evidence']}")
    if text_gate.get("basis"):
        ev_parts.append(f"转写稿：{text_gate['basis']}")
    evidence = "；".join(ev_parts)

    if visual_hard and text_signal in ("强", "弱"):
        mode, reason = DELIVERY_OBVIOUS, "画面显示手持非演示设备且视线全程锁屏，转写稿亦呈书面语朗读特征"
    elif visual_hard:
        mode, reason = DELIVERY_SUSPECT, "画面像在照念，但转写稿口语感自然——请老师抽听确认"
    elif visual_soft:
        mode, reason = DELIVERY_SUSPECT, "画面显示间歇看稿（可能只是瞄提纲）——请老师抽听确认"
    elif text_signal == "强" and not frames_available:
        mode, reason = DELIVERY_SUSPECT, "转写稿呈明显书面语朗读特征，但无画面证据可佐证——请老师抽听确认"
    elif text_signal == "强" and in_hand and is_demo:
        # 手里那台设备已判定为演示道具（如教电子绘画/剪辑/照食谱做菜）。
        # 不据此封顶，但转写稿仍很像稿子——可能在念画外的讲稿，请老师抽听。
        mode, reason = DELIVERY_SUSPECT, ("画面中的设备已判定为演示道具、不作照读依据；"
                                          "但转写稿呈书面语朗读特征，可能另有画外讲稿——请老师抽听确认")
    elif text_signal == "强":
        mode, reason = DELIVERY_SUSPECT, "转写稿呈书面语朗读特征，但画面未见照念——可能是讲稿背熟，请老师抽听确认"
    else:
        mode, reason = DELIVERY_NATURAL, ""

    return {"delivery_mode": mode, "reason": reason,
            "text_signal": text_signal,
            "visual": {"device_in_hand": in_hand,
                       "device_is_demo_subject": is_demo,
                       "gaze_on_device": gaze,
                       "frames_available": bool(frames_available)},
            "evidence": evidence}


def finalize_video_scores(rubric, result, final_scores, gate):
    """在代码里确定性执行照读封顶。就地修改 result / final_scores。

    只有 delivery_mode == 明显照读 才封顶；封顶目标 = 该维度「及格」档上限。
    审计串写进 result["auto_caps"]，复核页会显示，老师可直接改回。
    """
    caps = []
    result["reading_gate"] = gate
    if gate.get("delivery_mode") != DELIVERY_OBVIOUS:
        result["auto_caps"] = caps
        return caps

    for d in dims_of_judge(rubric, ("text_speech",)):
        k = d["key"]
        cur = final_scores.get(k)
        if not isinstance(cur, (int, float)):
            continue
        ceil = pass_ceiling(d)
        if cur > ceil:
            final_scores[k] = ceil
            info = result.setdefault("dimensions", {}).setdefault(k, {})
            info["score"] = ceil
            info["grade"] = level_of(d, ceil)["grade"]
            info["capped_by"] = "照读封顶"
            caps.append(f"「{d['name']}」判定为明显照读，"
                        f"AI 原给 {int(cur)} 分 → 按规则封顶 {ceil} 分"
                        f"（{level_of(d, ceil)['label']}档上限）")
    result["auto_caps"] = caps
    return caps


# ─────────────────────────────────────────────────────────────
# 小组个人分（2026-08-04 刘老师裁定 C 案）
# ════════════════════════════════════════════════════════════
# 规则来源（刘老师原话）："如果小组视频的分数等级为二上（40-44），
# 视频中表现优秀的同学（表达流利，完全脱稿）得分取上限 44；表现没那么好的
# 学生（结结巴巴，无法脱稿）得分取下限 40。"
#
# 为什么不做全自动逐人判定（2026-08-04 决策，重要）：
#   Whisper 转写稿【没有说话人标签】，AI 无法确定哪一段是第几位组员讲的；
#   GLM-4V 只能从静帧数出人数和谁手持设备，无法与语音对齐。硬做全自动
#   会安静地给错人扣分——比不做更糟。因此：AI 只出证据与预判，
#   老师在复核页逐人确认，本函数据此确定性算分。
# ─────────────────────────────────────────────────────────────

MEMBER_MARKS = {
    "top": "完全脱稿、表达流利",
    "mid": "基本脱稿、偶有卡顿",
    "low": "结结巴巴、无法脱稿",
    "reading": "明显照读 iPad/讲稿",
    "absent": "全程未出镜",
}
MEMBER_MARK_DEFAULT = "mid"


def member_score(rubric, group_total, mark):
    """组分定档 → 该档区间内按个人表现取位置。返回 (分数, 说明)。"""
    lv = total_level_of(rubric, group_total)
    if not lv:
        return int(group_total), "模板未配总分等级表，个人分沿用组分"
    lo, hi = int(lv["lo"]), int(lv["hi"])
    band = f"{lv['grade']}·{lv['label']}（{lo}-{hi}）"

    if mark == "absent":
        return 0, f"全程未出镜，不套用组分（组分档位 {band}），请老师单独处理"

    if mark == "reading":
        cap = reading_cap_total(rubric)
        base = lo
        if cap is not None and base > cap:
            return int(cap), (f"明显照读 → 取档底 {lo} 分后，"
                              f"按规则封顶 {cap} 分（组分档位 {band}）")
        return int(base), f"明显照读 → 取档底 {lo} 分（组分档位 {band}）"

    if mark == "top":
        return hi, f"完全脱稿、表达流利 → 取档顶 {hi} 分（{band}）"
    if mark == "low":
        return lo, f"结结巴巴、无法脱稿 → 取档底 {lo} 分（{band}）"
    return (lo + hi) // 2, f"基本脱稿、偶有卡顿 → 取档中 {(lo + hi) // 2} 分（{band}）"


def suggest_member_marks(ai_result, student_ids):
    """从 AI 的 members 证据预填每人的档内位置标记。拿不准一律给 mid。"""
    marks = {sid: MEMBER_MARK_DEFAULT for sid in student_ids}
    members = (ai_result or {}).get("members") or []
    if not isinstance(members, list):
        return marks
    for i, sid in enumerate(student_ids):
        m = members[i] if i < len(members) else None
        if not isinstance(m, dict):
            continue
        sf = str(m.get("script_free", "")).strip()
        fl = str(m.get("fluency", "")).strip()
        if sf == "明显念读":
            marks[sid] = "low"        # 预填只到 low；reading 需画面实锤，由老师定
        elif sf == "完全脱稿" and fl == "流利":
            marks[sid] = "top"
        elif fl == "结结巴巴":
            marks[sid] = "low"
    return marks


def compute_member_scores(rubric, group_total, student_ids, marks=None):
    """返回 {学号: {"mark","score","note"}}。marks 缺省一律 mid。"""
    marks = marks or {}
    out = {}
    for sid in student_ids:
        mk = marks.get(sid, MEMBER_MARK_DEFAULT)
        if mk not in MEMBER_MARKS:
            mk = MEMBER_MARK_DEFAULT
        sc, note = member_score(rubric, group_total, mk)
        out[sid] = {"mark": mk, "score": sc, "note": note}
    return out


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
    frames_result = None   # 2026-07-28：提到 if 外，模板无画面维度时也安全

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

    # 照读封顶（2026-07-28）：必须放在所有维度评完之后。
    # frames_available 为 False 时（GLM 失败/缺帧/模板无 frames 维度）
    # 拿不到画面实锤 → judge_reading 只会判到"疑似"，不会封顶。
    gate = judge_reading(
        text_gate=result.pop("reading_gate", None),
        frames_gate=(frames_result or {}).get("reading_gate")
        if frame_dims else None,
        frames_available=bool(frame_dims and frames_result))
    finalize_video_scores(rubric, result, final_scores, gate)

    result["engine_version"] = ENGINE_VERSION
    return result, final_scores


# ─────────────────────────────────────────────────────────────
# 复核标黄规则（按模板配置通用化）
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

    # 1.5 转写疑似失败（2026-07-21：语速异常低说明转写稿抓不到内容，
    #     残缺转写会导致所有文本维度冤枉学生，必须拦下）
    # 2026-07-21 实测修正：演示类视频（说一句做一段）语速天然低，
    # 不能只看语速就判转写失败——同时看总字数才有说服力
    _rate = metrics.get("speech_rate_cpm", 0)
    _dur = metrics.get("duration_sec", 0)
    _cjk = metrics.get("char_count_cjk", 0)
    if _dur >= 60 and _cjk < max(60, _dur * 0.3):
        flags.append(f"⚠转写内容极少（全片仅{_cjk}字）——可能转写失败，"
                     f"请核对转写稿；若确属失败请换 small 模型重传该视频")
    elif _dur >= 30 and 0 < _rate < 80:
        flags.append(f"语速偏低（{_rate}字/分）——若是做菜/演示类留白属正常，"
                     f"请顺带核对转写稿是否完整")

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

    # 2.5 照读判定（2026-07-28）：封顶了要让老师看见并可推翻；
    #     疑似的不扣分，但必须请老师抽听——这是甲案的核心
    gate = ai.get("reading_gate") or {}
    mode = gate.get("delivery_mode")
    if mode == DELIVERY_OBVIOUS:
        for c in (ai.get("auto_caps") or []):
            flags.append(f"🔒{c}（如判断有误，直接改回分数即可）")
        if not ai.get("auto_caps"):
            flags.append("🔒判定为明显照读（口语分本已在及格档内，未再下调）")
    elif mode == DELIVERY_SUSPECT:
        flags.append(f"❓疑似照读，未扣分：{gate.get('reason', '')}")

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
