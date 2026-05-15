"""Thin Sourcegraph GraphQL client.

Sourcegraph 6.x speaks GraphQL at /.api/graphql. We POST JSON with an
`Authorization: token <token>` header. Single dependency: urllib from the
stdlib — no extra runtime pulls.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request


class SourcegraphError(RuntimeError):
    """Raised on transport, auth, or GraphQL-level errors."""


class SourcegraphClient:
    def __init__(self, url: str | None = None, token: str | None = None):
        self.url = (url or os.environ.get("SOURCEGRAPH_URL") or "http://localhost:7080").rstrip("/")
        self.token = token if token is not None else os.environ.get("SOURCEGRAPH_TOKEN", "")
        self._endpoint = f"{self.url}/.api/graphql"

    def query(self, query: str, variables: dict | None = None, timeout: float = 15.0) -> dict:
        if not self.token:
            raise SourcegraphError(
                "no Sourcegraph token configured. Mint one in the Sourcegraph UI "
                "(Settings -> Access tokens, reach the UI via `make tunnel` then "
                "http://localhost:7080) and upload it with "
                "`make upload-sourcegraph-token TOKEN=<token>` from your laptop."
            )
        body = json.dumps({"query": query, "variables": variables or {}}).encode("utf-8")
        req = urllib.request.Request(
            self._endpoint,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"token {self.token}",
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
            raise SourcegraphError(f"HTTP {e.code} from Sourcegraph: {detail or e.reason}") from None
        except urllib.error.URLError as e:
            raise SourcegraphError(
                f"could not reach Sourcegraph at {self._endpoint}: {e.reason}"
            ) from None
        if "errors" in payload:
            msg = "; ".join(err.get("message", "?") for err in payload["errors"])
            raise SourcegraphError(f"GraphQL errors: {msg}")
        return payload.get("data") or {}
