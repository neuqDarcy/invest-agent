"""
用户管理：注册、认证、画像存取
"""
import uuid
import json
from datetime import datetime
from passlib.context import CryptContext
from app.knowledge.store import _get_db

pwd_ctx = CryptContext(schemes=["sha256_crypt"], deprecated="auto")


def _ensure_tables():
    with _get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id          TEXT PRIMARY KEY,
                username    TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at  TEXT DEFAULT (datetime('now','localtime'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_profiles (
                user_id         TEXT PRIMARY KEY REFERENCES users(id),
                display_name    TEXT,
                invest_style    TEXT DEFAULT 'value',
                risk_level      TEXT DEFAULT 'moderate',
                focus_industries TEXT DEFAULT '[]',
                invest_horizon  TEXT DEFAULT 'long',
                target_return   REAL DEFAULT 15.0,
                notes           TEXT DEFAULT '',
                updated_at      TEXT DEFAULT (datetime('now','localtime'))
            )
        """)
        conn.commit()


def create_user(username: str, password: str) -> dict:
    _ensure_tables()
    user_id = str(uuid.uuid4())
    pwd_hash = pwd_ctx.hash(password)
    with _get_db() as conn:
        try:
            conn.execute(
                "INSERT INTO users (id, username, password_hash) VALUES (?,?,?)",
                (user_id, username.strip(), pwd_hash),
            )
            conn.execute(
                "INSERT INTO user_profiles (user_id, display_name) VALUES (?,?)",
                (user_id, username.strip()),
            )
            conn.commit()
        except Exception as e:
            if "UNIQUE" in str(e):
                raise ValueError("用户名已存在")
            raise
    return {"id": user_id, "username": username}


def authenticate(username: str, password: str) -> dict | None:
    _ensure_tables()
    with _get_db() as conn:
        row = conn.execute(
            "SELECT id, username, password_hash FROM users WHERE username=?",
            (username.strip(),),
        ).fetchone()
    if not row:
        return None
    if not pwd_ctx.verify(password, row["password_hash"]):
        return None
    return {"id": row["id"], "username": row["username"]}


def get_profile(user_id: str) -> dict:
    _ensure_tables()
    with _get_db() as conn:
        row = conn.execute(
            "SELECT * FROM user_profiles WHERE user_id=?", (user_id,)
        ).fetchone()
    if not row:
        return {}
    d = dict(row)
    for field in ["focus_industries"]:
        try:
            d[field] = json.loads(d[field])
        except Exception:
            d[field] = []
    return d


def update_profile(user_id: str, **kwargs) -> dict:
    _ensure_tables()
    allowed = {
        "display_name", "invest_style", "risk_level",
        "focus_industries", "invest_horizon", "target_return", "notes",
    }
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
        return get_profile(user_id)
    if "focus_industries" in updates and isinstance(updates["focus_industries"], list):
        updates["focus_industries"] = json.dumps(updates["focus_industries"], ensure_ascii=False)
    sets = ", ".join(f"{k}=?" for k in updates)
    vals = list(updates.values()) + [user_id]
    with _get_db() as conn:
        conn.execute(
            f"UPDATE user_profiles SET {sets}, updated_at=datetime('now','localtime') WHERE user_id=?",
            vals,
        )
        conn.commit()
    return get_profile(user_id)


def get_user_by_id(user_id: str) -> dict | None:
    _ensure_tables()
    with _get_db() as conn:
        row = conn.execute(
            "SELECT id, username FROM users WHERE id=?", (user_id,)
        ).fetchone()
    return dict(row) if row else None
