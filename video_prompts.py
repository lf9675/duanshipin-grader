# -*- coding: utf-8 -*-
"""
video_prompts.py — 短视频批改提示词（2026-07-20 常年化改版）
════════════════════════════════════════════════════════
全部提示词按任务快照的评分标准(rubric)和题目要求(requirements)动态生成：
  1. build_text_grading_system/user — DeepSeek 判 text / text_speech 维度
  2. build_frames_prompt            — GLM-4V 判 frames 维度
  3. RUBRIC_PARSE_SYSTEM            — DeepSeek 把老师粘贴的评分表解析成模板
  4. REVIEW_AGG_SYSTEM              — 讲评聚合（含人名匿名化）

设计原则不变：why_and_how 底线；证据逐字引用带时间戳；JSON 输出；
temperature=0.2；PPT 素材匿名化。
"""

from video_rubric import (rubric_text_for_prompt, dims_of_judge, total_max,
                          JUDGE_LABELS)

PROMPTS_VERSION = "2026-07-20-b"


# ─────────────────────────────────────────────────────────────
# 1. DeepSeek 文本批改（text + text_speech 维度）
# ─────────────────────────────────────────────────────────────

def build_text_grading_system(rubric, requirements):
    text_dims = dims_of_judge(rubric, ("text", "text_speech"))
    speech_keys = [d["key"] for d in dims_of_judge(rubric, ("text_speech",))]
    dim_json_parts = []
    for d in text_dims:
        extra = ('"confidence": "low", "note": "请老师抽听确认：…", '
                 if d["key"] in speech_keys else '"confidence": "high", ')
        dim_json_parts.append(
            f'   "{d["key"]}": {{"score": 数字, "grade": "档位字母", {extra}'
            f'"comment": "两三句评语", '
            f'"evidence": [{{"t": "0:32", "quote": "转写稿原文", "point": "为何加/扣分"}}], '
            f'"issues": [{{"problem": "问题", "why": "为什么", "how": "怎么改（含示范）"}}]}}')
    speech_rule = ""
    if speech_keys:
        speech_rule = (
            f"3. 口语类维度（{', '.join(speech_keys)}）你只能从文本和客观指标间接判断"
            f"（流畅度、语速、停顿、填充词、口头禅），咬字和感染力你听不到——"
            f"这些维度必须 confidence 标 \"low\"，并在 note 写明老师需要抽听确认什么。\n")
    stance = rubric.get("stance", "")
    stance_block = (f"【宽严指引（老师定，必须遵守）】\n{stance}\n\n"
                    if stance else "")
    return f"""你是新加坡中学华文老师的批改助手。学生的作业是拍短视频，本次作业要求如下：
{requirements or '（老师未填写具体要求）'}

{stance_block}你收到的是视频的【语音转写稿】（带时间戳）和【口语客观指标】。请只评下列维度（画面类维度由另一个系统评，老师手评类维度不用你评）：

{rubric_text_for_prompt(rubric, ('text', 'text_speech'))}

重要规则：
0. 评分顺序：先判断该维度整体属于哪一档，再在档内定位分数。宽严指引里的起评锚点优先于你的默认判断；单条瑕疵只在档内扣分（每条最多1分），不得因单条瑕疵跨档。引用不存在的缺陷（如转写稿里明明有总结却说缺总结）是严重错误。
1. 转写稿可能有少量转写错字，明显是同音转写错误的不要算学生的问题。
2. 每个维度的 evidence 必须逐字引用转写稿原文（不许改写），并带时间戳，好的差的都要引。
{speech_rule}4. issues 里每条问题必须包含"为什么这是问题"和"具体怎么改"（给出改法示范），语气是教学生，不是下命令。
5. 亮点(praise)要具体到句子，不要空泛表扬。
6. 分数给建议值（必须落在该维度 0 到满分之间），最终由老师定夺。

只输出 JSON（不要 markdown 围栏），结构：
{{
 "dimensions": {{
{chr(10).join(dim_json_parts)}
 }},
 "one_line_comment": "给学生的一句话总评（先扬后抑，具体）",
 "top_issue": "最主要的一个问题（十字以内短语）",
 "top_strength": "最突出的一个优点（十字以内短语）",
 "next_level_advice": "距上一档最该做的一件事（一两句）",
 "praise_quotes": [{{"t": "时间", "quote": "值得全班学习的原句", "why": "好在哪"}}]
}}"""


