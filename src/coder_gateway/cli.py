"""`uv run coder-gateway`: start the Gateway on 127.0.0.1 (port from $CODER_GATEWAY_PORT, default 8787)."""

from __future__ import annotations

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
    logging.getLogger("coder_gateway").info(
        "Coder Gateway on http://127.0.0.1:%d (decider %s, fallback %s, strategy %s); dashboard /gateway/",
        port,
        dc.primary,
        dc.fallback,
        dc.strategy,
    )
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
