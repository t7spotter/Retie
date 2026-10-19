"""Destination health checking — deliberately opt-in.

Fetching links configured by users means SSRF splashing around whatever
network the server lives on. The checker is therefore disabled
(LINK_HEALTH_ENABLED=false) until the operator explicitly opts in, and even
then it only talks to the public DNS/internet path of the destination.
"""

import socket
import urllib.error
import urllib.request

TIMEOUT_SECONDS = 10

USER_AGENT_TEMPLATE = "Retie link-health checker (+{site_url})"


class Disabled(RuntimeError):
    """Raised when a check runs while LINK_HEALTH_ENABLED is false."""


def look_up(url: str, site_url: str, timeout: int = TIMEOUT_SECONDS) -> tuple:
    """Return (health_status, detail) for a destination.

    "ok" means the server answered HTTP (status < 400) or redirected;
    "error" means a 4xx/5xx answer or an unreachable host. No redirects to
    weird schemes can leak here because destinations are always http(s).
    """
    request = urllib.request.Request(
        url,
        method="HEAD",
        headers={"User-Agent": USER_AGENT_TEMPLATE.format(site_url=site_url)},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return _classify(response.status, method="HEAD")
    except urllib.error.HTTPError as error:
        if error.code in (405, 501):  # HEAD unimplemented: retry with GET
            return _with_get(url, site_url, timeout)
        return "error", f"HTTP {error.code}"
    except socket.timeout:
        return "error", "Timed out"
    except urllib.error.URLError as error:
        if isinstance(error.reason, socket.timeout):
            return "error", "Timed out"
        return "error", "Unreachable host"
    except ConnectionError:
        return "error", "Connection refused"
    except OSError:
        return "error", "Network error"


def _with_get(url: str, site_url: str, timeout: int) -> tuple:
    try:
        request = urllib.request.Request(
            url,
            method="GET",
            headers={"User-Agent": USER_AGENT_TEMPLATE.format(site_url=site_url),
                     "Range": "bytes=0-0"},
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return _classify(response.status, method="GET")
    except urllib.error.HTTPError as error:
        return "error", f"HTTP {error.code}"
    except (urllib.error.URLError, socket.timeout, ConnectionError, OSError):
        return "error", "Unreachable host"


def _classify(status: int, method: str = "HEAD") -> tuple:
    if status < 400:
        return "ok", f"HTTP {status}"
    return "error", f"HTTP {status}"