def build_text_grading_user(topic, student_ids, transcript_segments, metrics):
    lines = [f"[{_fmt_t(s['start'])}-{_fmt_t(s['end'])}] {s['text']}"
             for s in transcript_segments]
    m = metrics
    return f"""本次题目：{topic or '（未填写）'}
学号：{'、'.join(student_ids)}（{'小组' if len(student_ids) > 1 else '个人'}）

【口语客观指标】（本地测量，供口语类维度参考）
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
# 2. GLM-4V 关键帧批改（frames 维度，8帧拼贴单图）
# ─────────────────────────────────────────────────────────────

def build_frames_prompt(rubric, n_frames, n_students, requirements):
    stance = rubric.get("stance", "")
    stance_block = (f"【宽严指引（老师定，必须遵守）】\n{stance}\n\n"
                    if stance else "")
    frame_dims = dims_of_judge(rubric, ("frames",))
    face_req = rubric.get("precheck", {}).get("face_required", False)
    face_field = ('"face_ok": true或false, ' if face_req else "")
    face_rule = ("2. face_ok：画面中是否出现过人脸正面/侧面"
                 "（作业硬性要求出镜，出现过即算，不必每帧都有）。\n"
                 if face_req else "")
    dim_json = ", ".join(
        f'"{d["key"]}": {{"score": 0到{d["max"]}整数, "grade": "档位字母"}}'
        for d in frame_dims)
    return f"""下面这张图是按时间顺序从左到右、从上到下排列的关键帧拼贴（共{n_frames}帧）。这是新加坡中学生短视频作业，本组共 {n_students} 名学生。作业要求：{requirements or '（未填写）'}

请按下列标准评画面类维度：

{rubric_text_for_prompt(rubric, ('frames',))}

{stance_block}判断要点：
1. 你看不到动态画面和剪辑，只按静帧判断，把不确定写进 note。
{face_rule}3. 画面是否清晰（对焦、光线）、构图是否合理、演示对象是否看得清楚。

只输出 JSON（不要 markdown 围栏）：
{{"dimensions": {{{dim_json}}}, {face_field}
 "comment": "两句评语，具体指出画面优缺点",
 "note": "需要老师亲眼确认的事项（没有则空字符串）"}}"""


# ─────────────────────────────────────────────────────────────
# 3. 评分表解析（老师粘贴/上传评分表 → 结构化模板）
# ─────────────────────────────────────────────────────────────

RUBRIC_PARSE_SYSTEM = """你是评分表结构化助手。老师会粘贴一份视频作业的评分标准（可能来自 Word 表格复制，格式凌乱）。请解析成严格的 JSON 模板。

规则：
1. 每个评分维度：name（原文名称）、max（满分整数）、star（原文标注重点/⭐的为 true）、levels（各档位：grade 用 A/B/C/D… 顺序字母，label 用原文如"优秀"，lo/hi 分数区间整数，desc 逐字保留原文描述）。
2. 档位区间必须完整覆盖 0 到满分、不重叠。原文只给了部分区间的，合理补全到覆盖（如最低档补到0），补全处在 desc 末尾加"（区间为系统补全）"。
3. 每个维度标 judge（判定方式）：
   - "text"：能从语音转写文字判断（内容、结构、步骤、观点等）
   - "text_speech"：口语/朗读/表达类（AI 只能参考文字与语速指标，听不到咬字）
   - "frames"：画面/拍摄/镜头/出镜/字幕/服装道具等视觉类
   - "manual"：转写和画面都判不了的（如守时提交、组内合作态度）
4. precheck：从原文提取准入/硬性要求——max_duration_sec（时长上限秒数，没提则 0）、en_ratio_limit（语言夹杂上限小数，没提则 1.0）、face_required（是否要求出镜 true/false）、manual_items（其它需老师人工勾选的硬性要求，字符串数组）。
5. desc 一律逐字保留原文，不许改写润色。
6. 无法解析成评分维度的文字忽略，不要编造维度。

只输出 JSON（不要 markdown 围栏）：
{"name": "从原文取或概括的模板名",
 "dimensions": [{"name":"…","max":25,"star":true,"judge":"text",
   "levels":[{"grade":"A","label":"优秀","lo":21,"hi":25,"desc":"…"}]}],
 "precheck": {"max_duration_sec":240,"en_ratio_limit":0.10,
   "face_required":true,"manual_items":["…"]}}"""


# ─────────────────────────────────────────────────────────────
# 4. 讲评聚合（生成 PPT 素材，含匿名化）
# ─────────────────────────────────────────────────────────────

REVIEW_AGG_SYSTEM = """你是新加坡中学华文老师的讲评助手。你收到全班视频作业的批改结果汇总（每份含各维度问题、亮点、佳句）。请生成讲评 PPT 的素材。

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
    blocks = []
    for it in items:
        r = it.get("ai_result") or {}
        dims_r = r.get("dimensions", {})
        issues = []
        for k, info in dims_r.items():
            for iss in (info or {}).get("issues", []):
                issues.append(iss.get("problem", ""))
        praise = [f"[{p.get('t','')}] {p.get('quote','')}"
                  for p in r.get("praise_quotes", [])]
        blocks.append(
            f"— 学号{'、'.join(it['student_ids'])}：总分{it.get('final_total','?')}；"
            f"主要问题：{r.get('top_issue','')}；问题清单：{'；'.join(issues[:5])}；"
            f"佳句：{'；'.join(praise[:3])}"
        )
    return (f"题目：{topic}\n全班 {len(items)} 份作业批改汇总：\n"
            + "\n".join(blocks))
