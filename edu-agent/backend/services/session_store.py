"""会话状态存储（SPEC §8.3 会话表 + N11 段落索引 + N28 决策时间线 + N35 软删除）。

设计要点：
- `sessions` 表字段与 SPEC §8.3 一一对应（profile/onboarding/selected_school_id/shortlist/
  learning_state/plan/decisions 均为 JSON），`revision` **每次会话变更 +1**。
- 资料正文单独存表，并按**段落**切分存索引（N11），这样 `lab_state.evidence.locator`
  才能真的"点回原文"。
- 删除资料若已被诊断引用 → **软删除**，`material_id` 快照保留供历史 evidence 溯源（N35）。
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
import uuid
import copy
import threading
from functools import wraps
from pathlib import Path

from ..core.errors import AppError, session_not_found
from .. import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id         TEXT PRIMARY KEY,
    revision           INTEGER NOT NULL DEFAULT 1,
    profile            TEXT NOT NULL DEFAULT '{}',
    onboarding         TEXT NOT NULL DEFAULT '{}',
    selected_school_id TEXT,
    shortlist          TEXT NOT NULL DEFAULT '[]',
    learning_state     TEXT,
    plan               TEXT,
    decisions          TEXT NOT NULL DEFAULT '[]',
    recommendations    TEXT,
    is_demo            INTEGER NOT NULL DEFAULT 0,
    created_at         REAL NOT NULL,
    updated_at         REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS materials (
    material_id    TEXT PRIMARY KEY,
    session_id     TEXT NOT NULL,
    title          TEXT NOT NULL,
    content        TEXT NOT NULL,
    source_url     TEXT,
    is_demo        INTEGER NOT NULL DEFAULT 0,
    school_id      TEXT,
    subject        TEXT,
    deleted        INTEGER NOT NULL DEFAULT 0,
    referenced     INTEGER NOT NULL DEFAULT 0,
    created_at     REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_materials_session ON materials(session_id, deleted);

CREATE TABLE IF NOT EXISTS material_chunks (
    chunk_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    material_id  TEXT NOT NULL,
    session_id   TEXT NOT NULL,
    seq          INTEGER NOT NULL,
    locator      TEXT NOT NULL,
    text         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chunks_material ON material_chunks(material_id);
CREATE INDEX IF NOT EXISTS idx_chunks_session ON material_chunks(session_id);

CREATE TABLE IF NOT EXISTS lab_requests (
    session_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    result TEXT NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY(session_id, request_id)
);
CREATE TABLE IF NOT EXISTS lab_inflight (
    session_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    owner TEXT NOT NULL,
    expires_at REAL NOT NULL
);
"""

_conn: sqlite3.Connection | None = None
_lock = threading.RLock()


def _serialized(func):
    @wraps(func)
    def wrapped(*args, **kwargs):
        with _lock:
            return func(*args, **kwargs)
    return wrapped


def get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.executescript(SCHEMA)
        _conn.commit()
    return _conn


def init_db() -> None:
    get_conn()


def reset_db() -> None:
    """仅供测试：整库重建。"""
    global _conn
    if _conn is not None:
        _conn.close()
        _conn = None
    if config.DB_PATH.exists():
        config.DB_PATH.unlink()
    init_db()


# --------------------------------------------------------------------------
# JSON 辅助
# --------------------------------------------------------------------------

def _dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False)


def _loads(raw, default):
    if raw in (None, ""):
        return default
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return default


def new_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex[:12]}"


# --------------------------------------------------------------------------
# 会话
# --------------------------------------------------------------------------

def create_session(profile: dict | None = None, *, is_demo: bool = False) -> dict:
    conn = get_conn()
    session_id = new_id("s_")
    now = time.time()
    conn.execute(
        """INSERT INTO sessions(session_id, revision, profile, created_at, updated_at, is_demo)
           VALUES(?,?,?,?,?,?)""",
        (session_id, 1, _dumps(profile or {}), now, now, 1 if is_demo else 0),
    )
    conn.commit()
    return get_session(session_id)


