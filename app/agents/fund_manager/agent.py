"""
基金经理 Agent 核心逻辑。

采用 ReAct 模式（Reasoning + Acting）：
  思考 → 调用工具 → 观察结果 → 继续思考或汇报用户

使用 DeepSeek/Claude 原生 Tool Use 能力，工具定义见 tools.py。
"""
import json
from dataclasses import dataclass, field
from openai import OpenAI
from app.core.config import settings
from app.agents.fund_manager.profiles import FundManagerProfile, get_manager
from app.agents.fund_manager.tools import TOOL_DEFINITIONS, execute_tool

# 单次对话最多允许 10 轮工具调用，防止 Agent 陷入无限循环
MAX_TURNS = 10


@dataclass
class Message:
    """
    对话历史中的单条消息，统一表示 user/assistant/tool 三种角色。

    字段说明：
        role:        消息角色，"user" / "assistant" / "tool"
        content:     消息正文（assistant 调用工具时可能为空或思考过程）
        tool_calls:  assistant 发起的工具调用列表（仅 role="assistant" 时有值）
        tool_name:   工具结果对应的工具名（仅 role="tool" 时有值）
        tool_call_id: 工具结果对应的调用 ID（仅 role="tool" 时有值）
        thinking:    agent 的思考过程文本（用于前端展示推理链）
    """
    role: str
    content: str
    tool_calls: list = field(default_factory=list)
    tool_name: str = ""
    tool_call_id: str = ""
    thinking: str = ""


@dataclass
class AgentSession:
    """
    单个用户与 Agent 的会话状态，跨轮次保持对话历史。

    字段说明：
        manager_id:    基金经理角色 ID，决定 Agent 的投资风格和系统提示
        history:       完整对话历史（含工具调用记录）
        stock_context: 当前研究公司的背景摘要（追加到系统提示，提升回答相关性）
    """
    manager_id: str
    history: list[Message] = field(default_factory=list)
    stock_context: str = ""  # 当前研究公司的上下文信息（如公司简介、所属行业等）


def chat(
    session: AgentSession,
    user_message: str,
) -> list[Message]:
    """
    处理一轮用户消息（同步版本），返回本轮产生的所有新消息。

    包含完整的 ReAct 循环：LLM 决策 → 工具调用 → 观察结果 → 继续或结束。
    新消息列表包括 assistant 思考、工具调用、工具结果、最终回复。

    参数：
        session:      当前会话状态（含历史和角色配置）
        user_message: 用户本轮输入

    返回：本轮新产生的 Message 列表（按时序排列）。
    """
    manager_profile = get_manager(session.manager_id)
    llm_client = OpenAI(api_key=settings.llm_api_key, base_url=settings.llm_base_url)

    # 将用户消息写入会话历史
    user_msg = Message(role="user", content=user_message)
    session.history.append(user_msg)
    new_messages_this_turn = []

    # 将会话历史转为 OpenAI 消息格式
    llm_messages = _build_llm_messages(manager_profile, session.history, session.stock_context)

    # ReAct 主循环：最多 MAX_TURNS 轮，防止无限递归
    for _ in range(MAX_TURNS):
        api_response = llm_client.chat.completions.create(
            model=settings.llm_model,
            messages=llm_messages,
            tools=[{
                "type": "function",
                "function": {
                    "name": tool_def["name"],
                    "description": tool_def["description"],
                    "parameters": tool_def["input_schema"],
                },
            } for tool_def in TOOL_DEFINITIONS],
            tool_choice="auto",
            temperature=0.3,
        )

        response_message = api_response.choices[0].message
        finish_reason = api_response.choices[0].finish_reason

        if finish_reason == "tool_calls" and response_message.tool_calls:
            # ── Agent 决定调用工具：记录思考过程，执行工具 ──────────────
            thinking_text = response_message.content or ""
            assistant_msg = Message(
                role="assistant",
                content=thinking_text,
                tool_calls=response_message.tool_calls,
                thinking=thinking_text,
            )
            session.history.append(assistant_msg)
            new_messages_this_turn.append(assistant_msg)

            # 将 assistant 的工具调用请求追加到 LLM 消息链
            llm_messages.append({
                "role": "assistant",
                "content": thinking_text,
                "tool_calls": [
                    {
                        "id": tool_call.id,
                        "type": "function",
                        "function": {
                            "name": tool_call.function.name,
                            "arguments": tool_call.function.arguments,
                        },
                    }
                    for tool_call in response_message.tool_calls
                ],
            })

            # 逐个执行工具调用，将结果追加到消息链
            for tool_call in response_message.tool_calls:
                called_tool_name = tool_call.function.name
                try:
                    parsed_tool_inputs = json.loads(tool_call.function.arguments)
                except json.JSONDecodeError:
                    parsed_tool_inputs = {}

                tool_execution_result = execute_tool(called_tool_name, parsed_tool_inputs)

                # 工具结果消息：与对应的 tool_call_id 绑定，LLM 通过 ID 对应
                tool_result_msg = Message(
                    role="tool",
                    content=tool_execution_result,
                    tool_name=called_tool_name,
                    tool_call_id=tool_call.id,
                )
                session.history.append(tool_result_msg)
                new_messages_this_turn.append(tool_result_msg)

                llm_messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": tool_execution_result,
                })

        else:
            # ── Agent 给出最终回复，结束本轮 ReAct 循环 ─────────────────
            final_answer = response_message.content or ""
            final_msg = Message(role="assistant", content=final_answer)
            session.history.append(final_msg)
            new_messages_this_turn.append(final_msg)
            break

    return new_messages_this_turn


