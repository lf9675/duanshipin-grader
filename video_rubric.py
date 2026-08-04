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

# ════════════════════════════════════════════════════════════
# 内置模板二：2026 中二高华 小组口头报告（60分）
# ════════════════════════════════════════════════════════════
# 2026-08-04 决策（刘老师确认）：
# 1. 老师原表是【整体六档量表】，每档同时描述四个面。直接让 DeepSeek 解析
#    会把【全员参与】【内容详实】这些档位小标题误认成维度（实测解析出
#    "总分240分、四个60分维度"）。因此本模板【写进代码】，不走 AI 解析。
# 2. 四维度满分按比例切档，保证"四维同判某档 → 总分必落在该档区间"：
#      全一上 52-60 / 全一下 46-48 / 全二上 40-42 / 全二下 36 /
#      全三上 30-32 / 全三下 ≤26   —— 全部落在 total_levels 对应区间内。
# 3. member_mode=True：一个视频 4-5 名组员，组分定档后按个人表现取档内位置。
# 4. reading_cap_total=39：明显照读者【个人总分】封顶 39（二下「合格」档顶），
#    即念稿的学生不得进入 40 分以上的「良好」档。刘老师 2026-08-04 裁定 C 案。

def _lv6(a, b, c, d, e, f, descs):
    """按 (一上,一下,二上,二下,三上,三下) 的 (lo,hi) 元组生成六档。"""
    grades = ["一上", "一下", "二上", "二下", "三上", "三下"]
    labels = ["优异", "优秀", "良好", "合格", "及格", "不及格"]
    return [{"grade": g, "label": lb, "lo": r[0], "hi": r[1], "desc": ds}
            for g, lb, r, ds in zip(grades, labels, [a, b, c, d, e, f], descs)]


GROUP_VIDEO_RUBRIC = {
    "name": "2026 中二高华 小组视频（60分·六档）",
    "total_max": 60,
    "member_mode": True,
    "reading_cap_total": 39,
    "total_levels": [
        {"grade": "一上", "label": "优异", "lo": 50, "hi": 60},
        {"grade": "一下", "label": "优秀", "lo": 45, "hi": 49},
        {"grade": "二上", "label": "良好", "lo": 40, "hi": 44},
        {"grade": "二下", "label": "合格", "lo": 35, "hi": 39},
        {"grade": "三上", "label": "及格", "lo": 30, "hi": 34},
        {"grade": "三下", "label": "不及格", "lo": 0, "hi": 29},
    ],
    "dimensions": [
        {
            "key": "content", "name": "内容与主题", "max": 20, "star": True,
            "judge": "text",
            "levels": _lv6((17, 20), (15, 16), (13, 14), (12, 12),
                           (10, 11), (0, 9), [
                "完全符合主题。内容丰富且有深度，有独特的个人体会及精准的例子支撑",
                "内容符合主题且条理分明，但细节补充略显不足，说明不够深入",
                "虽然收集了资料，但内容比较简单，语句、用词有错误",
                "内容流于表面，缺乏必要的例子和分析，逻辑较为混乱",
                "基本没有切中社会话题要点，资料来源单一且未经过滤，语句错误百出",
                "内容完全跑题，无法体现任何学习目的",
            ]),
        },
        {
            "key": "speaking", "name": "语言表达", "max": 20, "star": True,
            "judge": "text_speech", "reading_cap": 12,
            "levels": _lv6((17, 20), (15, 16), (13, 14), (12, 12),
                           (10, 11), (0, 9), [
                "完全脱稿。说话流利且词语严谨，语调抑扬顿挫，表情自然，极具感染力",
                "说话基本清楚流利，偶尔看讲稿，但在语调起伏或词语严谨度上稍逊于最高等",
                "说话断断续续，缺乏语调起伏，有明显的读稿痕迹",
                "口语表达不通顺，词不达意，严重影响观者对内容的理解；表达极其生硬，无法直视镜头",
                "说话模糊不清，无法完成基本的介绍任务",
                "几乎没有有效的口语说明",
            ]),
        },
        {
            "key": "teamwork", "name": "全员参与与分工", "max": 10,
            "star": False, "judge": "frames",
            "levels": _lv6((9, 10), (8, 8), (7, 7), (6, 6), (5, 5), (0, 4), [
                "所有组员均出镜录制，都很熟悉讲解内容，分工明确，表情自然生动",
                "所有组员参与说明，个别组员表情略显不自然，对讲解内容不太熟悉",
                "不是所有组员都参与说明（缺1位），出镜者表情比较生硬",
                "仅少数组员参与说明",
                "出镜说明极其敷衍",
                "完全没有团队协作迹象",
            ]),
        },
        {
            "key": "production", "name": "视频制作质量", "max": 10,
            "star": False, "judge": "frames",
            "levels": _lv6((9, 10), (8, 8), (7, 7), (6, 6), (5, 5), (0, 4), [
                "视频以原创录制为主。剪辑流畅，搭配了合适的配乐、字幕与图片，创意十足",
                "视频剪辑较完整，有基本的图片和字幕搭配，但呈现方式略显传统",
                "剪辑较为简单或略显仓促，未能很好地利用辅助素材；不能准时呈交或内容不全",
                "几乎没有剪辑，画面或声音质量较差，原创比例偏低",
                "视频时长严重不足，或只是简单的片段堆砌，缺乏基本逻辑",
                "未能按要求提交视频，或视频中原创录制的画面极少，多数是网络视频或画面",
            ]),
        },
    ],
    "precheck": {
        "max_duration_sec": 420,
        "en_ratio_limit": 0.10,
        "face_required": True,
        "manual_items": ["准时呈交", "已提交旁白文字稿", "文件命名规范",
                         "原创比例达标（人物解说/现场探访为原创）",
                         "疑似盗用他人视频或抄袭（勾选即全组0分）"],
    },
    "stance": (
        "本作业对象是新加坡中二高级华文学生，目的是练习口试话题的口语表达，"
        "不是专业影片制作。评分锚点如下，优先于你的默认严格倾向：\n"
        "1. 先整体定档、再给维度分：先按六档总表判定本组属于哪一档，"
        "再给四个维度打分；若四维之和跳出该档区间，回头调整维度分，不改档。\n"
        "2. 评分基准是中二学生的日常口语水平，不是口试考官标准。敢开口、"
        "说得清楚、听众能跟上内容，语言表达即从二上（13分）起评，再往上加。\n"
        "3. 单项瑕疵只在档内扣分，一条最多扣1分，绝不因单一瑕疵跨档下调。\n"
        "4. 换镜头、换讲者之间的自然停顿，以及实地拍摄的环境噪音，"
        "【禁止】判为口语不流畅；只有句子内部的结巴、重复、卡顿才算。\n"
        "5. 有现场探访、人物访谈、多机位、自制字幕或配乐等任一用心之处的，"
        "视频制作质量给一上档。\n"
        "6. 判定「缺1位组员」之前必须先从关键帧数清实际出镜人数；"
        "数不清就不扣分，把不确定写进 note。\n"
        "7. 判定「内容跑题」之前必须先核对本组话题的要点清单；学生讲的内容"
        "只要落在任一要点范围内就算切题，不因为讲得浅而判跑题。\n"
        "8. 转写稿是机器识别的，个别错字与专有名词识别错误"
        "（如「新生水」「集水区」「小贩中心」）不计入学生的语言错误。\n"
        "9. 照读封顶（2026-08-04 刘老师裁定 C 案）：明显捧着 iPad/手机/讲稿"
        "照念的组员，其【个人总分】封顶 39 分（二下「合格」档顶）。"
        "判定以画面证据为准，由代码强制执行，不依赖你自行减分。"
    ),
}

