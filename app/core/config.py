from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    kimi_api_key: SecretStr | None = None
    kimi_base_url: str = 'https://api.moonshot.cn/v1'
    kimi_model: str = 'kimi-k2.6'
    charon_offline: bool = False
    charon_allow_live: bool = False
    charon_timeout_seconds: float = Field(default=45, gt=0, le=120)
    charon_max_calls: int = Field(default=80, ge=1, le=100)
    charon_max_retries: int = Field(default=1, ge=0, le=1)
    charon_max_output_tokens: int = Field(default=4096, ge=128, le=8192)
    records_dir: Path = PROJECT_ROOT / 'records'
    rubrics_dir: Path = PROJECT_ROOT / 'rubrics'
    # Document parsing runs MinerU as an external command; its models never enter uv.lock.
    # The default expects the MinerU checkout's own environment next to this project.
    mineru_command: str = (f'{PROJECT_ROOT.parent}/MinerU/MinerU/.venv/bin/python '
                           f'{PROJECT_ROOT}/scripts/mineru_text_parse.py')
    mineru_backend: str = 'pipeline'
    mineru_parse_method: Literal['txt', 'auto', 'ocr'] = 'txt'
    parse_timeout_seconds: float = Field(default=600, gt=0, le=3600)
    max_document_bytes: int = Field(default=20_000_000, ge=1)

    model_config = SettingsConfigDict(env_file=PROJECT_ROOT / '.env',
                                     env_file_encoding='utf-8', extra='ignore')


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
