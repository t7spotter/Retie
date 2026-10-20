"""Click webhooks: outbox rows on click, drained by flush_webhooks."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from django.core.management import call_command
from tests.conftest import make_link

from accounts.models import PendingWebhook, UserSettings
from analytics.models import ClickEvent

pytestmark = pytest.mark.django_db


@pytest.fixture
def webhook_url():
    """A throwaway local HTTP server capturing POST bodies."""
    captured = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            captured.append(json.loads(body.decode()))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/hook", captured
    server.shutdown()


def _enable_webhook(user, url="https://example.com/hook"):
    settings_row, _ = UserSettings.objects.get_or_create(user=user)
    settings_row.webhook_url = url
    settings_row.save(update_fields=["webhook_url"])
    return settings_row


def test_click_with_no_webhook_creates_no_rows(client, user):
    make_link(user, code="noweb")
    client.get("/noweb")
    assert PendingWebhook.objects.count() == 0


def test_click_enqueues_payload(client, user):
    _enable_webhook(user)
    make_link(user, code="withwh", qr_dark="")
    client.get("/withwh")
    row = PendingWebhook.objects.get()
    payload = json.loads(row.payload)
    assert payload["code"] == "withwh"
    assert payload["event"] == "click"
    assert "clicked_at" in payload
    assert "addr" not in json.dumps(payload)  # no IP addresses ever


def test_profile_save_and_clear(client_user, user):
    response = client_user.post(
        "/accounts/profile/", {"action": "webhook", "webhook_url": "https://example.com/hook"}
    )
    assert response.status_code == 200  # profile re-renders after actions
    assert UserSettings.objects.get(user=user).webhook_url == "https://example.com/hook"

    body = response.content.decode()
    assert "Currently posting to" in body

    client_user.post("/accounts/profile/", {"action": "webhook", "webhook_url": ""})
    assert UserSettings.objects.get(user=user).webhook_url == ""


def test_profile_rejects_http(client_user, user):
    client_user.post(
        "/accounts/profile/", {"action": "webhook", "webhook_url": "http://example.com/hook"}
    )
    assert UserSettings.objects.get(user=user).webhook_url == ""


def test_flush_posts_and_clears_outbox(user, webhook_url):
    url, captured = webhook_url
    _enable_webhook(user, url=url)
    link = make_link(user, code="flush1")
    row = PendingWebhook.objects.create(user=user, payload=json.dumps({"code": "flush1", "event": "click"}))

    call_command("flush_webhooks")
    assert PendingWebhook.objects.count() == 0
    assert len(captured) == 1
    assert captured[0]["code"] == "flush1"


def test_flush_retries_and_drops(user):
    _enable_webhook(user, url="http://127.0.0.1:1/hook")  # nothing listens
    link = make_link(user, code="retry9")
    ClickEvent.objects.create(link=link, device="desktop")

    call_command("flush_webhooks")  # will attempt (fails fast: connection refused)
    call_command("flush_webhooks")
    call_command("flush_webhooks")
    call_command("flush_webhooks")
    call_command("flush_webhooks")
    assert PendingWebhook.objects.count() == 0  # dropped after MAX_ATTEMPTS


def test_flush_skips_rows_without_webhook(user):
    link = make_link(user, code="none9")
    ClickEvent.objects.create(link=link, device="desktop")
    PendingWebhook.objects.create(user=user, payload="{}")
    call_command("flush_webhooks")
    assert PendingWebhook.objects.count() == 0
