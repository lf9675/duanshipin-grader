# -*- coding: utf-8 -*-
"""
video_ingest.py — 服务器端视频预处理
════════════════════════════════════
逻辑移植自 local_tool/yuchuli.py，改为在 Streamlit Cloud 服务器上跑：
  抽音频(mp3) → 抽关键帧(8张) → 本地模型转写 → 算口语指标 → 准入预判
原始视频【提取完成后立刻删除】——不等整条批改流程走完，留存时间最短。
只有 audio.mp3 + frame_*.jpg 留在会话临时目录（供覆核抽听/不入库），
transcript/metrics/precheck 三样纯文本才写入数据库。

2026-07-20 决策：云端算力有限，转写模型默认 tiny 档；classroom 场景
中文短语音准确率通常够用，如老师反馈转写质量不够，可在侧栏切换到
small（更准更慢）。ffmpeg 通过 packages.txt 装到系统，找不到时兜底
用 imageio_ffmpeg 自带的可执行文件。
"""

import re
import shutil
import subprocess
import sys
import wave
from pathlib import Path

import streamlit as st

VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"}
N_FRAMES = 8
MAX_DURATION_SEC = 240
EN_RATIO_LIMIT = 0.10
PAUSE_THRESHOLD = 1.5
FILLERS = ["嗯", "呃", "啊这", "那个那个", "就是就是"]


def get_ffmpeg():
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def _run(cmd):
    p = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    if p.returncode != 0:
        raise RuntimeError(p.stderr.decode("utf-8", errors="ignore")[-800:])


def parse_student_ids(stem):
    """'05' -> ['05']；'05_12'（两人组）-> ['05','12']；
    Windows 重复下载的 '05 (1)' 括号编号自动忽略。"""
    stem = re.sub(r"\(\d+\)", "", stem)
    parts = re.findall(r"\d+", stem)
    return parts if parts else [stem]


def extract_audio(ffmpeg, video_path, out_dir):
    wav = out_dir / "audio.wav"
    mp3 = out_dir / "audio.mp3"
    _run([ffmpeg, "-y", "-i", str(video_path), "-vn",
         "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav)])
    _run([ffmpeg, "-y", "-i", str(wav),
         "-c:a", "libmp3lame", "-b:a", "32k", str(mp3)])
    with wave.open(str(wav), "rb") as w:
        duration = w.getnframes() / float(w.getframerate())
    return wav, mp3, duration


def extract_frames(ffmpeg, video_path, out_dir, duration):
    frames = []
    for i in range(N_FRAMES):
        t = duration * (i + 0.5) / N_FRAMES
        out = out_dir / f"frame_{i+1:02d}.jpg"
        _run([ffmpeg, "-y", "-ss", f"{t:.2f}", "-i", str(video_path),
             "-frames:v", "1", "-vf", "scale=960:-2", "-q:v", "5", str(out)])
        frames.append(out.name)
    return frames


@st.cache_resource(show_spinner=False)
def _load_whisper_model(model_size):
    from faster_whisper import WhisperModel
    return WhisperModel(model_size, device="cpu", compute_type="int8")


def transcribe(model, wav):
    segments, info = model.transcribe(
        str(wav), language="zh", vad_filter=True, word_timestamps=False,
        initial_prompt="以下是新加坡中学生用简体中文华语讲解一项技能的短视频，可能夹杂少量英文。",
    )
    return [{"start": round(s.start, 2), "end": round(s.end, 2),
            "text": s.text.strip()} for s in segments]


def compute_metrics(segs, duration):
    full_text = "".join(s["text"] for s in segs)
    cjk = len(re.findall(r"[\u4e00-\u9fff]", full_text))
    latin = len(re.findall(r"[A-Za-z]", full_text))
    en_ratio = round(latin / (latin + cjk), 3) if (latin + cjk) else 0.0
    speech_time = sum(s["end"] - s["start"] for s in segs)
    speech_rate = round(cjk / (speech_time / 60), 1) if speech_time > 0 else 0.0
    pauses = []
    for a, b in zip(segs, segs[1:]):
        gap = b["start"] - a["end"]
        if gap >= PAUSE_THRESHOLD:
            pauses.append({"at": round(a["end"], 1), "sec": round(gap, 1)})
    filler_count = sum(full_text.count(f) for f in FILLERS)
    ranhou = full_text.count("然后")
    return {
        "duration_sec": round(duration, 1),
        "char_count_cjk": cjk,
        "en_char_ratio": en_ratio,
        "speech_rate_cpm": speech_rate,
        "speech_time_sec": round(speech_time, 1),
        "long_pauses": pauses,
        "long_pause_count": len(pauses),
        "filler_count": filler_count,
        "ranhou_count": ranhou,
    }


def precheck(metrics, pre_cfg=None):
    """按模板配置预判（2026-07-20 常年化）。上限为 0/1.0 表示不限。"""
    cfg = pre_cfg or {}
    max_dur = cfg.get("max_duration_sec", MAX_DURATION_SEC)
    en_lim = cfg.get("en_ratio_limit", EN_RATIO_LIMIT)
    return {
        "duration_ok": (metrics["duration_sec"] <= max_dur) if max_dur else True,
        "chinese_ok": (metrics["en_char_ratio"] <= en_lim) if en_lim < 1.0 else True,
    }


def preprocess_video(video_path, out_dir, model_size="tiny", pre_cfg=None):
    """核心入口：抽音频/帧 → 转写 → 指标 → 准入预判。
    调用方负责在此函数成功返回后【立刻删除 video_path 原视频】。
    返回 dict：{segments, metrics, precheck, frames}"""
    out_dir.mkdir(parents=True, exist_ok=True)
    ffmpeg = get_ffmpeg()
    wav, mp3, duration = extract_audio(ffmpeg, video_path, out_dir)
    frames = extract_frames(ffmpeg, video_path, out_dir, duration)
    model = _load_whisper_model(model_size)
    segs = transcribe(model, wav)
    metrics = compute_metrics(segs, duration)
    checks = precheck(metrics, pre_cfg)
    wav.unlink(missing_ok=True)  # 转写中间产物，不留
    return {"segments": segs, "metrics": metrics, "precheck": checks,
            "frames": frames}
