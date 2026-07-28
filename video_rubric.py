# -*- coding: utf-8 -*-
"""
video_rubric.py — 评分标准工具（2026-07-20 常年化改版）
════════════════════════════════════════════════════
评分标准不再写死在代码里，而是作为"作业模板"数据存数据库：
  模板 = 题目要求 + 评分维度（各带档位）+ 准入检查配置
本文件提供：内置默认模板（刘老师《短视频评分表》60分）、
规则工具函数、模板校验。

rubric 数据结构（JSON）：
{
  "name": "模板名",
  "total_max": 60,
  "dimensions": [
    {"key": "content", "name": "步骤讲述", "max": 25, "star": true,
     "judge": "text",          # text=转写稿判 / text_speech=口语类(AI仅参考)
                               # frames=画面判(关键帧) / manual=老师手评
     "levels": [{"grade":"A","label":"优秀","lo":21,"hi":25,"desc":"…"}, …]}
  ],
  "precheck": {
    "max_duration_sec": 240,      # 0 = 不限时长
    "en_ratio_limit": 0.10,       # 1.0 = 不限语言
    "face_required": true,        # 是否要求面容出镜
    "manual_items": ["已提交脚本", "文件命名规范"]   # 老师人工勾选项
  }
}

2026-07-20 决策：建任务时把模板完整快照进任务(video_batch_jobs.rubric)，
之后改模板不影响历史任务——同"引擎版本纪律"原则。
"""

RUBRIC_TOOL_VERSION = "2026-07-28"

# ── 内置默认模板：刘老师《短视频评分表》（逐字录入，2026-07-20 定稿）──
DEFAULT_RUBRIC = {
    "name": "短视频评分表（技能讲解·60分）",
    "total_max": 60,
    "dimensions": [
        {
            "key": "content", "name": "步骤讲述", "max": 25, "star": True,
            "judge": "text",
            "levels": [
                {"grade": "A", "label": "优秀", "lo": 21, "hi": 25,
                 "desc": "步骤清晰完整，逻辑分明，由浅入深，观众容易跟随学习"},
                {"grade": "B", "label": "良好", "lo": 16, "hi": 20,
                 "desc": "步骤基本清楚，有少量不连贯或略跳跃"},
                {"grade": "C", "label": "及格", "lo": 11, "hi": 15,
                 "desc": "步骤不够清晰，部分内容难理解"},
                {"grade": "D", "label": "待改进", "lo": 0, "hi": 10,
                 "desc": "步骤混乱或缺失，难以理解"},
            ],
        },
        {
            "key": "speaking", "name": "口语表达", "max": 20, "star": True,
            "judge": "text_speech",
            "levels": [
                {"grade": "A", "label": "优秀", "lo": 17, "hi": 20,
                 "desc": "表达流畅自然，咬字清晰，语速适中，有感染力"},
                {"grade": "B", "label": "良好", "lo": 13, "hi": 16,
                 "desc": "基本清楚，有少量停顿或发音问题"},
                {"grade": "C", "label": "及格", "lo": 9, "hi": 12,
                 "desc": "表达不够流畅，咬字偶有不清"},
                {"grade": "D", "label": "待改进", "lo": 0, "hi": 8,
                 "desc": "表达不清晰，声音小或影响理解"},
            ],
        },
        {
            "key": "design", "name": "内容设计", "max": 10, "star": False,
            "judge": "text",
            "levels": [
                {"grade": "A", "label": "优秀", "lo": 9, "hi": 10,
                 "desc": "结构完整（Hook–步骤–总结），内容有吸引力、有重点"},
                {"grade": "B", "label": "良好", "lo": 7, "hi": 8,
                 "desc": "结构基本完整，但吸引力一般"},
                {"grade": "C", "label": "及格", "lo": 5, "hi": 6,
                 "desc": "结构不完整或重点不突出"},
                {"grade": "D", "label": "待改进", "lo": 0, "hi": 4,
                 "desc": "内容零散，无结构"},
            ],
        },
        {
            "key": "video", "name": "拍摄与呈现", "max": 5, "star": False,
            "judge": "frames",
            "levels": [
                {"grade": "A", "label": "优秀", "lo": 5, "hi": 5,
                 "desc": "组员面容出镜，画面清晰，构图佳，演示清楚"},
                {"grade": "B", "label": "良好", "lo": 3, "hi": 4,
                 "desc": "组员面容出镜，画面尚可，略有不清楚"},
                {"grade": "C", "label": "及格", "lo": 2, "hi": 2,
                 "desc": "组员面容出镜，画面一般，影响观看"},
                {"grade": "D", "label": "待改进", "lo": 0, "hi": 1,
                 "desc": "组员面容未出镜，画面不清或难以观看"},
            ],
        },
    ],
    "precheck": {
        "max_duration_sec": 240,
        "en_ratio_limit": 0.10,
        "face_required": True,
        "manual_items": ["已提交脚本", "文件命名规范"],
    },
    # 2026-07-21 新增（刘老师指引）：宽严基调，逐字进入批改提示词
    "stance": (
        "本作业对象是中一学生，教学目的是练习华语口语，不是专业制作。"
        "评分必须先定档、后在档内定位，以下起评锚点优先于你的默认严格倾向：\n"
        "1. 步骤讲述：只要观众能照做、顺序合理，即入A档从21分起评；"
        "缺个别专业细节（火候、器具型号等）在A档内扣，每条最多扣1分，"
        "不得因此降到B档。只有观众明显跟不上/顺序混乱才落B档及以下。\n"
        "2. 口语表达：表达干净（无口头禅堆积、连接词清楚）即从15-16分起评，"
        "是否进A档由老师抽听决定。做菜/演示类视频说一句做一段，"
        "语速指标天然偏低，【禁止】把段落间留白或低语速算作不流畅；"
        "只有句子内部的结巴、重复、卡顿才算不流畅。\n"
        "3. 内容设计：Hook、步骤主体、总结收尾三段齐全即入A档从9分起评；"
        "判断收尾是否存在必须以转写稿末段原文为准，不得凭感觉说缺。\n"
        "4. 拍摄与呈现：画面清楚即3分起步，较好4分；有多机位/特写/字幕"
        "等任一用心之处即给5分；只有画面严重影响观看才低于3分。\n"
        "5. 照读封顶（2026-07-28 刘老师裁定）：明显捧着iPad/手机/讲稿照念的，"
        "口语表达最高只能给到「及格」档上限（本表为12分）。判定以画面为准，"
        "详见引擎 reading_gate 规则；此条由代码强制执行，不依赖你自行减分。"
    ),
}

