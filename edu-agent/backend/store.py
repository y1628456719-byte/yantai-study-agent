"""SQLite 存储层：业务数据 + 资料检索。

检索方案用「关键词切分 + LIKE 命中打分」，而不是向量库：
讲义规模小（几百 KB ~ 几 MB），LIKE 足够快，且中文分词零依赖、启动即用。
"""
import json
import re
import sqlite3
import time
import uuid

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,
    title       TEXT,
    created_at  REAL
);
CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT,
    role        TEXT,
    content     TEXT,
    created_at  REAL
);
CREATE TABLE IF NOT EXISTS materials (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT,
    name        TEXT,
    content     TEXT,
    created_at  REAL
);
CREATE TABLE IF NOT EXISTS chunks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    material_id INTEGER,
    seq         INTEGER,
    text        TEXT
);
CREATE TABLE IF NOT EXISTS goals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT,
    subject     TEXT,
    scope       TEXT,
    exam_date   TEXT,
    created_at  REAL
);
CREATE TABLE IF NOT EXISTS quizzes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT,
    topic       TEXT,
    kp          TEXT,
    qtype       TEXT,
    difficulty  TEXT,
    question    TEXT,
    options     TEXT,
    answer      TEXT,
    explanation TEXT,
    source      TEXT,
    created_at  REAL
);
CREATE TABLE IF NOT EXISTS attempts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT,
    quiz_id         INTEGER,
    student_answer  TEXT,
    is_correct      INTEGER,
    feedback        TEXT,
    created_at      REAL
);
CREATE INDEX IF NOT EXISTS idx_quizzes_session ON quizzes(session_id);
CREATE INDEX IF NOT EXISTS idx_attempts_quiz ON attempts(quiz_id);
CREATE INDEX IF NOT EXISTS idx_chunks_material ON chunks(material_id);
"""

_conn = None


def get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.executescript(SCHEMA)
        _conn.commit()
    return _conn


def init_db():
    get_conn()


# ---------------- 会话 / 消息 ----------------

def ensure_session(session_id: str | None, title: str = "新会话") -> str:
    conn = get_conn()
    if not session_id:
        session_id = uuid.uuid4().hex[:12]
    row = conn.execute("SELECT id FROM sessions WHERE id=?", (session_id,)).fetchone()
    if not row:
        conn.execute(
            "INSERT INTO sessions(id, title, created_at) VALUES(?,?,?)",
            (session_id, title, time.time()),
        )
        conn.commit()
    return session_id


def add_message(session_id: str, role: str, content: str):
    conn = get_conn()
    conn.execute(
        "INSERT INTO messages(session_id, role, content, created_at) VALUES(?,?,?,?)",
        (session_id, role, content, time.time()),
    )
    conn.commit()


def list_messages(session_id: str, limit: int = 40):
    conn = get_conn()
    rows = conn.execute(
        "SELECT role, content, created_at FROM messages WHERE session_id=? ORDER BY id DESC LIMIT ?",
        (session_id, limit),
    ).fetchall()
    return [dict(r) for r in reversed(rows)]


# ---------------- 资料 ----------------

def add_material(session_id: str, name: str, content: str) -> int:
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO materials(session_id, name, content, created_at) VALUES(?,?,?,?)",
        (session_id, name, content, time.time()),
    )
    material_id = cur.lastrowid
    for seq, text in enumerate(split_chunks(content)):
        conn.execute(
            "INSERT INTO chunks(material_id, seq, text) VALUES(?,?,?)",
            (material_id, seq, text),
        )
    conn.commit()
    return material_id


def split_chunks(text: str, size: int = 500, overlap: int = 80):
    """按长度切片，尽量切在换行处。"""
    text = (text or "").replace("\r\n", "\n").strip()
    if not text:
        return []
    chunks = []
    start = 0
    n = len(text)
    while start < n:
        end = min(start + size, n)
        if end < n:
            window = text.rfind("\n", start + size // 2, end)
            if window > 0:
                end = window
        piece = text[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= n:
            break
        start = max(end - overlap, start + 1)
    return chunks


def list_materials(session_id: str):
    conn = get_conn()
    rows = conn.execute(
        """SELECT m.id, m.name, m.created_at, LENGTH(m.content) AS size,
                  (SELECT COUNT(*) FROM chunks c WHERE c.material_id = m.id) AS chunk_count
           FROM materials m WHERE m.session_id=? ORDER BY m.id""",
        (session_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def delete_material(session_id: str, material_id: int) -> bool:
    conn = get_conn()
    conn.execute("DELETE FROM chunks WHERE material_id=?", (material_id,))
    cur = conn.execute(
        "DELETE FROM materials WHERE id=? AND session_id=?", (material_id, session_id)
    )
    conn.commit()
    return cur.rowcount > 0


_CJK = re.compile(r"[\u4e00-\u9fff]+")
_TOKEN = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]+")


def extract_keywords(query: str, limit: int = 24):
    """把自然语言查询切成检索关键词：英文/数字整词，中文切 2~4 字滑窗。"""
    keywords = []
    for tok in _TOKEN.findall(query or ""):
        if _CJK.fullmatch(tok) and len(tok) > 4:
            for n in (4, 3, 2):
                for i in range(len(tok) - n + 1):
                    keywords.append(tok[i:i + n])
        elif len(tok) >= 2:
            keywords.append(tok)
    seen, result = set(), []
    for k in keywords:
        if k not in seen:
            seen.add(k)
            result.append(k)
        if len(result) >= limit:
            break
    return result


def search_chunks(session_id: str, query: str, top_k: int = 5):
    """关键词命中打分检索，返回最相关的若干片段。"""
    keywords = extract_keywords(query)
    if not keywords:
        return []
    conn = get_conn()
    rows = conn.execute(
        """SELECT c.id, c.text, m.name AS material
           FROM chunks c JOIN materials m ON m.id = c.material_id
           WHERE m.session_id = ?""",
        (session_id,),
    ).fetchall()

    scored = []
    for r in rows:
        text = r["text"]
        score = 0
        for k in keywords:
            cnt = text.count(k)
            if cnt:
                # 长关键词权重更高
                score += cnt * (len(k) + 1)
        if score > 0:
            scored.append((score, dict(r)))
    scored.sort(key=lambda x: -x[0])

    results = []
    for score, row in scored[:top_k]:
        results.append({
            "chunk_id": row["id"],
            "material": row["material"],
            "score": score,
            "text": row["text"][:800],
        })
    return results


def count_chunks(session_id: str) -> int:
    conn = get_conn()
    row = conn.execute(
        """SELECT COUNT(*) AS n FROM chunks c
           JOIN materials m ON m.id = c.material_id WHERE m.session_id=?""",
        (session_id,),
    ).fetchone()
    return int(row["n"]) if row else 0


# ---------------- 备考目标 ----------------

def set_goal(session_id: str, subject: str, scope: str = "", exam_date: str = ""):
    conn = get_conn()
    conn.execute("DELETE FROM goals WHERE session_id=?", (session_id,))
    conn.execute(
        "INSERT INTO goals(session_id, subject, scope, exam_date, created_at) VALUES(?,?,?,?,?)",
        (session_id, subject, scope, exam_date, time.time()),
    )
    conn.commit()


def get_goal(session_id: str):
    conn = get_conn()
    row = conn.execute(
        "SELECT subject, scope, exam_date FROM goals WHERE session_id=? ORDER BY id DESC LIMIT 1",
        (session_id,),
    ).fetchone()
    return dict(row) if row else None


# ---------------- 题目 ----------------

def add_quiz(session_id: str, item: dict) -> int:
    conn = get_conn()
    options = item.get("options")
    if isinstance(options, (list, dict)):
        options = json.dumps(options, ensure_ascii=False)
    cur = conn.execute(
        """INSERT INTO quizzes(session_id, topic, kp, qtype, difficulty,
                               question, options, answer, explanation, source, created_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        (
            session_id,
            item.get("topic", ""),
            item.get("kp", ""),
            item.get("qtype", "short_answer"),
            item.get("difficulty", "medium"),
            item.get("question", ""),
            options,
            item.get("answer", ""),
            item.get("explanation", ""),
            item.get("source", ""),
            time.time(),
        ),
    )
    conn.commit()
    return cur.lastrowid


