"""python -m relay"""

from __future__ import annotations

import logging
import sys

from relay.config import Config, ConfigError
from relay.server import build_server


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    log = logging.getLogger("relay")
    try:
        config = Config.from_env()
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2
    if not config.relay_token:
        log.warning("RELAY_TOKEN is empty; /ask and /task will reject every request")
    if not config.callback_secret:
        log.warning("CALLBACK_SECRET is empty; /callback will reject every request")
    server = build_server(config)
    host, port = server.server_address[:2]
    log.info("listening on %s:%s", host, port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("shutting down")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