DEFAULT_REQUIREMENTS = (
    "拍一个不超过4分钟的短视频，用华语讲解一项自己会的技能，"
    "目标是让别人看了能照做。所有组员面容要出现在影片内。"
    "全程华文（夹杂英文不超过10%）。"
)

JUDGE_LABELS = {
    "text": "转写稿判（AI可靠）",
    "text_speech": "口语类（AI仅参考，需抽听）",
    "frames": "画面判（关键帧）",
    "manual": "老师手评（AI不判）",
}

# 口语客观指标的参考区间（复核标黄用）
SPEECH_RATE_COMFORT = (170, 270)
PAUSE_MANY = 5
FILLER_MANY = 8


# ── 工具函数（全部以 rubric dict 为参数）─────────────────────

def dims(rubric):
    return rubric.get("dimensions", [])


def dim_by_key(rubric, key):
    for d in dims(rubric):
        if d["key"] == key:
            return d
    return None


def dims_of_judge(rubric, judges):
    return [d for d in dims(rubric) if d.get("judge") in judges]


def total_max(rubric):
    return rubric.get("total_max") or sum(d["max"] for d in dims(rubric))


def level_of(dim, score):
    for lv in dim.get("levels", []):
        if lv["lo"] <= score <= lv["hi"]:
            return lv
    return {"grade": "?", "label": "—", "lo": 0, "hi": dim.get("max", 0),
            "desc": ""}


# 2026-07-28 决策（刘老师）：明显照读 → 口语类维度封顶到「及格」档上限。
# 因为评分模板是老师可编辑的数据（不是写死的常量），封顶值不能硬编码成 12，
# 必须从当前 rubric 里推出来。取值优先级如下，任一命中即返回。
PASS_LABEL_KEYWORDS = ("及格", "合格", "尚可", "pass")


