"""A deliberately slow, well-behaved HTTP client.

Rules enforced here, not left to callers:
  * one honest User-Agent with a contact URL
  * a minimum delay between requests to the same host
  * HTTP 429 honours Retry-After, then backs off exponentially, then gives up
  * conditional requests when the host gave us a validator last time
  * no parallelism per host
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any
from urllib.parse import urlsplit

import requests

from .config import (
    DATA_DIR,
    DEFAULT_CONTACT_URL,
    MAX_RATE_LIMIT_RETRIES,
    MIN_DELAY_SECONDS,
    RATE_LIMIT_BASE_BACKOFF,
    REQUEST_TIMEOUT,
    USER_AGENT_TEMPLATE,
)

log = logging.getLogger(__name__)

CACHE_PATH = DATA_DIR / "http_validators.json"


class HostAbandoned(RuntimeError):
    """Raised when a host rate-limited us past the retry budget."""


class PoliteSession:
    def __init__(self, min_delay: float = MIN_DELAY_SECONDS, dry_run: bool = False):
        contact = os.environ.get("POKEARB_CONTACT_URL", DEFAULT_CONTACT_URL)
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": USER_AGENT_TEMPLATE.format(contact=contact),
                "Accept-Encoding": "gzip, deflate",
            }
        )
        self.min_delay = min_delay
        self.dry_run = dry_run
        self._last_request: dict[str, float] = {}
        self._host_delay: dict[str, float] = {}
        self._validators: dict[str, dict[str, str]] = self._load_validators()

    # -- validator cache ----------------------------------------------------

    @staticmethod
    def _load_validators() -> dict[str, dict[str, str]]:
        if CACHE_PATH.exists():
            try:
                return json.loads(CACHE_PATH.read_text("utf-8"))
            except (json.JSONDecodeError, OSError):
                log.warning("Kunne ikke laese %s, starter forfra", CACHE_PATH)
        return {}

    def save_validators(self) -> None:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(json.dumps(self._validators, indent=1, sort_keys=True), "utf-8")

    # -- pacing -------------------------------------------------------------

    def set_host_delay(self, url: str, delay: float) -> None:
        self._host_delay[urlsplit(url).netloc] = max(delay, self.min_delay)

    def _delay_for(self, host: str) -> float:
        return self._host_delay.get(host, self.min_delay)

    def _wait(self, host: str) -> None:
        delay = self._delay_for(host)
        last = self._last_request.get(host)
        if last is not None:
            elapsed = time.monotonic() - last
            if elapsed < delay:
                time.sleep(delay - elapsed)
        self._last_request[host] = time.monotonic()

    # -- requests -----------------------------------------------------------

    def get(self, url: str, *, conditional: bool = False, **kwargs) -> requests.Response:
        host = urlsplit(url).netloc
        headers = dict(kwargs.pop("headers", {}))
        if conditional and url in self._validators:
            headers.update(self._validators[url])

        attempt = 0
        while True:
            self._wait(host)
            log.debug("GET %s", url)
            response = self.session.get(
                url, headers=headers, timeout=REQUEST_TIMEOUT, **kwargs
            )

            if response.status_code == 429:
                attempt += 1
                if attempt > MAX_RATE_LIMIT_RETRIES:
                    raise HostAbandoned(
                        f"{host} svarede 429 {attempt} gange, opgiver vaerten i dag"
                    )
                wait = self._retry_after(response, attempt)
                log.warning("429 fra %s, venter %ss (forsoeg %d)", host, wait, attempt)
                time.sleep(wait)
                continue

            if response.status_code in (502, 503, 504):
                attempt += 1
                if attempt > MAX_RATE_LIMIT_RETRIES:
                    response.raise_for_status()
                wait = RATE_LIMIT_BASE_BACKOFF * attempt
                log.warning("%s fra %s, venter %ss", response.status_code, host, wait)
                time.sleep(wait)
                continue

            break

        if response.status_code == 200:
            validator = {}
            if etag := response.headers.get("ETag"):
                validator["If-None-Match"] = etag
            if modified := response.headers.get("Last-Modified"):
                validator["If-Modified-Since"] = modified
            if validator:
                self._validators[url] = validator

        return response

    @staticmethod
    def _retry_after(response: requests.Response, attempt: int) -> float:
        raw = response.headers.get("Retry-After")
        if raw:
            try:
                return max(1.0, float(raw))
            except ValueError:
                pass  # HTTP-date form; fall through to the backoff below
        return float(RATE_LIMIT_BASE_BACKOFF * (2 ** (attempt - 1)))

    def get_json(self, url: str, **kwargs) -> Any:
        response = self.get(url, **kwargs)
        response.raise_for_status()
        return response.json()
