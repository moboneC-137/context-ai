"""AI providers behind one protocol (PRD FR-20 – FR-23). Only `contextai.actions` should call them."""

from .base import (
    APIError,
    Completion,
    NoNetwork,
    NotConfigured,
    Prompt,
    Provider,
    ProviderError,
    RateLimit,
    SelectionTooLong,
    Timeout,
)
from .mock import MockProvider
from .openai import OpenAIProvider, resolve_api_key

__all__ = [
    "APIError",
    "Completion",
    "MockProvider",
    "NoNetwork",
    "NotConfigured",
    "OpenAIProvider",
    "Prompt",
    "Provider",
    "ProviderError",
    "RateLimit",
    "SelectionTooLong",
    "Timeout",
    "resolve_api_key",
]
