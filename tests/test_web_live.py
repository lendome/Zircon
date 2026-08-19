"""Live network tests for fetch/search against real anti-bot sites.

These hit the network, so they are SKIPPED by default. Enable with:
    ZIRCON_LIVE_TESTS=1 python -m unittest zirconAgent.tests.test_web_live

They are regression guards for the TLS-fingerprinting fix: these exact
URLs returned zero bytes / timed out via httpx and only resolve because
fetches now go through curl_cffi's browser TLS impersonation. If a future
change breaks the transport, these turn red.
"""

from __future__ import annotations

import asyncio
import os
import sys
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_PARENT = _REPO_ROOT.parent
if str(_PARENT) not in sys.path:
    sys.path.insert(0, str(_PARENT))

from zirconAgent.tools.web_ops import FetchUrlTool, WebSearchTool, _HAS_CURL

# The two URLs that exposed the TLS-fingerprinting problem in a real run.
_ESPN = "https://www.espn.com/soccer/match/_/gameId/515276/bolton-wanderers-leeds-united"
_UEFA = "https://www.uefa.com/uefaeuropaleague/match/2014566--everton-vs-krasnodar/lineups/"

_LIVE = os.environ.get("ZIRCON_LIVE_TESTS") == "1"


@unittest.skipUnless(_LIVE, "set ZIRCON_LIVE_TESTS=1 to run network tests")
class TestLiveAntiBotFetch(unittest.TestCase):
    def test_curl_cffi_is_installed(self) -> None:
        # The whole point of these URLs is the curl_cffi path; warn loudly
        # if it silently fell back to httpx.
        self.assertTrue(_HAS_CURL, "curl_cffi not installed — TLS impersonation inactive")

    def test_espn_returns_real_content(self) -> None:
        out = asyncio.run(FetchUrlTool().run(_ESPN, max_length=4000))
        self.assertNotIn("[no content", out)
        self.assertNotIn("Fetch timed out", out)
        self.assertGreater(len(out), 500)
        # Match-stats page: some football content must be present
        self.assertRegex(out, r"(?i)shots|possession|corner|Bolton|Leeds")

    def test_uefa_lineups_resolve(self) -> None:
        out = asyncio.run(FetchUrlTool().run(_UEFA, max_length=2000))
        self.assertNotIn("Fetch timed out", out)
        self.assertNotIn("[no content", out)
        self.assertRegex(out, r"(?i)everton|krasnodar|line")

    def test_ddg_keyless_search_works(self) -> None:
        out = asyncio.run(WebSearchTool()._search_ddg("python asyncio gather", 3))
        self.assertNotIn("cooling down", out)
        self.assertNotIn("rate-limited", out)
        self.assertTrue(out.strip().startswith("1."))


if __name__ == "__main__":
    unittest.main()
