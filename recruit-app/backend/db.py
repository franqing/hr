"""SQLite 存取。所有模块共用此连接模式（照抄 BOM-AI：内联 schema + _migrate）。"""
import json
import sqlite3
from datetime import datetime

from .paths import data_dir

_SCHEMA = """
CREATE TABLE IF NOT EXISTS settings(
  key TEXT PRIMARY KEY, value_json TEXT, updated_at TEXT);

CREATE TABLE IF NOT EXISTS job_profiles(
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, version TEXT,
  profile_json TEXT, is_default INTEGER DEFAULT 0,
  created_at TEXT, updated_at TEXT);

CREATE TABLE IF NOT EXISTS search_profiles(
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT,
  job_profile_id INTEGER, plan_preset TEXT, filters_json TEXT,
  every_hours REAL DEFAULT 0, enabled INTEGER DEFAULT 1,
  last_run_at TEXT, created_at TEXT, updated_at TEXT);

CREATE TABLE IF NOT EXISTS search_runs(
  id INTEGER PRIMARY KEY AUTOINCREMENT, search_profile_id INTEGER,
  trigger TEXT, status TEXT DEFAULT 'running',
  stats_json TEXT, error TEXT, started_at TEXT, finished_at TEXT);

CREATE TABLE IF NOT EXISTS candidates(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id INTEGER, resume_id TEXT, user_id TEXT, im_id TEXT, resume_url TEXT,
  raw_card_json TEXT, raw_resume_json TEXT,
  name TEXT, desired_title TEXT, desired_salary TEXT, current_city TEXT,
  work_years TEXT, edu TEXT, current_company TEXT, school TEXT,
  age INTEGER, active_status TEXT,
  quick_gate INTEGER DEFAULT 0, quick_reason TEXT,
  hard_gate INTEGER DEFAULT 0, hard_reason TEXT,
  resume_fetched INTEGER DEFAULT 0, created_at TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS ux_candidates_resume ON candidates(resume_id);

-- 每次 run 命中的候选人；is_new 标记本轮新发现（跨轮次去重看 candidates.resume_id）
CREATE TABLE IF NOT EXISTS run_candidates(
  run_id INTEGER, candidate_id INTEGER, is_new INTEGER DEFAULT 0,
  PRIMARY KEY(run_id, candidate_id));

CREATE TABLE IF NOT EXISTS candidate_scores(
  candidate_id INTEGER PRIMARY KEY, method TEXT, dims_json TEXT,
  total REAL, source TEXT, explanation_json TEXT, scored_at TEXT);

CREATE TABLE IF NOT EXISTS invites(
  id INTEGER PRIMARY KEY AUTOINCREMENT, candidate_id INTEGER, run_id INTEGER,
  job_id TEXT, status TEXT DEFAULT 'pending', error TEXT, created_at TEXT, sent_at TEXT,
  UNIQUE(candidate_id, job_id));

CREATE TABLE IF NOT EXISTS liepin_cache(
  key TEXT PRIMARY KEY, result_json TEXT, fetched_at TEXT);

CREATE TABLE IF NOT EXISTS audit_log(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, user TEXT,
  action TEXT, detail TEXT);

-- 候选固定名单标签（快照式：存的是候选 id，跨 run 攒人；成员操作全显式）
CREATE TABLE IF NOT EXISTS tags(
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tag_members(
  tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
  candidate_id INTEGER NOT NULL REFERENCES candidates(id),
  added_at TEXT NOT NULL,
  PRIMARY KEY(tag_id, candidate_id));
"""


def get_conn() -> sqlite3.Connection:
    db_path = data_dir() / "app.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection):
    """轻量迁移：表缺列则补（旧库升级；新建库已有，无操作）。"""
    for table, col, ddl in [
        ("candidates", "im_id", "ALTER TABLE candidates ADD COLUMN im_id TEXT"),
        ("invites", "created_at", "ALTER TABLE invites ADD COLUMN created_at TEXT"),
    ]:
        cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if col not in cols:
            conn.execute(ddl)
    conn.commit()


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ---------- settings ----------
def get_settings() -> dict:
    conn = get_conn()
    rows = {r["key"]: json.loads(r["value_json"])
            for r in conn.execute("SELECT key,value_json FROM settings").fetchall()}
    conn.close()
    return rows


