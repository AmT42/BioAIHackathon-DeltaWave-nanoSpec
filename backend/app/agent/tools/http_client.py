from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
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

    def _retry_after_seconds(self, headers: dict[str, Any] | None) -> float | None:
        if not headers:
            return None
        raw = None
        for key in ("retry-after", "Retry-After"):
            if key in headers:
                raw = headers.get(key)
                break
        if raw is None:
            return None
        text = str(raw).strip()
        if not text:
            return None
        try:
            return max(float(text), 0.0)
        except Exception:
            pass
        try:
            dt = parsedate_to_datetime(text)
            now = time.time()
            return max(dt.timestamp() - now, 0.0)
        except Exception:
            return None

    def _backoff_seconds(self, *, attempt: int, retry_after_seconds: float | None = None) -> float:
        if retry_after_seconds is not None:
            # Add small jitter to avoid synchronized retries.
            return max(0.0, retry_after_seconds + random.uniform(0.0, 0.25))
        base = min(0.5 * (2**attempt), 8.0)
        return base + random.uniform(0.0, 0.25)

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
        last_retry_meta: dict[str, Any] = {
            "attempts": 0,
            "retry_count": 0,
            "delays_seconds": [],
            "retry_after_seconds": None,
        }
        for attempt in range(self.max_retries + 1):
            try:
                last_retry_meta["attempts"] = attempt + 1
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
                err_headers = dict(exc.headers or {})
                retry_after = self._retry_after_seconds(err_headers)
                if retry_after is not None:
                    last_retry_meta["retry_after_seconds"] = retry_after
                if retryable and attempt < self.max_retries:
                    delay = self._backoff_seconds(attempt=attempt, retry_after_seconds=retry_after)
                    last_retry_meta["retry_count"] = int(last_retry_meta["retry_count"]) + 1
                    last_retry_meta["delays_seconds"].append(round(delay, 3))
                    time.sleep(delay)
                    continue
                if exc.code == 404:
                    raise ToolExecutionError(code="NOT_FOUND", message=f"Upstream resource not found: {full_url}") from exc
                if exc.code == 429:
                    raise ToolExecutionError(
                        code="RATE_LIMIT",
                        message=f"Rate limited by upstream source: {full_url}",
                        retryable=True,
                        details={
                            "url": full_url,
                            "status_code": 429,
                            "retry": last_retry_meta,
                        },
                    ) from exc
                raise ToolExecutionError(
                    code="UPSTREAM_ERROR",
                    message=f"HTTP {exc.code} from upstream source",
                    retryable=retryable,
                    details={"url": full_url, "status_code": exc.code, "retry": last_retry_meta},
                ) from exc
            except URLError as exc:
                last_exc = exc
                if attempt < self.max_retries:
                    delay = self._backoff_seconds(attempt=attempt)
                    last_retry_meta["retry_count"] = int(last_retry_meta["retry_count"]) + 1
                    last_retry_meta["delays_seconds"].append(round(delay, 3))
                    time.sleep(delay)
                    continue
                raise ToolExecutionError(
                    code="UPSTREAM_ERROR",
                    message="Network error while contacting upstream source",
                    retryable=True,
                    details={"url": full_url, "retry": last_retry_meta},
                ) from exc

        raise ToolExecutionError(
            code="UPSTREAM_ERROR",
            message="Unexpected HTTP client failure",
            details={"url": full_url, "last_error": str(last_exc) if last_exc else None, "retry": last_retry_meta},
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
