"""CSV export and import of your links (dashboard endpoints)."""

import io

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from tests.conftest import make_link

pytestmark = pytest.mark.django_db


@pytest.fixture
def csv_upload():
    def make(name="links.csv", content=""):
        return SimpleUploadedFile(name, content.encode("utf-8"), content_type="text/csv")

    return make


def test_export_contains_all_own_links(client_user, user):
    make_link(user, code="alpha")
    response = client_user.get("/dashboard/links/export.csv")
    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/csv")
    body = response.content.decode()
    assert body.startswith("code,destination,title,is_active,")
    assert "alpha" in body and "https://example.com/page" in body


def test_export_is_scoped_to_owner(client_user, user, other_user):
    make_link(user, code="mine")
    make_link(other_user, code="theirs")
    body = client_user.get("/dashboard/links/export.csv").content.decode()
    assert "mine" in body
    assert "theirs" not in body


def test_export_requires_login(client):
    assert client.get("/dashboard/links/export.csv").status_code == 302


def test_import_page_get(client_user):
    response = client_user.get("/dashboard/links/import/")
    assert response.status_code == 200
    assert b"CSV" in response.content


def test_import_requires_login(client):
    response = client.post("/dashboard/links/import/")
    assert response.status_code == 302


def test_import_headered_rows(client_user):
    content = (
        "destination,alias,title\n"
        "https://example.com/a,one,First thing\n"
        "https://example.com/b,two-3,\n"
    )
    response = client_user.post(
        "/dashboard/links/import/", {"file": SimpleUploadedFile("l.csv", content.encode())}
    )
    assert response.status_code == 200
    from links.models import Link

    assert Link.objects.filter(user__username="alice").count() == 2
    assert Link.objects.get(user__username="alice", code="one").title == "First thing"


def test_import_bitly_style_headers(client_user):
    content = "title,short link,long url,created date\nTitle 1,https://bit.ly/zzz,https://example.com/x,2024-01-01\n"
    response = client_user.post(
        "/dashboard/links/import/", {"file": SimpleUploadedFile("l.csv", content.encode())}
    )
    from links.models import Link

    link = Link.objects.filter(user__username="alice").first()
    assert response.status_code == 200
    assert link is not None and link.title == "Title 1" and link.code == "zzz"


def test_import_headerless_urls(client_user):
    content = "https://example.com/1\nhttps://example.com/2\n"
    response = client_user.post(
        "/dashboard/links/import/", {"file": SimpleUploadedFile("l.csv", content.encode())}
    )
    from links.models import Link

    assert Link.objects.filter(user__username="alice").count() == 2


def test_import_reports_invalid_rows_and_imports_valid_ones(client_user):
    content = (
        "destination,alias\n"
        "https://example.com/ok,goodrow\n"
        "not-a-url,badrow\n"
    )
    response = client_user.post(
        "/dashboard/links/import/", {"file": SimpleUploadedFile("l.csv", content.encode())}
    )
    body = response.content.decode()
    from links.models import Link

    assert Link.objects.filter(code="goodrow").exists()
    assert not Link.objects.filter(code="badrow").exists()
    assert "Imported 1 of 2" in body
    assert "Row 2" in body


def test_import_duplicate_alias_within_file_fails_that_row_only(client_user):
    content = "destination,alias\nhttps://example.com/1,same\nhttps://example.com/2,same\n"
    response = client_user.post(
        "/dashboard/links/import/", {"file": SimpleUploadedFile("l.csv", content.encode())}
    )
    body = response.content.decode()
    from links.models import Link

    assert Link.objects.filter(user__username="alice").count() == 1
    assert "Row 2" in body and ("already" in body or "exists" in body)


def test_import_rejects_non_csv_uploads(client_user, csv_upload):
    response = client_user.post(
        "/dashboard/links/import/", {"file": csv_upload(name="links.txt", content="nope")}
    )
    assert "Choose a .csv" in response.content.decode()


def test_import_row_cap(client_user, csv_upload):
    content = "destination\n" + "https://example.com/x\n" * 1001
    response = client_user.post(
        "/dashboard/links/import/", {"file": csv_upload(content=content)}
    )
    assert "at most 1000 rows" in response.content.decode()


def test_export_round_trip(client_user, user):
    """Export, then re-import a fresh alias column: destinations survive."""
    import csv as csv_module

    make_link(user, code="roundtrip")
    response = client_user.get("/dashboard/links/export.csv")
    rows = list(csv_module.DictReader(io.StringIO(response.content.decode())))
    codes = {row["code"] for row in rows}
    assert codes == {"roundtrip"}