def get_quiz(quiz_id: int):
    conn = get_conn()
    row = conn.execute("SELECT * FROM quizzes WHERE id=?", (quiz_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    if d.get("options"):
        try:
            d["options"] = json.loads(d["options"])
        except Exception:
            pass
    # 附带批改记录
    att = conn.execute(
        "SELECT student_answer, is_correct, feedback FROM attempts WHERE quiz_id=? ORDER BY id DESC LIMIT 1",
        (quiz_id,),
    ).fetchone()
    d["attempt"] = dict(att) if att else None
    return d


def list_quizzes(session_id: str, limit: int = 50):
    conn = get_conn()
    rows = conn.execute(
        """SELECT q.id, q.topic, q.kp, q.qtype, q.difficulty, q.question, q.options,
                  q.answer, q.explanation,
                  (SELECT is_correct FROM attempts a WHERE a.quiz_id=q.id ORDER BY a.id DESC LIMIT 1) AS last_correct,
                  (SELECT student_answer FROM attempts a WHERE a.quiz_id=q.id ORDER BY a.id DESC LIMIT 1) AS last_answer
           FROM quizzes q WHERE q.session_id=? ORDER BY q.id DESC LIMIT ?""",
        (session_id, limit),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        if d.get("options"):
            try:
                d["options"] = json.loads(d["options"])
            except Exception:
                pass
        out.append(d)
    return out


# ---------------- 作答 / 批改 ----------------

def add_attempt(session_id: str, quiz_id: int, student_answer: str,
                is_correct: bool, feedback: str) -> int:
    conn = get_conn()
    cur = conn.execute(
        """INSERT INTO attempts(session_id, quiz_id, student_answer, is_correct, feedback, created_at)
           VALUES(?,?,?,?,?,?)""",
        (session_id, quiz_id, student_answer, 1 if is_correct else 0, feedback, time.time()),
    )
    conn.commit()
    return cur.lastrowid


def list_mistakes(session_id: str, limit: int = 30):
    """错题本：最后一次作答为错的题目。"""
    conn = get_conn()
    rows = conn.execute(
        """SELECT q.id, q.topic, q.kp, q.qtype, q.question, q.answer, q.explanation,
                  a.student_answer, a.feedback, a.created_at
           FROM quizzes q
           JOIN attempts a ON a.id = (
               SELECT id FROM attempts WHERE quiz_id=q.id ORDER BY id DESC LIMIT 1
           )
           WHERE q.session_id=? AND a.is_correct=0
           ORDER BY a.id DESC LIMIT ?""",
        (session_id, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def kp_stats(session_id: str):
    """按考点统计作答正确率，作为诊断的事实依据。"""
    conn = get_conn()
    rows = conn.execute(
        """SELECT q.kp AS kp,
                  COUNT(*) AS total,
                  SUM(a.is_correct) AS correct
           FROM quizzes q
           JOIN attempts a ON a.id = (
               SELECT id FROM attempts WHERE quiz_id=q.id ORDER BY id DESC LIMIT 1
           )
           WHERE q.session_id=? AND q.kp <> ''
           GROUP BY q.kp
           ORDER BY (CAST(SUM(a.is_correct) AS REAL) / COUNT(*)) ASC""",
        (session_id,),
    ).fetchall()
    stats = []
    for r in rows:
        total = int(r["total"] or 0)
        correct = int(r["correct"] or 0)
        stats.append({
            "kp": r["kp"],
            "total": total,
            "correct": correct,
            "accuracy": round(correct / total, 3) if total else 0.0,
        })
    return stats


def session_overview(session_id: str):
    conn = get_conn()
    q = conn.execute("SELECT COUNT(*) n FROM quizzes WHERE session_id=?", (session_id,)).fetchone()
    a = conn.execute("SELECT COUNT(*) n FROM attempts WHERE session_id=?", (session_id,)).fetchone()
    return {
        "quiz_count": int(q["n"]),
        "attempt_count": int(a["n"]),
        "goal": get_goal(session_id),
    }
