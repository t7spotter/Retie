"""Link health checker: look_up classification, the command, dashboard badge."""

import io

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from tests.conftest import make_link

pytestmark = pytest.mark.django_db


def test_check_links_disabled_raises_command_error(user):
    """Opt-in guard: the command refuses silently by default."""
    make_link(user, code="hc")
    with pytest.raises(CommandError):
        call_command("check_links", stdout=io.StringIO(), stderr=io.StringIO())


def test_command_records_health(user, monkeypatch, settings):
    settings.LINK_HEALTH_ENABLED = True
    make_link(user, code="hok", destination="https://up.example/x")
    make_link(user, code="hbad", destination="https://down.example/x")

    def fake_look_up(url, site_url, timeout=10):
        return {
            "https://up.example/x": ("ok", "HTTP 200"),
            "https://down.example/x": ("error", "HTTP 404"),
        }[url]

    monkeypatch.setattr("links.health.look_up", fake_look_up)

    out = io.StringIO()
    call_command("check_links", stdout=out)
    ok_link = user.links.get(code="hok")
    bad_link = user.links.get(code="hbad")
    assert ok_link.health_status == "ok" and ok_link.health_detail == "HTTP 200"
    assert ok_link.last_checked_at is not None
    assert bad_link.health_status == "error" and bad_link.health_detail == "HTTP 404"
    assert "2 link(s): 1 ok, 1 error" in out.getvalue()


def test_command_single_code_and_user_filter(user, other_user, monkeypatch, settings):
    settings.LINK_HEALTH_ENABLED = True
    make_link(user, code="target")
    make_link(other_user, code="untouched")

    def fake_look_up(url, site_url, timeout=10):
        return ("ok", "HTTP 200")

    monkeypatch.setattr("links.health.look_up", fake_look_up)
    call_command("check_links", "--code", "target", "--sleep", "0", stdout=io.StringIO())
    assert user.links.get(code="target").health_status == "ok"
    assert other_user.links.get(code="untouched").health_status == ""


def test_look_up_reports_unreachable_host():
    """Real network probe against a closed local port (safe, no internet)."""
    from links.health import look_up

    assert look_up("http://127.0.0.1:1/x", "http://127.0.0.1:8000", timeout=2)[0] == "error"


def test_dashboard_row_shows_health_badge(client_user, user):
    make_link(user, code="badge")
    link = user.links.get(code="badge")
    link.health_status = "error"
    link.health_detail = "HTTP 404"
    link.save(update_fields=["health_status", "health_detail"])
    response = client_user.get("/dashboard/links/table/")
    assert b"Destination down" in response.content
