"""OpenAI ``response_format`` -> Anthropic ``output_config`` for the auxiliary Anthropic adapter.

Split out of ``agent/auxiliary_client.py`` (code-health size ratchet); a leaf module.
"""

from __future__ import annotations

from typing import Any


def _translate_anthropic_response_format(anthropic_kwargs: dict[str, Any], response_format: Any) -> None:
    """Merge an OpenAI response format into Anthropic ``output_config``."""
    if not isinstance(response_format, dict):
        return
    format_type = response_format.get("type")
    if format_type == "json_schema":
        json_schema = response_format.get("json_schema")
        if not isinstance(json_schema, dict) or "schema" not in json_schema:
            return
        schema = json_schema["schema"]
    elif format_type == "json_object":
        # Anthropic SDK has no schema-less JSON mode; only ``json_schema``.
        schema = {"type": "object"}
    else:
        return
    output_config = anthropic_kwargs.get("output_config")
    if not isinstance(output_config, dict):
        output_config = {}
        anthropic_kwargs["output_config"] = output_config
    output_config["format"] = {"type": "json_schema", "schema": schema}