def chat_stream(
    session: AgentSession,
    user_message: str,
):
    """
    处理一轮用户消息（流式版本），逐步 yield 事件字典，适合前端实时展示。

    工具调用阶段用非流式接口（需要完整响应才能解析工具参数），
    最终回答阶段切换为流式接口（逐 token 推送，提升响应体验）。

    yield 事件格式：
      {"type": "thinking", "calls": [{"name": "...", "arguments": "..."}]}
          — Agent 决定调用哪些工具（含工具名和参数）
      {"type": "tool", "tool_name": "...", "content": "..."}
          — 工具执行结果
      {"type": "token", "content": "..."}
          — 最终回答的流式 token
      {"type": "done"}
          — 本轮对话结束

    参数：
        session:      当前会话状态
        user_message: 用户本轮输入
    """
    manager_profile = get_manager(session.manager_id)
    llm_client = OpenAI(api_key=settings.llm_api_key, base_url=settings.llm_base_url)

    # 将用户消息写入历史
    user_msg = Message(role="user", content=user_message)
    session.history.append(user_msg)

    llm_messages = _build_llm_messages(manager_profile, session.history, session.stock_context)
    # 将工具定义转为 OpenAI function calling 格式
    tools_for_api = [{
        "type": "function",
        "function": {
            "name": tool_def["name"],
            "description": tool_def["description"],
            "parameters": tool_def["input_schema"],
        },
    } for tool_def in TOOL_DEFINITIONS]

    for turn_index in range(MAX_TURNS):
        is_last_allowed_turn = (turn_index == MAX_TURNS - 1)

        # 工具调用阶段用非流式，确保能完整解析工具参数
        api_response = llm_client.chat.completions.create(
            model=settings.llm_model,
            messages=llm_messages,
            tools=tools_for_api,
            tool_choice="auto",
            temperature=0.3,
            stream=False,  # 非流式：需要完整响应才能判断是否调用工具
        )

        response_message = api_response.choices[0].message
        finish_reason = api_response.choices[0].finish_reason

        # 未到最后一轮且 LLM 决定调用工具
        if finish_reason == "tool_calls" and response_message.tool_calls and not is_last_allowed_turn:
            thinking_text = response_message.content or ""
            assistant_msg = Message(
                role="assistant",
                content=thinking_text,
                tool_calls=response_message.tool_calls,
                thinking=thinking_text,
            )
            session.history.append(assistant_msg)

            # 推送"正在思考并调用工具"事件，前端可展示工具调用过程
            yield {
                "type": "thinking",
                "calls": [
                    {
                        "name": tool_call.function.name,
                        "arguments": tool_call.function.arguments,
                    }
                    for tool_call in response_message.tool_calls
                ],
            }

            # 将 assistant 工具调用追加到 LLM 消息链
            llm_messages.append({
                "role": "assistant",
                "content": thinking_text,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                    }
                    for tc in response_message.tool_calls
                ],
            })

            # 逐个执行工具，推送结果事件
            for tool_call in response_message.tool_calls:
                called_tool_name = tool_call.function.name
                try:
                    parsed_tool_inputs = json.loads(tool_call.function.arguments)
                except json.JSONDecodeError:
                    parsed_tool_inputs = {}

                tool_execution_result = execute_tool(called_tool_name, parsed_tool_inputs)

                tool_result_msg = Message(
                    role="tool",
                    content=tool_execution_result,
                    tool_name=called_tool_name,
                    tool_call_id=tool_call.id,
                )
                session.history.append(tool_result_msg)
                yield {"type": "tool", "tool_name": called_tool_name, "content": tool_execution_result}

                llm_messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": tool_execution_result,
                })

        else:
            # ── 最终回答阶段：切换为流式接口，逐 token 推送 ──────────────
            # 不传 tools，避免 LLM 在最终回答时再次触发工具调用
            stream_response = llm_client.chat.completions.create(
                model=settings.llm_model,
                messages=llm_messages,
                temperature=0.3,
                stream=True,  # 流式输出，提升前端响应体验
            )
            full_answer_content = ""
            for stream_chunk in stream_response:
                token_delta = stream_chunk.choices[0].delta.content
                if token_delta:
                    full_answer_content += token_delta
                    yield {"type": "token", "content": token_delta}

            # 将完整回答写入历史，供后续轮次参考
            final_msg = Message(role="assistant", content=full_answer_content)
            session.history.append(final_msg)
            yield {"type": "done"}
            break


