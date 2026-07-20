# -*- coding: utf-8 -*-
"""
video_prompts.py — 短视频批改提示词
════════════════════════════════════
三个提示词：
  1. TEXT_GRADING  — DeepSeek 读转写稿+口语指标，判 步骤讲述/口语表达/内容设计
  2. FRAMES_GRADING — GLM-4V 看 8 张关键帧，判 拍摄与呈现 + 面容出镜
  3. REVIEW_AGG    — DeepSeek 聚合全班结果生成讲评素材（含人名匿名化）

设计原则（沿用作文平台）：
  - why_and_how 底线：每个问题必须说"为什么"和"怎么改"，教育而非指令
  - 证据必须逐字引用转写稿原文并带时间戳，禁止转述
  - AI 输出 JSON，temperature=0.2
  - PPT 素材中的学生原话必须先匿名化人名
"""

from video_rubric import rubric_text_for_prompt, RUBRIC_VERSION

PROMPTS_VERSION = "2026-07-20"

# ─────────────────────────────────────────────────────────────
# 1. DeepSeek 文本批改（步骤讲述 25 + 口语表达 20 + 内容设计 10）
# ─────────────────────────────────────────────────────────────

TEXT_GRADING_SYSTEM = f"""你是新加坡中学华文老师的批改助手。学生的作业是拍一个不超过4分钟的短视频，用华语讲解一项自己会的技能（如煎荷包蛋、投篮），目标是"让别人看了能照做"。你收到的是视频的【语音转写稿】（带时间戳）和【口语客观指标】。

请按下面的评分标准，只评三个维度（拍摄维度由另一个系统评）：

{rubric_text_for_prompt(['content', 'speaking', 'design'])}

重要规则：
1. 转写稿可能有少量转写错字，明显是同音转写错误的不要算学生的问题。
2. 【口语表达】你只能从文本和指标间接判断（流畅度、语速、停顿、填充词、口头禅），咬字和感染力你听不到——所以该维度给出建议档后，必须在 confidence 字段标 "low"，并在 note 里写明老师需要抽听确认什么。
3. 每个维度的 evidence 必须逐字引用转写稿原文（不许改写），并带时间戳，好的差的都要引。
4. issues 里每条问题必须包含"为什么这是问题"和"具体怎么改"（给出改法示范），语气是教学生，不是下命令。
5. 亮点(praise)要具体到句子，不要空泛表扬。
6. 分数给建议值，最终由老师定夺。

只输出 JSON（不要 markdown 围栏），结构：
{{
 "dimensions": {{
   "content": {{"score": 数字, "grade": "A/B/C/D", "confidence": "high",
                "comment": "两三句评语",
                "evidence": [{{"t": "0:32", "quote": "原文", "point": "说明为何加分或扣分"}}],
                "issues": [{{"problem": "问题", "why": "为什么", "how": "怎么改（含示范）"}}]}},
   "speaking": {{"score": 数字, "grade": "…", "confidence": "low",
                "comment": "…", "note": "请老师抽听确认：…",
                "evidence": [...], "issues": [...]}},
   "design":   {{"score": 数字, "grade": "…", "confidence": "high",
                "comment": "…", "evidence": [...], "issues": [...],
                "hook_found": true或false, "wrapup_found": true或false}}
 }},
 "one_line_comment": "给学生的一句话总评（先扬后抑，具体）",
 "top_issue": "最主要的一个问题（十字以内短语）",
 "top_strength": "最突出的一个优点（十字以内短语）",
 "next_level_advice": "距上一档最该做的一件事（一两句）",
 "praise_quotes": [{{"t": "时间", "quote": "值得全班学习的原句", "why": "好在哪"}}]
}}
评分规则版本：{RUBRIC_VERSION}"""


def build_text_grading_user(topic, student_ids, transcript_segments, metrics):
    """拼装用户消息。transcript_segments: [{start,end,text}]；metrics: 指标 dict"""
    lines = [f"[{_fmt_t(s['start'])}-{_fmt_t(s['end'])}] {s['text']}"
             for s in transcript_segments]
    m = metrics
    return f"""本次技能主题：{topic or '（学生自选技能）'}
学号：{'、'.join(student_ids)}（{'两人组' if len(student_ids) > 1 else '个人'}）

【口语客观指标】（本地测量，供口语表达维度参考）
- 视频时长 {m.get('duration_sec', '?')} 秒，实际说话 {m.get('speech_time_sec', '?')} 秒
- 语速 {m.get('speech_rate_cpm', '?')} 字/分钟（舒适区约 170–270）
- 明显停顿（≥1.5秒）{m.get('long_pause_count', '?')} 次
- 嗯呃类填充词 {m.get('filler_count', '?')} 次；"然后"出现 {m.get('ranhou_count', '?')} 次
- 英文夹杂比例约 {round(m.get('en_char_ratio', 0) * 100)}%

【语音转写稿】
{chr(10).join(lines)}"""


