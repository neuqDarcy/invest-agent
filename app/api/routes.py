import os
import uuid
import shutil
from fastapi import APIRouter, UploadFile, File, HTTPException, Depends
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
from app.agents.orchestrator import run_analysis
from app.valuation.engine import run_valuation
from app.screener.screener import screen_stocks, ScreenerCriteria
from app.knowledge.pipeline import build_knowledge_base
from app.knowledge.store import get_indexed_reports, save_feedback, get_feedback_stats
from app.knowledge.extractor import get_metrics
from app.knowledge.notes import save_note, update_note, delete_note, list_notes, get_note
from app.knowledge.watchlist import add_to_watchlist, remove_from_watchlist, get_watchlist, is_in_watchlist
from app.knowledge.announcement_sync import sync_in_background
from app.knowledge.chat_history import save_message, get_history, clear_history
from app.knowledge.qa import ask, ask_stream
from app.knowledge.comparison import compare
from app.knowledge.broker_report import index_broker_report, get_broker_reports
from app.data.stock_data import get_stock_basic, get_stock_valuation_history, get_pb_percentile, search_stocks
from app.agents.fund_manager.agent import AgentSession, chat as fm_chat, chat_stream as fm_chat_stream
from app.agents.fund_manager.profiles import FUND_MANAGERS
from app.agents.router import route as route_question
from app.core.llm import chat as llm_chat, chat_stream as chat_stream_fn
from app.auth.jwt import get_optional_user
from app.auth.profile_learner import learn_async
import json
from app.core.config import settings

router = APIRouter()

# 内存中的 session 存储（进程级，重启丢失）
_fm_sessions: dict[str, AgentSession] = {}


class CompareRequest(BaseModel):
    question: str


class UnifiedChatRequest(BaseModel):
    session_id: str | None = None
    manager_id: str = "value"
    message: str
    current_stock_code: str | None = None
    current_stock_name: str | None = None   # 新增：公司名称
    last_route: str | None = None


class KnowledgeBuildRequest(BaseModel):
    stock_code: str
    report_types: list[str] = ["annual"]
    start_year: int = 2020
    end_year: int = 2024


class KnowledgeAskRequest(BaseModel):
    stock_code: str
    question: str
    report_type: str = "annual"

class FeedbackRequest(BaseModel):
    stock_code: str
    question: str
    answer: str
    sources: list[str] = []
    rating: int          # 1=好 0=差
    comment: str = ""
    report_type: str = "annual"


class ValuationRequest(BaseModel):
    stock_code: str
    industry_name: str | None = None
    model: str = "pb"


class ScreenRequest(BaseModel):
    market_cap_min: float | None = None
    market_cap_max: float | None = None
    pb_max: float | None = None
    pb_min: float | None = None
    pe_max: float | None = None
    pe_min: float | None = None
    top_n: int = 50


