# -*- coding: utf-8 -*-
"""
test_group_report.py — 小组视频功能自测（2026-08-04）
════════════════════════════════════════════════════
覆盖：
  1. 新模板体检 + 四维同档求和必落在总分等级区间
  2. pass_ceiling 的「合格/及格」双命中歧义已修掉
  3. 个人分算法与照读封顶 39 的边界（含封顶不生效的低档情形）
  4. 学生 PDF：不出现"学号"字样、抬头是「第 NN 号视频」
  5. 分包：学生包只有 PDF，老师包只有 Excel/PPT
  6. 旧模板（技能讲解·个人作业）行为不变——回归保护
运行：python3 test_group_report.py
"""
import io
import json
import sys
import zipfile

from video_rubric import (GROUP_VIDEO_RUBRIC, DEFAULT_RUBRIC, normalize_rubric,
                          validate_rubric, dims, dim_by_key, total_levels,
                          total_level_of, member_mode, pass_ceiling,
                          reading_cap_total)
from video_engine import compute_member_scores, suggest_member_marks
from video_export import (build_student_pdf, build_student_zip,
                          build_teacher_zip)

FAILED = []


def check(name, cond, detail=""):
    print(("  ✓ " if cond else "  ✗ ") + name + (f"  [{detail}]" if detail else ""))
    if not cond:
        FAILED.append(name)


R = normalize_rubric(dict(GROUP_VIDEO_RUBRIC))
D = normalize_rubric(dict(DEFAULT_RUBRIC))


def fake_item():
    """一份逼真的批改结果：4 人组，讲新加坡水资源，二上档。"""
    return {
        "id": 1,
        "item_key": "01",
        "student_ids": ["05", "12", "18", "23"],
        "final_scores": {"content": 14, "speaking": 13,
                         "teamwork": 7, "production": 7},   # = 41 → 二上
        "teacher_comment": "实地去了滨海堤坝，很用心。",
        "ai_result": {
            "one_line_comment": "选题扎实，可惜有两位同学在念稿。",
            "report_strengths": [
                "去滨海堤坝实地拍摄，比只放网络片段有说服力。",
                "讲新生水那段用了具体数字，听得出查过资料。"],
            "report_improvements": [
                {"what": "讲集水区那段只说了『很大』，没说到底有多大。",
                 "how": "补一句占全岛面积的比例。"},
                {"what": "第三位同学全程低头看平板，听不清。",
                 "how": "把要点写成三个词的提纲，抬头讲。"}],
            "members": [
                {"slot": 1, "script_free": "完全脱稿", "fluency": "流利"},
                {"slot": 2, "script_free": "偶尔看稿", "fluency": "偶有卡顿"},
                {"slot": 3, "script_free": "明显念读", "fluency": "结结巴巴"},
                {"slot": 4, "script_free": "无法判断", "fluency": "无法判断"},
            ],
        },
        "metrics": {"speech_rate_cpm": 210, "long_pause_count": 3,
                    "filler_count": 4, "ranhou_count": 2},
        "precheck": {},
        "status": "confirmed",
    }


print("\n[1] 模板体检与落档验算")
check("新模板体检通过", validate_rubric(R) == [], str(validate_rubric(R)))
for i, g in enumerate(["一上", "一下", "二上", "二下", "三上", "三下"]):
    lo = sum(d["levels"][i]["lo"] for d in dims(R))
    hi = sum(d["levels"][i]["hi"] for d in dims(R))
    tl = [x for x in total_levels(R) if x["grade"] == g][0]
    check(f"{g} 四维和 {lo}-{hi} 落在总表 {tl['lo']}-{tl['hi']}",
          tl["lo"] <= lo and hi <= tl["hi"])

print("\n[2] 封顶线歧义（「合格」35-39 与「及格」30-34 双命中）")
check("显式 reading_cap 优先，语言表达封顶 = 12",
      pass_ceiling(dim_by_key(R, "speaking")) == 12,
      str(pass_ceiling(dim_by_key(R, "speaking"))))
check("个人总分封顶 = 39", reading_cap_total(R) == 39)
amb = {"max": 20, "levels": [
    {"grade": "二下", "label": "合格", "lo": 12, "hi": 12, "desc": ""},
    {"grade": "三上", "label": "及格", "lo": 10, "hi": 11, "desc": ""}]}
check("无显式值时多档命中取最高（宁宽不误伤）", pass_ceiling(amb) == 12,
      str(pass_ceiling(amb)))

