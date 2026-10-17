import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "codex_advisor", Path(__file__).resolve().parents[1] / "scripts" / "codex_advisor.py"
)
assert _SPEC is not None and _SPEC.loader is not None
codex_advisor = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(codex_advisor)


def _observation():
    return {
        "task": "Choose a candidate with lower estimated cost.",
        "feature_descriptions": {"estimated_cost": "Lower is better"},
        "features": {"estimated_cost": [8.0, 4.0]},
        "actions": [
            {"id": "2", "description": "Keep the first configuration"},
            {"id": "4", "description": "Use the second configuration"},
        ],
        "private_observation": "must not be sent",
    }


def _events(usage=None):
    return "\n".join(
        json.dumps(event)
        for event in [
            {"type": "thread.started", "thread_id": "test"},
            {"type": "turn.started"},
            {"type": "item.completed", "item": {"type": "agent_message"}},
            {
                "type": "turn.completed",
                "usage": usage or {"input_tokens": 100, "output_tokens": 12},
            },
        ]
    ).encode()


class _FakeProcess:
    pid = 19372

    def __init__(self, command, answer, stdout=None, returncode=0):
        self.command = command
        self.answer = answer
        self.stdout = stdout if stdout is not None else _events()
        self.returncode = returncode
        self.communicated = False

    def communicate(self, input=None, timeout=None):
        self.communicated = True
        if input is not None:
            self.prompt = input.decode("utf-8")
        answer_path = Path(self.command[self.command.index("--output-last-message") + 1])
        answer_path.write_text(json.dumps(self.answer), encoding="utf-8")
        return self.stdout, b"private stderr should not be surfaced"

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.returncode = -9


def _install_success(monkeypatch, *, answer=None, stdout=None, returncode=0):
    captured = {}

    def fake_popen(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        captured["schema"] = json.loads(
            Path(command[command.index("--output-schema") + 1]).read_text(encoding="utf-8")
        )
        process = _FakeProcess(
            command,
            answer or {"action_id": "4", "reason": "Its estimated cost is lower."},
            stdout=stdout,
            returncode=returncode,
        )
        captured["process"] = process
        return process

    monkeypatch.setattr(codex_advisor.shutil, "which", lambda _name: "/usr/bin/codex")
    monkeypatch.setattr(codex_advisor.subprocess, "Popen", fake_popen)
    return captured


def test_choose_uses_private_cli_packet_and_returns_provenance(monkeypatch):
    captured = _install_success(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "do-not-copy")
    monkeypatch.setenv("TYPESAFE_API_KEY", "do-not-copy")
    observation = _observation()

    decision = codex_advisor.choose(observation)

    command = captured["command"]
    kwargs = captured["kwargs"]
    schema = captured["schema"]
    assert schema["properties"]["action_id"]["enum"] == ["2", "4"]
    assert schema["additionalProperties"] is False
    assert kwargs["cwd"] != Path(__file__).resolve().parents[1]
    assert kwargs["cwd"].name.startswith("p4ai-codex-")
    assert kwargs["start_new_session"] is True
    assert kwargs["stdin"] == codex_advisor.subprocess.PIPE
    assert kwargs["stderr"] == codex_advisor.subprocess.PIPE
    assert "OPENAI_API_KEY" not in kwargs["env"]
    assert "TYPESAFE_API_KEY" not in kwargs["env"]
    assert "--ephemeral" in command
    assert "--ignore-user-config" in command
    assert "--ignore-rules" not in command
    assert command[command.index("--sandbox") + 1] == "read-only"
    assert command[command.index("--model") + 1] == "gpt-6-luna"
    assert command[command.index("-c") + 1] == 'model_reasoning_effort="max"'
    prompt = captured["process"].prompt
    assert "private_observation" not in prompt
    assert "must not be sent" not in prompt
    assert '"id":"4","description":"Use the second configuration","features":{"estimated_cost":4.0}' in prompt
    assert decision == {
        "provider": "codex",
        "chosen_action": "4",
        "reason": "Its estimated cost is lower.",
        "model": "gpt-6-luna",
        "reasoning_effort": "max",
        "usage": {"input_tokens": 100, "output_tokens": 12},
        "latency_ms": decision["latency_ms"],
    }
    assert decision["latency_ms"] >= 0


def test_choice_outside_schema_menu_is_rejected(monkeypatch):
    _install_success(monkeypatch, answer={"action_id": "1", "reason": "invalid"})

    with pytest.raises(codex_advisor.CodexUnavailable) as caught:
        codex_advisor.choose(_observation())

    assert caught.value.reason == "invalid_response"
    assert "outside the eligible menu" in str(caught.value)


def test_tool_call_event_is_rejected(monkeypatch):
    tool_event = json.dumps(
        {"type": "item.started", "item": {"type": "command_execution", "command": "cat source"}}
    ).encode()
    _install_success(monkeypatch, stdout=tool_event)

    with pytest.raises(codex_advisor.CodexUnavailable) as caught:
        codex_advisor.choose(_observation())

    assert caught.value.reason == "tool_call"


def test_timeout_kills_process_group_and_is_typed(monkeypatch):
    captured = {}

    class TimedOutProcess(_FakeProcess):
        def communicate(self, input=None, timeout=None):
            if input is not None:
                self.prompt = input.decode("utf-8")
            if timeout == codex_advisor.TIMEOUT_SECONDS:
                raise subprocess.TimeoutExpired("codex", timeout)
            self.returncode = -15
            return b"", b""

    def fake_popen(command, **kwargs):
        process = TimedOutProcess(command, {})
        captured["process"] = process
        return process

    signals = []
    monkeypatch.setattr(codex_advisor.shutil, "which", lambda _name: "/usr/bin/codex")
    monkeypatch.setattr(codex_advisor.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(codex_advisor.os, "killpg", lambda pid, sig: signals.append((pid, sig)))

    with pytest.raises(codex_advisor.CodexUnavailable) as caught:
        codex_advisor.choose(_observation())

    assert caught.value.reason == "timeout"
    assert signals == [(captured["process"].pid, codex_advisor.signal.SIGTERM)]


def test_missing_cli_is_typed(monkeypatch):
    monkeypatch.setattr(codex_advisor.shutil, "which", lambda _name: None)

    with pytest.raises(codex_advisor.CodexUnavailable) as caught:
        codex_advisor.choose(_observation())

    assert caught.value.reason == "missing_cli"


def test_cli_failure_does_not_expose_stderr(monkeypatch):
    _install_success(monkeypatch, returncode=1)

    with pytest.raises(codex_advisor.CodexUnavailable) as caught:
        codex_advisor.choose(_observation())

    assert caught.value.reason == "cli_error"
    assert "private stderr" not in str(caught.value)


def test_invalid_usage_is_rejected(monkeypatch):
    _install_success(
        monkeypatch,
        stdout=_events({"input_tokens": -1, "output_tokens": 12}),
    )

    with pytest.raises(codex_advisor.CodexUnavailable) as caught:
        codex_advisor.choose(_observation())

    assert caught.value.reason == "invalid_response"
