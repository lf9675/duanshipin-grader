# -*- coding: utf-8 -*-
"""
video_db.py — 短视频批改数据库层
════════════════════════════════
- 复用教师版 Supabase 项目（2026-07-20 决策），所有表加 video_ 前缀，
  与作文批改的表互不干扰。连接惯例与教师版 database.py 一致：
  Pooler 6543 + 连接池 + DictCursor + ON CONFLICT DO UPDATE。
- PDPA 硬规则：只存文本（转写稿/指标/AI结果/老师改动）。
  关键帧 jpg 和 audio.mp3 含学生人脸与声音，一律不入库——
  批改期间放会话临时目录，会话结束即失。断线续跑靠重新上传批改包。
- Streamlit Secrets 需要 SUPABASE_DB_URL（教师版同一条连接串）。
"""

import json

import streamlit as st
import psycopg2
from psycopg2 import pool as _pgpool
from psycopg2.extras import DictCursor

SCHEMA_VERSION = "2026-07-20"


@st.cache_resource
def _get_pool():
    return _pgpool.SimpleConnectionPool(
        1, 6,
        dsn=st.secrets["SUPABASE_DB_URL"],
        cursor_factory=DictCursor,
    )


class _PooledConn:
    """close = 回滚 + 归还连接池（与教师版 database.py 同款防泄漏包装）。"""
    def __init__(self, raw, direct=False):
        self._raw = raw
        self._direct = direct
        self._closed = False

    def cursor(self, *a, **k):
        return self._raw.cursor(*a, **k)

    def commit(self):
        self._raw.commit()

    def rollback(self):
        self._raw.rollback()

    def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            self._raw.rollback()
        except Exception:
            pass
        if self._direct:
            try:
                self._raw.close()
            except Exception:
                pass
            return
        try:
            _get_pool().putconn(self._raw)
        except Exception:
            pass

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass


def get_conn():
    try:
        return _PooledConn(_get_pool().getconn())
    except _pgpool.PoolError:
        raw = psycopg2.connect(st.secrets["SUPABASE_DB_URL"],
                               cursor_factory=DictCursor)
        return _PooledConn(raw, direct=True)


DDL = """
CREATE TABLE IF NOT EXISTS video_rubrics (
    id          BIGSERIAL PRIMARY KEY,
    teacher_id  TEXT NOT NULL DEFAULT 'default',
    name        TEXT NOT NULL,
    rubric      JSONB NOT NULL,           -- 评分标准（结构见 video_rubric.py）
    requirements TEXT NOT NULL DEFAULT '', -- 题目要求默认文本
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS video_batch_jobs (
    id          BIGSERIAL PRIMARY KEY,
    teacher_id  TEXT NOT NULL DEFAULT 'default',
    class_name  TEXT NOT NULL,
    topic       TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'grading',  -- grading / reviewing / done
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- 2026-07-20 常年化：任务保存模板【快照】，改模板不影响历史任务
ALTER TABLE video_batch_jobs ADD COLUMN IF NOT EXISTS rubric JSONB;
ALTER TABLE video_batch_jobs ADD COLUMN IF NOT EXISTS requirements TEXT NOT NULL DEFAULT '';
CREATE TABLE IF NOT EXISTS video_batch_items (
    id            BIGSERIAL PRIMARY KEY,
    job_id        BIGINT NOT NULL REFERENCES video_batch_jobs(id) ON DELETE CASCADE,
    item_key      TEXT NOT NULL,          -- 学号串，如 '05' 或 '05_12'
    student_ids   JSONB NOT NULL,         -- ["05","12"]
    source_file   TEXT NOT NULL DEFAULT '',
    transcript    JSONB,                  -- segments（纯文本+时间戳）
    metrics       JSONB,                  -- 口语客观指标
    precheck      JSONB,                  -- 准入检查（自动项+老师勾选项）
    ai_result     JSONB,                  -- DeepSeek + GLM-4V 合并结果
    final_scores  JSONB,                  -- 复核后各维度最终分 {"content":22,...}
    teacher_comment TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'pending',
                  -- uploaded(已传待预处理) / pending(待批改) / graded /
                  -- confirmed / rejected(打回) / failed
    error_msg     TEXT NOT NULL DEFAULT '',
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (job_id, item_key)
);
CREATE INDEX IF NOT EXISTS idx_video_items_job ON video_batch_items(job_id);
"""


