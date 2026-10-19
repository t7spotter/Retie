"""Country analytics: click events record a country, never an IP address."""

import pytest

from analytics import queries
from analytics.models import ClickEvent
from links import geo
from tests.conftest import make_link

pytestmark = pytest.mark.django_db


class FakeReader:
    def __init__(self, mapping):
        self.mapping = mapping

    def get(self, ip):
        return self.mapping.get(ip)


_STUB_READER = FakeReader({
    "8.8.8.8": {"country": {"iso_code": "NL"}},
    "8.8.4.4": {"country": {"iso_code": "DE"}},
    "9.9.9.9": {"country": {}},  # record without a country
})


@pytest.fixture
def fake_geodb(settings, monkeypatch):
    """Point GEOIP_DB at a stub database and reset the reader cache."""
    settings.GEOIP_DB = "/fake/GeoLite2-Country.mmdb"

    def open_db_stub(_path):
        return _STUB_READER

    monkeypatch.setattr("maxminddb.open_database", open_db_stub)
    yield
    geo._reader_cache.reader = None


def test_private_and_reserved_ips_never_resolve():
    for ip in ("127.0.0.1", "10.0.0.3", "192.168.1.8", "172.16.5.9", "::1", "169.254.2.2"):
        assert geo.country_for(ip) == ""


def test_no_geodb_configured_means_no_country(settings):
    settings.GEOIP_DB = None
    assert geo.country_for("8.8.8.8") == ""


def test_lookup_resolves_country_codes(fake_geodb):
    assert geo.country_for("8.8.8.8") == "NL"
    assert geo.country_for("8.8.4.4") == "DE"
    assert geo.country_for("9.9.9.9") == ""
    assert geo.country_for("1.1.1.1") == ""  # unknown record


def test_click_records_country_but_never_the_ip(client, user, fake_geodb):
    make_link(user, code="geolink", destination="https://example.com/geo")
    response = client.get("/geolink", REMOTE_ADDR="8.8.8.8")
    assert response.status_code == 302
    click = ClickEvent.objects.get(link__code="geolink")
    assert click.country == "NL"
    # No IP-shaped string is persisted on the event.
    assert "8.8.8.8" not in str(vars(click))


def test_click_from_private_address_records_blank(client, user, fake_geodb):
    make_link(user, code="localclick", destination="https://example.com/l")
    client.get("/localclick", REMOTE_ADDR="127.0.0.1")
    click = ClickEvent.objects.get(link__code="localclick")
    assert click.country == ""


def test_country_breakdown_counts_and_excludes_blank(user):
    link = make_link(user, code="breakdn")
    ClickEvent.objects.create(link=link, country="NL")
    ClickEvent.objects.create(link=link, country="NL")
    ClickEvent.objects.create(link=link, country="DE")
    ClickEvent.objects.create(link=link, country="")  # unknown: excluded

    rows = queries.country_breakdown(user, link=link)
    assert {row["country"]: row["count"] for row in rows} == {"NL": 2, "DE": 1}


def test_api_analytics_includes_countries(client_user, user):
    from rest_framework.authtoken.models import Token
    from rest_framework.test import APIClient

    make_link(user, code="apigeo")
    api = APIClient()
    api.credentials(HTTP_AUTHORIZATION=f"Token {Token.objects.create(user=user).key}")
    response = api.get("/api/v1/links/apigeo/analytics/")
    assert "countries" in response.json()


def test_analytics_page_shows_countries_card(client_user, user):
    make_link(user, code="pagegeo")
    response = client_user.get("/dashboard/links/pagegeo/analytics/")
    assert b"Countries" in response.content
    assert b"No country data yet" in response.content
