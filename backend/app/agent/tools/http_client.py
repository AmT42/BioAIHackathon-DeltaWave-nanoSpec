from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any
from urllib import parse, request
from urllib.error import HTTPError, URLError

from app.agent.tools.errors import ToolExecutionError


@dataclass(frozen=True)
class HttpResponse:
    url: str
    status_code: int
    headers: dict[str, str]
    body: bytes


class SimpleHttpClient:
    def __init__(
        self,
        *,
        timeout_seconds: int = 20,
        max_retries: int = 2,
        user_agent: str = "hackathon-agent-core/0.1",
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.user_agent = user_agent

    def _build_url(self, url: str, params: dict[str, Any] | None = None) -> str:
        if not params:
            return url
        clean_params: dict[str, Any] = {k: v for k, v in params.items() if v is not None}
        encoded = parse.urlencode(clean_params, doseq=True)
        separator = "&" if "?" in url else "?"
        return f"{url}{separator}{encoded}" if encoded else url

    def request(
        self,
        *,
        method: str,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> HttpResponse:
        full_url = self._build_url(url, params)
        req_headers = {
            "User-Agent": self.user_agent,
            "Accept": "application/json, text/plain, */*",
        }
        if headers:
            req_headers.update(headers)

        body: bytes | None = None
        if json_body is not None:
            body = json.dumps(json_body).encode("utf-8")
            req_headers["Content-Type"] = "application/json"

        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                req = request.Request(full_url, headers=req_headers, data=body, method=method.upper())
                with request.urlopen(req, timeout=self.timeout_seconds) as resp:
                    response_headers = {k.lower(): v for k, v in dict(resp.headers).items()}
                    return HttpResponse(
                        url=full_url,
                        status_code=int(resp.status),
                        headers=response_headers,
                        body=resp.read(),
                    )
            except HTTPError as exc:
                retryable = exc.code in {408, 409, 425, 429, 500, 502, 503, 504}
                if retryable and attempt < self.max_retries:
                    time.sleep(min(0.5 * (attempt + 1), 1.5))
                    continue
                if exc.code == 404:
                    raise ToolExecutionError(code="NOT_FOUND", message=f"Upstream resource not found: {full_url}") from exc
                if exc.code == 429:
                    raise ToolExecutionError(
                        code="RATE_LIMIT",
                        message=f"Rate limited by upstream source: {full_url}",
                        retryable=True,
                    ) from exc
                raise ToolExecutionError(
                    code="UPSTREAM_ERROR",
                    message=f"HTTP {exc.code} from upstream source",
                    retryable=retryable,
                    details={"url": full_url},
                ) from exc
            except URLError as exc:
                last_exc = exc
                if attempt < self.max_retries:
                    time.sleep(min(0.5 * (attempt + 1), 1.5))
                    continue
                raise ToolExecutionError(
                    code="UPSTREAM_ERROR",
                    message="Network error while contacting upstream source",
                    retryable=True,
                    details={"url": full_url},
                ) from exc

        raise ToolExecutionError(
            code="UPSTREAM_ERROR",
            message="Unexpected HTTP client failure",
            details={"url": full_url, "last_error": str(last_exc) if last_exc else None},
        )

    def get_json(
        self,
        *,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[Any, dict[str, str]]:
        resp = self.request(method="GET", url=url, params=params, headers=headers)
        try:
            payload = json.loads(resp.body.decode("utf-8"))
        except Exception as exc:
            raise ToolExecutionError(
                code="UPSTREAM_ERROR",
                message="Upstream returned non-JSON payload",
                details={"url": resp.url, "status_code": resp.status_code},
            ) from exc
        return payload, resp.headers

    def get_text(
        self,
        *,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[str, dict[str, str]]:
        resp = self.request(method="GET", url=url, params=params, headers=headers)
        return resp.body.decode("utf-8", errors="replace"), resp.headers

    def get_bytes(
        self,
        *,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[bytes, dict[str, str]]:
        resp = self.request(method="GET", url=url, params=params, headers=headers)
        return resp.body, resp.headers