def init_schema():
    from video_rubric import DEFAULT_RUBRIC, DEFAULT_REQUIREMENTS
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(DDL)
        # 播种内置模板（首次运行）
        cur.execute("SELECT count(*) FROM video_rubrics")
        if cur.fetchone()[0] == 0:
            cur.execute(
                "INSERT INTO video_rubrics (name, rubric, requirements) "
                "VALUES (%s, %s, %s)",
                (DEFAULT_RUBRIC["name"],
                 json.dumps(DEFAULT_RUBRIC, ensure_ascii=False),
                 DEFAULT_REQUIREMENTS))
        # 老任务回填默认模板快照（升级兼容）
        cur.execute(
            "UPDATE video_batch_jobs SET rubric=%s, requirements=%s "
            "WHERE rubric IS NULL",
            (json.dumps(DEFAULT_RUBRIC, ensure_ascii=False),
             DEFAULT_REQUIREMENTS))
        # 2026-07-21 迁移v2：内置模板（及其任务快照）始终与代码内
        # DEFAULT_RUBRIC 同步——内置模板是代码资产，锚点更新要跟上
        cur.execute(
            "UPDATE video_rubrics SET rubric=%s "
            "WHERE rubric->>'name' = %s "
            "AND rubric->>'stance' IS DISTINCT FROM %s",
            (json.dumps(DEFAULT_RUBRIC, ensure_ascii=False),
             DEFAULT_RUBRIC["name"], DEFAULT_RUBRIC["stance"]))
        cur.execute(
            "UPDATE video_batch_jobs SET rubric=%s "
            "WHERE rubric->>'name' = %s "
            "AND rubric->>'stance' IS DISTINCT FROM %s",
            (json.dumps(DEFAULT_RUBRIC, ensure_ascii=False),
             DEFAULT_RUBRIC["name"], DEFAULT_RUBRIC["stance"]))
        conn.commit()
    finally:
        conn.close()


# ── rubric 模板 ──────────────────────────────────────────────

def list_rubrics(teacher_id="default"):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM video_rubrics WHERE teacher_id=%s "
                    "ORDER BY id", (teacher_id,))
        return cur.fetchall()
    finally:
        conn.close()


def create_rubric(name, rubric, requirements, teacher_id="default"):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO video_rubrics (teacher_id, name, rubric, requirements) "
            "VALUES (%s,%s,%s,%s) RETURNING id",
            (teacher_id, name, json.dumps(rubric, ensure_ascii=False),
             requirements))
        rid = cur.fetchone()[0]
        conn.commit()
        return rid
    finally:
        conn.close()


def delete_rubric(rubric_id):
    """只删模板本身；历史任务持有快照不受影响。"""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM video_rubrics WHERE id=%s", (rubric_id,))
        conn.commit()
    finally:
        conn.close()


# ── jobs ─────────────────────────────────────────────────────

def create_job(class_name, topic, rubric, requirements, teacher_id="default"):
    """rubric 以【快照】形式存入任务（2026-07-20 决策）。"""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO video_batch_jobs "
            "(teacher_id, class_name, topic, rubric, requirements) "
            "VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (teacher_id, class_name, topic,
             json.dumps(rubric, ensure_ascii=False), requirements))
        job_id = cur.fetchone()[0]
        conn.commit()
        return job_id
    finally:
        conn.close()


def get_job(job_id):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM video_batch_jobs WHERE id=%s", (job_id,))
        return cur.fetchone()
    finally:
        conn.close()


def list_jobs(teacher_id="default", limit=20):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT j.*, "
            " (SELECT count(*) FROM video_batch_items i WHERE i.job_id=j.id) AS total, "
            " (SELECT count(*) FROM video_batch_items i WHERE i.job_id=j.id "
            "    AND i.status IN ('graded','confirmed','rejected')) AS done "
            "FROM video_batch_jobs j WHERE j.teacher_id=%s "
            "ORDER BY j.id DESC LIMIT %s",
            (teacher_id, limit))
        return cur.fetchall()
    finally:
        conn.close()


def set_job_status(job_id, status):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE video_batch_jobs SET status=%s WHERE id=%s",
                    (status, job_id))
        conn.commit()
    finally:
        conn.close()


# ── items ────────────────────────────────────────────────────

def create_uploaded_item(job_id, item_key, student_ids, source_file):
    """视频刚上传、还没预处理时插入一行占位（status='uploaded'）。
    重复上传已批改/已确认的学号不覆盖，避免分批上传互相打架。"""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT status FROM video_batch_items WHERE job_id=%s AND item_key=%s",
            (job_id, item_key))
        row = cur.fetchone()
        if row and row["status"] in ("graded", "confirmed", "rejected"):
            return False  # 已有更进一步的结果，跳过，不覆盖
        cur.execute(
            """INSERT INTO video_batch_items
               (job_id, item_key, student_ids, source_file, status)
               VALUES (%s,%s,%s,%s,'uploaded')
               ON CONFLICT (job_id, item_key) DO UPDATE SET
                 source_file = EXCLUDED.source_file,
                 status = 'uploaded', updated_at = now()""",
            (job_id, item_key, json.dumps(student_ids, ensure_ascii=False),
             source_file))
        conn.commit()
        return True
    finally:
        conn.close()


