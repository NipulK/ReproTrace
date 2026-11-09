"""Google Gemini REST structured outputs. Credentials stay in request headers."""
import json
import os
import re

import httpx


class GeminiError(Exception):
    def __init__(self, message, status_code=502):
        super().__init__(message)
        self.status_code = status_code


def inline_schema(schema):
    """Inline Pydantic definitions for the Gemini JSON-schema request."""
    definitions = schema.get("$defs", {})
    def expand(value):
        if isinstance(value, list):
            return [expand(item) for item in value]
        if not isinstance(value, dict):
            return value
        if "$ref" in value:
            return expand(definitions[value["$ref"].split("/")[-1]])
        return {key: expand(item) for key, item in value.items() if key not in ("$defs", "title")}
    return expand(schema)


def request_json(model, instructions, input_data, schema, max_tokens):
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key:
        raise GeminiError("Set GEMINI_API_KEY in .env and restart the API.", 503)
    if not re.fullmatch(r"[a-zA-Z0-9._-]+", model):
        raise GeminiError("Invalid Gemini model name in server configuration.", 503)
    try:
        response = httpx.post(
            "https://generativelanguage.googleapis.com/v1beta/models/" + model + ":generateContent",
            headers={"x-goog-api-key": key}, timeout=90, follow_redirects=False,
            json={"systemInstruction": {"parts": [{"text": instructions}]},
                  "contents": [{"role": "user", "parts": [{"text": json.dumps(input_data)}]}],
                  "generationConfig": {"responseMimeType": "application/json",
                                       "responseJsonSchema": inline_schema(schema),
                                       "maxOutputTokens": max_tokens}})
    except httpx.TimeoutException:
        raise GeminiError("Gemini timed out. Your saved review is unchanged.", 504)
    except httpx.RequestError:
        raise GeminiError("Could not reach Gemini. Check the server connection.")
    if response.status_code == 429:
        raise GeminiError("Gemini quota or rate limit reached. Check your AI Studio free-tier quota and retry later.", 429)
    if response.status_code in (401, 403):
        raise GeminiError("Gemini rejected the API key or project access. Check AI Studio key permissions.")
    if response.status_code == 404:
        raise GeminiError("Gemini model not available. Check GEMINI_MODEL and your project's model access.")
    if response.status_code != 200:
        raise GeminiError("Gemini rejected the request. Check model and structured-output support.")
    try:
        body = response.json()
        candidates = body.get("candidates", [])
        if not candidates or candidates[0].get("finishReason") != "STOP":
            raise GeminiError("Gemini returned a blocked or incomplete response. Review manually or retry with a shorter report.")
        parts = candidates[0].get("content", {}).get("parts", [])
        text = "".join(part.get("text", "") for part in parts if not part.get("thought"))
        return {"data": json.loads(text), "provider": "gemini", "model": model,
                "returned_model": body.get("modelVersion", model),
                "response_id": body.get("responseId"), "usage": body.get("usageMetadata")}
    except (ValueError, KeyError, TypeError, AttributeError):
        raise GeminiError("Gemini returned invalid structured JSON. No suggestions were applied.")