@router.post("/analyze")
async def analyze_report(file: UploadFile = File(...)):
    if not file.filename.endswith(".pdf"):
        raise HTTPException(status_code=400, detail="仅支持 PDF 文件")

    os.makedirs(settings.upload_dir, exist_ok=True)
    os.makedirs(settings.output_dir, exist_ok=True)

    file_path = os.path.join(settings.upload_dir, file.filename)
    with open(file_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    try:
        report, markdown = run_analysis(file_path)

        return JSONResponse({
            "status": "success",
            "file": file.filename,
            "from_cache": report.from_cache,
            "report": markdown,
        })
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/valuation")
def valuation(req: ValuationRequest):
    try:
        result = run_valuation(req.stock_code, req.industry_name, req.model)
        return JSONResponse({
            "status": "success",
            "stock_code": req.stock_code,
            "model": result.model_name,
            "current_price": result.current_price,
            "current_status": result.current_status,
            "fair_value_low": result.fair_value_low,
            "fair_value_high": result.fair_value_high,
            "buy_price": result.buy_price,
            "sell_price": result.sell_price,
            "reasoning": result.reasoning,
        })
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/screen")
def screen(req: ScreenRequest):
    try:
        criteria = ScreenerCriteria(
            market_cap_min=req.market_cap_min,
            market_cap_max=req.market_cap_max,
            pb_max=req.pb_max,
            pb_min=req.pb_min,
            pe_max=req.pe_max,
            pe_min=req.pe_min,
            top_n=req.top_n,
        )
        results = screen_stocks(criteria)
        return JSONResponse({
            "status": "success",
            "count": len(results),
            "stocks": [r.__dict__ for r in results],
        })
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/knowledge/build")
def knowledge_build(req: KnowledgeBuildRequest):
    """为指定股票下载年报并建立知识库（首次较慢）"""
    try:
        result = build_knowledge_base(
            stock_code=req.stock_code,
            report_types=req.report_types,
            start_year=req.start_year,
            end_year=req.end_year,
        )
        return JSONResponse({
            "status": "success",
            "stock_code": result.stock_code,
            "total": result.total,
            "indexed": result.indexed,
            "skipped": result.skipped,
            "failed": result.failed,
            "details": result.details,
        })
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/knowledge/status/{stock_code}")
def knowledge_status(stock_code: str):
    """查询某公司知识库入库状态"""
    reports = get_indexed_reports(stock_code)
    return JSONResponse({
        "stock_code": stock_code,
        "indexed_count": len(reports),
        "reports": reports,
    })


@router.get("/knowledge/metrics/{stock_code}")
def knowledge_metrics(stock_code: str, years: int = 3):
    """查询某公司结构化财务指标"""
    metrics = get_metrics(stock_code, years=years)
    from collections import defaultdict
    by_year: dict = defaultdict(dict)
    for m in metrics:
        year = m["ann_date"][:4]
        by_year[year][m["metric_name"]] = m["value"]
    return JSONResponse({
        "stock_code": stock_code,
        "years": dict(sorted(by_year.items(), reverse=True)),
    })


@router.post("/knowledge/compare")
def knowledge_compare(req: CompareRequest):
    """多公司横向对比问答，问题中需包含股票代码"""
    try:
        result = compare(question=req.question)
        return JSONResponse(result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/knowledge/ask")
def knowledge_ask(req: KnowledgeAskRequest):
    """对已入库的公司知识库提问"""
    try:
        result = ask(
            stock_code=req.stock_code,
            question=req.question,
            report_type=req.report_type,
        )
        return JSONResponse(result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/stock/overview/{stock_code}")
def stock_overview(stock_code: str, current_user: dict | None = Depends(get_optional_user)):
    """股票综合概览：行情 + PE/PB + 52周高低 + 财务指标 + 估值区间"""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from app.data.stock_data import _get_pro
    import pandas as pd
    from collections import defaultdict

    try:
        # 三个独立请求并行执行
        with ThreadPoolExecutor(max_workers=3) as ex:
            f_basic   = ex.submit(get_stock_basic, stock_code)
            f_history = ex.submit(get_stock_valuation_history, stock_code)
            f_metrics = ex.submit(lambda: get_metrics(stock_code, years=3))
            basic       = f_basic.result()
            history     = f_history.result()
            metrics_raw = f_metrics.result()

        pb_stats = get_pb_percentile(history)

        metrics_by_year: dict = defaultdict(dict)
        for m in metrics_raw:
            year = m["ann_date"][:4]
            metrics_by_year[year][m["metric_name"]] = m["value"]
        metrics_by_year = dict(sorted(metrics_by_year.items(), reverse=True))

        # 估值区间（复用已有 basic/history，不重复请求）
        valuation = None
        try:
            v = run_valuation(stock_code, basic=basic, history=history)
            valuation = {
                "model": v.model_name,
                "fair_value_low": v.fair_value_low,
                "fair_value_high": v.fair_value_high,
                "buy_price": v.buy_price,
                "sell_price": v.sell_price,
                "current_status": v.current_status,
                "reasoning": v.reasoning,
            }
        except Exception:
            pass

        return JSONResponse({
            "stock_code": basic.code,
            "name": basic.name,
            "current_price": basic.current_price,
            "market_cap": basic.market_cap,
            "circ_mv": basic.circ_mv,
            "pe": basic.pe,
            "pe_ttm": basic.pe_ttm,
            "pb": basic.pb,
            "dv_ratio": basic.dv_ratio,
            "total_share": basic.total_share,
            "float_share": basic.float_share,
            "eps": basic.eps,
            "bps": basic.bps,
            "week52_high": basic.week52_high,
            "week52_low": basic.week52_low,
            "pb_stats": pb_stats,
            "metrics_by_year": metrics_by_year,
            "valuation": valuation,
            "watched": is_in_watchlist((current_user or {}).get("id", ""), stock_code),
        })
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        # 后台异步同步公告，不阻塞响应
        try:
            sync_in_background(stock_code)
        except Exception:
            pass


@router.post("/knowledge/broker/upload")
async def broker_upload(file: UploadFile = File(...)):
    """上传券商研报 PDF，自动提取结构化信息并入向量库"""
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="仅支持 PDF 文件")

    save_dir = os.path.join(settings.upload_dir, "broker")
    os.makedirs(save_dir, exist_ok=True)
    file_path = os.path.join(save_dir, file.filename)
    with open(file_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    try:
        result = index_broker_report(file_path)
        return JSONResponse({"status": "success", **result})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/knowledge/broker/list")
def broker_list(stock_code: str | None = None):
    """查询已入库的券商研报列表"""
    reports = get_broker_reports(stock_code)
    return JSONResponse({"count": len(reports), "reports": reports})


@router.post("/knowledge/feedback")
def knowledge_feedback(req: FeedbackRequest):
    """记录问答反馈"""
    try:
        fid = save_feedback(
            stock_code=req.stock_code,
            question=req.question,
            answer=req.answer,
            sources=req.sources,
            rating=req.rating,
            comment=req.comment,
        )
        return JSONResponse({"status": "success", "id": fid})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/knowledge/feedback/stats")
def feedback_stats(stock_code: str | None = None):
    """查看反馈统计（差评问题列表供改进参考）"""
    return JSONResponse(get_feedback_stats(stock_code))



class FMChatRequest(BaseModel):
    session_id: str | None = None   # 为空则新建会话
    manager_id: str = "value"
    message: str


@router.post("/chat")
def unified_chat(req: UnifiedChatRequest):
    """
    统一对话入口：自动路由到知识库问答或基金经理 Agent。
    """
    routing = route_question(req.message, req.current_stock_code)
    r = routing["route"]
    stock_code = routing["stock_code"]

    # ── 路径1：知识库问答 ──────────────────────────────────────────────
    if r == "knowledge_qa" and stock_code:
        try:
            result = ask(stock_code=stock_code, question=req.message)
            return JSONResponse({
                "route": "knowledge_qa",
                "stock_code": stock_code,
                "session_id": req.session_id,
                "messages": [{
                    "role": "assistant",
                    "content": result.get("answer", ""),
                    "sources": result.get("sources", []),
                }],
            })
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    # ── 路径2：基金经理 Agent ─────────────────────────────────────────
    if r == "fund_manager":
        session_id = req.session_id
        if not session_id or session_id not in _fm_sessions:
            session_id = str(uuid.uuid4())
            _fm_sessions[session_id] = AgentSession(manager_id=req.manager_id)
        session = _fm_sessions[session_id]
        try:
            new_messages = fm_chat(session, req.message)
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
        return JSONResponse({
            "route": "fund_manager",
            "stock_code": stock_code,
            "session_id": session_id,
            "messages": [
                {
                    "role": m.role,
                    "content": m.content,
                    "thinking": m.thinking,
                    "tool_name": m.tool_name,
                    "tool_calls": [
                        {"name": tc.function.name, "arguments": tc.function.arguments}
                        for tc in (m.tool_calls or [])
                    ],
                }
                for m in new_messages
            ],
        })

    # ── 路径3：通用问题 ───────────────────────────────────────────────
    try:
        answer = llm_chat(
            system="你是一位专业的A股投资研究助手，回答简洁专业。",
            user=req.message,
        )
        return JSONResponse({
            "route": "general",
            "stock_code": None,
            "session_id": req.session_id,
            "messages": [{"role": "assistant", "content": answer, "sources": []}],
        })
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/chat/stream")
def unified_chat_stream(req: UnifiedChatRequest, current_user: dict | None = Depends(get_optional_user)):
    """统一流式对话接口，SSE 格式输出"""
    # 上下文感知路由：
    # 1. 若上一轮是 fund_manager 且有 session_id，继续走基金经理（用户在多轮对话中）
    # 2. 否则重新路由
    has_active_fm_session = (
        req.last_route == "fund_manager"
        and req.session_id
        and req.session_id in _fm_sessions
    )

    if has_active_fm_session:
        r = "fund_manager"
        stock_code = req.current_stock_code
    else:
        routing = route_question(req.message, req.current_stock_code)
        r = routing["route"]
        stock_code = routing["stock_code"]

    # 公司上下文前缀
    stock_ctx = ""
    if req.current_stock_code and req.current_stock_name:
        stock_ctx = f"当前用户正在研究的公司：{req.current_stock_name}（{req.current_stock_code}）。"
    elif req.current_stock_code:
        stock_ctx = f"当前用户正在研究的股票代码：{req.current_stock_code}。"

    uid = (current_user or {}).get("id", "")
    # 保存用户消息
    save_message(role="user", content=req.message,
                 stock_code=stock_code or req.current_stock_code or "",
                 route=r, user_id=uid)
    # 后台异步学习用户偏好
    if uid:
        learn_async(uid, req.message)

    def generate():
        yield f"data: {json.dumps({'type':'route','route':r,'stock_code':stock_code}, ensure_ascii=False)}\n\n"

        full_answer = []
        answer_sources = []

        if r == "knowledge_qa" and stock_code:
            for raw_event in ask_stream(stock_code=stock_code, question=req.message,
                                        stock_context=stock_ctx):
                yield f"data: {raw_event}\n\n"
                try:
                    ev = json.loads(raw_event)
                    if ev.get("type") == "token":
                        full_answer.append(ev["content"])
                    elif ev.get("type") == "sources":
                        answer_sources = ev.get("sources", [])
                except Exception:
                    pass

        elif r == "fund_manager":
            session_id = req.session_id
            if not session_id or session_id not in _fm_sessions:
                import uuid as _uuid
                session_id = str(_uuid.uuid4())
                _fm_sessions[session_id] = AgentSession(
                    manager_id=req.manager_id,
                    stock_context=stock_ctx,
                )
            yield f"data: {json.dumps({'type':'session_id','session_id':session_id})}\n\n"
            session = _fm_sessions[session_id]
            for event in fm_chat_stream(session, req.message):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                if event.get("type") == "token":
                    full_answer.append(event["content"])

        else:
            system = f"你是一位专业的A股投资研究助手，回答简洁专业。{stock_ctx}"
            for token in chat_stream_fn(system=system, user=req.message):
                full_answer.append(token)
                yield f"data: {json.dumps({'type':'token','content':token}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type':'done'})}\n\n"

        # 保存 assistant 回答
        if full_answer:
            save_message(
                role="assistant",
                content="".join(full_answer),
                stock_code=stock_code or req.current_stock_code or "",
                sources=answer_sources,
                route=r,
                user_id=uid,
            )

    return StreamingResponse(generate(), media_type="text/event-stream",
                              headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/fm/managers")
def fm_list_managers():
    """列出所有可用的基金经理"""
    return JSONResponse([
        {
            "id": m.id,
            "name": m.name,
            "style": m.style,
            "description": m.description,
            "analysis_priorities": m.analysis_priorities,
        }
        for m in FUND_MANAGERS.values()
    ])


@router.post("/fm/chat")
def fm_chat_endpoint(req: FMChatRequest):
    """和基金经理对话，返回本轮所有新消息"""
    # 获取或创建 session
    session_id = req.session_id
    if not session_id or session_id not in _fm_sessions:
        session_id = str(uuid.uuid4())
        _fm_sessions[session_id] = AgentSession(manager_id=req.manager_id)

    session = _fm_sessions[session_id]

    try:
        new_messages = fm_chat(session, req.message)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return JSONResponse({
        "session_id": session_id,
        "manager_id": req.manager_id,
        "messages": [
            {
                "role": m.role,
                "content": m.content,
                "thinking": m.thinking,
                "tool_name": m.tool_name,
                "tool_calls": [
                    {"name": tc.function.name, "arguments": tc.function.arguments}
                    for tc in (m.tool_calls or [])
                ],
            }
            for m in new_messages
        ],
    })


@router.delete("/fm/session/{session_id}")
def fm_clear_session(session_id: str):
    """清除会话历史，开始新对话"""
    _fm_sessions.pop(session_id, None)
    return JSONResponse({"status": "cleared"})


@router.get("/stock/search")
def stock_search(q: str = "", limit: int = 10):
    """模糊搜索股票，支持代码前缀和名称包含"""
    if not q.strip():
        return JSONResponse([])
    try:
        results = search_stocks(q.strip(), limit=limit)
        return JSONResponse(results)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class NoteRequest(BaseModel):
    content: str
    stock_code: str = ""
    title: str = ""


@router.post("/notes")
def note_create(req: NoteRequest, current_user: dict | None = Depends(get_optional_user)):
    uid = (current_user or {}).get("id", "")
    note = save_note(req.content, req.stock_code, req.title, user_id=uid)
    return JSONResponse(note)


@router.get("/notes")
def note_list(stock_code: str = "", current_user: dict | None = Depends(get_optional_user)):
    uid = (current_user or {}).get("id", "")
    notes = list_notes(stock_code=stock_code or None, user_id=uid)
    return JSONResponse(notes)


@router.put("/notes/{note_id}")
def note_update(note_id: str, req: NoteRequest):
    note = update_note(note_id, req.content, req.title)
    return JSONResponse(note)


@router.delete("/notes/{note_id}")
def note_delete(note_id: str):
    delete_note(note_id)
    return JSONResponse({"status": "deleted"})


@router.get("/chat/history")
def chat_history_get(stock_code: str = "", limit: int = 50,
                     current_user: dict | None = Depends(get_optional_user)):
    uid = (current_user or {}).get("id", "")
    records = get_history(stock_code=stock_code or None, limit=limit, user_id=uid)
    return JSONResponse(records)


@router.delete("/chat/history")
def chat_history_clear(stock_code: str = "", current_user: dict | None = Depends(get_optional_user)):
    uid = (current_user or {}).get("id", "")
    clear_history(stock_code=stock_code or None, user_id=uid)
    return JSONResponse({"status": "cleared"})


class WatchlistRequest(BaseModel):
    stock_code: str
    stock_name: str = ""


@router.get("/watchlist")
def watchlist_get(current_user: dict | None = Depends(get_optional_user)):
    uid = (current_user or {}).get("id", "")
    if not uid:
        return JSONResponse([])
    items = get_watchlist(uid)
    return JSONResponse(items)


@router.post("/watchlist")
def watchlist_add(req: WatchlistRequest, current_user: dict | None = Depends(get_optional_user)):
    uid = (current_user or {}).get("id", "")
    if not uid:
        raise HTTPException(status_code=401, detail="请先登录")
    item = add_to_watchlist(uid, req.stock_code, req.stock_name)
    return JSONResponse(item)


@router.delete("/watchlist/{stock_code}")
def watchlist_remove(stock_code: str, current_user: dict | None = Depends(get_optional_user)):
    uid = (current_user or {}).get("id", "")
    if not uid:
        raise HTTPException(status_code=401, detail="请先登录")
    remove_from_watchlist(uid, stock_code)
    return JSONResponse({"status": "removed"})


@router.get("/watchlist/check/{stock_code}")
def watchlist_check(stock_code: str, current_user: dict | None = Depends(get_optional_user)):
    uid = (current_user or {}).get("id", "")
    watched = is_in_watchlist(uid, stock_code) if uid else False
    return JSONResponse({"watched": watched})


@router.get("/health")
def health():
    return {"status": "ok"}