def save_setting(key: str, value) -> None:
    conn = get_conn()
    conn.execute("INSERT OR REPLACE INTO settings(key,value_json,updated_at) VALUES(?,?,?)",
                 (key, json.dumps(value, ensure_ascii=False), now()))
    conn.commit()
    conn.close()


def get_setting(key: str, default=None):
    return get_settings().get(key, default)


# ---------- audit ----------
def audit(action: str, detail: str = "", user: str = "hr") -> None:
    conn = get_conn()
    conn.execute("INSERT INTO audit_log(ts,user,action,detail) VALUES(?,?,?,?)",
                 (now(), user, action, detail))
    conn.commit()
    conn.close()


# ---------- job_profiles ----------
def save_job_profile(name: str, profile: dict, version: str = "1", is_default: int = 0,
                     profile_id: int | None = None) -> int:
    conn = get_conn()
    if profile_id:
        conn.execute("UPDATE job_profiles SET name=?,version=?,profile_json=?,updated_at=? WHERE id=?",
                     (name, version, json.dumps(profile, ensure_ascii=False), now(), profile_id))
        conn.commit()
        conn.close()
        return profile_id
    cur = conn.execute("INSERT INTO job_profiles(name,version,profile_json,is_default,created_at,updated_at)"
                       " VALUES(?,?,?,?,?,?)",
                       (name, version, json.dumps(profile, ensure_ascii=False), is_default, now(), now()))
    conn.commit()
    pid = cur.lastrowid
    conn.close()
    return pid


def get_job_profile(profile_id: int) -> dict | None:
    conn = get_conn()
    row = conn.execute("SELECT * FROM job_profiles WHERE id=?", (profile_id,)).fetchone()
    conn.close()
    if not row:
        return None
    d = dict(row)
    d["profile"] = json.loads(d.pop("profile_json"))
    return d


def get_default_job_profile() -> dict | None:
    conn = get_conn()
    row = conn.execute("SELECT * FROM job_profiles WHERE is_default=1 ORDER BY id LIMIT 1").fetchone()
    conn.close()
    if not row:
        return None
    d = dict(row)
    d["profile"] = json.loads(d.pop("profile_json"))
    return d


def list_job_profiles() -> list[dict]:
    conn = get_conn()
    rows = [dict(r) for r in conn.execute(
        "SELECT id,name,version,is_default,updated_at FROM job_profiles ORDER BY id").fetchall()]
    conn.close()
    return rows


def delete_job_profile(profile_id: int) -> None:
    conn = get_conn()
    conn.execute("DELETE FROM job_profiles WHERE id=?", (profile_id,))
    conn.commit()
    conn.close()


# ---------- search_profiles ----------
def save_search_profile(d: dict, profile_id: int | None = None) -> int:
    conn = get_conn()
    if profile_id:
        conn.execute("UPDATE search_profiles SET name=?,job_profile_id=?,plan_preset=?,"
                     "filters_json=?,every_hours=?,enabled=?,updated_at=? WHERE id=?",
                     (d["name"], d.get("job_profile_id"), d.get("plan_preset", "full"),
                      json.dumps(d.get("filters", {}), ensure_ascii=False),
                      float(d.get("every_hours", 0)), int(d.get("enabled", 1)), now(), profile_id))
        conn.commit()
        conn.close()
        return profile_id
    cur = conn.execute("INSERT INTO search_profiles(name,job_profile_id,plan_preset,filters_json,"
                       "every_hours,enabled,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                       (d["name"], d.get("job_profile_id"), d.get("plan_preset", "full"),
                        json.dumps(d.get("filters", {}), ensure_ascii=False),
                        float(d.get("every_hours", 0)), int(d.get("enabled", 1)), now(), now()))
    conn.commit()
    pid = cur.lastrowid
    conn.close()
    return pid


def get_search_profile(profile_id: int) -> dict | None:
    conn = get_conn()
    row = conn.execute("SELECT * FROM search_profiles WHERE id=?", (profile_id,)).fetchone()
    conn.close()
    if not row:
        return None
    d = dict(row)
    d["filters"] = json.loads(d.pop("filters_json") or "{}")
    return d