def _build_llm_messages(
    manager_profile: FundManagerProfile,
    history: list[Message],
    stock_context: str = "",
) -> list[dict]:
    """
    将 AgentSession 的 Message 历史转换为 OpenAI API 所需的消息列表格式。

    处理逻辑：
    - system 消息由 manager_profile 提供，附加公司背景信息
    - assistant 消息若含工具调用，需将紧随其后的 tool 消息一并打包
    - 跳过无法识别的消息角色（容错处理）

    参数：
        manager_profile: 基金经理角色配置（含系统提示）
        history:         完整的 Message 列表
        stock_context:   公司背景信息，追加到系统提示末尾

    返回：OpenAI API 格式的消息列表。
    """
    # 系统提示 = 基金经理角色提示 + 当前研究公司背景（如有）
    system_prompt_text = manager_profile.system_prompt
    if stock_context:
        system_prompt_text += f"\n\n{stock_context}"
    formatted_messages = [{"role": "system", "content": system_prompt_text}]

    msg_index = 0
    while msg_index < len(history):
        current_msg = history[msg_index]

        if current_msg.role == "user":
            formatted_messages.append({"role": "user", "content": current_msg.content})
            msg_index += 1

        elif current_msg.role == "assistant" and current_msg.tool_calls:
            # assistant 调用工具时，需同时附带 tool_calls 字段
            formatted_messages.append({
                "role": "assistant",
                "content": current_msg.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in current_msg.tool_calls
                ],
            })
            msg_index += 1
            # 紧接在 assistant tool_calls 之后的 tool 消息必须一起发送，否则 API 报错
            while msg_index < len(history) and history[msg_index].role == "tool":
                tool_result_msg = history[msg_index]
                formatted_messages.append({
                    "role": "tool",
                    "tool_call_id": tool_result_msg.tool_call_id,
                    "content": tool_result_msg.content,
                })
                msg_index += 1

        elif current_msg.role == "assistant":
            # 普通 assistant 回复（无工具调用）
            formatted_messages.append({"role": "assistant", "content": current_msg.content})
            msg_index += 1

        else:
            # 跳过无法识别的角色（如孤立的 tool 消息，防御性处理）
            msg_index += 1

    return formatted_messages
