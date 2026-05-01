import logging
import os
from logging.handlers import RotatingFileHandler

LOG_DIR = os.path.join(os.path.dirname(__file__), "../../logs")
os.makedirs(LOG_DIR, exist_ok=True)


def setup_logger():
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"
    formatter = logging.Formatter(fmt, datefmt)

    root = logging.getLogger()
    root.setLevel(logging.INFO)

    # 终端输出
    if not any(isinstance(h, logging.StreamHandler) for h in root.handlers):
        sh = logging.StreamHandler()
        sh.setFormatter(formatter)
        root.addHandler(sh)

    # 文件输出：按大小轮转，保留最近 5 个文件，每个最大 10MB
    log_file = os.path.join(LOG_DIR, "app.log")
    fh = RotatingFileHandler(log_file, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8")
    fh.setFormatter(formatter)
    root.addHandler(fh)

    # 单独记录 API 请求日志
    access_file = os.path.join(LOG_DIR, "access.log")
    access_handler = RotatingFileHandler(access_file, maxBytes=10 * 1024 * 1024, backupCount=3, encoding="utf-8")
    access_handler.setFormatter(formatter)
    logging.getLogger("uvicorn.access").addHandler(access_handler)

    # 压制 chromadb 的 telemetry 噪音
    logging.getLogger("chromadb.telemetry").setLevel(logging.ERROR)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
