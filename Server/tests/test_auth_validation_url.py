"""Credentials must only be sent to an explicitly configured HTTPS endpoint."""

import httpx
import pytest

from services.api_key_service import ApiKeyService


@pytest.fixture(autouse=True)
def reset_service(monkeypatch):
    monkeypatch.setattr(ApiKeyService, "_instance", None)


@pytest.mark.parametrize(
    "url",
    [
        "http://auth.example.com/validate",
        "http://127.0.0.1/validate",
        "//auth.example.com/validate",
        "/validate",
        "file:///validate",
        "https:///validate",
        "https://user:password@auth.example.com/validate",
        "https://@auth.example.com/validate",
        "https://auth.example.com:bad/validate",
        "https://auth.example.com:0/validate",
        "https://auth.example.com/#fragment",
        " https://auth.example.com/validate",
        "https://auth.example.com/\nvalidate",
    ],
)
def test_rejects_unsafe_validation_url_before_service_initialization(url):
    with pytest.raises(ValueError, match="HTTPS"):
        ApiKeyService(url, service_token="test-service-secret")
    assert not ApiKeyService.is_initialized()


@pytest.mark.asyncio
async def test_https_redirect_does_not_forward_credentials(monkeypatch):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(307, headers={"Location": "http://other.example.com/validate"})

    client_type = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: client_type(transport=httpx.MockTransport(respond), **kwargs),
    )
    service = ApiKeyService("https://auth.example.com/validate")

    result = await service.validate("test-client-key")

    assert not result.valid
    assert len(requests) == 1
    assert requests[0].url == "https://auth.example.com/validate"


@pytest.mark.parametrize(
    "url",
    [
        "https://auth.example.com/validate",
        "https://localhost:8443/validate",
        "https://[::1]:8443/validate",
    ],
)
def test_accepts_absolute_https_validation_urls(url):
    assert ApiKeyService(url).is_initialized()