GROUP_VIDEO_REQUIREMENTS = (
    "学生4-5人一组，抽签选定一个社会话题，制作约5分钟的口试题篇录影。"
    "每人必须根据话题下面的要点讲其中一个要点（每人约1分钟），每人都必须出镜。"
    "重点评价每个人的语言表达能力，不可以看着iPad或讲稿生硬念读。"
    "可部分使用网络视频，但「人物解说」和「现场探访」必须是原创录制；"
    "盗用他人视频或全篇抄袭一律0分。全程华文（夹杂英文不超过10%）。\n"
    "九个话题及要点：\n"
    "一、新加坡的水资源和节约用水：水的来源（集水区/海水淡化/新生水/进口水）；"
    "政府如何推动节约用水；学校和家庭的节水措施；气候变化与人口增长下的挑战与应对；节水倡议。\n"
    "二、电子垃圾和环保：什么是电子垃圾；新加坡如何处理；带来的环境问题；"
    "如何在日常生活中减少；让大家更愿意回收的创意点子。\n"
    "三、新加坡的社区菜园：什么是社区菜园及设立原因；好处（邻里/环境/教育/粮食自给）；"
    "面对的挑战；参观或采访的菜园特色；自己设计的小菜园创意。\n"
    "四、新加坡组屋的环保设计：绿色建筑特点；节能设施；居民如何参与环保；"
    "政府政策与未来规划；观察到的环保细节与改进建议。\n"
    "五、新加坡组屋区的设施：底层设施及其便利；游乐场设计概念；"
    "各年龄层最常使用的设施与改进建议；小组设计的理想组屋设施。\n"
    "六、新加坡的小贩文化：起源与历史；独特之处；面对的挑战；"
    "如何传承与保护；最喜欢的小贩故事或推荐摊位。\n"
    "七、如何识破假信息：定义与例子；危害；如何辨别真假；"
    "政府和媒体如何打击；小组设计的识假小贴士或短剧。\n"
    "八、新加坡的多元文化：主要种族与文化；日常生活中的体现；"
    "学校和社区如何推广；优势与挑战；最喜欢的多元文化活动。\n"
    "九、新加坡的文化遗产：植物园列入世界文化遗产；历史与文化价值；"
    "对新加坡人的意义；怎样保护和爱护；最喜欢的世界文化遗产体验。"
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


# ── 总分等级表（2026-08-04 新增）──────────────────────────────
# 小组视频作业的评分表是【整体量表】：先按六档判定小组等级，再在档内
# 按每位组员的表现取上下限（刘老师原话："二上40-44，表现优秀的取44，
# 结结巴巴无法脱稿的取40"）。四个维度分只是推导路径，最终等级以本表为准。
# 结构：rubric["total_levels"] = [{"grade":"一上","label":"优异","lo":50,"hi":60}, …]
# 模板没有这一项时（如旧的技能讲解模板），个人分功能自动关闭，行为不变。

def total_levels(rubric):
    return rubric.get("total_levels") or []


def total_level_of(rubric, total):
    """总分 → 等级档。没配总分等级表时返回 None。"""
    for lv in total_levels(rubric):
        if lv["lo"] <= total <= lv["hi"]:
            return lv
    return None


def member_mode(rubric):
    """是否是"一个视频多名组员、需要逐人给分"的小组作业模板。"""
    return bool(rubric.get("member_mode")) and bool(total_levels(rubric))


def reading_cap_total(rubric):
    """照读学生的【个人总分】封顶线。没配置则返回 None（不封顶）。"""
    v = rubric.get("reading_cap_total")
    return int(v) if isinstance(v, (int, float)) and v >= 0 else None


# 2026-07-28 决策（刘老师）：明显照读 → 口语类维度封顶到「及格」档上限。
# 因为评分模板是老师可编辑的数据（不是写死的常量），封顶值不能硬编码成 12，
# 必须从当前 rubric 里推出来。取值优先级如下，任一命中即返回。
PASS_LABEL_KEYWORDS = ("及格", "合格", "尚可", "pass")


def pass_ceiling(dim):
    """返回该维度「及格」档的分数上限（照读封顶用）。

    优先级：
      0) dim["reading_cap"] 显式数字 → 直接用（2026-08-04 新增，最高优先级）
      1) label 或 desc 含"及格/合格"等字样的档位 → 取其 hi
      2) 档位数 ≥3 → 取从高到低第 3 档（A/B/C 的 C）的 hi
      3) 兜底 → 满分的 60%（向下取整）
    默认《短视频评分表》口语表达：C档「及格」9–12 → 返回 12。

    # 2026-08-04 决策：必须新增显式 reading_cap 字段。
    # 起因：中二小组视频评分表里「二下·合格」(35-39) 和「三上·及格」(30-34)
    # 两个档位的 label 都会命中 PASS_LABEL_KEYWORDS，靠关键词猜会拿到哪一个
    # 取决于档位数组顺序，结果不确定——封顶线飘 5 分是不可接受的。
    # 关键词兜底逻辑同时改为：多个档位命中时取 hi 最大者（宁可宽，不误伤）。
    """
    cap = dim.get("reading_cap")
    if isinstance(cap, (int, float)) and cap >= 0:
        return int(min(cap, dim.get("max", cap)))

    lvs = dim.get("levels") or []
    hits = []
    for lv in lvs:
        text = f"{lv.get('label', '')}{lv.get('desc', '')}".lower()
        if any(kw in text for kw in PASS_LABEL_KEYWORDS):
            try:
                hits.append(int(lv["hi"]))
            except (KeyError, TypeError, ValueError):
                continue
    if hits:
        return max(hits)
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

    # 2026-08-04：总分等级表体检（配了才查）。个人分要在档区间内取位置，
    # 区间有洞或重叠会让某些总分找不到档 → 个人分算不出来。
    tls = rubric.get("total_levels") or []
    if tls:
        covered = set()
        for lv in tls:
            lo, hi = lv.get("lo", -1), lv.get("hi", -1)
            if not (0 <= lo <= hi <= actual):
                errs.append(f"总分等级「{lv.get('grade','?')}」区间越界")
                continue
            rng = set(range(int(lo), int(hi) + 1))
            if rng & covered:
                errs.append(f"总分等级「{lv.get('grade','?')}」与其它档重叠")
            covered |= rng
        miss = [s for s in range(0, actual + 1) if s not in covered]
        if miss:
            errs.append(f"总分等级表有分数没被覆盖：{miss[:8]}")
    if rubric.get("member_mode") and not tls:
        errs.append("勾了「小组逐人给分」但没有总分等级表")
    cap = rubric.get("reading_cap_total")
    if cap is not None and not (isinstance(cap, (int, float))
                                and 0 <= cap <= actual):
        errs.append("照读个人总分封顶值无效")
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
    # 2026-08-04：新字段一律补默认值——旧模板（技能讲解60分）拿到的是
    # member_mode=False、total_levels=[]，个人分功能自动关闭，行为完全不变。
    rubric.setdefault("member_mode", False)
    rubric.setdefault("total_levels", [])
    rubric.setdefault("reading_cap_total", None)
    rubric["total_max"] = sum(d["max"] for d in dims(rubric)
                              if isinstance(d.get("max"), int))
    return rubric
