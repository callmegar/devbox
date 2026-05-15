"""Thin Linear GraphQL client.

Linear's API lives at https://api.linear.app/graphql and authenticates via the
`Authorization` header set to the raw personal API key (`lin_api_...`) — no
`Bearer ` prefix. Stdlib only: urllib + json, no extra runtime deps.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request


class LinearError(RuntimeError):
    """Raised on transport, auth, or GraphQL-level errors."""


class LinearClient:
    def __init__(self, token: str | None = None, endpoint: str | None = None):
        self.token = token if token is not None else os.environ.get("LINEAR_API_KEY", "")
        self.endpoint = (endpoint or "https://api.linear.app/graphql").rstrip("/")

    def query(self, query: str, variables: dict | None = None, timeout: float = 20.0) -> dict:
        if not self.token:
            raise LinearError(
                "no Linear API key configured. Put a personal API key in SSM "
                "(`/devbox/linear-api-key`) and restart the wrapper. Mint one at "
                "https://linear.app/settings/account/security under 'Personal API keys'."
            )
        body = json.dumps({"query": query, "variables": variables or {}}).encode("utf-8")
        req = urllib.request.Request(
            self.endpoint,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": self.token,
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                detail = e.read().decode("utf-8", errors="replace")[:500]
            except Exception:
                detail = ""
            raise LinearError(f"HTTP {e.code} from Linear: {detail or e.reason}") from None
        except urllib.error.URLError as e:
            raise LinearError(
                f"could not reach Linear at {self.endpoint}: {e.reason}"
            ) from None
        if "errors" in payload:
            msg = "; ".join(err.get("message", "?") for err in payload["errors"])
            raise LinearError(f"GraphQL errors: {msg}")
        return payload.get("data") or {}
