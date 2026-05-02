import os
from openai import OpenAI
from app.core.config import settings

_client = None


def _setup_langsmith():
    """若配置了 LangSmith API Key，设置环境变量启用自动追踪。"""
    if settings.langchain_api_key:
        os.environ.setdefault("LANGCHAIN_TRACING_V2", "true")
        os.environ.setdefault("LANGCHAIN_API_KEY", settings.langchain_api_key)
        os.environ.setdefault("LANGCHAIN_PROJECT", settings.langchain_project)


# 启动时立即配置
_setup_langsmith()


def get_llm_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
        )
    return _client


def chat(system: str, user: str, temperature: float = 0.3) -> str:
    """同步 LLM 调用，自动被 LangSmith 追踪（若已配置）。"""
    from langsmith import traceable

    @traceable(name="llm_chat", run_type="llm", metadata={"model": settings.llm_model})
    def _call():
        client = get_llm_client()
        response = client.chat.completions.create(
            model=settings.llm_model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=temperature,
        )
        return response.choices[0].message.content

    return _call()


def chat_stream(system: str, user: str, temperature: float = 0.3):
    """流式输出，逐 token yield 字符串。流式调用单独追踪每次完整响应。"""
    client = get_llm_client()
    response = client.chat.completions.create(
        model=settings.llm_model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=temperature,
        stream=True,
    )
    for chunk in response:
        delta = chunk.choices[0].delta.content
        if delta:
            yield delta