def pass_ceiling(dim):
    """返回该维度「及格」档的分数上限（照读封顶用）。

    优先级：
      1) label 或 desc 含"及格/合格"等字样的档位 → 取其 hi
      2) 档位数 ≥3 → 取从高到低第 3 档（A/B/C 的 C）的 hi
      3) 兜底 → 满分的 60%（向下取整）
    默认《短视频评分表》口语表达：C档「及格」9–12 → 返回 12。
    """
    lvs = dim.get("levels") or []
    for lv in lvs:
        text = f"{lv.get('label', '')}{lv.get('desc', '')}".lower()
        if any(kw in text for kw in PASS_LABEL_KEYWORDS):
            try:
                return int(lv["hi"])
            except (KeyError, TypeError, ValueError):
                break
    if len(lvs) >= 3:
        try:
            return int(lvs[2]["hi"])
        except (KeyError, TypeError, ValueError):
            pass
    return int(dim.get("max", 0) * 0.6)


def rubric_text_for_prompt(rubric, judges=None):
    out = []
    for d in dims(rubric):
        if judges and d.get("judge") not in judges:
            continue
        out.append(f"【{d['name']}】(key={d['key']}) 满分 {d['max']} 分"
                   + ("（核心重点）" if d.get("star") else ""))
        for lv in d.get("levels", []):
            out.append(f"  {lv['grade']} {lv['label']}"
                       f"（{lv['lo']}–{lv['hi']}分）：{lv['desc']}")
    return "\n".join(out)


def validate_rubric(rubric):
    """模板保存前的体检。返回问题清单（空=通过）。"""
    errs = []
    ds = dims(rubric)
    if not ds:
        errs.append("没有解析出任何评分维度")
        return errs
    keys = [d.get("key") for d in ds]
    if len(keys) != len(set(keys)):
        errs.append("维度 key 重复")
    for d in ds:
        name = d.get("name", "?")
        if not isinstance(d.get("max"), int) or d["max"] <= 0:
            errs.append(f"维度「{name}」满分无效")
            continue
        if d.get("judge") not in JUDGE_LABELS:
            errs.append(f"维度「{name}」判定方式无效")
        lvs = d.get("levels", [])
        if not lvs:
            errs.append(f"维度「{name}」没有档位")
            continue
        for lv in lvs:
            if not (0 <= lv.get("lo", -1) <= lv.get("hi", -1) <= d["max"]):
                errs.append(f"维度「{name}」档位 {lv.get('grade','?')} "
                            f"分数区间越界")
        covered = set()
        for lv in lvs:
            covered.update(range(int(lv.get("lo", 0)),
                                 int(lv.get("hi", -1)) + 1))
        missing = [s for s in range(0, d["max"] + 1) if s not in covered]
        if missing:
            errs.append(f"维度「{name}」有分数没被任何档位覆盖：{missing[:8]}")
    declared = rubric.get("total_max")
    actual = sum(d["max"] for d in ds if isinstance(d.get("max"), int))
    if declared and declared != actual:
        errs.append(f"总分声明 {declared} 与各维度满分之和 {actual} 不一致")
    pre = rubric.get("precheck", {})
    if not isinstance(pre.get("manual_items", []), list):
        errs.append("人工勾选项格式无效")
    return errs


def normalize_rubric(rubric):
    """补默认值、生成缺失 key、夹取数值。就地修改并返回。"""
    for i, d in enumerate(dims(rubric)):
        d.setdefault("key", f"d{i+1}")
        d.setdefault("star", False)
        d.setdefault("judge", "text")
    pre = rubric.setdefault("precheck", {})
    pre.setdefault("max_duration_sec", 0)
    pre.setdefault("en_ratio_limit", 1.0)
    pre.setdefault("face_required", False)
    pre.setdefault("manual_items", [])
    rubric.setdefault("stance", "")
    rubric["total_max"] = sum(d["max"] for d in dims(rubric)
                              if isinstance(d.get("max"), int))
    return rubric
