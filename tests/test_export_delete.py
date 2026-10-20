"""Data export (GDPR JSON) and account deletion."""

import json

import pytest
from django.contrib.auth import get_user_model
from tests.conftest import make_link

from analytics.models import ClickEvent
from links.models import Link

pytestmark = pytest.mark.django_db

User = get_user_model()


def test_export_contains_everything(client_user, user):
    link = make_link(user, code="exp1", title="My page", notes="private note")
    ClickEvent.objects.create(link=link, device="desktop", country="NL")

    response = client_user.get("/accounts/export/")
    assert response.status_code == 200
    assert "application/json" in response["Content-Type"]
    assert "attachment" in response["Content-Disposition"]

    document = json.loads(response.content.decode())
    assert document["account"]["username"] == user.username
    assert document["account"]["weekly_digest"] is False
    assert document["account"]["two_factor"] is False
    exported = {row["code"]: row for row in document["links"]}
    assert exported["exp1"]["title"] == "My page"
    assert exported["exp1"]["notes"] == "private note"
    assert exported["exp1"]["password_protected"] is False
    assert document["clicks"] and document["clicks"][0]["code"] == "exp1"
    assert "addr" not in json.dumps(document["clicks"])  # no IP data


def test_export_requires_login(client, user):
    make_link(user, code="exp2")
    assert client.get("/accounts/export/").status_code == 302


def test_delete_flow_two_steps(client_user, user):
    make_link(user, code="bye1")
    ClickEvent.objects.create(link=Link.objects.get(code="bye1"), device="desktop")

    # Step 1: confirmation page, nothing deleted.
    response = client_user.post("/accounts/delete/")
    body = response.content.decode()
    assert "Delete your account?" in body
    assert User.objects.filter(username="alice").exists()

    # Step 2: confirmed.
    response = client_user.post("/accounts/delete/", {"confirm_deletion": "on"})
    assert response.status_code == 302
    assert not User.objects.filter(username="alice").exists()
    assert not Link.objects.all().exists()
    assert not Link.objects.filter(code="bye1").exists()
    assert not ClickEvent.objects.all().exists()
    # The short code now 404s (fresh anonymous browser).
    from django.test import Client

    assert Client().get("/bye1").status_code == 404


def test_username_free_after_deletion(client_user, user):
    client_user.post("/accounts/delete/", {"confirm_deletion": "on"})
    # Same username can re-register afterwards.
    User.objects.create_user(username="alice", password="pw12345")
    assert User.objects.filter(username="alice").exists()


def test_get_to_delete_endpoint_405s(client_user, user):
    assert client_user.get("/accounts/delete/").status_code == 405
