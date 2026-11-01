from google.auth.transport.requests import Request
from google.oauth2 import id_token
from .config import settings


def verify_google_token(credential: str):
    # google-auth validates Google's signature, issuer, audience and token time claims.
    transport = Request()

    def bounded_request(*args, **kwargs):
        kwargs["timeout"] = 10
        return transport(*args, **kwargs)

    try:
        return id_token.verify_oauth2_token(credential, bounded_request, audience=settings.google_client_id)
    finally:
        transport.session.close()
