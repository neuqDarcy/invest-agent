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

# 基金经理会话的内存存储（进程级，服务重启后丢失）
# key 为 session_id（UUID），value 为 AgentSession 实例
_fm_sessions: dict[str, AgentSession] = {}


# ──────────────────────────────────────────────────────────────────────────────
# 请求体模型定义
# ──────────────────────────────────────────────────────────────────────────────

class CompareRequest(BaseModel):
    """多公司横向对比问答请求，问题中需包含股票代码。"""
    question: str


class UnifiedChatRequest(BaseModel):
    """统一对话入口请求体，路由逻辑在接口内部处理。"""
    session_id: str | None = None          # 已有会话 ID，为空则新建
    manager_id: str = "value"              # 基金经理风格 ID，默认价值投资
    message: str                           # 用户消息内容
    current_stock_code: str | None = None  # 当前上下文股票代码
    current_stock_name: str | None = None  # 当前上下文公司名称
    last_route: str | None = None          # 上一轮路由结果，用于多轮会话保持


class KnowledgeBuildRequest(BaseModel):
    """知识库构建请求：指定股票、报告类型和年份范围。"""
    stock_code: str
    report_types: list[str] = ["annual"]
    start_year: int = 2020
    end_year: int = 2024


class KnowledgeAskRequest(BaseModel):
    """知识库问答请求。"""
    stock_code: str
    question: str
    report_type: str = "annual"


class FeedbackRequest(BaseModel):
    """问答反馈请求，用于收集用户对回答质量的评价。"""
    stock_code: str
    question: str
    answer: str
    sources: list[str] = []
    rating: int          # 1=好评，0=差评
    comment: str = ""
    report_type: str = "annual"


class ValuationRequest(BaseModel):
    """估值分析请求，支持指定行业和估值模型。"""
    stock_code: str
    industry_name: str | None = None
    model: str = "pb"


class ScreenRequest(BaseModel):
    """股票筛选请求，支持市值、PE、PB 区间过滤。"""
    market_cap_min: float | None = None
    market_cap_max: float | None = None
    pb_max: float | None = None
    pb_min: float | None = None
    pe_max: float | None = None
    pe_min: float | None = None
    top_n: int = 50


# ──────────────────────────────────────────────────────────────────────────────
# 报告分析
# ──────────────────────────────────────────────────────────────────────────────

@router.post("/analyze")
async def analyze_report(file: UploadFile = File(...)):
    """
    上传年报 PDF，触发多 Agent 分析流程，返回结构化研究报告。

    参数：
        file: 仅支持 .pdf 格式的上传文件

    返回：
        JSON，包含分析状态、文件名、是否命中缓存、Markdown 格式报告
    """
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


# ──────────────────────────────────────────────────────────────────────────────
# 估值与筛选
# ──────────────────────────────────────────────────────────────────────────────

@router.post("/valuation")
def valuation(req: ValuationRequest):
    """
    对指定股票进行估值分析，返回合理价值区间和买卖参考价。

    参数：
        req: 包含股票代码、行业名称（可选）、估值模型

    返回：
        JSON，包含当前价、估值区间、买卖参考价及推理说明
    """
    try:
        valuation_result = run_valuation(req.stock_code, req.industry_name, req.model)
        return JSONResponse({
            "status": "success",
            "stock_code": req.stock_code,
            "model": valuation_result.model_name,
            "current_price": valuation_result.current_price,
            "current_status": valuation_result.current_status,
            "fair_value_low": valuation_result.fair_value_low,
            "fair_value_high": valuation_result.fair_value_high,
            "buy_price": valuation_result.buy_price,
            "sell_price": valuation_result.sell_price,
            "reasoning": valuation_result.reasoning,
        })
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/screen")
def screen(req: ScreenRequest):
    """
    按市值、PE、PB 等条件筛选股票，返回符合条件的股票列表。

    参数：
        req: 筛选条件，所有字段可选，未填则不过滤

    返回：
        JSON，包含命中数量和股票详情列表
    """
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
        screen_results = screen_stocks(criteria)
        return JSONResponse({
            "status": "success",
            "count": len(screen_results),
            "stocks": [stock.__dict__ for stock in screen_results],
        })
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ──────────────────────────────────────────────────────────────────────────────
# 知识库管理
# ──────────────────────────────────────────────────────────────────────────────

