"""
Gemini wrapper for BLUEPRINT.
Uses google-genai (new unified SDK).

Primary:  settings.GEMINI_MODEL (gemini-3-flash-preview) via Gemini API key
Fallback: settings.VERTEX_MODEL (gemini-2.5-flash) via Vertex AI

Note: we use asyncio.to_thread() with the sync client to avoid a known
google-genai bug where the async client's aclose() fails with
AttributeError('_async_httpx_client') during cleanup.
"""
import asyncio
import json
import logging
import re

from google import genai
from google.genai import types as genai_types

from backend.config import settings

logger = logging.getLogger(__name__)

_api_client:    genai.Client | None = None
_vertex_client: genai.Client | None = None


def _get_api_client() -> genai.Client:
    global _api_client
    if _api_client is None:
        _api_client = genai.Client(api_key=settings.GEMINI_API_KEY)
    return _api_client


def _get_vertex_client() -> genai.Client:
    global _vertex_client
    if _vertex_client is None:
        _vertex_client = genai.Client(
            vertexai=True,
            project=settings.GOOGLE_CLOUD_PROJECT,
            location=settings.GOOGLE_CLOUD_REGION,
        )
    return _vertex_client


def _sync_generate(prompt: str, temperature: float) -> str:
    """Sync call — run in thread to keep the event loop free.
    Primary: GEMINI_MODEL via API key.
    Fallback: VERTEX_MODEL via Vertex AI.
    """
    try:
        client = _get_api_client()
        resp = client.models.generate_content(
            model=settings.GEMINI_MODEL,
            contents=prompt,
            config=genai_types.GenerateContentConfig(
                temperature=temperature,
                max_output_tokens=4096,
            ),
        )
        return resp.text or ""
    except Exception as e:
        logger.warning("[Gemini:%s] generate failed: %s — trying Vertex AI fallback", settings.GEMINI_MODEL, e)

    try:
        client = _get_vertex_client()
        resp = client.models.generate_content(
            model=settings.VERTEX_MODEL,
            contents=prompt,
            config=genai_types.GenerateContentConfig(
                temperature=temperature,
                max_output_tokens=4096,
            ),
        )
        return resp.text or ""
    except Exception as e2:
        logger.error("[Gemini] both models failed: %s", e2)
        return ""


async def generate(prompt: str, temperature: float = 0.3) -> str:
    """Generate plain text. Primary: GEMINI_MODEL via API key. Fallback: VERTEX_MODEL via Vertex AI."""
    return await asyncio.to_thread(_sync_generate, prompt, temperature)


async def generate_json(prompt: str, temperature: float = 0.1) -> dict | list:
    """Generate structured JSON output. Returns {} on failure.

    Robust extraction: strips markdown fences, then finds the outermost
    JSON object/array even when Gemini prepends reasoning text.
    """
    full_prompt = prompt + "\n\nRespond with ONLY valid JSON, no markdown fences, no explanatory text before or after."
    text = await generate(full_prompt, temperature=temperature)
    text = text.strip()

    # Strip markdown fences
    text = re.sub(r"^```[a-z]*\n?", "", text)
    text = re.sub(r"\n?```$", "", text)
    text = text.strip()

    # If Gemini prefixed reasoning text, find the first { or [
    if text and text[0] not in ('{', '['):
        for bracket, close in [('{', '}'), ('[', ']')]:
            idx = text.find(bracket)
            if idx != -1:
                end = text.rfind(close)
                if end > idx:
                    text = text[idx:end + 1]
                    break

    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        logger.warning("[Gemini] JSON parse failed: %s | raw: %.300s", e, text)
        return {}
