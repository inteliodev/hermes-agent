"""POST a Langfuse public ingestion batch. Fail-open. Never logs credentials."""

from __future__ import annotations

import base64
import json
import logging
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)


def post_ingestion(host: str, public_key: str, secret_key: str, batch: list[dict]) -> None:
    """Send ``batch`` to ``{host}/api/public/ingestion``. Raises on transport failure."""
    url = host.rstrip("/") + "/api/public/ingestion"
    token = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode("ascii")
    request = urllib.request.Request(
        url,
        data=json.dumps({"batch": batch}).encode("utf-8"),
        method="POST",
        headers={"Authorization": f"Basic {token}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=2) as response:
        response.read()


def post_ingestion_fail_open(host: str, public_key: str, secret_key: str, batch: list[dict]) -> None:
    try:
        post_ingestion(host, public_key, secret_key, batch)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        logger.debug("langfuse ingestion failed", exc_info=True)
