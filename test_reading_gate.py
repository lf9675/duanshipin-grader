# -*- coding: utf-8 -*-
"""照读封顶验证（2026-07-28）。运行：python3 test_reading_gate.py"""
import copy
from video_rubric import DEFAULT_RUBRIC, pass_ceiling, dim_by_key
from video_engine import (judge_reading, finalize_video_scores, review_flags,
                          DELIVERY_OBVIOUS, DELIVERY_SUSPECT, DELIVERY_NATURAL)

R = DEFAULT_RUBRIC
SPK = dim_by_key(R, "speaking")
fails = []


def check(name, got, want):
    ok = got == want
    print(("  ✓ " if ok else "  ✗ ") + name + ("" if ok else f"  期望{want} 实得{got}"))
    if not ok:
        fails.append(name)


def run(text_gate, frames_gate, frames_available, scores):
    g = judge_reading(text_gate, frames_gate, frames_available)
    res, fs = {"dimensions": {}}, dict(scores)
    caps = finalize_video_scores(R, res, fs, g)
    return g, fs, caps, res


print("\n【0】及格档上限取值")
check("口语表达及格档上限=12", pass_ceiling(SPK), 12)
check("步骤讲述及格档上限=15", pass_ceiling(dim_by_key(R, "content")), 15)

print("\n【1】明显照读：手持非演示设备+视线全程+文本有迹象 → 封顶12")
g, fs, caps, _ = run({"text_signal": "强", "basis": "通篇书面语"},
                     {"device_in_hand": True, "device_is_demo_subject": False,
                      "gaze_on_device": "全程", "evidence": "帧1-8均低头看平板"},
                     True, {"content": 22, "speaking": 18, "design": 9, "video": 4})
check("判定=明显照读", g["delivery_mode"], DELIVERY_OBVIOUS)
check("口语 18→12", fs["speaking"], 12)
check("步骤讲述不受影响", fs["content"], 22)
check("拍摄不受影响", fs["video"], 4)
check("产生1条审计串", len(caps), 1)

print("\n【2】甲案核心：只有文本迹象、无画面证据 → 疑似，不扣分")
g, fs, caps, _ = run({"text_signal": "强", "basis": "零填充词"},
                     None, False, {"speaking": 18})
check("判定=疑似照读", g["delivery_mode"], DELIVERY_SUSPECT)
check("口语分不动=18", fs["speaking"], 18)
check("无封顶审计串", len(caps), 0)

print("\n【3】防误伤：iPad 是演示对象（教电子绘画）→ 绝不封顶")
# 3a 文本迹象强：不封顶，但仍提示抽听（可能念画外稿），理由须点明"演示道具"
g, fs, caps, _ = run({"text_signal": "强"},
                     {"device_in_hand": True, "device_is_demo_subject": True,
                      "gaze_on_device": "全程"}, True, {"speaking": 19})
check("不封顶，19分不动", fs["speaking"], 19)
check("无封顶审计串", len(caps), 0)
check("理由点明演示道具", "演示道具" in g["reason"], True)
# 3b 文本迹象无：应完全放行
g, fs, _, _ = run({"text_signal": "无"},
                  {"device_in_hand": True, "device_is_demo_subject": True,
                   "gaze_on_device": "全程"}, True, {"speaking": 19})
check("判定=自然讲述", g["delivery_mode"], DELIVERY_NATURAL)
check("口语分不动=19", fs["speaking"], 19)

print("\n【4】视线间歇（瞄提纲）→ 疑似，不扣分")
g, fs, _, _ = run({"text_signal": "弱"},
                  {"device_in_hand": True, "device_is_demo_subject": False,
                   "gaze_on_device": "间歇"}, True, {"speaking": 17})
check("判定=疑似照读", g["delivery_mode"], DELIVERY_SUSPECT)
check("口语分不动=17", fs["speaking"], 17)

print("\n【5】画面像照念但转写稿口语感自然 → 疑似，不扣分")
g, fs, _, _ = run({"text_signal": "无"},
                  {"device_in_hand": True, "device_is_demo_subject": False,
                   "gaze_on_device": "全程"}, True, {"speaking": 18})
