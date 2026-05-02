from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    llm_api_key: str
    llm_base_url: str = "https://api.deepseek.com"
    llm_model: str = "deepseek-chat"

    tushare_token: str = ""

    # LangSmith 追踪配置（可选，留空则不启用）
    langchain_tracing_v2: str = ""
    langchain_api_key: str = ""
    langchain_project: str = "invest-agent"

    upload_dir: str = "./uploads"
    output_dir: str = "./outputs"
    db_path: str = "./report_agent.db"

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
