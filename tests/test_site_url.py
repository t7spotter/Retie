"""SITE_URL must be a full origin (with scheme) — the canonical URL used to
build every short link, so a scheme-less value would silently produce broken
links in the UI (resolved as relative paths by browsers). Fail at startup.
"""

import os
import subprocess
import sys

BAD_ENV = {
    "SITE_URL": "127.0.0.1:8000",
    "DATABASE_URL": "postgres://postgres:postgres@localhost:5432/test_shortener",
}
GOOD_ENV = {
    "SITE_URL": "https://links.example.com",
    "DATABASE_URL": "postgres://postgres:postgres@localhost:5432/test_shortener",
}


def run_check(site_url: str):
    env = {**os.environ, **site_url}
    return subprocess.run(
        [sys.executable, "manage.py", "check"],
        capture_output=True,
        text=True,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        env=env,
    )


def test_scheme_less_site_url_fails_startup():
    result = run_check(BAD_ENV)
    assert result.returncode != 0
    assert "SITE_URL" in result.stderr
    assert "must include the scheme" in result.stderr


def test_full_origin_site_url_passes_check():
    result = run_check(GOOD_ENV)
    assert result.returncode == 0, result.stderr


def test_trailing_slash_is_stripped():
    """SITE_URL=https://links.example.com/ builds links without a double slash."""
    result = run_check({**GOOD_ENV, "SITE_URL": "https://links.example.com/"})
    assert result.returncode == 0, result.stderr
