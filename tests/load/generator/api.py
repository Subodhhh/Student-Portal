"""HTTP client of the Institute Portal API as the browser uses it: session cookie + X-CSRF-Token on unsafe methods, staff login with
MFA (the one-time code is typed by the operator, who reads it from the LOAD-TEST identity service's console mail log).

Secrets: the password and the one-time code are requested with getpass, sent once, never stored, logged or written anywhere;
the session cookie and CSRF token live only in memory. Errors carry the API's code and message, never request bodies."""
import logging
import threading
from collections import Counter, defaultdict
import re
import time
from dataclasses import dataclass
from getpass import getpass
from typing import Callable

import httpx

log = logging.getLogger("lt.api")
_SECRETY = re.compile(r"(?i)(password|otp|csrf|cookie|token|secret|api[_-]?key)(\W{1,3})[^\s,;&\"']+")
_KNOWN: set[str] = set()          # exact secret values this process handles (password, one-time code, CSRF token, session id)


def register_secret(value: str | None) -> None:
    if value and len(value) >= 4:
        _KNOWN.add(value)


def redact(text: str) -> str:
    text = str(text)
    for secret in sorted(_KNOWN, key=len, reverse=True):     # exact values first: catches multi-word passwords anywhere
        text = text.replace(secret, "***")
    text = re.sub(r"(?i)postgres(?:ql)?(?:\+\w+)?://\S+", "postgresql://***", text)
    return _SECRETY.sub(lambda m: m.group(1) + m.group(2) + "***", text)


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, detail: dict | None = None):
        code, message = redact(code), redact(message)
        super().__init__(f"{status} {code}: {message}")
        self.status, self.code, self.message, self.detail = status, code, message, detail or {}

    @property
    def uncertain(self) -> bool:
        """The server may have applied the request: no response (timeout, reset), a gateway error (502/503/504: the upstream may
        still be working and commit later), or a 500 (a service can fail after its commit, e.g. while auditing)."""
        return self.status == 0 or self.status >= 500


_ID = re.compile(r"/(?:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|STU\d+|STF\d+)(?=/|$)", re.I)


def endpoint(method: str, path: str) -> str:
    """'POST /branches/{id}/batches': the timing bucket of a request (IDs removed)."""
    return f"{method} {_ID.sub('/{id}', path)}"


class SessionExpired(Exception):
    pass


@dataclass
class Credentials:
    staff_code: str
    password: str


def prompt_credentials(staff_code: str) -> Credentials:
    return Credentials(staff_code, getpass(f"Password for {staff_code} (not stored): "))


def prompt_otp() -> str:
    return getpass("One-time code from `LT_TARGET=neon tests/load/lt.sh logs institute-identity-service` (not stored): ").strip()