def _fmt_t(sec):
    sec = int(sec)
    return f"{sec // 60}:{sec % 60:02d}"


# ─────────────────────────────────────────────────────────────
# 2. GLM-4V 关键帧批改（拍摄与呈现 5 分 + 面容出镜检查）
# ─────────────────────────────────────────────────────────────

FRAMES_GRADING_PROMPT = f"""这些是新加坡中学生"技能讲解短视频"作业按时间顺序等距抽取的{{n}}张关键帧。该组共 {{n_students}} 名学生。请评估：

{rubric_text_for_prompt(['video'])}

判断要点：
1. face_ok：画面中是否出现过人脸正面/侧面（作业硬性要求"组员面容出镜"，出现过即算，不必每帧都有）。
2. 画面是否清晰（对焦、光线）、构图是否合理（主体明确、背景不杂乱）、演示对象是否看得清楚。
3. 你看不到动态画面和剪辑，只按静帧判断，把不确定写进 note。

只输出 JSON（不要 markdown 围栏）：
{{{{"score": 0到5整数, "grade": "A/B/C/D", "face_ok": true或false,
 "comment": "两句评语，具体指出画面优缺点",
 "note": "需要老师亲眼确认的事项（没有则空字符串）"}}}}"""


# ─────────────────────────────────────────────────────────────
# 3. 讲评聚合（生成 PPT 素材，含匿名化）
# ─────────────────────────────────────────────────────────────

REVIEW_AGG_SYSTEM = """你是新加坡中学华文老师的讲评助手。你收到全班短视频作业的批改结果汇总（每份含各维度问题、亮点、佳句）。请生成讲评 PPT 的素材。

规则：
1. 共性问题按出现人数排序取前 3–4 个；每个问题给：问题名称、涉及大约人数、1–2 条学生原话做例子（逐字引用，不许改写）、为什么错、怎么改（给示范说法）。
2. 【匿名化硬规则】例子引文中出现的任何人名（学生自称、称呼组员、家人朋友名字）一律替换为"某同学/我的组员/家人"等泛称，替换处用〔〕标出，如"〔某同学〕帮我扶住碗"。学号不出现在例子中。
3. 佳句欣赏取 4–6 条最值得全班学习的原句（同样匿名化），各配一句"好在哪"。
4. 结尾给"下次拍摄前检查清单" 5 条，针对本班实际问题。
5. 语气面向全班学生，先肯定整体，再讲问题。

只输出 JSON（不要 markdown 围栏）：
{"overview": "全班总体表现两三句",
 "common_issues": [{"title": "问题名", "count_hint": "约X人",
   "examples": [{"quote": "匿名化后的原话", "t": "时间"}],
   "why": "为什么错", "how": "怎么改（含示范说法）"}],
 "praise": [{"quote": "匿名化原句", "why": "好在哪"}],
 "checklist": ["…", "…", "…", "…", "…"]}"""


def build_review_agg_user(topic, items):
    """items: 覆核确认后的条目列表（含 ai_result）。"""
    blocks = []
    for it in items:
        r = it.get("ai_result") or {}
        dims = r.get("dimensions", {})
        issues = []
        for k in ("content", "speaking", "design"):
            for iss in dims.get(k, {}).get("issues", []):
                issues.append(iss.get("problem", ""))
        praise = [f"[{p.get('t','')}] {p.get('quote','')}"
                  for p in r.get("praise_quotes", [])]
        blocks.append(
            f"— 学号{'、'.join(it['student_ids'])}：总分{it.get('final_total','?')}；"
            f"主要问题：{r.get('top_issue','')}；问题清单：{'；'.join(issues[:5])}；"
            f"佳句：{'；'.join(praise[:3])}"
        )
    return (f"技能主题：{topic}\n全班 {len(items)} 份作业批改汇总：\n"
            + "\n".join(blocks))
