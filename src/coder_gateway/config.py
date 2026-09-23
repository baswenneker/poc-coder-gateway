"""Gateway configuration: upstream, Decider settings and Virtual Models (config/gateway.yaml)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

from coder_gateway.domain import VirtualModel
from coder_gateway.workflow import load_workflow

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "gateway.yaml"
CONFIG_ENV_VAR = "CODER_GATEWAY_CONFIG"


@dataclass(frozen=True)
class UpstreamConfig:
    base_url: str = "https://api.openai.com/v1"
    api_key: str | None = None


@dataclass(frozen=True)
class DeciderConfig:
    primary: str = "jev"
    fallback: str = "llm"
    timeout_s: float = 1.5
    strategy: str = "last_10_truncated"
    jev_model: str = "jev-latest"
    llm_model: str = "gpt-5.4-mini"
    typesafe_api_key: str | None = None

    @property
    def decision_timeout_s(self) -> float:
        """Outer timeout the Gateway puts on one Decision: primary + fallback + margin."""
        return self.timeout_s * 2 + 0.5


@dataclass(frozen=True)
class GatewayConfig:
    upstream: UpstreamConfig
    decider: DeciderConfig
    virtual_models: tuple[VirtualModel, ...]
    events_path: Path | None = None

    def virtual_model_for_key(self, api_key: str) -> VirtualModel | None:
        for vm in self.virtual_models:
            if vm.api_key == api_key:
                return vm
        return None


def load_env() -> None:
    """Load secrets: .env.local wins over .env; real environment variables win over both."""
    load_dotenv(PROJECT_ROOT / ".env.local", override=False)
    load_dotenv(PROJECT_ROOT / ".env", override=False)


def parse_config(data: dict[str, Any], base_dir: Path) -> GatewayConfig:
    up = data.get("upstream") or {}
    upstream = UpstreamConfig(
        base_url=str(up.get("base_url", UpstreamConfig.base_url)).rstrip("/"),
        api_key=os.environ.get(str(up.get("api_key_env", "OPENAI_API_KEY"))),
    )
    dc = data.get("decider") or {}
    decider = DeciderConfig(
        primary=str(dc.get("primary", DeciderConfig.primary)),
        fallback=str(dc.get("fallback", DeciderConfig.fallback)),
        timeout_s=float(dc.get("timeout_s", DeciderConfig.timeout_s)),
        strategy=str(dc.get("strategy", DeciderConfig.strategy)),
        jev_model=str(dc.get("jev_model", DeciderConfig.jev_model)),
        llm_model=str(dc.get("llm_model", DeciderConfig.llm_model)),
        typesafe_api_key=os.environ.get("TYPESAFE_API_KEY"),
    )
    vms: list[VirtualModel] = []
    for raw in data.get("virtual_models") or []:
        vms.append(
            VirtualModel(
                name=raw["name"],
                api_key=raw["api_key"],
                upstream_model=raw["upstream_model"],
                system_prompt=str(raw.get("system_prompt", "")).strip(),
                workflow=load_workflow(base_dir / raw["workflow"]),
            )
        )
    names = [vm.name for vm in vms]
    keys = [vm.api_key for vm in vms]
    if len(set(names)) != len(names) or len(set(keys)) != len(keys):
        raise ValueError("virtual model names and api keys must be unique")
    events_path = data.get("events_path")
    return GatewayConfig(
        upstream=upstream,
        decider=decider,
        virtual_models=tuple(vms),
        events_path=(base_dir / events_path).resolve() if events_path else None,
    )


def load_config(path: str | Path | None = None) -> GatewayConfig:
    """Load the gateway config. Path: argument, else $CODER_GATEWAY_CONFIG, else config/gateway.yaml."""
    load_env()
    cfg_path = Path(path or os.environ.get(CONFIG_ENV_VAR) or DEFAULT_CONFIG_PATH).resolve()
    with open(cfg_path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return parse_config(data, cfg_path.parent)
