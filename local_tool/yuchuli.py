# -*- coding: utf-8 -*-
"""
短视频批改 · 本地预处理工具  yuchuli.py
========================================
用途：把学生视频（大文件）在老师电脑上本地处理成"轻量批改包"：
  - 音频 mp3（复核时抽听用，约 1MB/份）
  - 转写稿 transcript.json（带时间戳，AI 批改的主要依据）
  - 口语客观指标（语速/停顿/填充词/华英比例，本地算好）
  - 关键帧 8 张 jpg（GLM-4V 判"拍摄与呈现"和面容出镜）
  - 准入检查预判（时长/华文比例）
视频原片不上传、不出电脑。——PDPA 原则与作文照片一致。

命名规则：个人 = 学号.mp4（如 05.mp4）；两人组 = 学号_学号.mp4（如 05_12.mp4）
支持格式：mp4 / mov / m4v / avi / mkv / webm

用法：
  python yuchuli.py 视频文件夹路径
  python yuchuli.py 视频文件夹路径 --model medium   （small 不够准时再用）

输出：视频文件夹旁生成 批改包_YYYYMMDD_HHMM.zip，上传到批改平台即可。

# 2026-07-20 决策：ASR 在本地跑（faster-whisper），转写稿+指标随包上传，
#   平台侧 DeepSeek 只读文本、GLM-4V 只看关键帧，不需要新增任何 API 供应商。
"""

import argparse
import datetime
import json
import re
import shutil
import subprocess
import sys
import wave
import zipfile
from pathlib import Path

VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"}
N_FRAMES = 8               # 关键帧张数
MAX_DURATION_SEC = 240     # 准入检查：时长 ≤ 4 分钟
EN_RATIO_LIMIT = 0.10      # 准入检查：夹杂英文 ≤ 10%
PAUSE_THRESHOLD = 1.5      # 超过 1.5 秒的语间停顿计为一次明显停顿
FILLERS = ["嗯", "呃", "啊这", "那个那个", "就是就是"]

TOOL_VERSION = "v1.0-20260720"


def get_ffmpeg():
    """优先用系统 ffmpeg；没有则用 imageio-ffmpeg 自带的。"""
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        sys.exit("找不到 ffmpeg。请先运行：pip install imageio-ffmpeg")


def run(cmd):
    p = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    if p.returncode != 0:
        raise RuntimeError(p.stderr.decode("utf-8", errors="ignore")[-800:])


def parse_student_ids(stem):
    """'05' -> ['05']；'05_12'（两人组）-> ['05','12']。
    Windows 重复下载的 '05 (1)' 括号编号会被剔除，不会误判成两人组。"""
    stem = re.sub(r"\(\d+\)", "", stem)   # 2026-07-20 决策：括号数字=重复下载序号，忽略
    parts = re.findall(r"\d+", stem)
    return parts if parts else [stem]


