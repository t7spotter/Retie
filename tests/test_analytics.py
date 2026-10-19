"""Click recording and dashboard/analytics aggregation."""

from datetime import timedelta

from django.utils import timezone
from django.urls import reverse

import pytest

from analytics import queries
from tests.conftest import make_link

pytestmark = pytest.mark.django_db


def _click(link, days_ago=0, device="desktop", referrer=""):
    from analytics.models import ClickEvent

    event = ClickEvent.objects.create(link=link, device=device, referrer=referrer)
    if days_ago:
        # auto_now_add overwrites direct values, so update after create.
        event.created_at = timezone.now() - timedelta(days=days_ago)
        event.save(update_fields=["created_at"])
    return event


def test_record_click_is_logged_on_redirect(client, user):
    link = make_link(user, code="cl1")
    client.get(
        reverse("redirect", kwargs={"code": "cl1"}),
        HTTP_USER_AGENT="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0)",
        HTTP_REFERER="https://news.example.com/story",
    )
    assert link.clicks.count() == 1
    click = link.clicks.first()
    assert click.device == "mobile"
    assert click.referrer == "https://news.example.com/story"


def test_device_categories():
    from analytics.queries import device_category

    assert device_category("Mozilla/5.0 (iPad; CPU)") == "tablet"
    assert device_category("Mozilla/5.0 (Android 14; Mobile)") == "mobile"
    assert device_category("Mozilla/5.0 (Macintosh)") == "desktop"
    assert device_category("") == "other"


def test_device_category_tablet_before_mobile():
    from analytics.queries import device_category

    ua = "Mozilla/5.0 (iPad; CPU Mac OS X) Safari mobile"
    assert device_category(ua) == "tablet"


def test_user_totals(client, user, other_user):
    link = make_link(user, code="tot1")
    other_link = make_link(other_user, code="tot2")
    make_link(user, code="tot3", is_active=False)
    _click(link, days_ago=1)
    _click(link, days_ago=0)
    _click(other_link, days_ago=0)  # not user's

    totals = queries.user_totals(user)
    assert totals["total_links"] == 2
    assert totals["active_links"] == 1
    assert totals["total_clicks"] == 2


def test_clicks_over_time_buckets_per_day(user):
    link = make_link(user, code="ovt1")
    _click(link, days_ago=3)
    _click(link, days_ago=3)
    _click(link, days_ago=0)

    series = queries.clicks_over_time(user, days=5)
    by_date = {p["date"]: p["clicks"] for p in series}
    today = timezone.localdate().isoformat()
    three_days_ago = (timezone.localdate() - timedelta(days=3)).isoformat()
    assert by_date[three_days_ago] == 2
    assert by_date[today] == 1
    assert len(series) == 5


def test_clicks_over_time_scopes_to_one_link(user):
    link_a = make_link(user, code="ovt2")
    link_b = make_link(user, code="ovt3")
    _click(link_a, days_ago=0)
    _click(link_b, days_ago=0)

    series = queries.clicks_over_time(user, days=1, link=link_a)
    assert series[0]["clicks"] == 1


def test_referrer_breakdown(user):
    link = make_link(user, code="ref1")
    _click(link, referrer="https://news.example.com/a")
    _click(link, referrer="https://news.example.com/a")
    _click(link, referrer="")

    breakdown = queries.referrer_breakdown(user)
    assert breakdown[0]["referrer"] == "https://news.example.com/a"
    assert breakdown[0]["count"] == 2
    assert all(row["referrer"] for row in breakdown)


def test_referrer_breakdown_scoped_to_user(user, other_user):
    mine = make_link(user, code="ref2")
    theirs = make_link(other_user, code="ref3")
    _click(mine, referrer="https://mine.example.com")
    _click(theirs, referrer="https://theirs.example.com")
    assert {r["referrer"] for r in queries.referrer_breakdown(user)} == {
        "https://mine.example.com"
    }


def test_device_breakdown(user):
    link = make_link(user, code="dev1")
    _click(link, device="mobile")
    _click(link, device="mobile")
    _click(link, device="desktop")

    breakdown = queries.device_breakdown(user)
    assert breakdown["mobile"] == 2
    assert breakdown["desktop"] == 1


def test_recent_clicks_ordering(user, other_user):
    mine = make_link(user, code="rec1")
    theirs = make_link(other_user, code="rec2")
    _click(mine, days_ago=2)
    _click(mine, days_ago=0)
    _click(theirs, days_ago=0)

    recent = queries.recent_clicks(user, limit=10)
    assert all(click.link.user == user for click in recent)
    assert recent[0].created_at > recent[1].created_at


def test_analytics_page_only_for_owner(client_user, user, other_user):
    make_link(user, code="ana1")
    link = make_link(other_user, code="ana2")
    assert client_user.get(reverse("link_analytics", kwargs={"code": "ana1"})).status_code == 200
    assert client_user.get(reverse("link_analytics", kwargs={"code": "ana2"})).status_code == 404


def test_deleting_link_removes_clicks_once_purged(client, user):
    """CASCADE policy: analytics never outlive their link.

    Normal delete is a soft move to trash (clicks are kept so a quick
    restore loses nothing); the explicit purge (or trash retention job)
    removes the click history with the link.
    """
    link = make_link(user, code="casc1")
    _click(link)
    client.login(username="alice", password="correct-horse-42")
    client.post(reverse("link_delete", kwargs={"code": "casc1"}))
    assert link.clicks.count() == 1  # trashed, not gone
    client.post(reverse("link_purge", kwargs={"code": "casc1"}))
    assert link.clicks.count() == 0