def next_uploaded_item(job_id):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM video_batch_items "
            "WHERE job_id=%s AND status='uploaded' ORDER BY item_key LIMIT 1",
            (job_id,))
        return cur.fetchone()
    finally:
        conn.close()


def save_preprocessed(item_id, transcript, metrics, precheck):
    """预处理完成：写入转写稿/指标/准入预判，状态推进到 pending（可送批改）。"""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE video_batch_items SET transcript=%s, metrics=%s, "
            "precheck=%s, status='pending', error_msg='', updated_at=now() "
            "WHERE id=%s",
            (json.dumps(transcript, ensure_ascii=False),
             json.dumps(metrics, ensure_ascii=False),
             json.dumps(precheck, ensure_ascii=False), item_id))
        conn.commit()
    finally:
        conn.close()


def upsert_item(job_id, item_key, student_ids, source_file,
                transcript, metrics, precheck):
    """入队。重复上传同一批改包时不覆盖已批改结果（断线续跑关键）。"""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO video_batch_items
               (job_id, item_key, student_ids, source_file,
                transcript, metrics, precheck)
               VALUES (%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (job_id, item_key) DO UPDATE SET
                 source_file = EXCLUDED.source_file,
                 transcript  = EXCLUDED.transcript,
                 metrics     = EXCLUDED.metrics,
                 updated_at  = now()""",
            (job_id, item_key, json.dumps(student_ids, ensure_ascii=False),
             source_file, json.dumps(transcript, ensure_ascii=False),
             json.dumps(metrics, ensure_ascii=False),
             json.dumps(precheck, ensure_ascii=False)))
        conn.commit()
    finally:
        conn.close()


def next_pending_item(job_id):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM video_batch_items "
            "WHERE job_id=%s AND status='pending' ORDER BY item_key LIMIT 1",
            (job_id,))
        return cur.fetchone()
    finally:
        conn.close()


def list_items(job_id):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM video_batch_items WHERE job_id=%s ORDER BY item_key",
            (job_id,))
        return cur.fetchall()
    finally:
        conn.close()


def save_ai_result(item_id, ai_result, final_scores):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE video_batch_items SET ai_result=%s, final_scores=%s, "
            "status='graded', error_msg='', updated_at=now() WHERE id=%s",
            (json.dumps(ai_result, ensure_ascii=False),
             json.dumps(final_scores, ensure_ascii=False), item_id))
        conn.commit()
    finally:
        conn.close()


def mark_failed(item_id, msg):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE video_batch_items SET status='failed', error_msg=%s, "
            "updated_at=now() WHERE id=%s", (str(msg)[:500], item_id))
        conn.commit()
    finally:
        conn.close()


def retry_failed(job_id):
    """失败重试分两类：已有转写稿的回 pending 重批（AI调用失败）；
    没有转写稿的回 uploaded（预处理失败，需重新上传该视频）。"""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE video_batch_items SET status='pending', error_msg='' "
            "WHERE job_id=%s AND status='failed' AND transcript IS NOT NULL",
            (job_id,))
        cur.execute(
            "UPDATE video_batch_items SET status='uploaded', error_msg='' "
            "WHERE job_id=%s AND status='failed' AND transcript IS NULL",
            (job_id,))
        conn.commit()
    finally:
        conn.close()


def save_review(item_id, final_scores, teacher_comment, precheck, status):
    """复核页保存：最终分、老师批注、人工勾选的准入项、状态。"""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE video_batch_items SET final_scores=%s, teacher_comment=%s, "
            "precheck=%s, status=%s, updated_at=now() WHERE id=%s",
            (json.dumps(final_scores, ensure_ascii=False), teacher_comment,
             json.dumps(precheck, ensure_ascii=False), status, item_id))
        conn.commit()
    finally:
        conn.close()


def requeue_for_regrade(job_id):
    """已批未确认的全部退回待批（转写稿在库，重批不用重传视频）。
    已确认(confirmed)的不动——老师定过的结果不被机器覆盖。"""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE video_batch_items SET status='pending', error_msg='' "
            "WHERE job_id=%s AND status IN ('graded','failed') "
            "AND transcript IS NOT NULL", (job_id,))
        n = cur.rowcount
        conn.commit()
        return n
    finally:
        conn.close()


def confirm_all_graded(job_id):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE video_batch_items SET status='confirmed', updated_at=now() "
            "WHERE job_id=%s AND status='graded'", (job_id,))
        n = cur.rowcount
        conn.commit()
        return n
    finally:
        conn.close()