def list_search_profiles() -> list[dict]:
    conn = get_conn()
    rows = [dict(r) for r in conn.execute(
        "SELECT sp.*, jp.name AS job_profile_name FROM search_profiles sp "
        "LEFT JOIN job_profiles jp ON jp.id=sp.job_profile_id ORDER BY sp.id").fetchall()]
    for r in rows:
        r["filters"] = json.loads(r.pop("filters_json") or "{}")
    conn.close()
    return rows


def delete_search_profile(profile_id: int) -> None:
    conn = get_conn()
    conn.execute("DELETE FROM search_profiles WHERE id=?", (profile_id,))
    conn.commit()
    conn.close()


def touch_search_profile(profile_id: int) -> None:
    conn = get_conn()
    conn.execute("UPDATE search_profiles SET last_run_at=? WHERE id=?", (now(), profile_id))
    conn.commit()
    conn.close()


# ---------- search_runs ----------
def create_search_run(search_profile_id: int, trigger: str) -> int:
    conn = get_conn()
    cur = conn.execute("INSERT INTO search_runs(search_profile_id,trigger,status,started_at)"
                       " VALUES(?,?,?,?)",
                       (search_profile_id, trigger, "running", now()))
    conn.commit()
    rid = cur.lastrowid
    conn.close()
    return rid


def update_run_status(run_id: int, status: str, stats: dict | None = None, error: str | None = None):
    conn = get_conn()
    sql = "UPDATE search_runs SET status=?, finished_at=?"
    args: list = [status, now() if status in ("done", "stopped", "failed") else None]
    if stats is not None:
        sql += ", stats_json=?"
        args.append(json.dumps(stats, ensure_ascii=False))
    if error is not None:
        sql += ", error=?"
        args.append(error)
    sql += " WHERE id=?"
    args.append(run_id)
    conn.execute(sql, args)
    conn.commit()
    conn.close()


def get_run(run_id: int) -> dict | None:
    conn = get_conn()
    row = conn.execute("SELECT * FROM search_runs WHERE id=?", (run_id,)).fetchone()
    conn.close()
    if not row:
        return None
    d = dict(row)
    d["stats"] = json.loads(d.pop("stats_json") or "{}")
    return d


def list_runs() -> list[dict]:
    conn = get_conn()
    rows = [dict(r) for r in conn.execute(
        "SELECT r.*, sp.name AS search_profile_name FROM search_runs r "
        "LEFT JOIN search_profiles sp ON sp.id=r.search_profile_id "
        "ORDER BY r.id DESC LIMIT 100").fetchall()]
    for r in rows:
        r["stats"] = json.loads(r.pop("stats_json") or "{}")
    conn.close()
    return rows


def running_runs() -> list[int]:
    conn = get_conn()
    rows = [r["id"] for r in conn.execute(
        "SELECT id FROM search_runs WHERE status='running'").fetchall()]
    conn.close()
    return rows


# ---------- candidates ----------
def upsert_candidate(run_id: int, card: dict) -> tuple[int, bool]:
    """按 resume_id 全局去重。返回 (candidate_id, is_new)。"""
    resume_id = card.get("resume_id", "")
    if not resume_id:
        raise ValueError("候选缺少 resume_id")
    conn = get_conn()
    row = conn.execute("SELECT id FROM candidates WHERE resume_id=?", (resume_id,)).fetchone()
    if row:
        cid, is_new = row["id"], False
    else:
        cur = conn.execute(
            "INSERT INTO candidates(run_id,resume_id,user_id,im_id,resume_url,raw_card_json,name,"
            "desired_title,desired_salary,current_city,work_years,edu,current_company,school,age,"
            "active_status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, resume_id, card.get("user_id"), card.get("im_id"), card.get("resume_url"),
             json.dumps(card, ensure_ascii=False), card.get("name"), card.get("desired_title"),
             card.get("desired_salary"), card.get("current_city"), card.get("work_years"),
             card.get("edu"), card.get("current_company"), card.get("school"),
             card.get("age"), card.get("active_status"), now()))
        cid, is_new = cur.lastrowid, True
    conn.execute("INSERT OR REPLACE INTO run_candidates(run_id,candidate_id,is_new) VALUES(?,?,?)",
                 (run_id, cid, int(is_new)))
    conn.commit()
    conn.close()
    return cid, is_new


