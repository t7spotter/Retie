"""Geo lookup for click events, with the same stinginess as the rest.

A GeoLite2-style mmdb (operator-supplied path in GEOIP_DB) is read once per
process; only the country code is returned and stored. The visitor's IP
address is never persisted anywhere. Private/reserved addresses always map
to "" (no data). Without GEOIP_DB the whole feature is dormant.
"""

import ipaddress
import threading

from django.conf import settings

_reader_cache = threading.local()


class NoGeoData(Exception):
    """GEOIP_DB is not configured, or the address has no country."""


def country_for(ip: str) -> str:
    """Two-letter ISO country for one IP, or "" when unknown/undecidable."""
    if not getattr(settings, "GEOIP_DB", None) or not ip:
        return ""
    try:
        parsed = ipaddress.ip_address(ip)
    except ValueError:
        return ""
    if parsed.is_private or parsed.is_loopback or parsed.is_reserved or parsed.is_link_local:
        return ""
    return _lookup(ip, parsed.version)


def _lookup(ip: str, version: int) -> str:
    reader = _reader()
    if reader is None:
        return ""
    try:
        record = reader.get(ip)
    except Exception:
        return ""  # malformed DB entries never break a redirect
    if not record:
        return ""
    country = record.get("country") or {}
    code = country.get("iso_code") or ""
    return code[:2].upper() if len(code) == 2 else ""


def _reader():
    cached = getattr(_reader_cache, "reader", None)
    if cached is not None:
        return cached
    import maxminddb

    try:
        reader = maxminddb.open_database(settings.GEOIP_DB)
    except FileNotFoundError:
        return None
    _reader_cache.reader = reader
    return reader