check("判定=疑似照读", g["delivery_mode"], DELIVERY_SUSPECT)
check("口语分不动=18", fs["speaking"], 18)

print("\n【6】安全降级：GLM 批改失败（无画面证据）→ 绝不封顶")
g, fs, caps, _ = run({"text_signal": "强"},
                     None, False, {"speaking": 20})
check("不封顶", fs["speaking"], 20)
check("无审计串", len(caps), 0)

print("\n【7】原分已在及格档内 → 不重复下调")
g, fs, caps, _ = run({"text_signal": "强"},
                     {"device_in_hand": True, "device_is_demo_subject": False,
                      "gaze_on_device": "全程"}, True, {"speaking": 10})
check("判定=明显照读", g["delivery_mode"], DELIVERY_OBVIOUS)
check("10分不变", fs["speaking"], 10)
check("无封顶审计串", len(caps), 0)

print("\n【8】字段缺失/脏值 → 安全降级为自然讲述")
for bad in (None, {}, {"text_signal": "乱码"}):
    g, fs, _, _ = run(bad, {"device_in_hand": "看不清"}, True, {"speaking": 20})
    check(f"脏值 {bad} 不封顶", fs["speaking"], 20)

print("\n【9】档位改名（老师自编模板：'尚可' 9-13）→ 封顶跟着变")
R2 = copy.deepcopy(DEFAULT_RUBRIC)
d2 = dim_by_key(R2, "speaking")
d2["levels"] = [{"grade": "A", "label": "优秀", "lo": 18, "hi": 20, "desc": ""},
                {"grade": "B", "label": "良好", "lo": 14, "hi": 17, "desc": ""},
                {"grade": "C", "label": "尚可", "lo": 9, "hi": 13, "desc": ""},
                {"grade": "D", "label": "待改进", "lo": 0, "hi": 8, "desc": ""}]
check("新模板及格上限=13", pass_ceiling(d2), 13)
g = judge_reading({"text_signal": "强"},
                  {"device_in_hand": True, "device_is_demo_subject": False,
                   "gaze_on_device": "全程"}, True)
res, fs = {"dimensions": {}}, {"speaking": 19}
finalize_video_scores(R2, res, fs, g)
check("按新档位封顶到13", fs["speaking"], 13)

print("\n【10】复核标黄：封顶项与疑似项都要出现")
g, fs, caps, res = run({"text_signal": "强"},
                       {"device_in_hand": True, "device_is_demo_subject": False,
                        "gaze_on_device": "全程"}, True,
                       {"content": 22, "speaking": 18, "design": 9, "video": 4})
item = {"status": "graded", "ai_result": res, "final_scores": fs,
        "metrics": {"duration_sec": 200, "speech_rate_cpm": 200,
                    "char_count_cjk": 660, "long_pause_count": 2,
                    "filler_count": 1}, "precheck": {}}
fl = review_flags(item, R)
check("标黄含封顶提示", any("🔒" in f for f in fl), True)

g2 = judge_reading({"text_signal": "强"}, None, False)
res2, fs2 = {"dimensions": {}}, {"speaking": 18}
finalize_video_scores(R, res2, fs2, g2)
item2 = dict(item, ai_result=res2, final_scores=fs2)
fl2 = review_flags(item2, R)
check("疑似项标黄且不含封顶锁", any("❓疑似照读" in f for f in fl2)
      and not any("🔒" in f for f in fl2), True)

print("\n【11】回归：正常学生不受任何影响")
g, fs, caps, res = run({"text_signal": "无"}, {"device_in_hand": False,
                       "device_is_demo_subject": False, "gaze_on_device": "无"},
                       True, {"content": 22, "speaking": 18, "design": 9, "video": 5})
check("判定=自然讲述", g["delivery_mode"], DELIVERY_NATURAL)
check("四维度全不动", [fs[k] for k in ("content", "speaking", "design", "video")],
      [22, 18, 9, 5])
check("auto_caps 为空列表", res["auto_caps"], [])

print("\n" + ("=" * 46))
print("❌ 失败 %d 项：%s" % (len(fails), fails) if fails else "✅ 全部通过")
