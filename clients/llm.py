"""Claude (Anthropic) helper that returns schema-validated JSON."""
import json

import anthropic

from config import CLAUDE_MODEL

_client = anthropic.Anthropic()


class LLMError(RuntimeError):
    pass


def ask_json(system: str, user: str, schema: dict, effort: str = "high",
             max_tokens: int = 32000) -> dict:
    """Send one request and return the JSON object that matches `schema`."""
    try:
        with _client.beta.messages.stream(
            model=CLAUDE_MODEL,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_config={
                "effort": effort,
                "format": {"type": "json_schema", "schema": schema},
            },
            # If the main model declines, the API retries on a suitable fallback model.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        ) as stream:
            message = stream.get_final_message()
    except anthropic.RateLimitError as exc:
        raise LLMError("Claude rate limit hit; try again in a minute.") from exc
    except anthropic.APIStatusError as exc:
        raise LLMError(f"Claude API error {exc.status_code}: {exc.message}") from exc
    except anthropic.APIConnectionError as exc:
        raise LLMError("Could not reach the Claude API.") from exc

    if message.stop_reason == "refusal":
        raise LLMError("Claude declined this request.")
    if message.stop_reason == "max_tokens":
        raise LLMError("Claude's answer was cut off (max_tokens).")

    text = "".join(b.text for b in message.content if b.type == "text")
    return json.loads(text)
