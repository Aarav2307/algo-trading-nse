"""Tests for the Kite API request timeout configuration."""
from unittest.mock import patch, MagicMock
from data.kite_fetcher import _load_kite, KITE_REQUEST_TIMEOUT_SECONDS


def test_load_kite_applies_timeout(monkeypatch, tmp_path):
    """_load_kite must construct KiteConnect with the configured timeout."""
    monkeypatch.setenv("KITE_API_KEY", "fake_key_for_test")
    token_file = tmp_path / "access_token.txt"
    token_file.write_text("fake_token_value\n")

    with patch("data.kite_fetcher._TOKEN_FILE", token_file), \
         patch("data.kite_fetcher.KiteConnect") as MockKiteConnect:
        mock_instance = MagicMock()
        MockKiteConnect.return_value = mock_instance

        result = _load_kite()

        _, kwargs = MockKiteConnect.call_args
        assert kwargs.get("timeout") == KITE_REQUEST_TIMEOUT_SECONDS
        mock_instance.set_access_token.assert_called_once_with("fake_token_value")
        assert result is mock_instance


def test_load_kite_missing_api_key_raises(monkeypatch):
    """Existing behavior must be unchanged: missing API key still raises EnvironmentError."""
    monkeypatch.delenv("KITE_API_KEY", raising=False)
    try:
        _load_kite()
        assert False, "expected EnvironmentError"
    except EnvironmentError:
        pass


def test_load_kite_missing_token_file_raises(monkeypatch, tmp_path):
    """Existing behavior must be unchanged: missing token file still raises FileNotFoundError."""
    monkeypatch.setenv("KITE_API_KEY", "fake_key_for_test")
    nonexistent = tmp_path / "does_not_exist.txt"
    with patch("data.kite_fetcher._TOKEN_FILE", nonexistent):
        try:
            _load_kite()
            assert False, "expected FileNotFoundError"
        except FileNotFoundError:
            pass


def test_load_kite_empty_token_file_raises(monkeypatch, tmp_path):
    """Existing behavior must be unchanged: empty token file still raises ValueError."""
    monkeypatch.setenv("KITE_API_KEY", "fake_key_for_test")
    empty_token_file = tmp_path / "access_token.txt"
    empty_token_file.write_text("")
    with patch("data.kite_fetcher._TOKEN_FILE", empty_token_file):
        try:
            _load_kite()
            assert False, "expected ValueError"
        except ValueError:
            pass