@router.post("/knowledge/build")
def knowledge_build(req: KnowledgeBuildRequest):
    """
    为指定股票下载年报并建立向量知识库（首次较慢，约数分钟）。

    参数：
        req: 包含股票代码、报告类型列表、起止年份

    返回：
        JSON，包含入库总数、成功数、跳过数、失败数及明细
    """
    try:
        build_result = build_knowledge_base(
            stock_code=req.stock_code,
            report_types=req.report_types,
            start_year=req.start_year,
            end_year=req.end_year,
        )
        return JSONResponse({
            "status": "success",
            "stock_code": build_result.stock_code,
            "total": build_result.total,
            "indexed": build_result.indexed,
            "skipped": build_result.skipped,
            "failed": build_result.failed,
            "details": build_result.details,
        })
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/knowledge/status/{stock_code}")
def knowledge_status(stock_code: str):
    """
    查询某公司知识库的入库状态（已索引报告列表）。

    参数：
        stock_code: 股票代码

    返回：
        JSON，包含已入库报告数量和报告明细列表
    """
    indexed_reports = get_indexed_reports(stock_code)
    return JSONResponse({
        "stock_code": stock_code,
        "indexed_count": len(indexed_reports),
        "reports": indexed_reports,
    })


@router.get("/knowledge/metrics/{stock_code}")
def knowledge_metrics(stock_code: str, years: int = 3):
    """
    查询某公司从年报中提取的结构化财务指标，按年份聚合。

    参数：
        stock_code: 股票代码
        years:      获取近几年数据，默认 3 年

    返回：
        JSON，按年份降序排列的财务指标字典
    """
    from collections import defaultdict

    raw_metrics = get_metrics(stock_code, years=years)

    # 将平铺的指标列表按年份聚合为嵌套字典，方便前端按年展示
    metrics_by_year: dict = defaultdict(dict)
    for metric_item in raw_metrics:
        year = metric_item["ann_date"][:4]
        metrics_by_year[year][metric_item["metric_name"]] = metric_item["value"]

    return JSONResponse({
        "stock_code": stock_code,
        "years": dict(sorted(metrics_by_year.items(), reverse=True)),
    })