class Api:
    RETRY_STATUS = {502, 503, 504}

    def __init__(self, base_url: str, *, transport: httpx.BaseTransport | None = None, timeout: float = 180.0,
                 retries: int = 5, backoff: float = 2.0, sleep: Callable[[float], None] = time.sleep):
        self.client = httpx.Client(base_url=base_url.rstrip("/") + "/api/v1", transport=transport, timeout=timeout,
                                   follow_redirects=False, headers={"Accept": "application/json"})
        self.csrf: str | None = None
        self.retries, self.backoff, self.sleep = retries, backoff, sleep
        self.calls = 0
        self.reauth: Callable[[], None] | None = None
        self._auth_lock = threading.Lock()      # one interactive re-login at a time, shared by every worker
        self._auth_generation = 0               # bumped by each successful re-login
        self._timing_lock = threading.Lock()
        self.timings: dict[str, list[float]] = defaultdict(list)
        self.statuses: dict[str, Counter] = defaultdict(Counter)

    # ---- timings
    def _record(self, method: str, path: str, seconds: float, status: int) -> None:
        key = endpoint(method, path)
        with self._timing_lock:
            self.timings[key].append(seconds)
            self.statuses[key][str(status)] += 1

    def timing_report(self) -> dict:
        def pct(values, p):
            v = sorted(values)
            return round(v[min(len(v) - 1, int(round(p / 100 * (len(v) - 1))))], 3)
        with self._timing_lock:
            return {k: {"count": len(v), "p50_s": pct(v, 50), "p95_s": pct(v, 95), "max_s": round(max(v), 3),
                        "statuses": dict(self.statuses[k])} for k, v in sorted(self.timings.items())}

    def _relogin(self, generation_seen: int) -> None:
        """Re-authenticate once for every worker that saw the same expired session: the first one prompts, the others wait for it
        and reuse the new session (no second MFA prompt, no burned MFA rate limit)."""
        with self._auth_lock:
            if self._auth_generation == generation_seen:
                self.reauth()
                self._auth_generation += 1

    # ---- authentication
    def login(self, staff_code: str, password_source: Callable[[], str], otp_source: Callable[[], str]) -> dict:
        password = password_source()
        register_secret(password)
        r = self._send("POST", "/auth/login", json={"staff_code": staff_code, "password": password}, auth=False)
        del password
        if r.get("status") == "MFA_REQUIRED":
            otp = otp_source()
            register_secret(otp)
            r = self._send("POST", "/auth/mfa/verify", json={"challenge_id": r["challenge_id"], "otp": otp}, auth=False)
        if r.get("status") != "AUTHENTICATED":
            raise ApiError(401, "LOGIN_FAILED", "login did not complete")
        me = r["me"]
        self.csrf = me.get("csrf_token")
        register_secret(self.csrf)
        for cookie in self.client.cookies.jar:
            register_secret(cookie.value)
        if "SUPER_ADMIN" != me.get("primary_role"):
            raise ApiError(403, "NOT_SUPER_ADMIN", "the loader must run as a Super Admin of the load-test environment")
        return {"staff_code": me["staff_code"], "primary_role": me["primary_role"]}

    # ---- requests
    def get(self, path: str, **params) -> dict | list:
        return self._send("GET", path, params={k: v for k, v in params.items() if v is not None})

    def post(self, path: str, json: dict | None = None, **kw) -> dict:
        """kw `retry=False` for creates whose duplicates the database cannot reject (branches, batches, enrollments, paper uploads):
        an uncertain outcome (no response, 502/503/504, 500) is raised at once, so the caller checks the server before anything is
        sent again, instead of blindly re-sending a create that may already have applied or may still be running."""
        return self._send("POST", path, json=json, **kw)

    def put(self, path: str, json: dict | None = None) -> dict:
        return self._send("PUT", path, json=json)

    def all_pages(self, path: str, **params) -> list[dict]:
        out, page = [], 1
        while True:
            res = self.get(path, page=page, page_size=100, **params)
            out += res["items"]
            if len(out) >= res.get("total", 0) or not res["items"]:
                return out
            page += 1

    def _send(self, method: str, path: str, *, auth: bool = True, json=None, params=None, files=None, data=None, headers=None,
              retry: bool = True):
        hdrs = dict(headers or {})
        unsafe = method not in {"GET", "HEAD", "OPTIONS"}
        if auth and unsafe and self.csrf:
            hdrs["X-CSRF-Token"] = self.csrf
        reauthed = False
        for attempt in range(1, self.retries + 1):
            self.calls += 1
            generation = self._auth_generation
            if auth and unsafe and self.csrf:
                hdrs["X-CSRF-Token"] = self.csrf            # the current session's token (a re-login replaces it)
            started = time.monotonic()
            try:
                resp = self.client.request(method, path, json=json, params=params, files=files, data=data, headers=hdrs)
            except httpx.TransportError as e:                      # includes timeouts: the request may or may not have applied
                self._record(method, path, time.monotonic() - started, 0)
                if attempt == self.retries or not retry:
                    raise ApiError(0, "TRANSPORT", f"{type(e).__name__} after {attempt} attempt(s)") from None
                log.warning("%s %s: %s (attempt %d), retrying", method, path, type(e).__name__, attempt)
                self.sleep(self.backoff * 2 ** (attempt - 1))
                continue
            self._record(method, path, time.monotonic() - started, resp.status_code)
            if resp.status_code in self.RETRY_STATUS and attempt < self.retries and retry:
                log.warning("%s %s: HTTP %d (attempt %d), retrying", method, path, resp.status_code, attempt)
                self.sleep(self.backoff * 2 ** (attempt - 1))
                continue
            if resp.status_code == 429 and attempt < self.retries:   # refused before any work: safe to repeat even for creates
                log.warning("%s %s: rate limited, waiting 60 s", method, path)
                self.sleep(60)
                continue
            if resp.status_code == 401 and auth:
                if not self.reauth or reauthed:
                    raise SessionExpired("session expired")
                self._relogin(generation)                       # one interactive re-login across all workers
                reauthed = True
                continue
            return self._decode(resp)
        raise ApiError(0, "RETRIES_EXHAUSTED", f"{method} {path}")

    @staticmethod
    def _decode(resp: httpx.Response):
        if resp.status_code == 204:
            return {}
        try:
            body = resp.json()
        except ValueError:
            body = {}
        if resp.is_success:
            return body
        detail = body.get("detail") if isinstance(body, dict) else None
        if isinstance(detail, dict):
            raise ApiError(resp.status_code, detail.get("code", "ERROR"), detail.get("message", ""), detail)
        if isinstance(detail, list):                               # FastAPI validation errors
            raise ApiError(resp.status_code, "VALIDATION", "; ".join(str(d.get("msg")) for d in detail[:5]))
        raise ApiError(resp.status_code, "ERROR", str(detail or resp.reason_phrase))

    def close(self):
        self.client.close()
