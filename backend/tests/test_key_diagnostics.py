import httpx
import pytest
from backend.provider import OpenRouter, ProviderError


@pytest.mark.parametrize("field", ["is_management_key", "is_provisioning_key"])
def test_wrong_key_type_is_reported_without_exposing_credentials(field):
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path.endswith("/key"):
            return httpx.Response(200, json={"data": {field: True}})
        return httpx.Response(401, json={"error": {"message": "User not found."}})

    provider = OpenRouter(httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(ProviderError, match="regular inference API key") as exc:
        provider.request("POST", "/audio/transcriptions", json={})
    assert "test-not-a-real-key" not in str(exc.value)
    assert len(calls) == 2


def test_failed_key_status_check_preserves_the_original_authentication_error():
    provider = OpenRouter(
        httpx.Client(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(401, json={"error": {"message": "Unauthorized"}})
            )
        )
    )
    with pytest.raises(ProviderError, match="HTTP 401"):
        provider.request("POST", "/chat/completions", json={})
