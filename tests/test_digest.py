"""Weekly digest email: preference, command behavior, content."""

import pytest
from django.core import mail
from django.core.management import call_command
from django.utils import timezone
from tests.conftest import make_link

from analytics.models import ClickEvent
from accounts.models import UserSettings

pytestmark = pytest.mark.django_db


def enable_digest(user):
    row, _ = UserSettings.objects.get_or_create(user=user)
    row.weekly_digest = True
    row.save(update_fields=["weekly_digest"])
    return row


def test_profile_toggle_enables_and_disables(client_user, user):
    client_user.post("/accounts/profile/", {"action": "digest", "weekly_digest": "on"})
    # Re-query: the one-to-one relation caches on first access in Python.
    assert UserSettings.objects.get(user=user).weekly_digest is True

    client_user.post("/accounts/profile/", {"action": "digest"})
    assert UserSettings.objects.get(user=user).weekly_digest is False


def test_digest_sent_with_top_links(client_user, user):
    enable_digest(user)
    hot = make_link(user, code="hot", title="Hot!")
    assert hot is not None
    for _ in range(4):
        ClickEvent.objects.create(link=hot, device="desktop")
    mid = make_link(user, code="mid")
    ClickEvent.objects.create(link=mid, device="desktop")
    # A bot click that must not inflate counts.
    ClickEvent.objects.create(link=hot, device="desktop", is_bot=True)

    mail.outbox.clear()
    call_command("send_digest", stdout=__import__("io").StringIO())

    assert len(mail.outbox) == 1
    message = mail.outbox[0]
    assert user.email in message.to
    assert "4 clicks" in message.body
    assert "Hot!" in message.body
    assert "Total non-bot clicks in the last 7 days: 5" in message.body


def test_digest_skips_users_without_clicks(client_user, user):
    enable_digest(user)
    make_link(user, code="sleepy")
    mail.outbox.clear()
    call_command("send_digest", stdout=__import__("io").StringIO())
    assert mail.outbox == []


def test_digest_skips_opted_out_users(client_user, user, other_user):
    enable_digest(user)
    # Alice gets clicks so her digest goes out; Bob has clicks too but no
    # UserSettings row at all — he must never be emailed.
    hot = make_link(user, code="hot")
    ClickEvent.objects.create(link=hot, device="desktop")
    from links.models import Link

    Link.objects.create(user=other_user, code="boblink", destination="https://x.example")
    ClickEvent.objects.create(link=Link.objects.get(code="boblink"), device="desktop")

    mail.outbox.clear()
    call_command("send_digest", stdout=__import__("io").StringIO())

    assert len(mail.outbox) == 1
    assert user.email in mail.outbox[0].to
    assert "bob@example.com" not in mail.outbox[0].to


def test_digest_honors_user_without_email(client_user, user):
    enable_digest(user)
    user.email = ""
    user.save(update_fields=["email"])
    hot = make_link(user, code="noise")
    ClickEvent.objects.create(link=hot, device="desktop")
    mail.outbox.clear()
    call_command("send_digest", stdout=__import__("io").StringIO())
    assert mail.outbox == []
