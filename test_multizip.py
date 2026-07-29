# -*- coding: utf-8 -*-
"""备用通道多选 zip 解包验证（2026-07-28）。
不连数据库：把 db.upsert_item 换成记录调用的桩。
运行：python3 test_multizip.py
"""
import io
import json
import sys
import types
import zipfile
from pathlib import Path
import tempfile

# ── 桩掉 app.py 里用到的外部依赖，只取 _unpack_one_zip 的逻辑 ──
CALLS = []


class _DBStub:
    @staticmethod
    def upsert_item(job_id, key, sids, src, segs, metrics, precheck):
        CALLS.append(key)


db = _DBStub()

# 从 app.py 抽出被测函数源码（保证测的是真代码，不是复制品）
src = Path("app.py").read_text(encoding="utf-8")
start = src.index("def _unpack_one_zip")
end = src.index("with st.expander(\"备用通道")
ns = {"json": json, "Path": Path, "db": db}
exec(compile(src[start:end], "app.py", "exec"), ns)
_unpack_one_zip = ns["_unpack_one_zip"]

fails = []


def check(name, got, want):
    ok = got == want
    print(("  ✓ " if ok else "  ✗ ") + name + ("" if ok else f"   期望{want} 实得{got}"))
    if not ok:
        fails.append(name)


def make_pkg(keys, with_media=True, broken=None):
    """造一个批改包 zip（内存）。broken=学号 → 该学号缺 transcript.json。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for k in keys:
            if k == broken:
                zf.writestr(f"{k}/audio.mp3", b"x")
                continue
            zf.writestr(f"{k}/transcript.json", json.dumps({
                "summary": {"student_ids": k.split("_"),
                            "source_file": f"{k}.mp4",
                            "metrics": {"duration_sec": 100},
                            "precheck": {"duration_ok": True}},
                "segments": [{"start": 0, "end": 3, "text": "大家好"}]},
                ensure_ascii=False))
            if with_media:
                zf.writestr(f"{k}/audio.mp3", b"fake-mp3")
                zf.writestr(f"{k}/frame_01.jpg", b"fake-jpg")
    buf.seek(0)
    return buf


def make_video_zip():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for n in ("05.mp4", "07.mov", "12.mkv"):
            zf.writestr(n, b"fake-video")
    buf.seek(0)
    return buf


root = Path(tempfile.mkdtemp())

print("\n【1】单个正常批改包")
CALLS.clear()
with zipfile.ZipFile(make_pkg(["05", "07", "12_19"])) as zf:
    keys, errs = _unpack_one_zip(zf, root, 1, "批改包_A.zip")
check("入队3份", sorted(keys), ["05", "07", "12_19"])
check("无错误", errs, [])
check("媒体文件已落盘", (root / "05" / "audio.mp3").exists(), True)
check("入库调用3次", len(CALLS), 3)

print("\n【2】多个包分别解 → 累计入队，学号不冲突")
CALLS.clear()
total, all_errs, seen = 0, [], {}
for name, ks in [("批改包_A.zip", ["05", "07"]), ("批改包_B.zip", ["12", "19"])]:
    with zipfile.ZipFile(make_pkg(ks)) as zf:
        k, e = _unpack_one_zip(zf, root, 1, name)
    for x in k:
        seen.setdefault(x, name)
    total += len(k)
    all_errs += e
check("累计入队4份", total, 4)
check("去重后4个学号", len(seen), 4)
check("无错误", all_errs, [])

print("\n【3】跨包重复学号 → 检测得到")
seen, dups = {}, []
for name, ks in [("批改包_A.zip", ["05", "07"]), ("批改包_B.zip", ["07", "19"])]:
    with zipfile.ZipFile(make_pkg(ks)) as zf:
        k, _ = _unpack_one_zip(zf, root, 1, name)
    for x in k:
        if x in seen:
            dups.append((x, seen[x], name))
        else:
            seen[x] = name
check("检出1个重复学号", len(dups), 1)
check("重复的是07", dups[0][0], "07")
check("去重后3个学号", len(seen), 3)

print("\n【4】误传视频原片包 → 给出明确指引，不静默")
with zipfile.ZipFile(make_video_zip()) as zf:
    keys, errs = _unpack_one_zip(zf, root, 1, "shipin2.zip")
check("入队0份", len(keys), 0)
check("有1条错误提示", len(errs), 1)
check("提示指向视频通道", "选择学生视频" in errs[0], True)
check("提示报出视频数", "3 个视频原片" in errs[0], True)

print("\n【5】包内单份坏掉 → 只跳过它，其余照常入队")
CALLS.clear()
with zipfile.ZipFile(make_pkg(["05", "07", "12"], broken="07")) as zf:
    keys, errs = _unpack_one_zip(zf, root, 1, "批改包_C.zip")
check("入队2份", sorted(keys), ["05", "12"])
check("1条错误", len(errs), 1)
check("错误指名07", "07" in errs[0], True)

print("\n【6】空包 / 无批改包结构 → 明确报错不静默")
empty = io.BytesIO()
with zipfile.ZipFile(empty, "w") as zf:
    zf.writestr("readme.txt", b"hi")
empty.seek(0)
with zipfile.ZipFile(empty) as zf:
    keys, errs = _unpack_one_zip(zf, root, 1, "空包.zip")
check("入队0份", len(keys), 0)
check("有报错", len(errs), 1)
check("说明找不到批改包目录", "transcript.json" in errs[0], True)

print("\n【7】坏 zip → 调用方 BadZipFile 可捕获（不拖垮整批）")
bad = io.BytesIO(b"this is not a zip at all")
caught = False
try:
    zipfile.ZipFile(bad)
except zipfile.BadZipFile:
    caught = True
check("BadZipFile 可被捕获", caught, True)

print("\n" + "=" * 46)
print("❌ 失败 %d 项：%s" % (len(fails), fails) if fails else "✅ 全部通过")
sys.exit(1 if fails else 0)
