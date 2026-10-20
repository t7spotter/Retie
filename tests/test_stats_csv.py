"""Per-link analytics CSV export."""

import csv
import io

import pytest
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient
from tests.conftest import make_link

from analytics.models import ClickEvent

pytestmark = pytest.mark.django_db


def test_csv_contains_daily_and_breakdowns(client_user, user):
    link = make_link(user, code="csvme", title="Exported!")
    ClickEvent.objects.create(link=link, device="desktop", country="US", referrer="https://r.example/")
    ClickEvent.objects.create(link=link, device="mobile", country="US")

    response = client_user.get("/dashboard/links/csvme/analytics.csv")
    assert response.status_code == 200
    assert response["Content-Type"] == "text/csv"
    assert "attachment" in response["Content-Disposition"]

    rows = list(csv.reader(io.StringIO(response.content.decode())))
    kinds = {row[0] for row in rows[1:]}
    assert {"meta", "daily", "device", "country"} <= kinds
    # 30 daily rows, non-bot totals consistent.
    daily_totals = sum(int(row[2]) for row in rows[1:] if row[0] == "daily")
    assert daily_totals == 2
    total_row = [r for r in rows[1:] if r[:2] == ["meta", "total_clicks_non_bot"]]
    assert total_row and total_row[0][2] == "2"
    country_rows = [r for r in rows[1:] if r[0] == "country"]
    assert {"US": "2"} == {r[1]: r[2] for r in country_rows}


def test_csv_is_owner_only(client_user, user, other_user):
    other_user.links.create(code="theirs", destination="https://x.example")

    from django.test import Client

    foreign = Client()
    foreign.login(username="bob", password="sticky-notes-99")
    assert foreign.get("/dashboard/links/theirs/analytics.csv").status_code == 200
    assert client_user.get("/dashboard/links/theirs/analytics.csv").status_code == 404


def test_csv_404_for_trashed(client_user, user):
    make_link(user, code="gonesv").delete()
    assert client_user.get("/dashboard/links/gonesv/analytics.csv").status_code == 404


def test_csv_requires_login(client, user):
    make_link(user, code="anoncsv")
    assert client.get("/dashboard/links/anoncsv/analytics.csv").status_code == 302


def test_button_on_analytics_page(client_user, user):
    make_link(user, code="btncsv")
    body = client_user.get("/dashboard/links/btncsv/analytics/").content.decode()
    assert "/dashboard/links/btncsv/analytics.csv" in body
