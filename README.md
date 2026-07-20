# 华文通 · 短视频批改（duanshipin-grader）

面向新加坡中学华文老师的短视频作业批量批改平台。
与作文批改平台（zuowenpigai / zuowenpigai-teacher）**完全独立**：
独立仓库、独立 Streamlit 部署、评分规则独立（video_rubric.py，
与引擎母本 prompts.py 无关）。数据库复用教师版 Supabase 项目，
所有表带 video_ 前缀（2026-07-20 决策）。

## 流程（2026-07-20 改版：零本地安装）
学生视频传谷歌课室 → 老师批量下载 → **直接上传本平台**（单次 ≤1GB，
可分批，互不覆盖）→ 服务器抽音频/关键帧/转写（**提取完立刻删除视频**）
→ 队列批改（DeepSeek 判文本三维度，GLM-4V 判拍摄维度）→
覆核（异常标黄）→ 下载总包（每生PDF + 班级Excel + 讲评PPT）→
谷歌课室逐个退还。

备用通道：家里电脑能装 Python 时，可用 local_tool/yuchuli.py 本地
预处理后上传批改包 zip，云端零转写负担、速度最快。

## 部署（Streamlit Community Cloud）
1. fonts/ 目录放 NotoSansSC-Regular.ttf 和 NotoSansSC-Bold.ttf
   （从 zuowenpigai-teacher 仓库的 fonts/ 复制同一套）。
2. Streamlit Secrets 配两条：
   - `SUPABASE_DB_URL`：教师版同一条 Pooler(6543) 连接串
   - `APP_PASSWORD`：平台口令（自定）
3. packages.txt 会让云端装好系统 ffmpeg；requirements.txt 含
   faster-whisper（云端转写）。首次运行自动建 video_ 前缀表。
4. DeepSeek / GLM Key 由老师在侧栏输入，只存会话不落库。

## 云端转写须知
- 转写模型默认 tiny（云端算力有限）；侧栏可切 small（更准更慢）。
- 建议首次先传 3-5 个视频试跑，确认速度与转写质量再传全班。
- 转写稿入库后断线不丢；覆核抽听音频/关键帧只存会话，断线后
  重传对应视频即可补。

## PDPA
- 视频原片：提取完音频和关键帧后**立刻删除**，不落数据库不留存。
- 只有文本入库（转写稿/指标/AI结果）；音频和关键帧含学生人脸与
  声音，仅存会话临时目录，会话结束即失。
- 学生姓名不入库；PPT 引用原话前由 AI 匿名化人名。

## 版本
- 评分规则 video_rubric.py: 2026-07-20
- 引擎 video_engine.py: video-1.0-20260720
- 预处理 video_ingest.py: 2026-07-20（服务器端）
- 导出 video_export.py: video-1.0-20260720
