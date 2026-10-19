"""Bot filtering: flagged at click time, excluded from analytics by default."""

import pytest
from tests.conftest import make_link

from analytics import queries
from analytics.models import ClickEvent

pytestmark = pytest.mark.django_db

BOT_UA = "Mozilla/5.0 (compatible; Googlebot/2.1)"
HUMAN_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"


def click(link, ua, **kwargs):
    return ClickEvent.objects.create(link=link, device=queries.device_category(ua), is_bot=queries.looks_like_bot(ua), **kwargs)


def test_crawler_user_agents_flagged_as_bots():
    for ua in ("curl/8.4", "python-requests/2.31", "HeadlessChrome/120", "facebookexternalhit/1.1"):
        assert queries.looks_like_bot(ua) is True


def test_human_and_empty_agents_are_not_bots():
    assert queries.looks_like_bot(HUMAN_UA) is False
    assert queries.looks_like_bot("") is False


def test_bots_recorded_and_excluded_from_series(user):
    link = make_link(user, code="botcheck")
    click(link, HUMAN_UA)
    click(link, BOT_UA)
    click(link, "wget -qO- https://example")

    today = queries.clicks_over_time(user, 1, link=link)[-1]["clicks"]
    with_bots = queries.clicks_over_time(user, 1, link=link, include_bots=True)[-1]["clicks"]
    assert today == 1 and with_bots == 3


def test_user_totals_exclude_bot_clicks(user):
    link = make_link(user, code="totals")
    click(link, HUMAN_UA)
    click(link, BOT_UA)
    click(link, BOT_UA)
    totals = queries.user_totals(user)
    assert totals["total_clicks"] == 1


def test_breakdowns_exclude_bots(user):
    link = make_link(user, code="breakz")
    click(link, HUMAN_UA, referrer="https://news.example/x", country="NL")
    click(link, BOT_UA, referrer="https://bot.example/", country="DE")

    devices = queries.device_breakdown(user, link=link)
    assert sum(devices.values()) == 1  # only the human click survives

    referrers = queries.referrer_breakdown(user, link=link, limit=5)
    assert [row["referrer"] for row in referrers] == ["https://news.example/x"]

    countries = queries.country_breakdown(user, link=link)
    assert [row["country"] for row in countries] == ["NL"]

    referrers_bots = queries.referrer_breakdown(user, link=link, limit=5, include_bots=True)
    assert {row["referrer"] for row in referrers_bots} == {
        "https://news.example/x", "https://bot.example/"
    }


def test_record_click_flags_via_request(user, rf):
    from analytics.queries import record_click

    link = make_link(user, code="reqbot")
    bot_event = record_click(link, rf.get("/x", HTTP_USER_AGENT="Twitterbot/1.0"))
    human_ev = record_click(link, rf.get("/x", HTTP_USER_AGENT=HUMAN_UA))
    assert bot_event.is_bot is True
    assert human_ev.is_bot is False


def test_mcp_api_bots_param(client_user, user):
    import json as jsonlib

    from rest_framework.authtoken.models import Token
    from rest_framework.test import APIClient

    link = make_link(user, code="parambot")
    click(link, BOT_UA)
    click(link, HUMAN_UA)

    api = APIClient()
    api.credentials(HTTP_AUTHORIZATION=f"Token {Token.objects.create(user=user).key}")
    default = api.get(f"/api/v1/links/{link.code}/analytics/").json()
    with_bots = api.get(f"/api/v1/links/{link.code}/analytics/", {"bots": "1"}).json()
    assert default["bot_clicks"] == 1
    assert sum(p["clicks"] for p in default["clicks_over_time"]) == 1
    assert sum(p["clicks"] for p in with_bots["clicks_over_time"]) == 2

    token = Token.objects.get(user=user)

    def rpc(payload):
        return jsonlib.loads(
            client_user.post(
                "/mcp/",
                data=jsonlib.dumps(payload),
                content_type="application/json",
                HTTP_AUTHORIZATION=f"Token {token.key}",
            ).json()["result"]["content"][0]["text"]
        )

    mc = rpc(
        {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "id": 1,
            "params": {"name": "get_link_analytics", "arguments": {"code": "parambot"}},
        }
    )
    assert mc["bot_clicks"] == 1
    assert sum(p["clicks"] for p in mc["clicks_over_time"]) == 1
