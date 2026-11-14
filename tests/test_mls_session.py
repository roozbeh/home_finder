from types import SimpleNamespace

import pytest

from data_service.mls_service.mls_session import (
    MLSAuthenticationError,
    assert_authenticated_response,
)


def response(status=200, url="https://bridge.connectmls.com/api/search/listing/list", **kwargs):
    return SimpleNamespace(
        status_code=status,
        url=url,
        is_redirect=kwargs.get("is_redirect", False),
        headers=kwargs.get("headers", {}),
        text=kwargs.get("text", ""),
    )


def test_auth_response_rejects_unauthorized_status():
    with pytest.raises(MLSAuthenticationError, match="HTTP 401"):
        assert_authenticated_response(response(401))


def test_auth_response_rejects_login_redirect():
    with pytest.raises(MLSAuthenticationError, match="login"):
        assert_authenticated_response(response(
            302,
            is_redirect=True,
            headers={"Location": "https://maxebrdi.clareityiam.net/idp/login"},
        ))


def test_auth_response_rejects_login_html():
    with pytest.raises(MLSAuthenticationError, match="login page"):
        assert_authenticated_response(response(
            headers={"Content-Type": "text/html"},
            text='<input id="username">',
        ))


def test_auth_response_does_not_retire_session_for_rate_limit():
    assert_authenticated_response(response(429))