@router.post("/knowledge/compare")
def knowledge_compare(req: CompareRequest):
    """
    多公司横向对比问答，问题中需包含股票代码（如"茅台和五粮液谁的毛利率更高"）。

    参数：
        req: 包含对比问题的请求体

    返回：
        JSON，包含对比分析结果
    """
    try:
        compare_result = compare(question=req.question)
        return JSONResponse(compare_result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/knowledge/ask")
def knowledge_ask(req: KnowledgeAskRequest):
    """
    对已入库的公司知识库提问（非流式，适合 API 调用）。

    参数：
        req: 包含股票代码、问题、报告类型

    返回：
        JSON，包含回答内容和来源文档片段
    """
    try:
        qa_result = ask(
            stock_code=req.stock_code,
            question=req.question,
            report_type=req.report_type,
        )
        return JSONResponse(qa_result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ──────────────────────────────────────────────────────────────────────────────
# 股票综合概览
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/stock/overview/{stock_code}")
def stock_overview(stock_code: str, current_user: dict | None = Depends(get_optional_user)):
    """
    股票综合概览：行情 + PE/PB + 52 周高低 + 财务指标 + 估值区间 + 自选状态。

    三类数据（基础行情、历史估值、财务指标）并行拉取以缩短响应时间。
    估值计算复用已拉取的 basic/history 数据，不额外发起请求。
    响应返回后在后台异步同步公告，不阻塞接口响应。

    参数：
        stock_code:   股票代码
        current_user: 当前登录用户（可选），用于判断是否已加入自选

    返回：
        JSON，包含行情、估值、财务指标、PB 历史分位、自选状态等完整信息
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from app.data.stock_data import _get_pro
    import pandas as pd
    from collections import defaultdict

    try:
        # 三类数据并行拉取，减少总等待时间
        with ThreadPoolExecutor(max_workers=3) as executor:
            future_basic   = executor.submit(get_stock_basic, stock_code)
            future_history = executor.submit(get_stock_valuation_history, stock_code)
            future_metrics = executor.submit(lambda: get_metrics(stock_code, years=3))
            basic_info       = future_basic.result()
            valuation_history = future_history.result()
            raw_metrics      = future_metrics.result()

        pb_stats = get_pb_percentile(valuation_history)

        # 将平铺的指标列表按年份聚合
        metrics_by_year: dict = defaultdict(dict)
        for metric_item in raw_metrics:
            year = metric_item["ann_date"][:4]
            metrics_by_year[year][metric_item["metric_name"]] = metric_item["value"]
        metrics_by_year = dict(sorted(metrics_by_year.items(), reverse=True))

        # 估值计算复用已有数据，避免重复请求 Tushare
        valuation_data = None
        try:
            valuation_result = run_valuation(stock_code, basic=basic_info, history=valuation_history)
            valuation_data = {
                "model": valuation_result.model_name,
                "fair_value_low": valuation_result.fair_value_low,
                "fair_value_high": valuation_result.fair_value_high,
                "buy_price": valuation_result.buy_price,
                "sell_price": valuation_result.sell_price,
                "current_status": valuation_result.current_status,
                "reasoning": valuation_result.reasoning,
            }
        except Exception:
            # 估值计算失败不影响概览其他数据的返回
            pass

        return JSONResponse({
            "stock_code": basic_info.code,
            "name": basic_info.name,
            "current_price": basic_info.current_price,
            "market_cap": basic_info.market_cap,
            "circ_mv": basic_info.circ_mv,
            "pe": basic_info.pe,
            "pe_ttm": basic_info.pe_ttm,
            "pb": basic_info.pb,
            "dv_ratio": basic_info.dv_ratio,
            "total_share": basic_info.total_share,
            "float_share": basic_info.float_share,
            "eps": basic_info.eps,
            "bps": basic_info.bps,
            "week52_high": basic_info.week52_high,
            "week52_low": basic_info.week52_low,
            "pb_stats": pb_stats,
            "metrics_by_year": metrics_by_year,
            "valuation": valuation_data,
            # 未登录用户默认为未自选
            "watched": is_in_watchlist((current_user or {}).get("id", ""), stock_code),
        })
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        # 后台异步同步公告，不阻塞响应（即使出错也不影响主流程）
        try:
            sync_in_background(stock_code)
        except Exception:
            pass


# ──────────────────────────────────────────────────────────────────────────────
# 券商研报
# ──────────────────────────────────────────────────────────────────────────────

@router.post("/knowledge/broker/upload")
async def broker_upload(file: UploadFile = File(...)):
    """
    上传券商研报 PDF，自动提取结构化信息并入向量知识库。

    参数：
        file: 仅支持 .pdf 格式

    返回：
        JSON，包含入库状态和提取的结构化信息
    """
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="仅支持 PDF 文件")

    save_dir = os.path.join(settings.upload_dir, "broker")
    os.makedirs(save_dir, exist_ok=True)
    file_path = os.path.join(save_dir, file.filename)
    with open(file_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    try:
        index_result = index_broker_report(file_path)
        return JSONResponse({"status": "success", **index_result})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/knowledge/broker/list")
def broker_list(stock_code: str | None = None):
    """
    查询已入库的券商研报列表，可按股票代码过滤。

    参数：
        stock_code: 股票代码（可选），为空则返回所有研报

    返回：
        JSON，包含研报数量和列表
    """
    broker_reports = get_broker_reports(stock_code)
    return JSONResponse({"count": len(broker_reports), "reports": broker_reports})


# ──────────────────────────────────────────────────────────────────────────────
# 反馈
# ──────────────────────────────────────────────────────────────────────────────

@router.post("/knowledge/feedback")
def knowledge_feedback(req: FeedbackRequest):
    """
    记录用户对问答结果的反馈（好评/差评），用于后续质量改进。

    参数：
        req: 包含问答内容、评分和备注

    返回：
        JSON，包含反馈记录 ID
    """
    try:
        feedback_id = save_feedback(
            stock_code=req.stock_code,
            question=req.question,
            answer=req.answer,
            sources=req.sources,
            rating=req.rating,
            comment=req.comment,
        )
        return JSONResponse({"status": "success", "id": feedback_id})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/knowledge/feedback/stats")
def feedback_stats(stock_code: str | None = None):
    """
    查看反馈统计数据，差评问题列表可用于定向改进知识库。

    参数：
        stock_code: 股票代码（可选），为空则返回全局统计

    返回：
        JSON，包含好评/差评数量及差评问题列表
    """
    return JSONResponse(get_feedback_stats(stock_code))


# ──────────────────────────────────────────────────────────────────────────────
# 统一对话（非流式 + 流式）
# ──────────────────────────────────────────────────────────────────────────────

class FMChatRequest(BaseModel):
    """基金经理专属对话请求体。"""
    session_id: str | None = None   # 为空则自动新建会话
    manager_id: str = "value"
    message: str


@router.post("/chat")
def unified_chat(req: UnifiedChatRequest):
    """
    统一对话入口（非流式），自动路由到三条处理路径之一：
      - knowledge_qa:  知识库问答（问题包含可识别的股票代码）
      - fund_manager:  基金经理 Agent（需要深度分析或多轮对话）
      - general:       通用 LLM 问答（兜底）

    参数：
        req: 统一对话请求体

    返回：
        JSON，包含路由结果、会话 ID 和消息列表
    """
    routing_result = route_question(req.message, req.current_stock_code)
    route_name = routing_result["route"]
    stock_code = routing_result["stock_code"]

    # ── 路径1：知识库问答 ──────────────────────────────────────────────
    if route_name == "knowledge_qa" and stock_code:
        try:
            qa_result = ask(stock_code=stock_code, question=req.message)
            return JSONResponse({
                "route": "knowledge_qa",
                "stock_code": stock_code,
                "session_id": req.session_id,
                "messages": [{
                    "role": "assistant",
                    "content": qa_result.get("answer", ""),
                    "sources": qa_result.get("sources", []),
                }],
            })
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    # ── 路径2：基金经理 Agent ─────────────────────────────────────────
    if route_name == "fund_manager":
        session_id = req.session_id
        # session_id 不存在或已过期时，创建新会话
        if not session_id or session_id not in _fm_sessions:
            session_id = str(uuid.uuid4())
            _fm_sessions[session_id] = AgentSession(manager_id=req.manager_id)
        agent_session = _fm_sessions[session_id]
        try:
            new_messages = fm_chat(agent_session, req.message)
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
        return JSONResponse({
            "route": "fund_manager",
            "stock_code": stock_code,
            "session_id": session_id,
            "messages": [
                {
                    "role": msg.role,
                    "content": msg.content,
                    "thinking": msg.thinking,
                    "tool_name": msg.tool_name,
                    "tool_calls": [
                        {"name": tc.function.name, "arguments": tc.function.arguments}
                        for tc in (msg.tool_calls or [])
                    ],
                }
                for msg in new_messages
            ],
        })

    # ── 路径3：通用问题（兜底） ───────────────────────────────────────
    try:
        answer_text = llm_chat(
            system="你是一位专业的A股投资研究助手，回答简洁专业。",
            user=req.message,
        )
        return JSONResponse({
            "route": "general",
            "stock_code": None,
            "session_id": req.session_id,
            "messages": [{"role": "assistant", "content": answer_text, "sources": []}],
        })
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/chat/stream")
def unified_chat_stream(req: UnifiedChatRequest, current_user: dict | None = Depends(get_optional_user)):
    """
    统一流式对话接口，以 SSE（Server-Sent Events）格式逐 token 输出。

    路由策略（上下文感知）：
      1. 若上一轮是 fund_manager 且 session_id 仍有效，继续走基金经理（保持多轮对话连贯性）
      2. 否则重新路由，避免用户切换话题时被锁定在错误路径

    参数：
        req:          统一对话请求体
        current_user: 当前登录用户（可选），用于保存对话历史和学习偏好

    返回：
        StreamingResponse，media_type 为 text/event-stream
    """
    # 判断是否应继续已有的基金经理会话（多轮对话保持）
    has_active_fm_session = (
        req.last_route == "fund_manager"
        and req.session_id
        and req.session_id in _fm_sessions
    )

    if has_active_fm_session:
        route_name = "fund_manager"
        stock_code = req.current_stock_code
    else:
        routing_result = route_question(req.message, req.current_stock_code)
        route_name = routing_result["route"]
        stock_code = routing_result["stock_code"]

    # 构造股票上下文前缀，注入到 LLM system prompt，提升回答相关性
    stock_context = ""
    if req.current_stock_code and req.current_stock_name:
        stock_context = f"当前用户正在研究的公司：{req.current_stock_name}（{req.current_stock_code}）。"
    elif req.current_stock_code:
        stock_context = f"当前用户正在研究的股票代码：{req.current_stock_code}。"

    user_id = (current_user or {}).get("id", "")
    # 保存用户消息到对话历史
    save_message(role="user", content=req.message,
                 stock_code=stock_code or req.current_stock_code or "",
                 route=route_name, user_id=user_id)
    # 后台异步学习用户偏好，不阻塞流式响应
    if user_id:
        learn_async(user_id, req.message)

    def generate():
        """SSE 生成器：按路由分发，收集完整回答后保存到历史。"""
        yield f"data: {json.dumps({'type':'route','route':route_name,'stock_code':stock_code}, ensure_ascii=False)}\n\n"

        accumulated_tokens = []   # 收集所有 token 用于保存完整回答
        answer_sources = []       # 收集知识库来源文档

        if route_name == "knowledge_qa" and stock_code:
            for raw_event in ask_stream(stock_code=stock_code, question=req.message,
                                        stock_context=stock_context):
                yield f"data: {raw_event}\n\n"
                # 同时解析事件，收集 token 和 sources 用于后续保存
                try:
                    event_data = json.loads(raw_event)
                    if event_data.get("type") == "token":
                        accumulated_tokens.append(event_data["content"])
                    elif event_data.get("type") == "sources":
                        answer_sources = event_data.get("sources", [])
                except Exception:
                    pass

        elif route_name == "fund_manager":
            session_id = req.session_id
            # session_id 不存在或已过期时，创建新会话
            if not session_id or session_id not in _fm_sessions:
                import uuid as _uuid
                session_id = str(_uuid.uuid4())
                _fm_sessions[session_id] = AgentSession(
                    manager_id=req.manager_id,
                    stock_context=stock_context,
                )
            # 先推送 session_id，让前端绑定后续请求
            yield f"data: {json.dumps({'type':'session_id','session_id':session_id})}\n\n"
            agent_session = _fm_sessions[session_id]
            for event in fm_chat_stream(agent_session, req.message):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                if event.get("type") == "token":
                    accumulated_tokens.append(event["content"])

        else:
            # 通用问题：直接调用 LLM 流式接口
            system_prompt = f"你是一位专业的A股投资研究助手，回答简洁专业。{stock_context}"
            for token in chat_stream_fn(system=system_prompt, user=req.message):
                accumulated_tokens.append(token)
                yield f"data: {json.dumps({'type':'token','content':token}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type':'done'})}\n\n"

        # 流式输出完成后，将完整回答保存到对话历史
        if accumulated_tokens:
            save_message(
                role="assistant",
                content="".join(accumulated_tokens),
                stock_code=stock_code or req.current_stock_code or "",
                sources=answer_sources,
                route=route_name,
                user_id=user_id,
            )

    return StreamingResponse(generate(), media_type="text/event-stream",
                              headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ──────────────────────────────────────────────────────────────────────────────
# 基金经理 Agent
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/fm/managers")
def fm_list_managers():
    """
    列出所有可用的基金经理角色及其风格描述。

    返回：
        JSON 数组，每项包含 id / name / style / description / analysis_priorities
    """
    return JSONResponse([
        {
            "id": manager.id,
            "name": manager.name,
            "style": manager.style,
            "description": manager.description,
            "analysis_priorities": manager.analysis_priorities,
        }
        for manager in FUND_MANAGERS.values()
    ])


@router.post("/fm/chat")
def fm_chat_endpoint(req: FMChatRequest):
    """
    与指定基金经理进行对话（非流式），返回本轮所有新消息。

    session_id 为空时自动创建新会话；已有 session_id 则继续上下文。

    参数：
        req: 包含 session_id、manager_id 和用户消息

    返回：
        JSON，包含 session_id 和本轮新增的消息列表（含 tool_calls）
    """
    session_id = req.session_id
    # session_id 不存在或已过期时，创建新会话
    if not session_id or session_id not in _fm_sessions:
        session_id = str(uuid.uuid4())
        _fm_sessions[session_id] = AgentSession(manager_id=req.manager_id)

    agent_session = _fm_sessions[session_id]

    try:
        new_messages = fm_chat(agent_session, req.message)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return JSONResponse({
        "session_id": session_id,
        "manager_id": req.manager_id,
        "messages": [
            {
                "role": msg.role,
                "content": msg.content,
                "thinking": msg.thinking,
                "tool_name": msg.tool_name,
                "tool_calls": [
                    {"name": tc.function.name, "arguments": tc.function.arguments}
                    for tc in (msg.tool_calls or [])
                ],
            }
            for msg in new_messages
        ],
    })


@router.delete("/fm/session/{session_id}")
def fm_clear_session(session_id: str):
    """
    清除指定会话的上下文历史，下次对话将从头开始。

    参数：
        session_id: 要清除的会话 ID

    返回：
        JSON，status="cleared"
    """
    _fm_sessions.pop(session_id, None)
    return JSONResponse({"status": "cleared"})


# ──────────────────────────────────────────────────────────────────────────────
# 股票搜索
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/stock/search")
def stock_search(q: str = "", limit: int = 10):
    """
    模糊搜索股票，支持代码前缀和名称包含匹配。

    参数：
        q:     搜索关键词（空则返回空列表）
        limit: 最多返回条数，默认 10

    返回：
        JSON 数组，每项包含 code / ts_code / name / industry
    """
    if not q.strip():
        return JSONResponse([])
    try:
        search_results = search_stocks(q.strip(), limit=limit)
        return JSONResponse(search_results)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ──────────────────────────────────────────────────────────────────────────────
# 研究笔记
# ──────────────────────────────────────────────────────────────────────────────

class NoteRequest(BaseModel):
    """研究笔记创建/更新请求体。"""
    content: str
    stock_code: str = ""
    title: str = ""


@router.post("/notes")
def note_create(req: NoteRequest, current_user: dict | None = Depends(get_optional_user)):
    """
    创建研究笔记，可关联特定股票。

    参数：
        req:          笔记内容、关联股票代码（可选）、标题（可选）
        current_user: 当前登录用户，未登录则笔记归属匿名

    返回：
        JSON，包含新创建的笔记完整信息
    """
    user_id = (current_user or {}).get("id", "")
    note = save_note(req.content, req.stock_code, req.title, user_id=user_id)
    return JSONResponse(note)


@router.get("/notes")
def note_list(stock_code: str = "", current_user: dict | None = Depends(get_optional_user)):
    """
    获取研究笔记列表，可按股票代码过滤。

    参数：
        stock_code:   股票代码（可选），为空则返回所有笔记
        current_user: 当前登录用户，只返回该用户的笔记

    返回：
        JSON 数组，包含笔记列表
    """
    user_id = (current_user or {}).get("id", "")
    notes = list_notes(stock_code=stock_code or None, user_id=user_id)
    return JSONResponse(notes)


@router.put("/notes/{note_id}")
def note_update(note_id: str, req: NoteRequest):
    """
    更新指定笔记的内容和标题。

    参数：
        note_id: 笔记 ID
        req:     新的内容和标题

    返回：
        JSON，包含更新后的笔记完整信息
    """
    updated_note = update_note(note_id, req.content, req.title)
    return JSONResponse(updated_note)


@router.delete("/notes/{note_id}")
def note_delete(note_id: str):
    """
    删除指定笔记。

    参数：
        note_id: 笔记 ID

    返回：
        JSON，status="deleted"
    """
    delete_note(note_id)
    return JSONResponse({"status": "deleted"})


# ──────────────────────────────────────────────────────────────────────────────
# 对话历史
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/chat/history")
def chat_history_get(stock_code: str = "", limit: int = 50,
                     current_user: dict | None = Depends(get_optional_user)):
    """
    获取对话历史记录，可按股票代码过滤。

    参数：
        stock_code:   股票代码（可选）
        limit:        最多返回条数，默认 50
        current_user: 当前登录用户

    返回：
        JSON 数组，包含历史消息列表（含角色、内容、时间戳等）
    """
    user_id = (current_user or {}).get("id", "")
    history_records = get_history(stock_code=stock_code or None, limit=limit, user_id=user_id)
    return JSONResponse(history_records)


@router.delete("/chat/history")
def chat_history_clear(stock_code: str = "", current_user: dict | None = Depends(get_optional_user)):
    """
    清空对话历史，可按股票代码范围清除。

    参数：
        stock_code:   股票代码（可选），为空则清空所有历史
        current_user: 当前登录用户

    返回：
        JSON，status="cleared"
    """
    user_id = (current_user or {}).get("id", "")
    clear_history(stock_code=stock_code or None, user_id=user_id)
    return JSONResponse({"status": "cleared"})


# ──────────────────────────────────────────────────────────────────────────────
# 自选股
# ──────────────────────────────────────────────────────────────────────────────

class WatchlistRequest(BaseModel):
    """自选股添加请求体。"""
    stock_code: str
    stock_name: str = ""


@router.get("/watchlist")
def watchlist_get(current_user: dict | None = Depends(get_optional_user)):
    """
    获取当前用户的自选股列表。未登录返回空列表。

    返回：
        JSON 数组，包含自选股列表
    """
    user_id = (current_user or {}).get("id", "")
    if not user_id:
        return JSONResponse([])
    watchlist_items = get_watchlist(user_id)
    return JSONResponse(watchlist_items)


@router.post("/watchlist")
def watchlist_add(req: WatchlistRequest, current_user: dict | None = Depends(get_optional_user)):
    """
    将股票加入自选列表。需要登录。

    参数：
        req:          包含股票代码和名称
        current_user: 当前登录用户

    返回：
        JSON，包含新增的自选股信息
    """
    user_id = (current_user or {}).get("id", "")
    if not user_id:
        raise HTTPException(status_code=401, detail="请先登录")
    watchlist_item = add_to_watchlist(user_id, req.stock_code, req.stock_name)
    return JSONResponse(watchlist_item)


@router.delete("/watchlist/{stock_code}")
def watchlist_remove(stock_code: str, current_user: dict | None = Depends(get_optional_user)):
    """
    从自选列表移除指定股票。需要登录。

    参数：
        stock_code:   要移除的股票代码
        current_user: 当前登录用户

    返回：
        JSON，status="removed"
    """
    user_id = (current_user or {}).get("id", "")
    if not user_id:
        raise HTTPException(status_code=401, detail="请先登录")
    remove_from_watchlist(user_id, stock_code)
    return JSONResponse({"status": "removed"})


@router.get("/watchlist/check/{stock_code}")
def watchlist_check(stock_code: str, current_user: dict | None = Depends(get_optional_user)):
    """
    检查指定股票是否已在当前用户的自选列表中。

    参数：
        stock_code:   股票代码
        current_user: 当前登录用户（未登录则返回 watched=false）

    返回：
        JSON，包含 watched 布尔值
    """
    user_id = (current_user or {}).get("id", "")
    is_watched = is_in_watchlist(user_id, stock_code) if user_id else False
    return JSONResponse({"watched": is_watched})


# ──────────────────────────────────────────────────────────────────────────────
# 健康检查
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/health")
def health():
    """服务健康检查，供负载均衡器或监控系统探活使用。"""
    return {"status": "ok"}