def extract_audio(ffmpeg, video, out_dir):
    """抽 16kHz 单声道 wav（给转写）+ 32kbps mp3（随包给老师抽听）。"""
    wav = out_dir / "audio.wav"
    mp3 = out_dir / "audio.mp3"
    run([ffmpeg, "-y", "-i", str(video), "-vn",
         "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav)])
    run([ffmpeg, "-y", "-i", str(wav),
         "-c:a", "libmp3lame", "-b:a", "32k", str(mp3)])
    with wave.open(str(wav), "rb") as w:
        duration = w.getnframes() / float(w.getframerate())
    return wav, mp3, duration


def extract_frames(ffmpeg, video, out_dir, duration):
    """按时间等距抽 N_FRAMES 张关键帧，缩到 960px 宽。"""
    frames = []
    for i in range(N_FRAMES):
        t = duration * (i + 0.5) / N_FRAMES
        out = out_dir / f"frame_{i+1:02d}.jpg"
        run([ffmpeg, "-y", "-ss", f"{t:.2f}", "-i", str(video),
             "-frames:v", "1", "-vf", "scale=960:-2", "-q:v", "5", str(out)])
        frames.append(out.name)
    return frames


def transcribe(model, wav):
    """faster-whisper 转写，返回 segments 列表（含词级时间戳）。"""
    segments, info = model.transcribe(
        str(wav),
        language="zh",
        vad_filter=True,
        word_timestamps=True,
        initial_prompt="以下是新加坡中学生用简体中文华语讲解一项技能的短视频，可能夹杂少量英文。",
    )
    segs = []
    for s in segments:
        segs.append({
            "start": round(s.start, 2),
            "end": round(s.end, 2),
            "text": s.text.strip(),
            "words": [{"w": w.word, "s": round(w.start, 2), "e": round(w.end, 2)}
                      for w in (s.words or [])],
        })
    return segs


def compute_metrics(segs, duration):
    """口语客观指标：仅作教师复核参考，不直接决定分数。"""
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
        "en_char_ratio": en_ratio,                # 英文字符占比（近似值）
        "speech_rate_cpm": speech_rate,           # 语速：字/分钟（180-260 为舒适区）
        "speech_time_sec": round(speech_time, 1),
        "long_pauses": pauses,                    # ≥1.5s 的停顿位置
        "long_pause_count": len(pauses),
        "filler_count": filler_count,             # 嗯/呃 等填充词次数
        "ranhou_count": ranhou,                   # "然后"出现次数（口头禅指标）
    }


def precheck(metrics):
    """准入检查预判：任何一项 fail 平台上会标黄，最终由老师裁定。"""
    return {
        "duration_ok": metrics["duration_sec"] <= MAX_DURATION_SEC,
        "chinese_ok": metrics["en_char_ratio"] <= EN_RATIO_LIMIT,
        # 面容出镜由平台侧 GLM-4V 看关键帧判断；脚本提交/命名规范由老师人工勾选
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", help="学生视频所在文件夹")
    ap.add_argument("--model", default="small",
                    help="whisper 模型：small（默认，较快）/ medium（更准，较慢）")
    args = ap.parse_args()

    src = Path(args.folder).expanduser()
    if not src.is_dir():
        sys.exit(f"文件夹不存在：{src}")

    videos = sorted(p for p in src.iterdir() if p.suffix.lower() in VIDEO_EXTS)
    if not videos:
        sys.exit("文件夹里没找到视频文件（支持 mp4/mov/m4v/avi/mkv/webm）")

    ffmpeg = get_ffmpeg()
    print(f"找到 {len(videos)} 个视频。正在加载转写模型（首次运行需下载，请耐心等待）…")
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        sys.exit("缺少转写库。请先运行：pip install faster-whisper")
    model = WhisperModel(args.model, device="cpu", compute_type="int8")
    print(f"模型 {args.model} 加载完成。开始处理：\n")

    work = src.parent / "_批改包_临时"
    work.mkdir(exist_ok=True)
    manifest = {"tool_version": TOOL_VERSION,
                "created_at": datetime.datetime.now().isoformat(timespec="seconds"),
                "whisper_model": args.model,
                "items": []}
    failed = []

    for idx, video in enumerate(videos, 1):
        stem = video.stem
        ids = parse_student_ids(stem)
        item_dir = work / "_".join(ids)
        done_flag = item_dir / "transcript.json"
        if done_flag.exists():
            print(f"[{idx}/{len(videos)}] {video.name} 已处理过，跳过")
            with open(done_flag, encoding="utf-8") as f:
                manifest["items"].append(json.load(f)["summary"])
            continue

        print(f"[{idx}/{len(videos)}] {video.name} …", end=" ", flush=True)
        try:
            item_dir.mkdir(exist_ok=True)
            wav, mp3, duration = extract_audio(ffmpeg, video, item_dir)
            frames = extract_frames(ffmpeg, video, item_dir, duration)
            print(f"音频/帧完成，转写中（约{duration/60:.1f}分钟素材）…", end=" ", flush=True)
            segs = transcribe(model, wav)
            metrics = compute_metrics(segs, duration)
            checks = precheck(metrics)
            summary = {"student_ids": ids, "source_file": video.name,
                       "metrics": metrics, "precheck": checks, "frames": frames}
            with open(item_dir / "transcript.json", "w", encoding="utf-8") as f:
                json.dump({"summary": summary, "segments": segs},
                          f, ensure_ascii=False, indent=1)
            wav.unlink()  # wav 只是转写中间产物，不进包
            manifest["items"].append(summary)
            flag = "" if all(checks.values()) else "  ⚠ 准入检查有异常"
            print(f"完成 ✓{flag}")
        except Exception as e:
            print(f"失败 ✗  ({e})")
            failed.append(video.name)
            shutil.rmtree(item_dir, ignore_errors=True)

    with open(work / "manifest.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)

    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M")
    zip_path = src.parent / f"批改包_{stamp}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(work.rglob("*")):
            if p.is_file():
                zf.write(p, p.relative_to(work))

    size_mb = zip_path.stat().st_size / 1024 / 1024
    print(f"\n全部完成：成功 {len(manifest['items'])} 份，失败 {len(failed)} 份")
    if failed:
        print("失败清单（可修复后重跑，已完成的会自动跳过）：", "、".join(failed))
    print(f"批改包已生成：{zip_path}（{size_mb:.0f} MB）")
    print("下一步：打开批改平台网页，上传这个 zip 即可。视频原片无需上传。")


if __name__ == "__main__":
    main()
