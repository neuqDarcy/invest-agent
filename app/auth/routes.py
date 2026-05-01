from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator
from app.auth.users import create_user, authenticate, get_profile, update_profile
from app.auth.jwt import create_token, get_current_user

router = APIRouter(prefix="/auth", tags=["auth"])


class RegisterRequest(BaseModel):
    username: str
    password: str

    @field_validator("username")
    @classmethod
    def username_valid(cls, v):
        v = v.strip()
        if len(v) < 2:
            raise ValueError("用户名至少2个字符")
        if len(v) > 20:
            raise ValueError("用户名最多20个字符")
        return v

    @field_validator("password")
    @classmethod
    def password_valid(cls, v):
        if len(v) < 6:
            raise ValueError("密码至少6位")
        return v


class LoginRequest(BaseModel):
    username: str
    password: str


class ProfileUpdateRequest(BaseModel):
    display_name: str | None = None
    invest_style: str | None = None    # value/growth/garp
    risk_level: str | None = None      # conservative/moderate/aggressive
    focus_industries: list[str] | None = None
    invest_horizon: str | None = None  # short/medium/long
    target_return: float | None = None
    notes: str | None = None


@router.post("/register")
def register(req: RegisterRequest):
    try:
        user = create_user(req.username, req.password)
        token = create_token(user["id"], user["username"])
        return JSONResponse({"token": token, "user": user})
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/login")
def login(req: LoginRequest):
    user = authenticate(req.username, req.password)
    if not user:
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    token = create_token(user["id"], user["username"])
    profile = get_profile(user["id"])
    return JSONResponse({"token": token, "user": {**user, "profile": profile}})


@router.get("/me")
def me(current_user: dict = Depends(get_current_user)):
    profile = get_profile(current_user["id"])
    return JSONResponse({"user": {**current_user, "profile": profile}})


@router.put("/profile")
def update_my_profile(
    req: ProfileUpdateRequest,
    current_user: dict = Depends(get_current_user),
):
    data = {k: v for k, v in req.model_dump().items() if v is not None}
    profile = update_profile(current_user["id"], **data)
    return JSONResponse({"profile": profile})