print("\n[3] 个人分与封顶边界")
ids = ["05", "12", "18", "23"]
marks = {"05": "top", "12": "mid", "18": "low", "23": "reading"}
r42 = compute_member_scores(R, 42, ids, marks)
check("二上(40-44)：脱稿流利 → 44", r42["05"]["score"] == 44)
check("二上：偶有卡顿 → 42", r42["12"]["score"] == 42)
check("二上：无法脱稿 → 40", r42["18"]["score"] == 40)
check("二上：明显照读 → 39（封顶生效）", r42["23"]["score"] == 39)
r32 = compute_member_scores(R, 32, ids, marks)
check("三上(30-34)：照读 → 30，封顶不该把人提上去", r32["23"]["score"] == 30)
r55 = compute_member_scores(R, 55, ids, marks)
check("一上(50-60)：照读 → 39（C案保持原样）", r55["23"]["score"] == 39)
check("一上：脱稿流利 → 60", r55["05"]["score"] == 60)
ra = compute_member_scores(R, 42, ["07"], {"07": "absent"})
check("未出镜 → 0 分并提示单独处理", ra["07"]["score"] == 0
      and "未出镜" in ra["07"]["note"])
check("缺 marks 时一律取档中，不猜",
      compute_member_scores(R, 42, ["09"])["09"]["score"] == 42)

print("\n[4] AI 预填不越权")
sg = suggest_member_marks(fake_item()["ai_result"], ids)
check("『明显念读』只预填到 low，不敢直接判 reading", sg["18"] == "low",
      sg["18"])
check("『完全脱稿+流利』预填 top", sg["05"] == "top")
check("『无法判断』回落到 mid", sg["23"] == "mid", sg["23"])

print("\n[5] 学生 PDF")
it = fake_item()
mem = compute_member_scores(R, 41, ids, marks)
it["member_scores"] = mem
pdf_a = build_student_pdf(it, "2A1", "新加坡的水资源", R, "05", mem["05"])
pdf_b = build_student_pdf(it, "2A1", "新加坡的水资源", R, "23", mem["23"])
check("PDF 生成成功", len(pdf_a) > 2000, f"{len(pdf_a)} bytes")
check("同组不同人的 PDF 不相同（个人层生效）", pdf_a != pdf_b)
try:
    from pdfminer.high_level import extract_text
    txt = extract_text(io.BytesIO(pdf_a))
    check("抬头是「第 01 号视频」", "第01号视频" in txt.replace(" ", ""))
    check("全文不出现「学号」字样", "学号" not in txt)
    check("印了优点", "滨海堤坝" in txt.replace(" ", ""))
    check("印了不足与改法", "抬头讲" in txt.replace(" ", ""))
    check("不印口语小数据（已砍）", "语速" not in txt)
except ImportError:
    print("  … 跳过 PDF 文字校验（未装 pdfminer）")

print("\n[6] 分包隔离")
items = [it]
sz = build_student_zip(items, "2A1", "新加坡的水资源", R)
names = zipfile.ZipFile(io.BytesIO(sz)).namelist()
check("学生包一人一份", sorted(names) == ["01_05.pdf", "01_12.pdf",
                                          "01_18.pdf", "01_23.pdf"],
      str(sorted(names)))
check("学生包里没有 Excel", not any(n.endswith(".xlsx") for n in names))
check("学生包里没有 PPT", not any(n.endswith(".pptx") for n in names))
tz = build_teacher_zip(items, "2A1", "新加坡的水资源", R, agg=None)
tnames = zipfile.ZipFile(io.BytesIO(tz)).namelist()
check("老师包有成绩总表", any(n.endswith(".xlsx") for n in tnames))
check("老师包里没有学生 PDF", not any(n.endswith(".pdf") for n in tnames))

print("\n[7] 旧模板回归（个人作业·技能讲解 60 分）")
check("旧模板体检通过", validate_rubric(D) == [])
check("旧模板不进小组模式", member_mode(D) is False)
check("旧模板口语封顶仍是 12", pass_ceiling(dim_by_key(D, "speaking")) == 12)
old_item = {"id": 2, "item_key": "07", "student_ids": ["07"],
            "final_scores": {"content": 20, "speaking": 15,
                             "design": 8, "video": 4},
            "ai_result": {"top_strength": "步骤讲得清楚",
                          "top_issue": "收尾太仓促",
                          "next_level_advice": "结尾补一句总结"},
            "metrics": {}, "precheck": {}, "teacher_comment": ""}
old_pdf = build_student_pdf(old_item, "1B2", "教一样技能", D)
check("旧模板 PDF 仍能生成", len(old_pdf) > 1500)
old_zip = zipfile.ZipFile(io.BytesIO(
    build_student_zip([old_item], "1B2", "教一样技能", D))).namelist()
check("旧模板学生包按学号命名", old_zip == ["07.pdf"], str(old_zip))

print("\n" + "=" * 46)
if FAILED:
    print(f"✗ {len(FAILED)} 项未通过：")
    for f in FAILED:
        print("   -", f)
    sys.exit(1)
print("✓ 全部通过")
