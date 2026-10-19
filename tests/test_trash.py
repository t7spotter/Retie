"""Trash/undo: soft delete, restore, purge, redirect silence, purge command."""

from io import StringIO

import pytest
from django.core.management import call_command
from django.utils import timezone

from links.models import Link
from tests.conftest import make_link

pytestmark = pytest.mark.django_db


def test_delete_moves_to_trash_and_hides_everywhere(client_user, user):
    make_link(user, code="trashme", destination="https://example.com/gone")
    client_user.post("/dashboard/links/trashme/delete/")
    link = Link.all_objects.get(code="trashme")
    assert link.deleted_at is not None
    # Default manager hides it; dashboard listing no longer shows it.
    assert link not in Link.objects.filter(user=user)
    # API list excludes it.
    from rest_framework.authtoken.models import Token
    from rest_framework.test import APIClient

    api = APIClient()
    api.credentials(HTTP_AUTHORIZATION=f"Token {Token.objects.create(user=user).key}")
    data = api.get("/api/v1/links/").json()
    results = data.get("results") if isinstance(data, dict) and "results" in data else data
    assert "trashme" not in [row["code"] for row in results]


def test_deleted_link_stops_redirecting(client_user, user, client):
    make_link(user, code="stopdel", destination="https://example.com/x")
    assert client.get("/stopdel").status_code == 302
    client_user.post("/dashboard/links/stopdel/delete/")
    assert client.get("/stopdel").status_code == 404


def test_restore(client_user, user, client):
    make_link(user, code="comeback", destination="https://example.com/back")
    client_user.post("/dashboard/links/comeback/delete/")
    client_user.post("/dashboard/links/comeback/restore/")
    link = Link.objects.get(code="comeback")
    assert link.deleted_at is None
    assert client.get("/comeback").status_code == 302


def test_purge_deletes_forever(client_user, user):
    make_link(user, code="forevergone")
    client_user.post("/dashboard/links/forevergone/delete/")
    client_user.post("/dashboard/links/forevergone/purge/")
    assert not Link.all_objects.filter(code="forevergone").exists()


def test_trash_view_lists_trashed_links(client_user, user):
    make_link(user, code="in-trash")
    client_user.post("/dashboard/links/in-trash/delete/")
    response = client_user.get("/dashboard/links/table/", {"status": "trash"})
    assert response.status_code == 200
    assert b"in-trash" in response.content


def test_cannot_restore_someone_elses_trash(client_user, other_user):
    make_link(other_user, code="steal-me")
    response = client_user.post("/dashboard/links/steal-me/restore/")
    assert response.status_code == 404


def test_cannot_purge_someone_elses_trash(client_user, other_user):
    make_link(other_user, code="nopurge")
    response = client_user.post("/dashboard/links/nopurge/purge/")
    assert response.status_code == 404
    assert Link.all_objects.filter(code="nopurge").exists()


def test_purge_command_removes_only_old_trash(user):
    make_link(user, code="old-trash", destination="https://example.com/old")
    Link.all_objects.filter(code="old-trash").update(
        deleted_at=timezone.now() - timezone.timedelta(days=40)
    )
    make_link(user, code="new-trash", destination="https://example.com/new")
    Link.all_objects.filter(code="new-trash").update(
        deleted_at=timezone.now() - timezone.timedelta(days=2)
    )
    call_command("purge_trash", "--days", "30", stdout=StringIO())
    assert not Link.all_objects.filter(code="old-trash").exists()
    assert Link.all_objects.filter(code="new-trash").exists()


def test_purge_command_dry_run_deletes_nothing(user):
    make_link(user, code="drykind")
    Link.all_objects.filter(code="drykind").update(
        deleted_at=timezone.now() - timezone.timedelta(days=90)
    )
    out = StringIO()
    call_command("purge_trash", "--days", "30", "--dry-run", stdout=out)
    assert Link.all_objects.filter(code="drykind").exists()
    assert "1 link(s)" in out.getvalue()
