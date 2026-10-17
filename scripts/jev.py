"""Small HTTP adapter for bounded Jev choices.

The caller supplies precomputed numeric feature columns and already-eligible
actions. Only that data, the caller's task description, and action descriptions
are sent to TypeSafe; unknown context fields are ignored.
"""

from __future__ import annotations

import json
import math
import os
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

API_URL = "https://api.typesafe.ai/v1/systemone"
MODEL_ID = "jev-1.14.0"
TIMEOUT_SECONDS = 30
_QUESTION_ID = "action"


class JevUnavailable(RuntimeError):
    """Jev did not return a usable decision; ``reason`` is safe to log."""

    def __init__(self, reason: str, message: str):
        self.reason = reason
        super().__init__(message)


def choose(observation: dict[str, Any]) -> dict[str, Any]:
    """Choose one supplied eligible action using a pinned Jev model.

    The input is the advisor's allowlisted provider packet: ``task``,
    ``features``, ``feature_descriptions``, and ``actions``. Each feature vector
    is already aligned to the eligible action order. Masks and raw compiler
    observations are kept outside this packet.
    """
    features = observation.get("features") if isinstance(observation, dict) else None
    actions, task, feature_descriptions = _validate_inputs(features, observation)
    body = {
        "model": MODEL_ID,
        "state": {
            "task": task,
            "feature_descriptions": feature_descriptions,
            "actions": [
                {
                    "id": action["id"],
                    "description": action["description"],
                    "features": {
                        name: features[name][index]
                        for name in feature_descriptions
                    },
                }
                for index, action in enumerate(actions)
            ],
        },
        "questions": {
            _QUESTION_ID: {
                "type": "choice",
                "instructions": (
                    "Choose the eligible action that best satisfies the task, "
                    "using only the supplied feature values and descriptions. "
                    "The listed actions have already passed the host's legality "
                    "checks. Choose one listed option; do not invent or modify actions."
                ),
                "criteria": {action["id"]: action["description"] for action in actions},
            }
        },
    }

    api_key = _load_api_key()
    request = Request(
        API_URL,
        data=json.dumps(body, allow_nan=False, separators=(",", ":")).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    started = time.perf_counter()
    try:
        with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            response_body = response.read()
    except HTTPError as error:
        reason = "unauthorized" if error.code == 401 else "http_error"
        raise JevUnavailable(reason, f"Jev request failed with HTTP {error.code}") from None
    except TimeoutError:
        raise JevUnavailable("timeout", "Jev request timed out") from None
    except URLError as error:
        if isinstance(error.reason, TimeoutError):
            raise JevUnavailable("timeout", "Jev request timed out") from None
        raise JevUnavailable("transport_error", "Jev request could not reach the service") from None
    except OSError:
        raise JevUnavailable("transport_error", "Jev request could not reach the service") from None
    latency_ms = (time.perf_counter() - started) * 1000

    try:
        payload = json.loads(response_body)
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
        raise JevUnavailable("invalid_response", "Jev returned invalid JSON") from None
    return _parse_decision(payload, actions, latency_ms)


def _load_api_key() -> str:
    api_key = os.environ.get("TYPESAFE_API_KEY", "").strip()
    if not api_key:
        try:
            api_key = (Path.home() / ".config" / "typesafe" / "api-key").read_text().strip()
        except OSError:
            api_key = ""
    if not api_key:
        raise JevUnavailable("missing_api_key", "Jev API key is unavailable")
    return api_key


def _validate_inputs(
    features: dict[str, list[int | float]], context: dict[str, Any]
) -> tuple[list[dict[str, str]], str, dict[str, str]]:
    if not isinstance(features, dict) or not features:
        raise ValueError("features must be a non-empty mapping of names to numeric vectors")
    if not isinstance(context, dict):
        raise TypeError("context must be a mapping")

    task = context.get("task")
    if not isinstance(task, str) or not task.strip():
        raise ValueError("context.task must be a non-empty description of the decision")
    raw_actions = context.get("actions")
    if not isinstance(raw_actions, list) or not 1 <= len(raw_actions) <= 255:
        raise ValueError("context.actions must contain between 1 and 255 eligible actions")

    actions: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    for action in raw_actions:
        if not isinstance(action, dict):
            raise TypeError("each action must be a mapping with id and description")
        action_id = action.get("id")
        description = action.get("description")
        if not isinstance(action_id, str) or not action_id:
            raise ValueError("action ids must be non-negative candidate indexes")
        if action_id in seen_ids:
            raise ValueError("action ids must be unique")
        if not isinstance(description, str) or not description.strip():
            raise ValueError("action descriptions must be non-empty strings")
        seen_ids.add(action_id)
        actions.append({"id": action_id, "description": description})

    for name, values in features.items():
        if not isinstance(name, str) or not name:
            raise ValueError("feature names must be non-empty strings")
        if name == "mask":
            raise ValueError("mask must stay outside the provider feature packet")
        if not isinstance(values, list):
            raise TypeError("each feature must be an eligible-action vector")
        if len(values) != len(actions):
            raise ValueError("each feature vector must align with context.actions")
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not _is_finite_number(value)
            for value in values
        ):
            raise ValueError("feature values must be finite numbers")

    descriptions = context.get("feature_descriptions", {})
    if not isinstance(descriptions, dict):
        raise TypeError("context.feature_descriptions must be a mapping")
    normalized_descriptions: dict[str, str] = {}
    for name in features:
        description = descriptions.get(name, name)
        if not isinstance(description, str) or not description.strip():
            raise ValueError("feature descriptions must be non-empty strings")
        normalized_descriptions[name] = description
    return actions, task.strip(), normalized_descriptions


