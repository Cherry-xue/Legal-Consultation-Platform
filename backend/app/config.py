from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / '.env', extra='ignore', env_file_encoding='utf-8-sig')
    cookie_secure: bool = False
    session_days: int = Field(default=7, ge=1, le=30)
    allowed_origins: str = 'http://127.0.0.1:5174,http://localhost:5174,http://127.0.0.1:8091'
    shared_env_path: str = '../../.env'
    dashscope_api_key: SecretStr = SecretStr('')
    dashscope_base_url: str = 'https://dashscope.aliyuncs.com/compatible-mode/v1'
    mysql_host: str = '127.0.0.1'
    mysql_port: int = 3306
    mysql_database: str = 'legal_assistant'
    mysql_user: str = 'legal_app'
    mysql_password: SecretStr = SecretStr('')
    chroma_host: str = '127.0.0.1'
    chroma_port: int = 8100
    agent_contract_model: str = 'qwen-plus-2025-11-05'
    agent_labor_model: str = 'qwen-plus-2025-07-14'
    agent_family_model: str = 'qwen-plus-2025-04-28'
    agent_consumer_model: str = 'qwen3.7-flash'
    agent_procedure_model: str = 'qwen-plus-2025-01-25'
    agent_collaboration_model: str = 'qwen3.7-max'
    embedding_model_name: str = 'qwen3.7-text-embedding'
    embedding_batch_size: int = Field(default=10, ge=1, le=10)
    rag_hybrid_enabled: bool = True
    rag_candidate_limit: int = Field(default=30, ge=10, le=100)
    rag_max_vector_distance: float = Field(default=.5, ge=0, le=2)
    rag_min_lexical_coverage: float = Field(default=.28, ge=0, le=1)
    rag_min_rerank_score: float = Field(default=.20, ge=0, le=2)
    enable_thinking: bool = False
    model_timeout_seconds: int = 30
    collaboration_timeout_seconds: int = 75
    max_upload_mb: int = 10
    data_dir: str = './data'

    @property
    def storage(self):
        path = Path(self.data_dir)
        return path if path.is_absolute() else ROOT / path

    @property
    def db_url(self):
        return URL.create('mysql+pymysql', username=self.mysql_user,
                          password=self.mysql_password.get_secret_value(), host=self.mysql_host,
                          port=self.mysql_port, database=self.mysql_database,
                          query={'charset': 'utf8mb4'})

    def api_key(self):
        key = self.dashscope_api_key.get_secret_value()
        if key:
            return key
        # Deliberately import only the API key, never sibling DB or model settings.
        path = Path(self.shared_env_path)
        path = path if path.is_absolute() else ROOT / path
        if path.is_file():
            from dotenv import dotenv_values
            return dotenv_values(path, encoding='utf-8-sig').get('DASHSCOPE_API_KEY') or ''
        return ''


@lru_cache
def settings():
    return Settings()
