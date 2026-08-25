"""
auth/test_auto_login_redaction.py — secrets must not reach stdout or logs.

auth/auto_login.py runs unattended twice a day under cron and its stdout is
persisted to paper_trading/logs/. A survey of the server on 2026-08-25 found a
live TOTP in 108 of 173 log files and the KITE_API_KEY in full in every one.

Two SEPARATE claims are tested here, deliberately not conflated:

  1. FUNCTIONAL — the real, full TOTP still reaches Kite's /api/twofa payload.
     Redaction must change what is PRINTED, never what is SENT.
  2. LOG CLEANLINESS — no 6-digit code and no credential value appears in
     captured stdout.

A test that only checked (2) would pass if login were broken outright; one that
only checked (1) would pass if the log were still full of secrets.

No network: requests.Session is replaced wholesale.
"""
import io
import re
import sys
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from auth.auto_login import (
    _redact_url, _redact_headers, _redact_blob,
    _SENSITIVE_QUERY_KEYS, _SENSITIVE_HEADERS,
)

_SIX_DIGITS = re.compile(r"\b\d{6}\b")

# The real shapes, taken from an actual server log line on 2026-08-25.
_HOP1 = ("https://kite.zerodha.com/connect/finish"
         "?api_key=7g9gybqj3woxntss&sess_id=PrA7GOVlongersessionvalue")
_HOP2 = ("https://127.0.0.1/?action=login&type=login&status=success"
         "&request_token=P0nx9QCUabcdefghijklmnopqrstuvwx")


# =============================================================================
# 1. LOG CLEANLINESS — _redact_url
# =============================================================================

def test_api_key_value_is_redacted():
    """The exact leak found on the server: api_key printed in full, every run."""
    out = _redact_url(_HOP1)
    assert "7g9gybqj3woxntss" not in out, f"api_key still present: {out}"
    assert "api_key" in out, "the parameter NAME should survive — it is diagnostic"


def test_request_token_value_is_redacted():
    out = _redact_url(_HOP2)
    assert "P0nx9QCUabcdefghijklmnopqrstuvwx" not in out
    assert "P0nx9QCU" not in out, (
        "the old [:80] truncation leaked the first 8 chars; redaction must not"
    )


def test_session_id_value_is_redacted():
    out = _redact_url(_HOP1)
    assert "PrA7GOVlongersessionvalue" not in out


@pytest.mark.parametrize("key", sorted(_SENSITIVE_QUERY_KEYS))
def test_every_declared_sensitive_key_is_actually_redacted(key):
    """The frozenset is the contract; prove each member is honoured."""
    out = _redact_url(f"https://example.com/x?{key}=SUPERSECRETVALUE123")
    assert "SUPERSECRETVALUE123" not in out, f"{key} not redacted: {out}"


def test_non_sensitive_params_survive_for_diagnosability():
    """Redaction must not destroy the debugging value of the hop line."""
    out = _redact_url(_HOP2)
    for keep in ("action", "login", "status", "success", "127.0.0.1"):
        assert keep in out, f"lost diagnostic detail {keep!r}: {out}"


def test_scheme_host_and_path_survive():
    out = _redact_url(_HOP1)
    assert out.startswith("https://kite.zerodha.com/connect/finish")


def test_redaction_fails_closed_on_unparseable_input():
    """A redaction failure must never fall back to emitting the raw URL."""
    with patch("auth.auto_login.urlsplit", side_effect=ValueError("boom")):
        out = _redact_url("https://x.com/p?api_key=SECRETVALUE")
        assert "SECRETVALUE" not in out
        assert "redacted" in out


def test_empty_and_no_query_urls_are_safe():
    assert _redact_url("") == ""
    assert _redact_url("https://example.com/path") == "https://example.com/path"


# =============================================================================
# 2. FUNCTIONAL — the real TOTP still reaches the API
# =============================================================================

def _fake_session(captured: dict):
    """A Session stand-in that records the /api/twofa payload and then stops."""
    sess = MagicMock()
    sess.get.return_value = MagicMock(status_code=405, headers={}, text="")

    def _post(url, data=None, **kw):
        if url.endswith("/api/twofa"):
            captured["twofa"] = dict(data or {})
            raise RuntimeError("__stop_after_twofa__")
        resp = MagicMock(status_code=200, headers={}, text="{}")
        # /api/login requires status == "success" plus a request_id, or
        # auto_login bails at Step 2 and never reaches the code under test.
        resp.json.return_value = {
            "status": "success",
            "data": {"request_id": "REQ1234567890"},
        }
        return resp

    sess.post.side_effect = _post
    return sess


