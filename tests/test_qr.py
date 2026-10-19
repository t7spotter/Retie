"""QR code endpoint: /{code}.qr.svg renders an SVG of the short URL."""

import pytest

from tests.conftest import make_link

pytestmark = pytest.mark.django_db


def test_qr_returns_svg(client, user, settings):
    import io

    import segno

    make_link(user, code="qrtst")
    response = client.get("/qrtst.qr.svg")
    assert response.status_code == 200
    assert response["Content-Type"] == "image/svg+xml"
    # Identical bytes to the QR of the short URL (not the destination).
    expected = io.BytesIO()
    segno.make(f"{settings.SITE_URL}/qrtst", error="m").save(
        expected, kind="svg", scale=8, border=2, dark="#0e353c", light="#ffffff"
    )
    assert response.content == expected.getvalue()


def test_qr_unknown_code_404s(client):
    assert client.get("/nosuch.qr.svg").status_code == 404

def test_short_url_still_redirects_after_qr_route(client, user):
    """The suffix route must not shadow the catch-all redirect."""
    make_link(user, code="qrtst2", destination="https://example.com/target")
    response = client.get("/qrtst2")
    assert response.status_code == 302
    assert response.url == "https://example.com/target"


def test_qr_is_rate_limited_per_ip(client, user):
    """Same IP after the limit gets a 403 (documented rate_limited behavior)."""
    from django.core.cache import cache
    from django.core.exceptions import PermissionDenied
    from django.test import RequestFactory

    from links import views

    cache.clear()
    make_link(user, code="qrrate")
    view = views.rate_limited("qrtest", limit=3, window_seconds=60)(views.link_qr.__wrapped__)
    request = RequestFactory().request(REMOTE_ADDR="10.9.8.7")
    for _ in range(3):
        view(request, code="qrrate")
    with pytest.raises(PermissionDenied):
        view(request, code="qrrate")
