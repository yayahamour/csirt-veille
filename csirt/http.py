"""Construction declarative des requetes et limites communes RSS/JSON."""
import os
import re
import time
from dataclasses import dataclass
from datetime import timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote

import httpx

from csirt.config import Settings


@dataclass
class FetchResult:
    status: int
    headers: httpx.Headers
    body: bytes


def render(value, query: str):
    if isinstance(value, str):
        return value.replace("{query}", query)
    if isinstance(value, dict):
        return {k: render(v, query) for k, v in value.items()}
    if isinstance(value, list):
        return [render(v, query) for v in value]
    return value


def fetch_request(client: httpx.Client, config: dict, settings: Settings, *, query: str = "", validators=None) -> FetchResult:
    request = config["request"]
    headers = {"User-Agent": settings.user_agent, "Accept-Encoding": "identity",
               "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml"}
    headers.update({k: str(v) for k, v in request["headers"].items()})
    for header, env in request["headers_env"].items():
        if not os.getenv(env):
            raise ValueError(f"Variable d'environnement manquante : {env}")
        headers[header] = request.get("headers_env_prefix", {}).get(header, "") + os.environ[env]
    headers.update(validators or {})
    started = time.monotonic()
    kwargs = {}
    if "json" in request:
        kwargs["json"] = render(request["json"], query)
    url = request["url"].replace("{query}", quote(query, safe=""))
    url = httpx.URL(url).copy_merge_params(render(request["params"], query))
    with client.stream(request["method"], url, headers=headers, **kwargs, follow_redirects=False,
                       timeout=httpx.Timeout(10.0)) as response:
        body = bytearray()
        if response.status_code == 200:
            for chunk in response.iter_bytes():
                if len(body) + len(chunk) > settings.max_bytes:
                    raise ValueError("Reponse trop volumineuse")
                if time.monotonic() - started > settings.fetch_budget_seconds:
                    raise ValueError("Budget de temps HTTP depasse")
                body.extend(chunk)
        return FetchResult(response.status_code, response.headers, bytes(body))


def retry_after(value: str | None, now: float) -> float:
    if not value:
        return 0
    try:
        if value.strip().isdigit():
            return float(int(value.strip()))
        date = parsedate_to_datetime(value)
        if date.tzinfo is None:
            date = date.replace(tzinfo=timezone.utc)
        return max(0, date.timestamp() - now)
    except (ValueError, TypeError, OverflowError):
        return 0


def cache_delay(headers) -> float:
    match = re.search(r"(?:^|,)\s*max-age\s*=\s*\"?(\d+)", headers.get("cache-control", ""), re.I)
    if not match:
        return 0
    try:
        return max(0, float(match.group(1)) - max(0, float(headers.get("age", "0"))))
    except ValueError:
        return 0
