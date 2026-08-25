"""
auth/auto_login.py — Automated Kite Connect login via plain HTTP requests.

No browser. No Playwright. No Chromium. Just requests.Session().

Flow:
  Step 1  GET  /api/login          — seed session cookies
  Step 2  POST /api/login          — submit credentials, receive request_id
  Step 3  POST /api/twofa          — submit TOTP, receive redirect with request_token
  Step 4  Parse request_token from the 302 Location header
  Step 5  kite.generate_session()  — exchange for access_token
  Step 6  Save access_token to auth/access_token.txt

Requirements:
    pip install requests pyotp python-dotenv kiteconnect

.env keys required:
    KITE_API_KEY, KITE_API_SECRET
    ZERODHA_USER_ID, ZERODHA_PASSWORD, ZERODHA_TOTP_SECRET
"""

import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, parse_qsl, urlencode, urlparse, urlsplit, urlunsplit

import pyotp
import requests
from dotenv import load_dotenv
from kiteconnect import KiteConnect

# ── Paths ─────────────────────────────────────────────────────────────────────
_AUTH_DIR   = Path(__file__).parent
_ROOT       = _AUTH_DIR.parent
_TOKEN_FILE = _AUTH_DIR / "access_token.txt"

load_dotenv(_ROOT / ".env")

# ── Constants ─────────────────────────────────────────────────────────────────
BASE_URL = "https://kite.zerodha.com"

