"""
Environment configuration, read once at import time.

Lambda reuses a warm container across invocations, so anything read at module scope
is paid for on the cold start only. Reading os.environ inside the handler instead
would repeat the work on every single click.
"""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    model_id: str
    bedrock_region: str
    effort: str
    max_tokens: int
    prompt_version: str
    cache_table: str
    cache_ttl_days: int
    log_level: str

    @property
    def cache_enabled(self) -> bool:
        """
        No table name means no cache. That is a supported mode, not an error: it is
        exactly what `sam local invoke` looks like when you have not deployed the
        stack yet, and the endpoint should still work there.
        """
        return bool(self.cache_table)


def _int_env(name: str, default: int) -> int:
    """
    Environment variables are always strings, and a typo'd one should not take the
    whole function down at import time with a ValueError - a bad number falls back
    to the default rather than turning every request into a 502.
    """
    raw = os.environ.get(name, "")
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


CONFIG = Config(
    model_id=os.environ.get("MODEL_ID", "anthropic.claude-opus-5"),
    bedrock_region=os.environ.get("BEDROCK_REGION", "us-east-1"),
    effort=os.environ.get("EFFORT", "low"),
    max_tokens=_int_env("MAX_TOKENS", 1024),
    prompt_version=os.environ.get("PROMPT_VERSION", "v1"),
    cache_table=os.environ.get("CACHE_TABLE", ""),
    cache_ttl_days=_int_env("CACHE_TTL_DAYS", 30),
    log_level=os.environ.get("LOG_LEVEL", "INFO"),
)

# Version of the request/response wire format. Unity sends this and reads it back.
# Bumped only for a breaking shape change, so an old build talking to a new backend
# can be detected rather than silently mis-parsing.
SCHEMA_VERSION = "1.0"
