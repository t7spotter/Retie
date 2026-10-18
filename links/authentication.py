"""API-key authentication with two accepted Authorization schemes.

Same token store as DRF's TokenAuthentication (manage.py drf_create_token,
POST /api/v1/token/), but the Authorization header accepts both spellings:

    Authorization: Token <key>     (DRF convention)
    Authorization: Bearer <key>    (what the docs portal and generic HTTP
                                    clients send)

This is the only token authenticator in settings — one class, one schema
identity, one stored token, two header spellings.
"""

from rest_framework import HTTP_HEADER_ENCODING, authentication


class MultiSchemeTokenAuthentication(authentication.TokenAuthentication):
    """Token auth under either the `Token` or `Bearer` header scheme."""

    def authenticate(self, request):
        header = authentication.get_authorization_header(request).split()
        if not header or len(header) != 2:
            return None
        if header[0].lower() not in (b"token", b"bearer"):
            return None
        try:
            key = header[1].decode(HTTP_HEADER_ENCODING)
        except UnicodeDecodeError:
            return None
        return self.authenticate_credentials(key)
