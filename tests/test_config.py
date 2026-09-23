from pathlib import Path

import pytest

from coder_gateway.cli import is_loopback
from coder_gateway.config import DEFAULT_CONFIG_PATH, load_config


def test_default_config_loads() -> None:
    cfg = load_config(DEFAULT_CONFIG_PATH)
    vm = cfg.virtual_model_for_key("sk-gw-fwd-demo")
    assert vm is not None and vm.name == "fwd-coder" and vm.upstream_model == "gpt-5.4"
    assert vm.system_prompt and vm.workflow.rule("propose_issue")
    assert cfg.upstream.base_url == "https://api.openai.com/v1"
    assert cfg.decider.jev_model == "jev-latest" and cfg.decider.llm_model == "gpt-5.4-mini"
    assert cfg.events_path is not None and cfg.events_path.name == "events.jsonl"
    assert cfg.virtual_model_for_key("nope") is None


def test_config_path_from_env_and_relative_workflow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    wf = tmp_path / "wf.yaml"
    wf.write_text("name: t\nrules: []\n")
    cfg_file = tmp_path / "gw.yaml"
    cfg_file.write_text(
        "decider: {primary: none, fallback: none, timeout_s: 0.2}\n"
        "virtual_models:\n"
        "  - {name: a, api_key: k, upstream_model: gpt-5.4-mini, workflow: wf.yaml}\n"
    )
    monkeypatch.setenv("CODER_GATEWAY_CONFIG", str(cfg_file))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env")
    cfg = load_config()
    assert cfg.decider.primary == "none" and cfg.decider.decision_timeout_s == pytest.approx(0.9)
    assert cfg.virtual_models[0].workflow.name == "t" and cfg.virtual_models[0].system_prompt == ""
    assert cfg.upstream.api_key == "sk-from-env"
    assert cfg.events_path is None


def test_dashboard_token_optional(tmp_path: Path) -> None:
    assert load_config(DEFAULT_CONFIG_PATH).dashboard_token is None
    wf = tmp_path / "wf.yaml"
    wf.write_text("name: t\nrules: []\n")
    cfg_file = tmp_path / "gw.yaml"
    cfg_file.write_text("dashboard_token: s3cret\nvirtual_models: []\n")
    assert load_config(cfg_file).dashboard_token == "s3cret"


def test_is_loopback() -> None:
    assert is_loopback("127.0.0.1") and is_loopback("::1") and is_loopback("localhost")
    assert not is_loopback("0.0.0.0") and not is_loopback("192.168.1.5") and not is_loopback("example.com")
