"""Bulk actions on the dashboard table."""

import pytest

from links.models import Link

pytestmark = pytest.mark.django_db


def bulk(client, action, codes, follow=False, **extra):
    payload = {"action": action, "codes": ",".join(codes), **extra}
    return client.post("/dashboard/links/bulk/", payload, follow=follow)


def test_bulk_deactivates_and_activates(client_user, user):
    a = user.links.create(code="b1", destination="https://a.example")
    b = user.links.create(code="b2", destination="https://b.example")

    bulk(client_user, "deactivate", ["b1", "b2"])

    a.refresh_from_db(); b.refresh_from_db()
    assert a.is_active is False and b.is_active is False

    bulk(client_user, "activate", ["b1"])
    a.refresh_from_db()
    assert a.is_active is True
    b.refresh_from_db()
    assert b.is_active is False  # untouched


def test_bulk_trash(client_user, user):
    user.links.create(code="t1", destination="https://a.example")
    user.links.create(code="t2", destination="https://b.example")
    response = bulk(client_user, "trash", ["t1", "t2"])
    assert response.status_code == 302
    trashed = {c["code"] for c in Link.all_objects.filter(user=user, deleted_at__isnull=False).values("code")}
    assert {"t1", "t2"} == trashed


def test_bulk_tag(client_user, user):
    user.links.create(code="tg1", destination="https://a.example")
    user.links.create(code="tg2", destination="https://b.example")
    bulk(client_user, "tag", ["tg1", "tg2"], tag="bulk, tagged")
    names1 = {t.name for t in user.links.get(code="tg1").tags.all()}
    names2 = {t.name for t in user.links.get(code="tg2").tags.all()}
    assert names1 == {"bulk", "tagged"} == names2


def test_bulk_never_touches_other_users_links(client_user, user, other_user):
    user.links.create(code="mine", destination="https://a.example")
    theirs = other_user.links.create(code="theirs", destination="https://b.example")

    bulk(client_user, "deactivate", ["mine", "theirs"])
    theirs.refresh_from_db()
    assert theirs.is_active is True  # bob's link untouched

    bulk(client_user, "trash", ["theirs"])
    assert Link.all_objects.get(code="theirs").deleted_at is None


def test_bulk_requires_action_and_codes(client_user, user):
    user.links.create(code="s1", destination="https://a.example")
    assert bulk(client_user, "", ["s1"]).status_code == 302
    assert bulk(client_user, "trash", []).status_code == 302
    assert Link.all_objects.get(code="s1").deleted_at is None


def test_bulk_empty_selection_redirects_with_warning(client_user, user):
    bulk(client_user, "trash", ["nope"], follow=True)