def update_candidate_gate(candidate_id: int, quick_gate: int, quick_reason: str,
                          hard_gate: int, hard_reason: str):
    conn = get_conn()
    conn.execute("UPDATE candidates SET quick_gate=?,quick_reason=?,hard_gate=?,hard_reason=? WHERE id=?",
                 (int(quick_gate), quick_reason, int(hard_gate), hard_reason, candidate_id))
    conn.commit()
    conn.close()


def update_candidate_resume(candidate_id: int, resume: dict | None, raw_text: str,
                            fetched: bool = True, error: str = ""):
    """写入简历抓取结果。fetched=False（失败）时 resume_fetched 置 0 —— 失败不误标已抓，
    下次批量/refetch 会重试。error 存进 raw_resume_json 免迁移。"""
    conn = get_conn()
    conn.execute("UPDATE candidates SET raw_resume_json=?, resume_fetched=? WHERE id=?",
                 (json.dumps({"resume": resume or {}, "text": raw_text, "error": error},
                             ensure_ascii=False), int(bool(fetched)), candidate_id))
    conn.commit()
    conn.close()


def top_fetch_candidates(run_id: int, limit: int = 30) -> list[dict]:
    """run 内待抓简历的候选：quick_gate=1 且未抓过（resume_fetched=0），按规则分降序。
    LEFT JOIN candidate_scores 让没分的有分者优先排序（分越高越值得先抓）。"""
    conn = get_conn()
    rows = [dict(r) for r in conn.execute(
        "SELECT c.id AS candidate_id, c.resume_id, c.user_id FROM run_candidates rc "
        "JOIN candidates c ON c.id=rc.candidate_id "
        "LEFT JOIN candidate_scores s ON s.candidate_id=c.id "
        "WHERE rc.run_id=? AND c.quick_gate=1 AND c.resume_fetched=0 "
        "ORDER BY COALESCE(s.total,-1) DESC, c.id LIMIT ?", (run_id, limit)).fetchall()]
    conn.close()
    return rows


def get_candidate(candidate_id: int) -> dict | None:
    conn = get_conn()
    row = conn.execute("SELECT * FROM candidates WHERE id=?", (candidate_id,)).fetchone()
    conn.close()
    if not row:
        return None
    d = dict(row)
    d["card"] = json.loads(d.pop("raw_card_json") or "{}")
    d["resume"] = json.loads(d.pop("raw_resume_json") or "{}")
    return d


def list_run_candidates(run_id: int) -> list[dict]:
    """run 的候选列表（列表视图列）。只取表格/导出需要的显式列 —— 不返回
    raw_resume_json / raw_card_json / resume_url 等重型字段：列表每 4s 轮询，
    全量 JSON 曾达 2.3MB+；详情页 /candidates/{id} 才返回简历全文。"""
    conn = get_conn()
    rows = [dict(r) for r in conn.execute(
        "SELECT c.id, c.name, c.desired_title, c.desired_salary, c.current_company,"
        " c.work_years, c.edu, c.school, c.age, c.current_city, c.active_status,"
        " c.quick_gate, c.hard_gate, c.resume_fetched, c.resume_id, rc.is_new,"
        " EXISTS(SELECT 1 FROM invites i WHERE i.candidate_id=c.id) AS invited "
        "FROM run_candidates rc JOIN candidates c ON c.id=rc.candidate_id "
        "WHERE rc.run_id=? ORDER BY c.id", (run_id,)).fetchall()]
    conn.close()
    return rows


def list_gated_candidates(run_id: int) -> list[int]:
    """过 hard_gate 的候选人 id（LLM 评分 / 批量邀请目标）。"""
    conn = get_conn()
    rows = [r["id"] for r in conn.execute(
        "SELECT c.id FROM run_candidates rc JOIN candidates c ON c.id=rc.candidate_id "
        "WHERE rc.run_id=? AND c.hard_gate=1 ORDER BY c.id", (run_id,)).fetchall()]
    conn.close()
    return rows


