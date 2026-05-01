"""
JWT token 生成与验证
"""
import os
from datetime import datetime, timedelta
from jose import JWTError, jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from app.auth.users import get_user_by_id

SECRET_KEY = os.environ.get("JWT_SECRET", "report-agent-secret-change-in-prod-2026")
ALGORITHM = "HS256"
EXPIRE_DAYS = 30

bearer_scheme = HTTPBearer(auto_error=False)


def create_token(user_id: str, username: str) -> str:
    expire = datetime.utcnow() + timedelta(days=EXPIRE_DAYS)
    return jwt.encode(
        {"sub": user_id, "username": username, "exp": expire},
        SECRET_KEY, algorithm=ALGORITHM,
    )


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        raise HTTPException(status_code=401, detail="Token 无效或已过期")


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> dict:
    """FastAPI 依赖项：从 Bearer token 获取当前用户"""
    if not credentials:
        raise HTTPException(status_code=401, detail="未登录，请先登录")
    payload = decode_token(credentials.credentials)
    user = get_user_by_id(payload["sub"])
    if not user:
        raise HTTPException(status_code=401, detail="用户不存在")
    return user


def get_optional_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> dict | None:
    """可选认证：有 token 则返回用户，无则返回 None（用于兼容未登录场景）"""
    if not credentials:
        return None
    try:
        payload = decode_token(credentials.credentials)
        return get_user_by_id(payload["sub"])
    except Exception:
        return None
