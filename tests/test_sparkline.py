"""Row sparklines: per-link 14-day click micro-bars (non-bot only)."""

import json

import pytest
from datetime import timedelta

from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient
from tests.conftest import make_link

from analytics.models import ClickEvent

pytestmark = pytest.mark.django_db


def click_today(user, link, n=1):
    for _ in range(n):
        ClickEvent.objects.create(link=link, device="desktop")
    return n


def _sparks(client_user, user, path="/dashboard/"):
    response = client_user.get(path)
    markers = [
        f'data-spark="{link.id}"'
        for link in user.links.all()
    ]
    body = response.content.decode()
    return body, {m: body.count(m) for m in markers}


def _spark_data(body):
    """Parse the single spark-all-data script tag into a dict."""
    chunk = body.split('id="spark-all-data" type="application/json">')[1]
    return json.loads(chunk.split("</script>")[0])


def click(link, created_at=None, **kwargs):
    """Create a click event; backdate afterwards (created_at is auto_now_add)."""
    event = ClickEvent.objects.create(link=link, **kwargs)
    if created_at is not None:
        ClickEvent.objects.filter(pk=event.pk).update(created_at=created_at)
    return event


def test_spark_returns_two_weeks_of_counts(client_user, user):
    now = timezone.now()
    link = make_link(user, code="sparkday")
    click(link, created_at=now - timedelta(days=1))
    click(link, created_at=now - timedelta(days=1))
    click(link, created_at=now)
    # A bot click must be excluded.
    click(link, created_at=now, is_bot=True)

    body = client_user.get("/dashboard/").content.decode()
    data = _spark_data(body)
    assert str(link.id) in data
    series = data[str(link.id)]
    assert len(series) == 14
    assert series[12] == 2  # yesterday
    assert series[13] == 1  # today
    assert sum(series) == 3


def test_sparkline_rendered_per_row(client_user, user):
    make_link(user, code="bar1")
    make_link(user, code="bar2")
    body = client_user.get("/dashboard/").content.decode()
    assert 'data-spark="' in body
    assert body.count('data-spark="') == 2


def test_spark_data_isolated_to_owner(client_user, user, other_user):
    mine = make_link(user, code="mine1")
    other = make_link(other_user, code="theirs1")
    ClickEvent.objects.create(link=other, device="desktop")
    body = client_user.get("/dashboard/").content.decode()
    data = _spark_data(body)
    assert str(mine.id) in data
    assert str(other.id) not in data


def test_spark_in_htmx_table_partial(client_user, user):
    make_link(user, code="partial")
    response = client_user.get("/dashboard/links/table/")
    body = response.content.decode()
    assert 'id="spark-all-data"' in body
    assert 'data-spark="' in body
