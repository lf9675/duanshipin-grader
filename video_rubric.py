# -*- coding: utf-8 -*-
"""
video_rubric.py — 短视频评分规则（本产品的单一事实源）
════════════════════════════════════════════════════
来源：刘老师《短视频评分表》（总分 60 分），2026-07-20 定稿。
与作文平台的 prompts.py / 引擎母本完全无关——这里是短视频批改的
独立评分规则，修改只在本文件进行。

RUBRIC_VERSION 改动规则：任何维度/档位/描述变化都要更新日期戳。
"""

RUBRIC_VERSION = "2026-07-20"
TOTAL_MAX = 60

# 基本要求（准入检查，任何一项不符可直接打回）
PRECHECK_ITEMS = {
    "duration_ok":  "时长 ≤ 4 分钟",
    "chinese_ok":   "全程华文（夹杂英文 ≤ 10%）",
    "face_ok":      "组员面容有出镜",
    "script_ok":    "已提交脚本（老师人工勾选）",
    "naming_ok":    "文件命名规范（老师人工勾选）",
}
# 前三项自动预判（时长/华文比例来自本地工具，面容出镜来自 GLM-4V），
# 后两项无法自动判断，覆核表上由老师勾选。

DIMENSIONS = [
    {
        "key": "content", "name": "步骤讲述", "max": 25, "star": True,
        "judge": "text",   # 由 DeepSeek 读转写稿判定
        "levels": [
            {"grade": "A", "label": "优秀",   "lo": 21, "hi": 25,
             "desc": "步骤清晰完整，逻辑分明，由浅入深，观众容易跟随学习"},
            {"grade": "B", "label": "良好",   "lo": 16, "hi": 20,
             "desc": "步骤基本清楚，有少量不连贯或略跳跃"},
            {"grade": "C", "label": "及格",   "lo": 11, "hi": 15,
             "desc": "步骤不够清晰，部分内容难理解"},
            {"grade": "D", "label": "待改进", "lo": 0,  "hi": 10,
             "desc": "步骤混乱或缺失，难以理解"},
        ],
    },
    {
        "key": "speaking", "name": "口语表达", "max": 20, "star": True,
        "judge": "text_low_confidence",  # 转写稿+客观指标只能判一部分，AI 结果仅供参考
        "levels": [
            {"grade": "A", "label": "优秀",   "lo": 17, "hi": 20,
             "desc": "表达流畅自然，咬字清晰，语速适中，有感染力"},
            {"grade": "B", "label": "良好",   "lo": 13, "hi": 16,
             "desc": "基本清楚，有少量停顿或发音问题"},
            {"grade": "C", "label": "及格",   "lo": 9,  "hi": 12,
             "desc": "表达不够流畅，咬字偶有不清"},
            {"grade": "D", "label": "待改进", "lo": 0,  "hi": 8,
             "desc": "表达不清晰，声音小或影响理解"},
        ],
    },
    {
        "key": "design", "name": "内容设计", "max": 10, "star": False,
        "judge": "text",
        "levels": [
            {"grade": "A", "label": "优秀",   "lo": 9, "hi": 10,
             "desc": "结构完整（Hook–步骤–总结），内容有吸引力、有重点"},
            {"grade": "B", "label": "良好",   "lo": 7, "hi": 8,
             "desc": "结构基本完整，但吸引力一般"},
            {"grade": "C", "label": "及格",   "lo": 5, "hi": 6,
             "desc": "结构不完整或重点不突出"},
            {"grade": "D", "label": "待改进", "lo": 0, "hi": 4,
             "desc": "内容零散，无结构"},
        ],
    },
    {
        "key": "video", "name": "拍摄与呈现", "max": 5, "star": False,
        "judge": "frames",  # 由 GLM-4V 看关键帧判定
        "levels": [
            {"grade": "A", "label": "优秀",   "lo": 5, "hi": 5,
             "desc": "组员面容出镜，画面清晰，构图佳，演示清楚"},
            {"grade": "B", "label": "良好",   "lo": 3, "hi": 4,
             "desc": "组员面容出镜，画面尚可，略有不清楚"},
            {"grade": "C", "label": "及格",   "lo": 2, "hi": 2,
             "desc": "组员面容出镜，画面一般，影响观看"},
            {"grade": "D", "label": "待改进", "lo": 0, "hi": 1,
             "desc": "组员面容未出镜，画面不清或难以观看"},
        ],
    },
]

DIM_BY_KEY = {d["key"]: d for d in DIMENSIONS}

# 口语客观指标的参考区间（覆核标黄用，不直接决定分数）
SPEECH_RATE_COMFORT = (170, 270)   # 字/分钟 舒适区
PAUSE_MANY = 5                     # 明显停顿 ≥5 次视为偏多
FILLER_MANY = 8                    # 嗯呃填充词 ≥8 次视为偏多


def level_of(dim_key, score):
    """根据分数返回所在档位 dict。"""
    for lv in DIM_BY_KEY[dim_key]["levels"]:
        if lv["lo"] <= score <= lv["hi"]:
            return lv
    return DIM_BY_KEY[dim_key]["levels"][-1]


def rubric_text_for_prompt(keys):
    """把指定维度的评分标准渲染成给 AI 的文字。"""
    out = []
    for d in DIMENSIONS:
        if d["key"] not in keys:
            continue
        out.append(f"【{d['name']}】满分 {d['max']} 分" + ("（核心重点）" if d["star"] else ""))
        for lv in d["levels"]:
            out.append(f"  {lv['grade']} {lv['label']}（{lv['lo']}–{lv['hi']}分）：{lv['desc']}")
    return "\n".join(out)
