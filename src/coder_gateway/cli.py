"""`uv run coder-gateway`: start the Gateway on 127.0.0.1 (port from $CODER_GATEWAY_PORT, default 8787).

$CODER_GATEWAY_HOST binds another address; without `dashboard_token` the dashboard is then open to
the network, which is logged as a warning (DECISIONS.md #27).
"""

from __future__ import annotations

import ipaddress
import logging
import os

import uvicorn

from coder_gateway.app import create_app
from coder_gateway.config import load_config
from coder_gateway.deciders import build_decider


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    config = load_config()
    dc = config.decider
    decider = build_decider(
        primary=dc.primary,
        fallback=dc.fallback,
        timeout_s=dc.timeout_s,
        jev_model=dc.jev_model,
        llm_model=dc.llm_model,
        openai_api_key=config.upstream.api_key,
        openai_base_url=config.upstream.base_url,
        typesafe_api_key=dc.typesafe_api_key,
    )
    app = create_app(config, decider=decider)
    port = int(os.environ.get("CODER_GATEWAY_PORT", "8787"))
    host = os.environ.get("CODER_GATEWAY_HOST", "127.0.0.1")
    log = logging.getLogger("coder_gateway")
    if not is_loopback(host) and not config.dashboard_token:
        log.warning("bound to %s without dashboard_token: /gateway/ is readable from the network", host)
    log.info(
        "Coder Gateway on http://%s:%d (decider %s, fallback %s, strategy %s); dashboard /gateway/",
        host,
        port,
        dc.primary,
        dc.fallback,
        dc.strategy,
    )
    uvicorn.run(app, host=host, port=port, log_level="warning")


def is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


if __name__ == "__main__":
    main()
