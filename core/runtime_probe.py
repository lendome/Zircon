"""Automatic detection and health-checking of local URLs in command output.

When a shell command (e.g. a dev server, build tool, or installer) prints a
URL such as `http://localhost:5173/` or `http://0.0.0.0:8000`, this module
extracts it, normalizes wildcard listeners to a probeable loopback address,
and issues a bounded HTTP GET so the agent can immediately see whether the
service is actually reachable — without spending an extra tool turn on a
manual `fetch_url` call.

Automatic probing is restricted to loopback, link-local, and RFC1918 private
addresses. Arbitrary public URLs are never auto-probed; the agent can still
request those explicitly via `fetch_url`.
"""
from __future__ import annotations

import ipaddress
import logging
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

logger = logging.getLogger("agent.core.runtime_probe")

# Match http(s)://host[:port][/path] hosts that are IPs, [IPv6], or DNS labels.
# Stop at whitespace or common surrounding punctuation that dev tools emit.
_URL_RE = re.compile(
    r"https?://"
    r"(?:"
    r"\[[0-9a-fA-F:]+\]"               # [IPv6]
    r"|(?:\d{1,3}\.){3}\d{1,3}"         # IPv4
    r"|[A-Za-z0-9](?:[A-Za-z0-9\-]*[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9\-]*[A-Za-z0-9])?)*"  # hostname
    r")"
    r"(?::\d+)?"                        # optional :port
    r"(?:/[^\s\"'<>`]*)?"               # optional path/query
)

# Trailing punctuation that dev servers append after a URL on a log line.
_TRAILING_PUNCT = ".,;:)"

_ARTIFACT_RE = re.compile(
    r"((?:[A-Za-z]:[\\/]|[./~])?(?:[^\s:\"'<>|]*?[\\/])*[^\s:\"'<>|]*?"
    r"(?:\.exe|\.msi|\.msix|\.app|\.dmg|\.pkg|\.deb|\.rpm|\.whl|\.tar\.gz|\.zip|"
    r"\.jar|\.dll|\.so|\.dylib|\.bin|\.apk|\.aab))\b",
    re.IGNORECASE,
)

# Lines that report a file as MISSING or a command as failed must never yield
# "artifacts" — a path in `ls: cannot access '/c/xampp/php/php.exe'` is
# evidence of ABSENCE, not a build product. Without this filter, probing a
# machine for installed tools poisons the execution state with phantom
# artifacts, and the model starts trusting nonexistent paths over its own
# observations.
_ARTIFACT_NEGATIVE_LINE_RE = re.compile(
    r"cannot access|no such file|not found|could not find|"
    r"command not found|not recognized|permission denied|does not exist|"
    r"\berror:|\berr!|\bfatal:|\bfailed\b|\bstderr\b",
    re.IGNORECASE,
)

# Automatic previews use the same rough four-characters-per-token estimate as
# the context manager. Larger responses remain available through fetch_url.
AUTO_PREVIEW_MAX_TOKENS = 2000
AUTO_PREVIEW_MAX_CHARS = AUTO_PREVIEW_MAX_TOKENS * 4


def truncate_preview(text: str, max_chars: int = AUTO_PREVIEW_MAX_CHARS) -> str:
    """Bound automatic response previews and direct deeper inspection to fetch_url."""
    if len(text) <= max_chars:
        return text
    return (
        text[:max_chars]
        + "\n... (automatic preview truncated; use fetch_url to inspect more)"
    )


@dataclass
class ProbeResult:
    advertised_url: str
    probe_url: str
    ok: bool
    status_code: int = 0
    final_url: str = ""
    content_type: str = ""
    body_preview: str = ""
    response_preview: str = ""
    error: str = ""

    def to_line(self) -> str:
        if self.ok:
            bits = [
                f"[url-health] {self.advertised_url} -> HTTP {self.status_code}",
            ]
            if self.final_url and self.final_url != self.probe_url:
                bits.append(f"final={self.final_url}")
            if self.content_type:
                bits.append(f"type={self.content_type}")
            if self.body_preview:
                bits.append(f"body={self.body_preview!r}")
            return " ".join(bits)
        err = self.error or "unreachable"
        return f"[url-health] {self.advertised_url} -> UNREACHABLE ({err})"

    def preview_block(self) -> str:
        """Return the bounded automatic page preview, if a request succeeded."""
        if not self.ok or not self.response_preview:
            return ""
        return (
            f'<url_preview url="{self.advertised_url}">\n'
            f"{self.response_preview}\n"
            "</url_preview>"
        )


