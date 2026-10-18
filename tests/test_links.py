"""Link model validation, code generation, and the public redirect view."""

import datetime

from django.urls import reverse
from django.utils import timezone

import pytest

from links.forms import LinkForm
from links.models import (
    CODE_ALPHABET,
    RESERVED_ALIASES,
    Link,
    generate_short_code,
    validate_alias,
    validate_destination,
)
from tests.conftest import make_link

pytestmark = pytest.mark.django_db


# ---- Destination URL validation ------------------------------------------

@pytest.mark.parametrize(
    "url",
    [
        "https://example.com",
        "http://example.com/path?q=1",
        "https://sub.domain.example.org/deep/link#frag",
        "http://192.168.1.1:8080/admin",
    ],
)
def test_valid_destinations(url, user):
    assert LinkForm(user=user, data={"destination": url, "is_active": True}).is_valid()


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/file",
        "javascript:alert(1)",
        "data:text/html,stuff",
        "example.com/no-scheme",
        "not a url",
        "",
    ],
)
def test_invalid_destinations(url, user):
    form = LinkForm(user=user, data={"destination": url, "is_active": True})
    assert not form.is_valid()
    assert "destination" in form.errors


def test_validate_destination_rejects_unknown_scheme(user):
    with pytest.raises(Exception):
        validate_destination("gopher://example.com")


# ---- Alias validation -----------------------------------------------------

@pytest.mark.parametrize("code", ["good-alias", "abc123", "a-b-c-d"])
def test_valid_aliases(code, user):
    validate_alias(code)


@pytest.mark.parametrize("code", ["A", "bad_alias", "-leading", "UPPER", "has space", "", "x"])
def test_invalid_aliases(code, user):
    with pytest.raises(Exception):
        validate_alias(code)


@pytest.mark.parametrize("reserved", sorted(RESERVED_ALIASES))
def test_reserved_aliases_rejected(reserved, user):
    with pytest.raises(Exception):
        validate_alias(reserved)


def test_alias_collision_rejected(user, other_user):
    make_link(other_user, code="taken")
    form = LinkForm(
        user=user,
        data={"destination": "https://example.com", "code": "taken", "is_active": True},
    )
    assert not form.is_valid()
    assert "code" in form.errors


# ---- Code generation & collisions -----------------------------------------

def test_generated_codes_are_valid_and_unique(user):
    seen = set()
    for _ in range(50):
        code = generate_short_code()
        assert len(code) == 6
        assert all(char in CODE_ALPHABET for char in code)
        seen.add(code)
    assert len(seen) == 50


def test_auto_code_collides_into_unused_value(user, monkeypatch):
    """When a generated code already exists, a retry finds a fresh one."""
    make_link(user, code="abc123")
    codes = iter(["abc123", "zzz999"])
    monkeypatch.setattr("links.forms.generate_short_code", lambda: next(codes))

    form = LinkForm(user=user, data={"destination": "https://example.com", "is_active": True})
    assert form.is_valid()
    link = form.save()
    assert link.code == "zzz999"


def test_custom_alias_is_kept(user):
    form = LinkForm(
        user=user,
        data={
            "destination": "https://example.com",
            "code": "MyAlias",
            "is_active": True,
        },
    )
    assert form.is_valid()
    assert form.cleaned_data["code"] == "myalias"


# ---- Redirects ------------------------------------------------------------

def test_redirect_follows_destination(client, user):
    make_link(user, code="go123", destination="https://example.com/here")
    response = client.get(reverse("redirect", kwargs={"code": "go123"}))
    assert response.status_code == 302
    assert response["Location"] == "https://example.com/here"


def test_unknown_code_is_404(client, user):
    assert client.get(reverse("redirect", kwargs={"code": "nope404"})).status_code == 404


def test_disabled_code_is_404(client, user):
    make_link(user, code="off1", is_active=False)
    assert client.get(reverse("redirect", kwargs={"code": "off1"})).status_code == 404


def test_expired_code_is_404(client, user):
    expired = timezone.now() - datetime.timedelta(days=1)
    make_link(user, code="old1", expires_at=expired)
    assert client.get(reverse("redirect", kwargs={"code": "old1"})).status_code == 404


def test_redirect_not_cached(client, user):
    make_link(user, code="nc1")
    response = client.get(reverse("redirect", kwargs={"code": "nc1"}))
    assert response.headers.get("cache-control") == "no-store"


def test_status_labels(user):
    assert make_link(user, code="a1").status == "active"
    assert make_link(user, code="a2", is_active=False).status == "inactive"
    past = timezone.now() - datetime.timedelta(days=2)
    assert make_link(user, code="a3", expires_at=past).status == "expired"