# ---------- scores ----------
def save_score(candidate_id: int, method: str, dims: dict, total: float,
               source: str, explanation: dict):
    conn = get_conn()
    conn.execute("INSERT OR REPLACE INTO candidate_scores(candidate_id,method,dims_json,total,"
                 "source,explanation_json,scored_at) VALUES(?,?,?,?,?,?,?)",
                 (candidate_id, method, json.dumps(dims, ensure_ascii=False), total,
                  source, json.dumps(explanation, ensure_ascii=False), now()))
    conn.commit()
    conn.close()


def get_score(candidate_id: int) -> dict | None:
    conn = get_conn()
    row = conn.execute("SELECT * FROM candidate_scores WHERE candidate_id=?", (candidate_id,)).fetchone()
    conn.close()
    if not row:
        return None
    d = dict(row)
    d["dims"] = json.loads(d.pop("dims_json") or "{}")
    d["explanation"] = json.loads(d.pop("explanation_json") or "{}")
    return d


def attach_scores(candidates: list[dict]) -> list[dict]:
    """为候选人列表批量附加评分（N+1 查询量小，run 候选通常 <200）。"""
    ids = [c["id"] for c in candidates]
    if not ids:
        return candidates
    conn = get_conn()
    ph = ",".join("?" * len(ids))
    rows = {r["candidate_id"]: dict(r) for r in conn.execute(
        f"SELECT * FROM candidate_scores WHERE candidate_id IN ({ph})", ids).fetchall()}
    conn.close()
    for c in candidates:
        s = rows.get(c["id"])
        if s:
            s["dims"] = json.loads(s.pop("dims_json") or "{}")
            s["explanation"] = json.loads(s.pop("explanation_json") or "{}")
            c["score"] = s
        else:
            c["score"] = None
    return candidates


# ---------- invites ----------
def create_invite(candidate_id: int, run_id: int, job_id: str) -> tuple[int, bool]:
    """幂等：同一候选人同一岗位只建一条。返回 (invite_id, created)。"""
    conn = get_conn()
    row = conn.execute("SELECT id FROM invites WHERE candidate_id=? AND job_id=?",
                       (candidate_id, job_id)).fetchone()
    if row:
        conn.close()
        return row["id"], False
    cur = conn.execute("INSERT INTO invites(candidate_id,run_id,job_id,status,created_at,sent_at)"
                       " VALUES(?,?,?,?,?,?)",
                       (candidate_id, run_id, job_id, "pending", now(), None))
    conn.commit()
    conn.close()
    return cur.lastrowid, True


def set_invite_status(invite_id: int, status: str, error: str | None = None):
    conn = get_conn()
    sent_at = now() if status == "sent" else None
    conn.execute("UPDATE invites SET status=?,error=?,sent_at=? WHERE id=?",
                 (status, error, sent_at, invite_id))
    conn.commit()
    conn.close()


