from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from app.api.routes import router
from app.auth.routes import router as auth_router
from app.core.logger import setup_logger, get_logger
import time

setup_logger()
logger = get_logger("app")

# 启动时自动运行数据库迁移
from app.auth.migrate import run_migrations
run_migrations()

app = FastAPI(title="研报分析 Agent", version="0.1.0")


@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.time()
    response = await call_next(request)
    duration = round((time.time() - start) * 1000)
    logger.info(f"{request.method} {request.url.path} → {response.status_code} ({duration}ms)")
    return response

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:5174"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api")
app.include_router(auth_router, prefix="/api")
