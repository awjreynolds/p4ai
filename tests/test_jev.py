import importlib.util
import io
import json
from pathlib import Path
from urllib.error import HTTPError

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "jev", Path(__file__).resolve().parents[1] / "scripts" / "jev.py"
)
assert _SPEC is not None and _SPEC.loader is not None
jev = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(jev)


def _inputs():
    return {
        "task": "Select the action with the lower estimated cost while considering register use.",
        "features": {"estimated_cost": [8.0, 4.0], "registers": [3, 6]},
        "feature_descriptions": {
            "estimated_cost": "Estimated execution cost; lower is better",
            "registers": "Estimated registers used",
        },
        "actions": [
            {"id": "4", "description": "Keep candidate 4"},
            {"id": "7", "description": "Use candidate 7"},
        ],
        "mask": [0, 0, 0, 0, 1, 0, 0, 1],
        "features_sha256": "local-trace-only",
        "source_ir": "must not be sent",
    }


def _payload(**overrides):
    result = {
        "model": "jev-1.14.0",
        "answers": {
            "action": {
                "type": "choice",
                "choice": "7",
                "probabilities": {"4": 0.1, "7": 0.9},
                "confidence": 0.87,
            }
        },
        "usage": {"input_tokens": 96, "output_tokens": 14},
    }
    result.update(overrides)
    return result


class _Response:
    def __init__(self, value):
        self.body = json.dumps(value).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self.body


def test_choose_sends_only_eligible_actions_and_precomputed_features(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-secret")
    requests = []

    def fake_urlopen(request, timeout):
        requests.append((request, timeout))
        return _Response(_payload())

    monkeypatch.setattr(jev, "urlopen", fake_urlopen)
    observation = _inputs()

    decision = jev.choose(observation)

    request, timeout = requests[0]
    body = json.loads(request.data)
    assert request.full_url == "https://api.typesafe.ai/v1/systemone"
    assert request.get_header("Authorization") == "Bearer test-secret"
    assert timeout == jev.TIMEOUT_SECONDS
    assert body["model"] == "jev-1.14.0"
    assert body["questions"]["action"]["type"] == "choice"
    assert set(body["questions"]["action"]["criteria"]) == {"4", "7"}
    assert body["state"]["actions"] == [
        {
            "id": "4",
            "description": "Keep candidate 4",
            "features": {"estimated_cost": 8.0, "registers": 3},
        },
        {
            "id": "7",
            "description": "Use candidate 7",
            "features": {"estimated_cost": 4.0, "registers": 6},
        },
    ]
    assert not {"mask", "source_ir", "features_sha256"} & body["state"].keys()
    assert decision["provider"] == "jev"
    assert decision["chosen_action"] == "7"
    assert decision["probabilities"] == {"4": 0.1, "7": 0.9}
    assert decision["confidence"] == 0.87
    assert decision["confidence_type"] == "distribution-derived"
    assert decision["model"] == "jev-1.14.0"
    assert decision["usage"] == {"input_tokens": 96, "output_tokens": 14}
    assert decision["latency_ms"] >= 0


def test_unauthorized_response_is_typed_and_does_not_expose_key_or_body(monkeypatch):
    secret = "test-secret"
    monkeypatch.setenv("TYPESAFE_API_KEY", secret)

    def fake_urlopen(_request, timeout):
        raise HTTPError(
            jev.API_URL,
            401,
            "Unauthorized",
            {},
            io.BytesIO(f"{secret} should not be surfaced".encode()),
        )

    monkeypatch.setattr(jev, "urlopen", fake_urlopen)
    observation = _inputs()

    with pytest.raises(jev.JevUnavailable) as caught:
        jev.choose(observation)

    assert caught.value.reason == "unauthorized"
    assert secret not in str(caught.value)
    assert "should not be surfaced" not in str(caught.value)


def test_timeout_is_typed_unavailable(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-secret")
    monkeypatch.setattr(jev, "urlopen", lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError()))
    observation = _inputs()

    with pytest.raises(jev.JevUnavailable) as caught:
        jev.choose(observation)

    assert caught.value.reason == "timeout"


def test_out_of_menu_choice_is_rejected(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-secret")
    payload = _payload()
    payload["answers"]["action"]["choice"] = "illegal"
    monkeypatch.setattr(jev, "urlopen", lambda *_args, **_kwargs: _Response(payload))
    observation = _inputs()

    with pytest.raises(jev.JevUnavailable) as caught:
        jev.choose(observation)

    assert caught.value.reason == "invalid_response"
    assert "outside the eligible menu" in str(caught.value)


def test_invalid_json_is_rejected_without_falling_back(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-secret")
    response = _Response(_payload())
    response.body = b"not json"
    monkeypatch.setattr(jev, "urlopen", lambda *_args, **_kwargs: response)
    observation = _inputs()

    with pytest.raises(jev.JevUnavailable) as caught:
        jev.choose(observation)

    assert caught.value.reason == "invalid_response"


def test_missing_key_does_not_read_the_users_default_credential_path(monkeypatch, tmp_path):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.setattr(jev.Path, "home", lambda: tmp_path)
    observation = _inputs()

    with pytest.raises(jev.JevUnavailable) as caught:
        jev.choose(observation)

    assert caught.value.reason == "missing_api_key"


def test_provider_packet_rejects_unaligned_features_and_mask(monkeypatch):
    observation = _inputs()
    observation["features"]["short"] = [1]
    with pytest.raises(ValueError, match="align with context.actions"):
        jev.choose(observation)

    observation = _inputs()
    observation["features"]["mask"] = [1, 1]
    with pytest.raises(ValueError, match="mask must stay outside"):
        jev.choose(observation)