def _is_finite_number(value: float) -> bool:
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _parse_decision(
    payload: Any, actions: list[dict[str, str]], latency_ms: float
) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("model") != MODEL_ID:
        raise JevUnavailable("invalid_response", "Jev returned an unexpected model version")
    answers = payload.get("answers")
    answer = answers.get(_QUESTION_ID) if isinstance(answers, dict) else None
    allowed_ids = {action["id"] for action in actions}
    if not isinstance(answer, dict) or answer.get("type") != "choice":
        raise JevUnavailable("invalid_response", "Jev returned no choice answer")

    chosen = answer.get("choice")
    probabilities = answer.get("probabilities")
    confidence = answer.get("confidence")
    if not isinstance(chosen, str) or chosen not in allowed_ids:
        raise JevUnavailable("invalid_response", "Jev selected an action outside the eligible menu")
    if not isinstance(probabilities, dict) or set(probabilities) != allowed_ids:
        raise JevUnavailable("invalid_response", "Jev returned an incomplete action distribution")
    if any(
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not _is_finite_number(value)
        or value < 0
        or value > 1
        for value in probabilities.values()
    ) or not math.isclose(sum(probabilities.values()), 1.0, rel_tol=0.0, abs_tol=1e-4):
        raise JevUnavailable("invalid_response", "Jev returned an invalid action distribution")
    if (
        isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
        or not _is_finite_number(confidence)
        or not 0 <= confidence <= 1
    ):
        raise JevUnavailable("invalid_response", "Jev returned invalid confidence")
    if probabilities[chosen] < max(probabilities.values()):
        raise JevUnavailable("invalid_response", "Jev choice did not match its probability distribution")

    usage = payload.get("usage")
    if not isinstance(usage, dict):
        raise JevUnavailable("invalid_response", "Jev returned no token usage")
    input_tokens = usage.get("input_tokens")
    output_tokens = usage.get("output_tokens")
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value < 0
        for value in (input_tokens, output_tokens)
    ):
        raise JevUnavailable("invalid_response", "Jev returned invalid token usage")

    return {
        "provider": "jev",
        "chosen_action": chosen,
        "probabilities": probabilities,
        "confidence": confidence,
        "confidence_type": "distribution-derived",
        "confidence_semantics": "model-reported certainty derived from the choice distribution, not correctness probability",
        "model": payload["model"],
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
        "latency_ms": latency_ms,
    }
