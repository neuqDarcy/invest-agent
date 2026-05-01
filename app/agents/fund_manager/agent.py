"""
基金经理 Agent 核心逻辑。
ReAct 模式：思考 → 调用工具 → 观察结果 → 汇报用户 → 继续。
使用 DeepSeek/Claude 的原生 Tool Use 能力。
"""
import json
from dataclasses import dataclass, field
from openai import OpenAI
from app.core.config import settings
from app.agents.fund_manager.profiles import FundManagerProfile, get_manager
from app.agents.fund_manager.tools import TOOL_DEFINITIONS, execute_tool

MAX_TURNS = 10   # 最多 10 轮工具调用，防止无限循环


@dataclass
class Message:
    role: str       # user / assistant / tool
    content: str
    tool_calls: list = field(default_factory=list)   # assistant 发起的工具调用
    tool_name: str = ""    # tool 结果对应的工具名
    tool_call_id: str = "" # tool 结果对应的 call_id
    thinking: str = ""     # agent 的思考过程（展示给用户）


@dataclass
class AgentSession:
    manager_id: str
    history: list[Message] = field(default_factory=list)
    stock_context: str = ""  # 当前研究的公司上下文


def chat(
    session: AgentSession,
    user_message: str,
) -> list[Message]:
    """
    处理一轮用户消息，返回本轮产生的所有新消息（包括思考、工具调用、最终回复）。
    """
    manager = get_manager(session.manager_id)
    client = OpenAI(api_key=settings.llm_api_key, base_url=settings.llm_base_url)

    # 把用户消息加入历史
    user_msg = Message(role="user", content=user_message)
    session.history.append(user_msg)
    new_messages = []

    # 构建发给 LLM 的消息列表
    llm_messages = _build_llm_messages(manager, session.history, session.stock_context)

    for _ in range(MAX_TURNS):
        response = client.chat.completions.create(
            model=settings.llm_model,
            messages=llm_messages,
            tools=[{"type": "function", "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["input_schema"],
            }} for t in TOOL_DEFINITIONS],
            tool_choice="auto",
            temperature=0.3,
        )

        msg = response.choices[0].message
        finish_reason = response.choices[0].finish_reason

        if finish_reason == "tool_calls" and msg.tool_calls:
            # ── Agent 决定调用工具 ──────────────────────────────────────
            thinking = msg.content or ""
            assistant_msg = Message(
                role="assistant",
                content=thinking,
                tool_calls=msg.tool_calls,
                thinking=thinking,
            )
            session.history.append(assistant_msg)
            new_messages.append(assistant_msg)

            # 执行所有工具调用
            llm_messages.append({
                "role": "assistant",
                "content": thinking,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                    }
                    for tc in msg.tool_calls
                ],
            })

            for tc in msg.tool_calls:
                tool_name = tc.function.name
                try:
                    tool_inputs = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    tool_inputs = {}

                tool_result = execute_tool(tool_name, tool_inputs)

                tool_msg = Message(
                    role="tool",
                    content=tool_result,
                    tool_name=tool_name,
                    tool_call_id=tc.id,
                )
                session.history.append(tool_msg)
                new_messages.append(tool_msg)

                llm_messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": tool_result,
                })

        else:
            # ── Agent 给出最终回复 ─────────────────────────────────────
            final_content = msg.content or ""
            final_msg = Message(role="assistant", content=final_content)
            session.history.append(final_msg)
            new_messages.append(final_msg)
            break

    return new_messages


def chat_stream(
    session: AgentSession,
    user_message: str,
):
    """
    流式版本：工具调用过程 yield 事件，最终回答流式输出。
    yield dict：
      {"type":"thinking","calls":[...]}
      {"type":"tool","tool_name":"...","content":"..."}
      {"type":"token","content":"..."}
      {"type":"done"}
    """
    manager = get_manager(session.manager_id)
    client = OpenAI(api_key=settings.llm_api_key, base_url=settings.llm_base_url)

    user_msg = Message(role="user", content=user_message)
    session.history.append(user_msg)

    llm_messages = _build_llm_messages(manager, session.history, session.stock_context)
    tools_def = [{"type": "function", "function": {
        "name": t["name"],
        "description": t["description"],
        "parameters": t["input_schema"],
    }} for t in TOOL_DEFINITIONS]

    for turn in range(MAX_TURNS):
        is_last_turn = (turn == MAX_TURNS - 1)

        # 最后一轮或者已经完成工具调用，用流式输出最终回答
        response = client.chat.completions.create(
            model=settings.llm_model,
            messages=llm_messages,
            tools=tools_def,
            tool_choice="auto",
            temperature=0.3,
            stream=False,   # 先非流式判断是否需要工具调用
        )

        msg = response.choices[0].message
        finish_reason = response.choices[0].finish_reason

        if finish_reason == "tool_calls" and msg.tool_calls and not is_last_turn:
            thinking = msg.content or ""
            assistant_msg = Message(role="assistant", content=thinking, tool_calls=msg.tool_calls, thinking=thinking)
            session.history.append(assistant_msg)

            yield {"type": "thinking", "calls": [
                {"name": tc.function.name, "arguments": tc.function.arguments}
                for tc in msg.tool_calls
            ]}

            llm_messages.append({
                "role": "assistant", "content": thinking,
                "tool_calls": [{"id": tc.id, "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                    for tc in msg.tool_calls],
            })

            for tc in msg.tool_calls:
                tool_name = tc.function.name
                try:
                    tool_inputs = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    tool_inputs = {}
                tool_result = execute_tool(tool_name, tool_inputs)

                tool_msg = Message(role="tool", content=tool_result, tool_name=tool_name, tool_call_id=tc.id)
                session.history.append(tool_msg)
                yield {"type": "tool", "tool_name": tool_name, "content": tool_result}

                llm_messages.append({"role": "tool", "tool_call_id": tc.id, "content": tool_result})

        else:
            # 最终回答：用流式输出
            stream_resp = client.chat.completions.create(
                model=settings.llm_model,
                messages=llm_messages,
                temperature=0.3,
                stream=True,
            )
            full_content = ""
            for chunk in stream_resp:
                delta = chunk.choices[0].delta.content
                if delta:
                    full_content += delta
                    yield {"type": "token", "content": delta}

            final_msg = Message(role="assistant", content=full_content)
            session.history.append(final_msg)
            yield {"type": "done"}
            break


def _build_llm_messages(manager: FundManagerProfile, history: list[Message],
                        stock_context: str = "") -> list[dict]:
    """把 session history 转为 OpenAI 格式的 messages 列表"""
    system = manager.system_prompt
    if stock_context:
        system += f"\n\n{stock_context}"
    messages = [{"role": "system", "content": system}]

    i = 0
    while i < len(history):
        msg = history[i]

        if msg.role == "user":
            messages.append({"role": "user", "content": msg.content})
            i += 1

        elif msg.role == "assistant" and msg.tool_calls:
            messages.append({
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                    }
                    for tc in msg.tool_calls
                ],
            })
            i += 1
            # 紧接着的 tool 消息
            while i < len(history) and history[i].role == "tool":
                tm = history[i]
                messages.append({
                    "role": "tool",
                    "tool_call_id": tm.tool_call_id,
                    "content": tm.content,
                })
                i += 1

        elif msg.role == "assistant":
            messages.append({"role": "assistant", "content": msg.content})
            i += 1

        else:
            i += 1

    return messages