# Mimic a real browser so Zerodha doesn't reject the request as a bot.
# X-Kite-Version: 3 is required by the Kite API.
HEADERS = {
    "User-Agent":    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
    "X-Kite-Version": "3",
    "Content-Type":  "application/x-www-form-urlencoded",
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _require_env(key: str) -> str:
    val = os.getenv(key, "").strip()
    if not val:
        print(f"ERROR: {key} is not set in {_ROOT / '.env'}")
        sys.exit(1)
    return val


# Query-parameter names whose VALUES must never reach stdout or a log file.
# This runs unattended twice a day via cron and its output is persisted to
# paper_trading/logs/, so anything printed here is written to disk permanently.
_SENSITIVE_QUERY_KEYS = frozenset({
    "api_key", "api_secret", "request_token", "access_token",
    "sess_id", "session_id", "twofa_value", "password",
})


def _redact_url(url: str, max_len: int = 120) -> str:
    """
    Return `url` with every sensitive query-parameter VALUE replaced by a
    length-preserving placeholder, keeping scheme/host/path so a redirect hop
    is still diagnosable.

    Replaces a bare `location[:80]` truncation that only ever protected these
    values by accident: the cut landed after api_key but before sess_id purely
    because of how long Zerodha's URL happens to be. Verified against a real
    log line — `api_key=7g9gy...` (16 chars) printed IN FULL every run, while
    request_token was clipped to 8 of its 32 characters. Reorder that query
    string upstream and the whole token prints. Truncation is not redaction.
    """
    if not url:
        return ""
    try:
        parts = urlsplit(url)
        if not parts.query:
            return url[:max_len]
        pairs = parse_qsl(parts.query, keep_blank_values=True)
        safe = [
            (k, f"<redacted:{len(v)}>") if k.lower() in _SENSITIVE_QUERY_KEYS else (k, v)
            for k, v in pairs
        ]
        rebuilt = urlunsplit(
            (parts.scheme, parts.netloc, parts.path,
             urlencode(safe, safe="<>:"), parts.fragment)
        )
        return rebuilt[:max_len]
    except Exception:
        # Never fall back to the raw URL — a redaction failure must fail closed.
        return f"{url.split('?', 1)[0]}?<unparseable — redacted>"


# Response headers whose VALUES are credentials. Set-Cookie is the sharp one:
# unlike a TOTP (dead in 30s), a session cookie dumped on a FAILED login can
# still be live.
_SENSITIVE_HEADERS = frozenset({
    "set-cookie", "cookie", "authorization", "proxy-authorization",
    "x-csrf-token", "x-auth-token",
})

# `key=value` or `"key": "value"` for any _SENSITIVE_QUERY_KEYS name, so the
# same vocabulary covers URLs, form bodies and JSON error payloads.
_SENSITIVE_BLOB_RE = re.compile(
    r'("?)(' + "|".join(sorted(_SENSITIVE_QUERY_KEYS)) + r')\1\s*[:=]\s*"?([^"&,\s}]+)"?',
    re.IGNORECASE,
)


def _redact_headers(headers) -> dict:
    """Mask credential-bearing header VALUES, keeping every header NAME."""
    out = {}
    for k, v in dict(headers).items():
        out[k] = f"<redacted:{len(str(v))}>" if k.lower() in _SENSITIVE_HEADERS else v
    return out


def _redact_blob(text: str, limit: int = 600) -> str:
    """
    Mask credential values inside a free-form response body, preserving the
    surrounding text so the actual error message stays readable.
    """
    if not text:
        return ""
    try:
        scrubbed = _SENSITIVE_BLOB_RE.sub(
            lambda m: f"{m.group(1)}{m.group(2)}{m.group(1)}: <redacted:{len(m.group(3))}>",
            text[:limit],
        )
        return scrubbed
    except Exception:
        return "<body suppressed — redaction failed>"


def _die(message: str, resp: requests.Response = None) -> None:
    """
    Print an error with optional response diagnostics, then exit.

    Diagnostics are REDACTED, not omitted. Reconsidered 2026-08-25: the first
    pass left this path raw on the grounds that it only fires on failure. That
    reasoning confused frequency with risk. Failure-path output is MORE likely
    to be copied into a debugging thread than success-path output, so the same
    "logs travel" argument that justified masking the TOTP applies at least as
    strongly here — and Set-Cookie is a worse leak than an expired TOTP.
    Header names, status code and the error message body all survive, so the
    diagnostic value this block exists for is intact.
    """
    print(f"\nERROR: {message}")
    if resp is not None:
        print(f"  HTTP status : {resp.status_code}")
        try:
            print(f"  Headers     : {_redact_headers(resp.headers)}")
        except Exception:
            print("  Headers     : <unavailable — redaction failed>")
        try:
            print(f"  Body        : {_redact_blob(resp.text)}")
        except Exception:
            pass
    sys.exit(1)


# ── Main ──────────────────────────────────────────────────────────────────────

def login() -> None:
    api_key     = _require_env("KITE_API_KEY")
    api_secret  = _require_env("KITE_API_SECRET")
    user_id     = _require_env("ZERODHA_USER_ID")
    password    = _require_env("ZERODHA_PASSWORD")
    totp_secret = _require_env("ZERODHA_TOTP_SECRET")

    kite    = KiteConnect(api_key=api_key)
    session = requests.Session()
    session.headers.update(HEADERS)

    # ── Step 1: Seed session cookies ──────────────────────────────────────────
    # A GET to /api/login initialises the Zerodha session and sets cookies that
    # the subsequent POST calls depend on. Failures here are non-fatal — we log
    # and continue because some server configurations skip this step.
    print("[auto_login] Step 1: Seeding session cookies…")
    try:
        r = session.get(f"{BASE_URL}/api/login", timeout=15)
        print(f"[auto_login] Step 1 OK (status {r.status_code})")
    except requests.RequestException as exc:
        print(f"[auto_login] Step 1 warning: {exc} — continuing anyway")

    # ── Step 2: POST credentials ──────────────────────────────────────────────
    print("[auto_login] Step 2: Submitting user ID and password…")
    try:
        r = session.post(
            f"{BASE_URL}/api/login",
            data={"user_id": user_id, "password": password},
            timeout=15,
        )
    except requests.RequestException as exc:
        _die(f"Network error on /api/login: {exc}")

    try:
        body = r.json()
    except ValueError:
        _die("Non-JSON response from /api/login", r)

    if body.get("status") != "success":
        message = body.get("message", body.get("error", "unknown error"))
        _die(f"Credentials rejected — {message}", r)

    request_id = body.get("data", {}).get("request_id")
    if not request_id:
        _die("request_id missing from /api/login response", r)

    print(f"[auto_login] Step 2 OK — request_id: {request_id[:10]}…")

    # ── Step 3: Generate TOTP and POST two-factor auth ────────────────────────
    # Generate the code as late as possible — right before the POST — so the
    # minimum amount of time elapses between generation and server validation.
    # If the current window has fewer than 5 seconds left, wait for a fresh one:
    # a code with 1-4 seconds remaining could expire while the HTTP request is
    # in flight or while Zerodha's server processes it.
    totp       = pyotp.TOTP(totp_secret)
    seconds_remaining = totp.interval - (int(time.time()) % totp.interval)
    if seconds_remaining < 5:
        print(f"[auto_login] TOTP expires in {seconds_remaining}s — waiting for fresh code")
        time.sleep(seconds_remaining + 1)

    totp_code = totp.now()   # generated immediately before the POST below
    seconds_remaining = totp.interval - (int(time.time()) % totp.interval)
    # The code itself is NOT printed. test_kite.py shows its last 2 digits so a
    # human can compare against their authenticator app while running it by
    # hand; that affordance is worthless here, because this path runs unattended
    # under cron with nobody watching. Any digits printed would be pure downside.
    # seconds_remaining IS kept — it is the diagnostic the surrounding
    # wait-for-fresh-window logic exists to support.
    print(f"[auto_login] Step 3: Submitting TOTP  ({seconds_remaining}s remaining)…")

    try:
        r = session.post(
            f"{BASE_URL}/api/twofa",
            data={
                "user_id":    user_id,
                "request_id": request_id,
                "twofa_value": totp_code,   # 6-digit TOTP code
                "twofa_type":  "totp",
            },
            timeout=15,
            allow_redirects=False,   # capture the 302 Location header directly;
                                     # do NOT follow it — the redirect URL
                                     # (e.g. http://127.0.0.1/) doesn't exist
        )
    except requests.RequestException as exc:
        _die(f"Network error on /api/twofa: {exc}")

    # ── Step 4: Confirm 2FA succeeded ────────────────────────────────────────
    try:
        twofa_body = r.json()
    except ValueError:
        _die("Non-JSON response from /api/twofa", r)

    if twofa_body.get("status") != "success":
        message = twofa_body.get("message", twofa_body.get("error", "unknown error"))
        _die(f"2FA rejected — {message}", r)

    print("[auto_login] Step 4 OK — 2FA accepted, session is now authenticated")

    # ── Step 5: Follow the OAuth connect redirect chain to get request_token ──
    # The session cookies set by /api/twofa authenticate us to kite.zerodha.com.
    # A GET to the Kite Connect OAuth URL (/connect/login?api_key=...) now sees
    # us as logged in and triggers a redirect chain:
    #   /connect/login  →  /connect/finish  →  <your redirect_url>?request_token=XXX
    # We follow each hop manually (allow_redirects=False per request) so we can
    # stop the moment we see request_token in a Location header — before requests
    # tries to connect to 127.0.0.1 which doesn't exist on the server.
    connect_url   = f"https://kite.zerodha.com/connect/login?api_key={api_key}&v=3"
    request_token = None
    url           = connect_url

    print(f"[auto_login] Step 5: Following OAuth redirect chain…")
    for hop in range(1, 6):   # max 5 hops to avoid infinite loops
        try:
            resp = session.get(url, allow_redirects=False, timeout=15)
        except requests.RequestException as exc:
            _die(f"Network error on redirect hop {hop}: {exc}")

        location = resp.headers.get("Location", "")
        print(f"[auto_login]   hop {hop}: {resp.status_code}  →  "
              f"{_redact_url(location) or '(no redirect)'}")

        if "request_token=" in location:
            params        = parse_qs(urlparse(location).query)
            request_token = params.get("request_token", [None])[0]
            break

        if not location:
            # No more redirects — request_token never appeared
            _die(
                f"Redirect chain ended at hop {hop} without a request_token.\n"
                f"  Final URL    : {_redact_url(url)}\n"
                f"  Final status : {resp.status_code}\n"
                f"  Final body   : {_redact_blob(resp.text, limit=400)}",
            )

        # Resolve relative redirects (e.g. /connect/finish) against the base URL
        if location.startswith("/"):
            location = f"https://kite.zerodha.com{location}"
        url = location

    if request_token is None:
        _die("Followed 5 redirects without finding request_token — giving up.")

    print(f"[auto_login] Step 5 OK — request_token captured ({len(request_token)} chars)")

    # ── Step 6: Exchange request_token for access_token ───────────────────────
    print("[auto_login] Step 6: Exchanging request_token for access_token…")
    try:
        data         = kite.generate_session(request_token, api_secret=api_secret)
        access_token = data["access_token"]
    except Exception as exc:
        _die(f"generate_session() failed — {exc}")

    kite.set_access_token(access_token)

    # ── Step 7: Save token (same format as kite_login.py) ─────────────────────
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _TOKEN_FILE.write_text(f"{access_token}\n# saved {timestamp}\n")
    print(f"[auto_login] Step 7 OK — token saved → {_TOKEN_FILE}")

    # ── Verify ────────────────────────────────────────────────────────────────
    try:
        profile = kite.profile()
        print(
            f"[auto_login] Login successful — "
            f"{profile['user_name']} ({profile.get('email', 'no email')})"
        )
    except Exception as exc:
        print(f"[auto_login] Login successful (profile fetch skipped: {exc})")


if __name__ == "__main__":
    login()
