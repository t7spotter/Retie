"""Destination blocklist: BLOCKED_DESTINATIONS must never be shortenable.

The check lives in validate_destination so forms, the REST serializer, and
the MCP tools all enforce identical rules.
"""

import pytest
from django.core.exceptions import ValidationError

from links.models import validate_destination

pytestmark = pytest.mark.django_db

OK_DEST = "https://good.example/page"


@pytest.fixture
def blocked(settings):
    settings.BLOCKED_DESTINATIONS = ["evil.example", "banned.org"]
    return settings.BLOCKED_DESTINATIONS


def test_no_blocklist_by_default_accepts_everything():
    validate_destination("https://anything.at.all/x")


def test_exact_host_blocked(blocked):
    with pytest.raises(ValidationError):
        validate_destination("https://evil.example/phish")


def test_subdomain_of_blocked_host_blocked(blocked):
    with pytest.raises(ValidationError):
        validate_destination("https://login.evil.example/phish")


def test_similar_but_different_host_allowed(blocked):
    # notevil.example is NOT evil.example; matching is domain-suffix based.
    validate_destination("https://notevil.example/x")


def test_match_is_case_insensitive(blocked):
    with pytest.raises(ValidationError):
        validate_destination("https://EVIL.EXAMPLE/x")


def test_wildcard_entry_prefix_is_accepted(blocked, settings):
    settings.BLOCKED_DESTINATIONS = ["*.banned.org"]
    validate_destination(OK_DEST)
    with pytest.raises(ValidationError):
        validate_destination("https://banned.org/x")


def test_empty_entries_are_ignored(settings):
    settings.BLOCKED_DESTINATIONS = ["", "  "]
    validate_destination(OK_DEST)


def test_error_message_names_the_server_not_the_host(blocked):
    with pytest.raises(ValidationError) as excinfo:
        validate_destination("https://evil.example/x")
    assert "not allowed on this server" in str(excinfo.value)


def test_dashboard_form_refuses_blocked_destination(blocked, client_user):
    response = client_user.post(
        "/dashboard/links/new/",
        {"destination": "https://evil.example/x", "code": ""},
    )
    assert "not allowed on this server" in response.content.decode()
