"""
用户管理：注册、认证、画像存取
"""
import uuid
import json
from datetime import datetime
from passlib.context import CryptContext
from app.knowledge.store import _get_db

# 使用 sha256_crypt 方案存储密码哈希，deprecated="auto" 支持未来算法迁移
pwd_ctx = CryptContext(schemes=["sha256_crypt"], deprecated="auto")


def _ensure_tables():
    """确保 users 和 user_profiles 表已创建，幂等操作，每次调用前执行。"""
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
    """
    注册新用户，同时创建默认画像记录。

    参数:
        username: 用户名（不含前后空格）
        password: 明文密码

    返回:
        {id, username} 字典

    抛出:
        ValueError: 用户名已存在
    """
    _ensure_tables()
    user_id = str(uuid.uuid4())
    password_hash = pwd_ctx.hash(password)
    with _get_db() as conn:
        try:
            conn.execute(
                "INSERT INTO users (id, username, password_hash) VALUES (?,?,?)",
                (user_id, username.strip(), password_hash),
            )
            conn.execute(
                "INSERT INTO user_profiles (user_id, display_name) VALUES (?,?)",
                (user_id, username.strip()),
            )
            conn.commit()
        except Exception as error:
            if "UNIQUE" in str(error):
                raise ValueError("用户名已存在")
            raise
    return {"id": user_id, "username": username}


def authenticate(username: str, password: str) -> dict | None:
    """
    验证用户名和密码。

    参数:
        username: 用户名
        password: 明文密码

    返回:
        认证成功返回 {id, username}，失败返回 None
    """
    _ensure_tables()
    with _get_db() as conn:
        row = conn.execute(
            "SELECT id, username, password_hash FROM users WHERE username=?",
            (username.strip(),),
        ).fetchone()
    if not row:
        return None
    # 使用 passlib 的 verify 做恒时比较，防止时序攻击
    if not pwd_ctx.verify(password, row["password_hash"]):
        return None
    return {"id": row["id"], "username": row["username"]}


def get_profile(user_id: str) -> dict:
    """
    获取用户投资画像。

    参数:
        user_id: 用户唯一 ID

    返回:
        画像字典，focus_industries 已反序列化为列表；用户不存在时返回空字典
    """
    _ensure_tables()
    with _get_db() as conn:
        row = conn.execute(
            "SELECT * FROM user_profiles WHERE user_id=?", (user_id,)
        ).fetchone()
    if not row:
        return {}
    profile_dict = dict(row)
    # focus_industries 在数据库中以 JSON 字符串存储，读取时反序列化
    for json_field in ["focus_industries"]:
        try:
            profile_dict[json_field] = json.loads(profile_dict[json_field])
        except Exception:
            profile_dict[json_field] = []
    return profile_dict


def update_profile(user_id: str, **kwargs) -> dict:
    """
    更新用户投资画像的指定字段。

    参数:
        user_id: 用户唯一 ID
        **kwargs: 允许更新的字段及新值，不在白名单内的字段会被忽略

    返回:
        更新后的完整画像字典
    """
    _ensure_tables()
    # 白名单控制，防止通过 kwargs 篡改 user_id 等敏感字段
    allowed_fields = {
        "display_name", "invest_style", "risk_level",
        "focus_industries", "invest_horizon", "target_return", "notes",
    }
    valid_updates = {key: value for key, value in kwargs.items() if key in allowed_fields}
    if not valid_updates:
        return get_profile(user_id)
    # focus_industries 为列表类型，存储前序列化为 JSON 字符串
    if "focus_industries" in valid_updates and isinstance(valid_updates["focus_industries"], list):
        valid_updates["focus_industries"] = json.dumps(
            valid_updates["focus_industries"], ensure_ascii=False
        )
    set_clause = ", ".join(f"{key}=?" for key in valid_updates)
    param_values = list(valid_updates.values()) + [user_id]
    with _get_db() as conn:
        conn.execute(
            f"UPDATE user_profiles SET {set_clause}, updated_at=datetime('now','localtime') WHERE user_id=?",
            param_values,
        )
        conn.commit()
    return get_profile(user_id)


def get_user_by_id(user_id: str) -> dict | None:
    """
    通过 ID 查询用户基本信息。

    参数:
        user_id: 用户唯一 ID

    返回:
        {id, username} 字典，用户不存在时返回 None
    """
    _ensure_tables()
    with _get_db() as conn:
        row = conn.execute(
            "SELECT id, username FROM users WHERE id=?", (user_id,)
        ).fetchone()
    return dict(row) if row else None
