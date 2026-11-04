import pytest
from fastapi import HTTPException
from starlette.requests import Request
from backend.config import Settings, settings
from backend.security import origin_check


@pytest.mark.parametrize('origin', ['http://localhost:5173', 'http://127.0.0.1:5173'])
def test_loopback_origin_works_with_cors_and_mutations(client, monkeypatch, origin):
    monkeypatch.setattr(settings, 'app_url', 'http://localhost:5173')
    response = client.options('/api/auth/google', headers={
        'Origin': origin, 'Access-Control-Request-Method': 'POST',
        'Access-Control-Request-Headers': 'Content-Type',
    })
    assert response.status_code == 200
    assert response.headers['access-control-allow-origin'] == origin
    origin_check(Request({'type': 'http', 'headers': [(b'origin', origin.encode())]}))


@pytest.mark.parametrize('origin', ['http://localhost:5174', 'http://127.0.0.2:5173', 'https://evil.example', 'null'])
def test_unlisted_origins_rejected(client, origin):
    response = client.options('/api/auth/google', headers={
        'Origin': origin, 'Access-Control-Request-Method': 'POST',
    })
    assert response.status_code == 400
    with pytest.raises(HTTPException) as error:
        origin_check(Request({'type': 'http', 'headers': [(b'origin', origin.encode())]}))
    assert error.value.status_code == 403


def test_deployed_origin_stays_exact():
    configured = Settings(app_url='https://sumx.example', cookie_secure=True)
    assert configured.frontend_origins == ['https://sumx.example']