def _run_login_until_twofa():
    """Drive auto_login far enough to capture the 2FA payload. Returns (payload, stdout)."""
    import auth.auto_login as al
    captured: dict = {}
    buf = io.StringIO()
    env = {
        "ZERODHA_USER_ID": "AB1234", "ZERODHA_PASSWORD": "hunter2",
        "ZERODHA_TOTP_SECRET": "JBSWY3DPEHPK3PXP",
        "KITE_API_KEY": "7g9gybqj3woxntss", "KITE_API_SECRET": "s3cr3t",
    }
    with patch.dict("os.environ", env, clear=False), \
         patch.object(al.requests, "Session", return_value=_fake_session(captured)), \
         patch.object(al, "KiteConnect", MagicMock()):
        with redirect_stdout(buf):
            try:
                al.login()
            except (RuntimeError, SystemExit):
                pass
    return captured, buf.getvalue()


def test_real_full_totp_still_reaches_the_api_payload():
    """
    FUNCTIONAL claim. The value POSTed as twofa_value must be the genuine
    6-digit code from the shared secret — unmasked, unmodified.
    """
    import pyotp
    captured, _ = _run_login_until_twofa()
    assert "twofa" in captured, "never reached /api/twofa — harness broken, not a real pass"
    sent = captured["twofa"]["twofa_value"]
    assert _SIX_DIGITS.fullmatch(sent), f"payload is not a bare 6-digit code: {sent!r}"
    assert sent == pyotp.TOTP("JBSWY3DPEHPK3PXP").now(), (
        "the code SENT to Kite was altered — redaction must only change what is PRINTED"
    )


def test_no_six_digit_code_appears_in_captured_stdout():
    """
    LOG-CLEANLINESS claim, checked over the SAME run that proved the functional
    claim above — so it cannot pass by the login simply not happening.
    """
    captured, out = _run_login_until_twofa()
    assert "twofa" in captured, "login did not reach 2FA; a clean log here proves nothing"
    sent = captured["twofa"]["twofa_value"]
    assert sent not in out, f"the live TOTP {sent} was printed to stdout"
    assert not _SIX_DIGITS.search(out), (
        f"a 6-digit sequence appears in stdout: {_SIX_DIGITS.findall(out)}"
    )


def test_no_credential_values_appear_in_captured_stdout():
    """Neither the API key nor the password may be printed."""
    _, out = _run_login_until_twofa()
    for secret in ("7g9gybqj3woxntss", "hunter2", "s3cr3t", "JBSWY3DPEHPK3PXP"):
        assert secret not in out, f"{secret!r} leaked to stdout"


def test_seconds_remaining_is_still_reported():
    """The diagnostic that justified the print in the first place must survive."""
    _, out = _run_login_until_twofa()
    assert "Submitting TOTP" in out
    assert "remaining" in out


# =============================================================================
# 3. FAILURE-PATH diagnostics — _die()'s header and body dumps
# =============================================================================
# Reconsidered on review: "it only fires on failure" describes frequency, not
# risk. Failure output is MORE likely to be pasted into a debugging thread than
# success output, and a Set-Cookie dumped on a failed login can still be a LIVE
# session — strictly worse than an expired TOTP.


def test_set_cookie_value_is_redacted_but_header_name_survives():
    out = _redact_headers({"Set-Cookie": "kf_session=AbC123Live; Path=/", "Server": "nginx"})
    assert "AbC123Live" not in str(out)
    assert "Set-Cookie" in out, "the header NAME is diagnostic and must survive"
    assert out["Server"] == "nginx", "non-sensitive headers must pass through untouched"


@pytest.mark.parametrize("hdr", sorted(_SENSITIVE_HEADERS))
def test_every_declared_sensitive_header_is_redacted(hdr):
    out = _redact_headers({hdr: "SUPERSECRETHEADERVALUE"})
    assert "SUPERSECRETHEADERVALUE" not in str(out), f"{hdr} not redacted: {out}"


def test_sensitive_header_match_is_case_insensitive():
    out = _redact_headers({"SET-COOKIE": "LiveSessionValue"})
    assert "LiveSessionValue" not in str(out)


def test_body_redaction_keeps_the_error_message():
    """The body dump exists to show WHY auth failed — that must survive."""
    body = ('{"status":"error","message":"Invalid TOTP",'
            '"access_token":"xLiveTokenAbc123","api_key":"7g9gybqj3woxntss"}')
    out = _redact_blob(body)
    assert "Invalid TOTP" in out, "lost the actual diagnostic"
    assert "xLiveTokenAbc123" not in out
    assert "7g9gybqj3woxntss" not in out


def test_body_redaction_handles_form_encoded_too():
    out = _redact_blob("status=error&request_token=LiveTok123&msg=nope")
    assert "LiveTok123" not in out
    assert "nope" in out


def test_body_redaction_fails_closed():
    with patch("auth.auto_login._SENSITIVE_BLOB_RE") as rx:
        rx.sub.side_effect = ValueError("boom")
        out = _redact_blob('{"api_key":"SECRET"}')
        assert "SECRET" not in out
        assert "suppressed" in out


def test_body_redaction_empty_input_is_safe():
    assert _redact_blob("") == ""
    assert _redact_blob(None) == ""