def get_invite(invite_id: int) -> dict | None:
    conn = get_conn()
    row = conn.execute("SELECT * FROM invites WHERE id=?", (invite_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def list_invites(run_id: int | None = None, tag_id: int | None = None) -> list[dict]:
    """run_id 按 run 过滤；tag_id 按「候选 ∈ 标签」跨 run 过滤（标签模式）。"""
    conn = get_conn()
    if tag_id:
        rows = [dict(r) for r in conn.execute(
            "SELECT i.*, c.name AS candidate_name FROM invites i "
            "JOIN candidates c ON c.id=i.candidate_id "
            "WHERE i.candidate_id IN (SELECT candidate_id FROM tag_members WHERE tag_id=?) "
            "ORDER BY i.id DESC LIMIT 200", (tag_id,)).fetchall()]
    elif run_id:
        rows = [dict(r) for r in conn.execute(
            "SELECT i.*, c.name AS candidate_name FROM invites i "
            "JOIN candidates c ON c.id=i.candidate_id WHERE i.run_id=? ORDER BY i.id",
            (run_id,)).fetchall()]
    else:
        rows = [dict(r) for r in conn.execute(
            "SELECT i.*, c.name AS candidate_name FROM invites i "
            "JOIN candidates c ON c.id=i.candidate_id ORDER BY i.id DESC LIMIT 200").fetchall()]
    conn.close()
    return rows


# ---------- 候选标签（固定名单快照） ----------
def list_tags() -> list[dict]:
    conn = get_conn()
    rows = [dict(r) for r in conn.execute(
        "SELECT t.id, t.name, t.created_at, COUNT(tm.candidate_id) AS member_count "
        "FROM tags t LEFT JOIN tag_members tm ON tm.tag_id=t.id "
        "GROUP BY t.id ORDER BY t.id").fetchall()]
    conn.close()
    return rows


def get_tag(tag_id: int) -> dict | None:
    conn = get_conn()
    row = conn.execute("SELECT * FROM tags WHERE id=?", (tag_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def create_tag(name: str) -> int:
    """名称唯一；撞名抛 ValueError。"""
    conn = get_conn()
    try:
        cur = conn.execute("INSERT INTO tags(name,created_at) VALUES(?,?)", (name, now()))
        conn.commit()
        tid = cur.lastrowid
        conn.close()
        return tid
    except sqlite3.IntegrityError:
        conn.close()
        raise ValueError(f"标签已存在：{name}")


def rename_tag(tag_id: int, name: str):
    """不存在抛 ValueError(标签不存在)；撞名抛 ValueError(标签已存在)。"""
    conn = get_conn()
    try:
        cur = conn.execute("UPDATE tags SET name=? WHERE id=?", (name, tag_id))
        conn.commit()
        conn.close()
        if cur.rowcount == 0:
            raise ValueError("标签不存在")
    except sqlite3.IntegrityError:
        conn.close()
        raise ValueError(f"标签已存在：{name}")


def delete_tag(tag_id: int) -> bool:
    """删标签并清空成员。连接未开 foreign_keys，级联不生效，故显式先删成员。"""
    conn = get_conn()
    conn.execute("DELETE FROM tag_members WHERE tag_id=?", (tag_id,))
    cur = conn.execute("DELETE FROM tags WHERE id=?", (tag_id,))
    conn.commit()
    conn.close()
    return cur.rowcount > 0


def add_tag_members(tag_id: int, candidate_ids: list[int]) -> dict:
    """幂等追加：已在标签内的跳过。返回 {added, skipped}。"""
    added = 0
    conn = get_conn()
    for cid in candidate_ids:
        cur = conn.execute("INSERT OR IGNORE INTO tag_members(tag_id,candidate_id,added_at)"
                           " VALUES(?,?,?)", (tag_id, cid, now()))
        added += cur.rowcount
    conn.commit()
    conn.close()
    return {"added": added, "skipped": len(candidate_ids) - added}


def list_tag_candidates(tag_id: int) -> list[dict]:
    """标签成员（跨 run）：candidates 全字段 + invited 标记，按加入先后。"""
    conn = get_conn()
    rows = [dict(r) for r in conn.execute(
        "SELECT c.*,"
        " EXISTS(SELECT 1 FROM invites i WHERE i.candidate_id=c.id) AS invited "
        "FROM tag_members tm JOIN candidates c ON c.id=tm.candidate_id "
        "WHERE tm.tag_id=? ORDER BY tm.added_at, tm.rowid", (tag_id,)).fetchall()]
    conn.close()
    return rows


def remove_tag_member(tag_id: int, candidate_id: int) -> bool:
    conn = get_conn()
    cur = conn.execute("DELETE FROM tag_members WHERE tag_id=? AND candidate_id=?",
                       (tag_id, candidate_id))
    conn.commit()
    conn.close()
    return cur.rowcount > 0


# ---------- liepin cache ----------
def cache_get(key: str) -> str | None:
    conn = get_conn()
    row = conn.execute("SELECT result_json FROM liepin_cache WHERE key=?", (key,)).fetchone()
    conn.close()
    return row["result_json"] if row else None


def cache_put(key: str, result_json: str):
    conn = get_conn()
    conn.execute("INSERT OR REPLACE INTO liepin_cache(key,result_json,fetched_at) VALUES(?,?,?)",
                 (key, result_json, now()))
    conn.commit()
    conn.close()
