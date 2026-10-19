"""UTM builder: optional UTM fields folded into the destination at save."""

import pytest
from tests.conftest import make_link

from links.forms import LinkForm

pytestmark = pytest.mark.django_db


def save_form(user, data, instance=None):
    form = LinkForm(data={**data, "is_active": "on"}, user=user, instance=instance)
    assert form.is_valid(), form.errors
    return form.save()


def test_utms_appended_to_plain_destination(user):
    link = save_form(
        user,
        {
            "destination": "https://example.com/article",
            "utm_source": "newsletter",
            "utm_medium": "email",
            "utm_campaign": "spring",
        },
    )
    assert link.destination == "https://example.com/article?utm_source=newsletter&utm_medium=email&utm_campaign=spring"


def test_utms_do_not_duplicate_or_overwrite(user):
    link = save_form(
        user,
        {
            "destination": "https://example.com/a?utm_source=old&utm_campaign=oldcamp",
            "utm_source": "new",
            "utm_campaign": "fresh",
            "utm_medium": "rss",
        },
    )
    # Form-provided UTM values win over existing utm_* params (the fields
    # exist precisely to set/add them); non-utm query params survive.
    assert "utm_source=new" in link.destination
    assert "utm_medium=rss" in link.destination
    assert "utm_campaign=fresh" in link.destination
    assert "utm_source=old" not in link.destination


def test_fragment_and_existing_params_preserved(user):
    link = save_form(
        user,
        {
            "destination": "https://example.com/p?ref=twitter&utm_content=x#top",
            "utm_source": "news",
        },
    )
    assert "ref=twitter" in link.destination
    assert "utm_content=x" in link.destination
    assert link.destination.endswith("#top")
    assert "utm_source=news" in link.destination


def test_empty_utm_fields_change_nothing(user):
    link = make_link(user, code="plain")
    before = link.destination
    save_form(
        user,
        {"destination": before, "code": "plain", "utm_source": "", "utm_medium": "", "utm_campaign": ""},
        instance=link,
    )
    link.refresh_from_db()
    assert link.destination == before


def test_edit_same_destination_keeps_query(user):
    """Editing without touching UTM fields keeps the destination's query."""
    link = save_form(
        user,
        {"destination": "https://example.com/x", "utm_source": "keepme"},
    )
    edited = save_form(
        user,
        {"destination": "https://example.com/x", "utm_source": "keepme"},
        instance=link,
    )
    assert "utm_source=keepme" in edited.destination


def test_fresh_destination_replaces_stored_utms(user):
    """Rewriting the destination to a raw URL drops old UTM params — the
    destination field is authoritative on edit."""
    link = save_form(
        user,
        {"destination": "https://example.com/x", "utm_source": "old"},
    )
    edited = save_form(
        user,
        {"destination": "https://example.com/x/y"},
        instance=link,
    )
    assert edited.destination == "https://example.com/x/y"