@dataclass
class CommandFact:
    """A concise, machine-derivable fact extracted from a command's output."""
    kind: str  # "command" | "server_url" | "artifact" | "background_pid"
    detail: str
    ok: bool = True


@dataclass
class ExtractedFacts:
    urls: list[str] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)
    background_pids: list[str] = field(default_factory=list)
    exit_code: int | None = None


def _is_loopback_or_private(host: str) -> bool:
    host = host.strip("[]")
    # Hostnames we always consider local.
    if host.lower() in ("localhost", "ip6-localhost", "ip6-loopback"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    # Wildcard / unspecified listeners (0.0.0.0, ::) bind all interfaces and
    # are always local dev-server addresses.
    if ip.is_unspecified:
        return True
    if ip.is_loopback:
        return True
    if ip.is_link_local:
        return True
    if ip.version == 4 and ip in ipaddress.ip_network("10.0.0.0/8"):
        return True
    if ip.version == 4 and ip in ipaddress.ip_network("172.16.0.0/12"):
        return True
    if ip.version == 4 and ip in ipaddress.ip_network("192.168.0.0/16"):
        return True
    # Unique local IPv6
    if ip.version == 6 and ip in ipaddress.ip_network("fc00::/7"):
        return True
    return False


def is_local_url(url: str) -> bool:
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    if parts.scheme not in ("http", "https"):
        return False
    host = (parts.hostname or "").lower()
    if not host:
        return False
    return _is_loopback_or_private(host)


def normalize_probe_url(url: str) -> str:
    """Map wildcard listeners (0.0.0.0, [::]) to a probeable loopback URL."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    host = (parts.hostname or "").lower()
    scheme = parts.scheme or "http"
    netloc = parts.netloc
    if host in ("0.0.0.0", ""):
        netloc = netloc.replace("0.0.0.0", "127.0.0.1") if "0.0.0.0" in netloc else ("127.0.0.1" + (":" + parts.netloc.split(":", 1)[1] if ":" in parts.netloc else ""))
    elif host == "::" or host == "0:0:0:0:0:0:0:0":
        netloc = "[::1]" + (":" + parts.netloc.rsplit(":", 1)[-1] if ":" in parts.netloc else "")
    # Reconstruct
    query = f"?{parts.query}" if parts.query else ""
    return f"{scheme}://{netloc}{parts.path or '/'}{query}"


def extract_local_urls(text: str) -> list[str]:
    """Return deduplicated, order-preserving local URLs found in `text`."""
    seen: set[str] = set()
    out: list[str] = []
    for m in _URL_RE.finditer(text or ""):
        url = m.group(0).rstrip(_TRAILING_PUNCT)
        if url.endswith(")"):
            # Likely a parenthesized note after the URL on same line; keep core.
            url = url.split("(", 1)[0].rstrip(_TRAILING_PUNCT)
        if not is_local_url(url):
            continue
        if url not in seen:
            seen.add(url)
            out.append(url)
    return out


def extract_artifacts(text: str) -> list[str]:
    """Return build/package artifact paths mentioned in command output."""
    seen: set[str] = set()
    out: list[str] = []
    # Work line-by-line so a match can be attributed to its source line:
    # artifact-looking paths on failure/absence lines (ls errors, 'command
    # not found', failed builds) are NOT artifacts — they are things the
    # command was looking for and did not find.
    for line in (text or "").splitlines():
        if _ARTIFACT_NEGATIVE_LINE_RE.search(line):
            continue
        for m in _ARTIFACT_RE.finditer(line):
            path = m.group(1).strip().strip("'\"")
            # Filter false positives that are clearly code references, not paths.
            if not path or path.endswith(".py") or path.endswith(".js") or path.endswith(".ts"):
                continue
            # Require a path separator or drive to qualify as a real artifact path,
            # unless it's a bare filename with a known binary extension.
            has_sep = ("/" in path) or ("\\" in path) or (len(path) >= 2 and path[1] == ":")
            ext = path.lower().rsplit(".", 1)[-1] if "." in path else ""
            binary_exts = {"exe", "msi", "msix", "app", "dmg", "pkg", "deb", "rpm",
                           "whl", "jar", "dll", "so", "dylib", "bin", "apk", "aab"}
            if not (has_sep or ext in binary_exts):
                continue
            if path not in seen:
                seen.add(path)
                out.append(path)
    return out


def extract_background_pids(text: str) -> list[str]:
    """Return background job IDs (bg_N) mentioned in shell_start/poll output."""
    return list(dict.fromkeys(re.findall(r"\bbg_\d+\b", text or "")))


def extract_exit_code(text: str) -> int | None:
    m = re.search(r"[Ee]xit code:?\s*(\d+)", text or "")
    return int(m.group(1)) if m else None


def extract_facts(text: str) -> ExtractedFacts:
    return ExtractedFacts(
        urls=extract_local_urls(text),
        artifacts=extract_artifacts(text),
        background_pids=extract_background_pids(text),
        exit_code=extract_exit_code(text),
    )


async def probe_url(url: str, timeout: float = 4.0) -> ProbeResult:
    """Probe a single URL with a bounded GET. Never raises."""
    import httpx
    probe_url = normalize_probe_url(url)
    advertised = url
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            resp = await client.get(probe_url, headers={"User-Agent": "zircon-runtime-probe"})
            ct = resp.headers.get("content-type", "")
            body = resp.text[:160].replace("\n", " ").strip()
            return ProbeResult(
                advertised_url=advertised,
                probe_url=probe_url,
                ok=True,
                status_code=resp.status_code,
                final_url=str(resp.url),
                content_type=ct,
                body_preview=body,
                response_preview=truncate_preview(resp.text),
            )
    except httpx.ConnectError as e:
        return ProbeResult(advertised_url=advertised, probe_url=probe_url, ok=False, error=f"connection refused ({e})")
    except httpx.TimeoutException:
        return ProbeResult(advertised_url=advertised, probe_url=probe_url, ok=False, error="timeout")
    except Exception as e:  # noqa: BLE001
        return ProbeResult(advertised_url=advertised, probe_url=probe_url, ok=False, error=type(e).__name__)


class RuntimeProbe:
    """Per-execution probe cache. Probes each discovered local URL once."""

    def __init__(self) -> None:
        self._seen: set[str] = set()
        self.results: list[ProbeResult] = []

    def reset(self) -> None:
        self._seen.clear()
        self.results.clear()

    async def probe_new(self, text: str, retry_failed: bool = False) -> list[ProbeResult]:
        """Probe discovered local URLs, optionally retrying earlier failures."""
        urls = extract_local_urls(text)
        failed_urls = {
            result.advertised_url
            for result in self.results
            if not result.ok
        }
        new = [u for u in urls if u not in self._seen or (retry_failed and u in failed_urls)]
        for u in urls:
            self._seen.add(u)
        if not new:
            return []
        import asyncio
        tasks = [probe_url(u) for u in new]
        results = await asyncio.gather(*tasks, return_exceptions=False)
        self.results.extend(results)
        return list(results)

    def has_reachable_url(self) -> bool:
        return any(r.ok and r.status_code < 500 for r in self.results)

    def has_unreachable_url(self) -> bool:
        return any(not r.ok for r in self.results)

    def format_diagnostics(self, results: list[ProbeResult]) -> str:
        if not results:
            return ""
        return "\n".join(r.to_line() for r in results)


def append_probe_diagnostics(tool_result: str, results: list[ProbeResult]) -> str:
    if not results:
        return tool_result
    block = "\n".join(r.to_line() for r in results)
    if not tool_result.endswith("\n"):
        tool_result += "\n"
    return f"{tool_result}\n{block}\n"