def _row(session_id: str) -> sqlite3.Row:
    row = get_conn().execute(
        "SELECT * FROM sessions WHERE session_id=?", (session_id,)
    ).fetchone()
    if row is None:
        raise session_not_found(session_id)
    return row


def exists(session_id: str) -> bool:
    return get_conn().execute(
        "SELECT 1 FROM sessions WHERE session_id=?", (session_id,)
    ).fetchone() is not None


def snapshot(session_id: str) -> dict:
    """GET /sessions/{id} 的 `data`（§7.3 + §7.12）。"""
    row = _row(session_id)
    return {
        "session_id": row["session_id"],
        "revision": row["revision"],
        "profile": _loads(row["profile"], {}),
        "onboarding": _loads(row["onboarding"], {}),
        "selected_school_id": row["selected_school_id"],
        "shortlist": _loads(row["shortlist"], []),
        "materials": list_materials(session_id),
        "learning_state": _loads(row["learning_state"], None),
        "plan": _loads(row["plan"], None),
        "recommendations": _loads(row["recommendations"], None),
        "decisions": _loads(row["decisions"], []),
        "is_demo": bool(row["is_demo"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def get_session(session_id: str) -> dict:
    return snapshot(session_id)


def _bump(conn, session_id: str, expected_revision: int | None = None) -> int:
    row = conn.execute(
        "SELECT revision FROM sessions WHERE session_id=?", (session_id,)
    ).fetchone()
    if row is None:
        raise session_not_found(session_id)
    current = int(row["revision"])
    if expected_revision is not None and int(expected_revision) != current:
        raise AppError(
            "CONFLICT",
            f"会话状态已被其它操作更新（当前 revision={current}，你提交的是 {expected_revision}），请刷新后重试。",
        )
    new_revision = current + 1
    conn.execute(
        "UPDATE sessions SET revision=?, updated_at=? WHERE session_id=?",
        (new_revision, time.time(), session_id),
    )
    return new_revision


def update_fields(session_id: str, **fields) -> dict:
    """按字段名更新 sessions 表；值会自动 JSON 序列化。"""
    allowed = {
        "profile", "onboarding", "selected_school_id", "shortlist",
        "learning_state", "plan", "decisions", "recommendations",
    }
    expected = fields.pop("expected_revision", None)
    unknown = set(fields) - allowed
    if unknown:
        raise ValueError(f"不可写字段：{unknown}")
    conn = get_conn()
    if not fields:
        return snapshot(session_id)
    try:
        conn.execute("BEGIN IMMEDIATE")
        _bump(conn, session_id, expected)
        fields = _sync_minutes(session_id, fields)
        fields = _sync_plan(session_id, fields)
        sets, values = [], []
        for key, value in fields.items():
            sets.append(f"{key}=?")
            values.append(_dumps(value) if key != "selected_school_id" else value)
        values.append(session_id)
        conn.execute(f"UPDATE sessions SET {', '.join(sets)} WHERE session_id=?", values)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return snapshot(session_id)


def _sync_minutes(session_id: str, fields: dict) -> dict:
    """所有档案时间入口同时更新可信宿主；time 动作不调用模型。"""
    profile = fields.get("profile")
    minutes = profile.get("daily_minutes") if isinstance(profile, dict) else None
    if type(minutes) is not int:
        return fields
    current = snapshot(session_id)
    learning = copy.deepcopy(fields.get("learning_state", current.get("learning_state")) or {})
    raw = learning.get("blindspot")
    if not raw:
        return fields
    from vendor.blindspot_lab import AdaptiveLab
    from vendor.blindspot_lab.examples.adaptive_host import AdaptiveHost
    from vendor.blindspot_lab.lab.models import GenerateJSONAdapter
    host = AdaptiveHost.restore_json(raw)
    if (host.context.get("profile") or {}).get("daily_minutes") == minutes:
        return fields
    def no_network(*args):
        raise RuntimeError("时间同步不允许调用模型")
    mode = host.context["runtime_mode"]
    model = GenerateJSONAdapter(no_network) if mode == "live" else None
    lab = AdaptiveLab(model=model)
    if host.context.get("learning_state") and not host.context["learning_state"].get("stopped"):
        host.execute(lab, "time", uuid.uuid4().hex, host.context["revision"], minutes=minutes)
    else:
        host.update_daily_minutes(minutes, host.context["revision"])
    learning["blindspot"] = host.export_json()
    learning["revision"] = host.context["revision"]
    fields["learning_state"] = learning
    return fields


def _sync_plan(session_id: str, fields: dict) -> dict:
    """与档案/目标同事务重排已有整体计划，避免旧预算或旧方向继续生效。"""
    if "plan" in fields:
        return fields
    current = snapshot(session_id)
    if not isinstance(current.get("plan"), dict):
        return fields
    old_profile = current.get("profile") or {}
    profile = fields.get("profile", old_profile) or {}
    minutes = profile.get("daily_minutes") or current["plan"].get("daily_minutes") or 60
    changed = any(profile.get(key) != old_profile.get(key)
                  for key in ("daily_minutes", "major", "target_major"))
    changed |= fields.get("selected_school_id", current.get("selected_school_id")) != current.get("selected_school_id")
    # 兼容升级前已经产生的预算不一致；重复设置当前时间也不能保留旧计划。
    changed |= "profile" in fields and current["plan"].get("daily_minutes") != minutes
    if changed:
        from .planner import stored_rule_plan
        fields["plan"] = stored_rule_plan({**current, **fields}, minutes)
    return fields


def cached_lab_request(session_id: str, request_id: str, fingerprint: str) -> dict | None:
    row = get_conn().execute(
        "SELECT fingerprint, result FROM lab_requests WHERE session_id=? AND request_id=?",
        (session_id, request_id),
    ).fetchone()
    if row is None:
        return None
    if row["fingerprint"] != fingerprint:
        raise AppError("CONFLICT", "同一 request_id 携带了不同参数，请为新动作使用新的请求 ID。")
    return _loads(row["result"], None)


def reserve_lab_request(session_id: str, request_id: str, fingerprint: str):
    """Reserve one session before paid work, across threads and processes.

    Ten minutes exceeds the bounded three model calls (at most 120s each).
    A crashed worker's reservation expires; live owners are fenced at commit.
    """
    conn = get_conn()
    try:
        conn.execute('BEGIN IMMEDIATE')
        prior = cached_lab_request(session_id, request_id, fingerprint)
        if prior is not None:
            conn.rollback()
            return None, prior
        _row(session_id)
        active = conn.execute('SELECT * FROM lab_inflight WHERE session_id=?', (session_id,)).fetchone()
        now = time.time()
        if active:
            if active['request_id'] == request_id and active['fingerprint'] != fingerprint:
                raise AppError('CONFLICT', '同一 request_id 携带了不同参数。')
            if active['expires_at'] > now:
                raise AppError('CONFLICT', '该会话的请求仍在处理中，请稍候用原请求 ID 核对结果。')
        owner = uuid.uuid4().hex
        conn.execute('INSERT OR REPLACE INTO lab_inflight VALUES(?,?,?,?,?)',
                     (session_id, request_id, fingerprint, owner, now + 600))
        conn.commit()
        return owner, None
    except Exception:
        conn.rollback()
        raise


def release_lab_request(session_id: str, owner: str) -> None:
    conn = get_conn()
    with conn:
        conn.execute('DELETE FROM lab_inflight WHERE session_id=? AND owner=?', (session_id, owner))


def renew_lab_request(session_id: str, owner: str) -> bool:
    """Keep a still-running background operation reserved; never revive a lost owner."""
    conn = get_conn()
    now = time.time()
    with conn:
        result = conn.execute('UPDATE lab_inflight SET expires_at=? WHERE session_id=? AND owner=? AND expires_at>?',
                              (now+600, session_id, owner, now))
    return result.rowcount == 1


def commit_lab_request(session_id: str, request_id: str, fingerprint: str,
                       expected_revision: int, fields: dict, result: dict, *, claim_owner=None) -> dict:
    """原子 CAS + 完整结果缓存；跨线程、跨进程都由 SQLite 写锁裁定。"""
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        prior = cached_lab_request(session_id, request_id, fingerprint)
        if prior is not None:
            conn.rollback()
            return prior
        if claim_owner is not None:
            claim = conn.execute('SELECT owner,expires_at FROM lab_inflight WHERE session_id=?',
                                 (session_id,)).fetchone()
            if claim is None or claim['owner'] != claim_owner or claim['expires_at'] <= time.time():
                raise AppError('CONFLICT', '请求占用已失效，本次候选未保存，请刷新核对。')
        _bump(conn, session_id, expected_revision)
        allowed = {"profile", "learning_state", "plan"}
        if set(fields) - allowed:
            raise ValueError("lab commit 字段无效")
        fields = _sync_minutes(session_id, fields)
        fields = _sync_plan(session_id, fields)
        sets = ", ".join(f"{k}=?" for k in fields)
        conn.execute(f"UPDATE sessions SET {sets} WHERE session_id=?",
                     [*(_dumps(v) for v in fields.values()), session_id])
        # 派生计划及更新时间以实际落库内容为准，精确缓存本次公开响应。
        result = copy.deepcopy(result)
        saved = snapshot(session_id)
        result["state"]["session"] = public_session(saved)
        result["state"]["revision"] = saved["revision"]
        conn.execute("INSERT INTO lab_requests VALUES(?,?,?,?,?)",
                     (session_id, request_id, fingerprint, _dumps(result), time.time()))
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise


def commit_journey_demo(origin_session_id: str, demo_session_id: str, request_id: str,
                        fingerprint: str, expected_revision: int, *, profile: dict,
                        learning_state: dict, result: dict) -> dict:
    """Atomically CAS the origin, create an isolated demo row, and cache its response.

    The origin row itself is intentionally not updated or revision-bumped.
    """
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        prior = cached_lab_request(origin_session_id, request_id, fingerprint)
        if prior is not None:
            conn.rollback()
            return prior
        origin = conn.execute("SELECT revision FROM sessions WHERE session_id=?",
                              (origin_session_id,)).fetchone()
        if origin is None:
            raise session_not_found(origin_session_id)
        if int(origin["revision"]) != expected_revision:
            raise AppError("CONFLICT", "页面状态已更新，请读取最新状态后重试；未创建演示档案。")
        if conn.execute("SELECT 1 FROM sessions WHERE session_id=?", (demo_session_id,)).fetchone():
            raise AppError("CONFLICT", "演示档案标识冲突，请重试；源档案未修改。")
        now = time.time()
        conn.execute(
            """INSERT INTO sessions(session_id, revision, profile, learning_state, is_demo, created_at, updated_at)
               VALUES(?,?,?,?,?,?,?)""",
            (demo_session_id, 1, _dumps(profile or {}), _dumps(learning_state), 1, now, now),
        )
        result = copy.deepcopy(result)
        saved = snapshot(demo_session_id)
        result["state"].update(session_id=demo_session_id, revision=saved["revision"],
                               session=public_session(saved))
        conn.execute("INSERT INTO lab_requests VALUES(?,?,?,?,?)",
                     (origin_session_id, request_id, fingerprint, _dumps(result), now))
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise


def commit_journey_return(demo_session_id: str, request_id: str, fingerprint: str,
                          expected_revision: int, origin_session_id: str,
                          origin_revision: int, result: dict) -> dict | None:
    """Cache a return response without changing either session revision.

    Return None when the origin changed after its public state was read so the
    service can reread and retry before caching the response.
    """
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        prior = cached_lab_request(demo_session_id, request_id, fingerprint)
        if prior is not None:
            conn.rollback()
            return prior
        demo = snapshot(demo_session_id)
        if demo["revision"] != expected_revision:
            raise AppError("CONFLICT", "演示档案状态已更新，请读取最新状态后重试。")
        learning = demo.get("learning_state") or {}
        journey = learning.get("journey") if isinstance(learning, dict) else None
        if not demo.get("is_demo") or not isinstance(journey, dict) or journey.get("return_session_id") != origin_session_id:
            raise AppError("CONFLICT", "当前档案没有权限返回该来源。")
        origin = conn.execute("SELECT revision FROM sessions WHERE session_id=?",
                              (origin_session_id,)).fetchone()
        if origin is None:
            raise session_not_found(origin_session_id)
        if int(origin["revision"]) != origin_revision:
            conn.rollback()
            return None
        result = copy.deepcopy(result)
        state = result.get("state") if isinstance(result, dict) else None
        if (not isinstance(state, dict) or state.get("session_id") != origin_session_id
                or state.get("revision") != origin_revision):
            raise ValueError("journey return response must match the latest origin snapshot")
        conn.execute("INSERT INTO lab_requests VALUES(?,?,?,?,?)",
                     (demo_session_id, request_id, fingerprint, _dumps(result), time.time()))
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise


def public_session(session: dict) -> dict:
    """浏览器投影：保留可解释摘要，永不下发可信快照或请求缓存。"""
    out = copy.deepcopy(session)
    learning = out.get("learning_state")
    if isinstance(learning, dict) and "blindspot" in learning:
        out["learning_state"] = {k: copy.deepcopy(learning[k]) for k in
            ("adapter", "runtime_mode", "lab_version", "revision", "lab", "ability_profile", "is_demo")
            if k in learning}
    elif isinstance(learning, dict) and "journey" in learning:
        # Journey is projected at the raw-state top level; keep its private
        # deduplication IDs and source records out of the nested session view.
        out["learning_state"] = {k: copy.deepcopy(v) for k, v in learning.items() if k != "journey"}
    return out


def merge_profile(session_id: str, patch: dict) -> dict:
    """部分更新 profile（§7.2）。空值（None）不下发，避免误清空。"""
    row = _row(session_id)
    profile = _loads(row["profile"], {})
    for key, value in (patch or {}).items():
        if value is None:
            continue
        profile[key] = value
    return update_fields(session_id, profile=profile, expected_revision=int(row["revision"]))


def append_decision(session_id: str, choice: str, reason: str = "", alternatives: list | None = None) -> dict:
    """N28：追加式决策时间线。"""
    row = _row(session_id)
    decisions = _loads(row["decisions"], [])
    decisions.append({
        "time": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "choice": choice,
        "reason": reason,
        "alternatives": alternatives or [],
    })
    return update_fields(session_id, decisions=decisions, expected_revision=int(row["revision"]))


def set_target(session_id: str, school_id: str, *, school_name: str = "", user_note: str | None = None,
               alternatives: list | None = None) -> dict:
    """确认目标院校 + 自动写决策记录（N9 / N28）。"""
    row = _row(session_id)
    shortlist = _loads(row["shortlist"], [])
    if school_id in shortlist:
        shortlist = [sid for sid in shortlist if sid != school_id]
    decisions = _loads(row["decisions"], [])
    decisions.append({"time": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "choice": school_name or school_id,
                      "reason": user_note or "", "alternatives": alternatives or []})
    return update_fields(session_id, selected_school_id=school_id, shortlist=shortlist,
                         decisions=decisions, expected_revision=int(row["revision"]))


# --------------------------------------------------------------------------
# 资料（N11 段落索引 / N35 软删除）
# --------------------------------------------------------------------------

_PARA_SPLIT = re.compile(r"\n\s*\n+")


def split_paragraphs(text: str, max_len: int = 500) -> list[tuple[str, str]]:
    """按段落切分并给出 `locator`（N11）。

    - 空行分段；段内再按换行/长度切，locator 写成「第 3 段（第 2 节）」
    - locator 是给人看的、能定位回原文的，不是数据库 id
    """
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return []
    raw_paras = [p.strip() for p in _PARA_SPLIT.split(text) if p.strip()]
    out: list[tuple[str, str]] = []
    for idx, para in enumerate(raw_paras, start=1):
        if len(para) <= max_len:
            out.append((f"第 {idx} 段", para))
            continue
        pieces: list[str] = []
        start = 0
        while start < len(para):
            end = min(start + max_len, len(para))
            if end < len(para):
                window = para.rfind("\n", start + max_len // 2, end)
                if window > 0:
                    end = window
            piece = para[start:end].strip()
            if piece:
                pieces.append(piece)
            if end >= len(para):
                break
            start = end
        for sub, piece in enumerate(pieces, start=1):
            locator = f"第 {idx} 段" if len(pieces) == 1 else f"第 {idx} 段（第 {sub} 节）"
            out.append((locator, piece))
    return out


def add_material(session_id: str, *, title: str, text: str, source_url: str | None = None,
                 is_demo: bool = False, school_id: str | None = None,
                 subject: str | None = None) -> dict:
    _row(session_id)  # 校验会话存在
    conn = get_conn()
    material_id = new_id("m_")
    conn.execute(
        """INSERT INTO materials(material_id, session_id, title, content, source_url,
                                 is_demo, school_id, subject, created_at)
           VALUES(?,?,?,?,?,?,?,?,?)""",
        (material_id, session_id, title, text, source_url, 1 if is_demo else 0,
         school_id, subject, time.time()),
    )
    for seq, (locator, piece) in enumerate(split_paragraphs(text), start=1):
        conn.execute(
            "INSERT INTO material_chunks(material_id, session_id, seq, locator, text) VALUES(?,?,?,?,?)",
            (material_id, session_id, seq, locator, piece),
        )
    conn.commit()
    _bump(conn, session_id)
    conn.commit()
    return {
        "material_id": material_id,
        "title": title,
        "character_count": len(text),
        "is_demo": bool(is_demo),
        "school_id": school_id,
        "subject": subject,
        "preview": text[:200],          # N13
        "paragraph_count": len(split_paragraphs(text)),
    }


def list_materials(session_id: str) -> list[dict]:
    rows = get_conn().execute(
        """SELECT material_id, title, LENGTH(content) AS character_count, is_demo,
                  school_id, subject, created_at,
                  (SELECT COUNT(*) FROM material_chunks c WHERE c.material_id = m.material_id) AS paragraph_count
           FROM materials m WHERE session_id=? AND deleted=0 ORDER BY created_at""",
        (session_id,),
    ).fetchall()
    return [{
        "material_id": r["material_id"],
        "title": r["title"],
        "character_count": int(r["character_count"] or 0),
        "is_demo": bool(r["is_demo"]),
        "school_id": r["school_id"],
        "subject": r["subject"],
        "created_at": r["created_at"],
        "paragraph_count": int(r["paragraph_count"] or 0),
    } for r in rows]


def get_material_row(session_id: str, material_id: str) -> sqlite3.Row:
    row = get_conn().execute(
        "SELECT * FROM materials WHERE material_id=? AND session_id=?", (material_id, session_id)
    ).fetchone()
    if row is None:
        raise AppError("MATERIAL_NOT_FOUND", f"资料不存在（{material_id}），可能已被删除。")
    return row


def material_content(session_id: str, material_id: str) -> str:
    return get_material_row(session_id, material_id)["content"]


def material_paragraphs(session_id: str, material_id: str) -> list[dict]:
    """N11：按段落返回 `locator` + 原文，供 `lab_state.evidence` 溯源。"""
    rows = get_conn().execute(
        "SELECT seq, locator, text FROM material_chunks WHERE material_id=? AND session_id=? ORDER BY seq",
        (material_id, session_id),
    ).fetchall()
    return [{"seq": r["seq"], "locator": r["locator"], "text": r["text"]} for r in rows]


def mark_referenced(session_id: str, material_id: str) -> None:
    """诊断引用过该资料后打标：之后删除走软删除（N35）。"""
    conn = get_conn()
    conn.execute(
        "UPDATE materials SET referenced=1 WHERE material_id=? AND session_id=?",
        (material_id, session_id),
    )
    conn.commit()


def delete_material(session_id: str, material_id: str) -> dict:
    """N35：已引用 → 软删除（保留 locator 快照）；未引用 → 硬删除。幂等。"""
    row = get_conn().execute(
        "SELECT material_id, referenced FROM materials WHERE material_id=? AND session_id=?",
        (material_id, session_id),
    ).fetchone()
    if row is None:
        return {"deleted": True, "material_id": material_id, "soft": False, "note": "该资料此前已不存在，按幂等返回成功"}
    conn = get_conn()
    soft = bool(row["referenced"])
    conn.execute(
        "UPDATE materials SET deleted=1 WHERE material_id=? AND session_id=?",
        (material_id, session_id),
    )
    if not soft:
        conn.execute("DELETE FROM material_chunks WHERE material_id=?", (material_id,))
    conn.commit()
    _bump(conn, session_id)
    conn.commit()
    return {
        "deleted": True,
        "material_id": material_id,
        "soft": soft,
        "note": "该资料已被诊断引用，已从列表移除但保留历史证据溯源" if soft else "已删除",
    }


_TOKEN = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]+")
_CJK = re.compile(r"[\u4e00-\u9fff]+")


def extract_keywords(query: str, limit: int = 24) -> list[str]:
    keywords: list[str] = []
    for tok in _TOKEN.findall(query or ""):
        if _CJK.fullmatch(tok) and len(tok) > 4:
            for n in (4, 3, 2):
                for i in range(len(tok) - n + 1):
                    keywords.append(tok[i:i + n])
        elif len(tok) >= 2:
            keywords.append(tok)
    seen, out = set(), []
    for k in keywords:
        if k not in seen:
            seen.add(k)
            out.append(k)
        if len(out) >= limit:
            break
    return out


def search_material(session_id: str, query: str, *, material_id: str | None = None,
                    top_k: int = 5) -> list[dict]:
    """关键词打分检索，返回带 `locator` 的原文片段（N11 的证据来源）。"""
    keywords = extract_keywords(query)
    sql = """SELECT c.chunk_id, c.material_id, c.locator, c.text, m.title
             FROM material_chunks c JOIN materials m ON m.material_id = c.material_id
             WHERE c.session_id=? AND m.deleted=0"""
    params: list = [session_id]
    if material_id:
        sql += " AND c.material_id=?"
        params.append(material_id)
    rows = get_conn().execute(sql, params).fetchall()

    scored = []
    for r in rows:
        text = r["text"]
        score = 0
        for k in keywords:
            cnt = text.count(k)
            if cnt:
                score += cnt * (len(k) + 1)
        if score > 0:
            scored.append((score, r))
    scored.sort(key=lambda item: -item[0])

    if not scored and rows:
        # 无命中时给出首段，保证证据链不为空（并在 note 里说明是"取首段"）
        head = rows[0]
        return [{
            "material_id": head["material_id"],
            "material_title": head["title"],
            "locator": head["locator"],
            "quote": head["text"][:400],
            "score": 0,
            "fallback": True,
        }]

    return [{
        "material_id": r["material_id"],
        "material_title": r["title"],
        "locator": r["locator"],
        "quote": r["text"][:400],
        "score": score,
        "fallback": False,
    } for score, r in scored[:top_k]]


# The shared connection cannot participate in two transactions simultaneously.
# RLock permits composed helpers; SQL BEGIN IMMEDIATE supplies process isolation.
for _name in ("get_conn", "init_db", "reset_db", "create_session", "exists", "snapshot", "get_session",
              "update_fields", "merge_profile", "append_decision", "set_target", "add_material",
              "list_materials", "get_material_row", "material_content", "material_paragraphs",
              "mark_referenced", "delete_material", "search_material", "cached_lab_request",
              "commit_lab_request", "reserve_lab_request", "release_lab_request", "renew_lab_request", "commit_journey_demo", "commit_journey_return"):
    globals()[_name] = _serialized(globals()[_name])
