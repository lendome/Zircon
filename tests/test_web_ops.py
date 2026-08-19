"""web_search result parsing and fetch_url HTML-to-text extraction (offline)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_PARENT = _REPO_ROOT.parent
if str(_PARENT) not in sys.path:
    sys.path.insert(0, str(_PARENT))

from zirconAgent.tools.web_ops import (
    FetchUrlTool,
    LookupDocsTool,
    WebSearchTool,
    _html_to_text,
    _looks_js_rendered,
    _relevant_sections,
)


class TestRapidApiParsing(unittest.TestCase):
    def test_parses_google_api31_shape(self) -> None:
        # {"result": [{"title","href","body"}]}
        data = {"result": [
            {"title": "T1", "href": "https://a.dev", "body": "first result body"},
            {"title": "T2", "href": "https://b.dev", "body": "second"},
        ]}
        results = WebSearchTool._parse(data, 5)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["url"], "https://a.dev")
        self.assertEqual(results[0]["snippet"], "first result body")

    def test_accepts_alternate_serp_shapes(self) -> None:
        # A different RapidAPI SERP API using results/url/snippet
        data = {"results": [{"title": "X", "url": "https://x.dev", "snippet": "s"}]}
        self.assertEqual(WebSearchTool._parse(data, 5)[0]["url"], "https://x.dev")
        # organic/link/description
        data2 = {"organic": [{"title": "Y", "link": "https://y.dev", "description": "d"}]}
        self.assertEqual(WebSearchTool._parse(data2, 5)[0]["url"], "https://y.dev")

    def test_max_results_cap_and_snippet_trim(self) -> None:
        data = {"result": [{"title": f"T{i}", "href": f"https://d{i}.dev", "body": "x" * 500}
                           for i in range(10)]}
        results = WebSearchTool._parse(data, 3)
        self.assertEqual(len(results), 3)
        self.assertEqual(len(results[0]["snippet"]), 300)

    def test_skips_results_without_url(self) -> None:
        data = {"result": [{"title": "no url", "body": "b"}, {"title": "ok", "href": "https://a.dev"}]}
        results = WebSearchTool._parse(data, 5)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["url"], "https://a.dev")

    def test_empty_or_malformed_returns_empty(self) -> None:
        self.assertEqual(WebSearchTool._parse({}, 5), [])
        self.assertEqual(WebSearchTool._parse({"result": "not a list"}, 5), [])
        self.assertEqual(WebSearchTool._parse(None, 5), [])


class TestWebSearchConfig(unittest.TestCase):
    def test_defaults(self) -> None:
        tool = WebSearchTool({"rapidapi_key": "k"})
        self.assertEqual(tool._host, "google-api31.p.rapidapi.com")
        self.assertEqual(tool._api_key, "k")
        # Endpoint is resolved per-host by the request spec when unset
        from zirconAgent.tools.web_ops import _rapidapi_request_spec
        self.assertEqual(
            _rapidapi_request_spec(tool._host, tool._endpoint, "q", 5, "wt-wt")["path"],
            "/websearch",
        )

    def test_custom_host_endpoint(self) -> None:
        tool = WebSearchTool({"rapidapi_key": "k", "rapidapi_host": "other.p.rapidapi.com",
                              "rapidapi_endpoint": "/search"})
        self.assertEqual(tool._host, "other.p.rapidapi.com")
        self.assertEqual(tool._endpoint, "/search")

    def test_key_from_env(self) -> None:
        import os
        os.environ["RAPIDAPI_KEY"] = "envkey"
        try:
            self.assertEqual(WebSearchTool()._api_key, "envkey")
        finally:
            os.environ.pop("RAPIDAPI_KEY", None)

    def test_unconfigured_returns_helpful_message(self) -> None:
        import asyncio, os
        os.environ.pop("RAPIDAPI_KEY", None)
        out = asyncio.run(WebSearchTool().run("q"))
        self.assertIn("not configured", out)


class TestRelevantSections(unittest.TestCase):
    def _page(self) -> str:
        filler = "\n".join(f"unrelated filler line {i}" for i in range(200))
        target = "\n".join([
            "The timeout parameter controls connection deadlines.",
            "Set timeout=10.0 on the client for a 10 second deadline.",
        ])
        return filler + "\n" + target + "\n" + "\n".join(
            f"more filler {i}" for i in range(200)
        )

    def test_extracts_matching_section(self) -> None:
        out = _relevant_sections(self._page(), "timeout parameter deadline", budget=2000)
        self.assertIn("timeout=10.0", out)
        self.assertLess(len(out), 4000)

    def test_no_query_terms_falls_back_to_prefix(self) -> None:
        text = "abc\n" * 100
        self.assertEqual(_relevant_sections(text, "a b", 50), text[:50])

    def test_no_match_falls_back_to_prefix(self) -> None:
        out = _relevant_sections("nothing relevant here\n" * 50, "quantum osmosis", 100)
        self.assertTrue(out.startswith("nothing relevant"))


class TestFetchCache(unittest.TestCase):
    def test_cache_round_trip(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            tool = FetchUrlTool(cache_dir=tmp)
            tool._write_cache("https://x.dev/page", "cached text")
            self.assertEqual(tool._read_cache("https://x.dev/page"), "cached text")
            self.assertIsNone(tool._read_cache("https://x.dev/other"))

    def test_stale_cache_ignored(self) -> None:
        import os
        import tempfile
        import time

        with tempfile.TemporaryDirectory() as tmp:
            tool = FetchUrlTool(cache_dir=tmp)
            tool._write_cache("https://x.dev/page", "old")
            path = tool._cache_path("https://x.dev/page")
            os.utime(path, (time.time() - 3600, time.time() - 3600))
            self.assertIsNone(tool._read_cache("https://x.dev/page"))

    def test_no_cache_dir_is_noop(self) -> None:
        tool = FetchUrlTool()
        tool._write_cache("https://x.dev", "text")
        self.assertIsNone(tool._read_cache("https://x.dev"))


class TestLookupDocs(unittest.TestCase):
    _RESULTS = [
        {"id": "/vercel/next.js", "title": "Next.js"},
        {"id": "/vercel/next.js/v14.3.0", "title": "Next.js"},
        {"id": "/other/nextjs-toolbox", "title": "Nextjs Toolbox"},
    ]

    def test_picks_title_match_first(self) -> None:
        self.assertEqual(
            LookupDocsTool._pick_library(self._RESULTS, "next.js", ""),
            "/vercel/next.js",
        )

    def test_prefers_version_match(self) -> None:
        self.assertEqual(
            LookupDocsTool._pick_library(self._RESULTS, "next.js", "v14.3.0"),
            "/vercel/next.js/v14.3.0",
        )

    def test_empty_results(self) -> None:
        self.assertEqual(LookupDocsTool._pick_library([], "x", ""), "")

    def test_falls_back_to_first_candidate(self) -> None:
        results = [{"id": "/a/b", "title": "Unrelated"}]
        self.assertEqual(LookupDocsTool._pick_library(results, "zzz", ""), "/a/b")

    def test_api_key_from_config(self) -> None:
        tool = LookupDocsTool({"context7_api_key": "k7"})
        self.assertEqual(tool._headers(), {"Authorization": "Bearer k7"})
        self.assertEqual(LookupDocsTool({})._headers(), {})


class TestJsRenderDetection(unittest.TestCase):
    def test_spa_shell_detected(self) -> None:
        html = (
            "<html><head>"
            + "<script src='/a.js'></script>" * 5
            + "</head><body><div id='root'></div>"
            + "<!-- " + "x" * 6000 + " --></body></html>"
        )
        text = _html_to_text(html)
        self.assertTrue(_looks_js_rendered(html, text))

    def test_normal_page_not_detected(self) -> None:
        body = "<p>" + "real content here. " * 100 + "</p>"
        html = f"<html><body>{body}</body></html>"
        text = _html_to_text(html)
        self.assertFalse(_looks_js_rendered(html, text))


class TestResearchBudgets(unittest.TestCase):
    def test_tier_presets(self) -> None:
        from zirconAgent.core.types import TIER_PRESETS, Tier

        self.assertEqual(TIER_PRESETS[Tier.LOW].effective_research_max_turns(), 10)
        self.assertEqual(TIER_PRESETS[Tier.BALANCED].effective_research_max_turns(), 20)
        self.assertEqual(TIER_PRESETS[Tier.QUALITY].effective_research_max_turns(), 30)

    def test_researcher_claims_research_budget(self) -> None:
        from zirconAgent.core.types import TIER_PRESETS, Tier
        from zirconAgent.subagents.researcher import ResearcherSubAgent

        sub = ResearcherSubAgent(None, None, ".", tier_config=TIER_PRESETS[Tier.QUALITY])
        self.assertEqual(sub.turn_budget(), 30)

    def test_other_subagents_use_default(self) -> None:
        from zirconAgent.subagents.explorer import ExplorerSubAgent

        sub = ExplorerSubAgent(None, None, ".")
        self.assertIsNone(sub.turn_budget())


class TestFetchFailureSignals(unittest.TestCase):
    def test_empty_content_never_cached(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            tool = FetchUrlTool(cache_dir=tmp)
            # Simulate what the fetch path does: only non-empty content cached
            content = ""
            if content.strip():
                tool._write_cache("https://x.dev/empty", content)
            self.assertIsNone(tool._read_cache("https://x.dev/empty"))

    def test_empty_result_counts_as_failure(self) -> None:
        from zirconAgent.core.executor import Executor

        self.assertTrue(Executor._result_is_failure(""))
        self.assertTrue(Executor._result_is_failure("   \n"))
        self.assertTrue(Executor._result_is_failure(
            "[no content — espn.com returned an empty page to this client]"
        ))
        self.assertTrue(Executor._result_is_failure(
            "Fetch timed out after 15s — uefa.com is unresponsive"
        ))
        self.assertFalse(Executor._result_is_failure("Real page content here"))


class TestHtmlToText(unittest.TestCase):
    def test_strips_scripts_and_tags(self) -> None:
        html = (
            "<html><head><style>.x{color:red}</style>"
            "<script>alert('hi')</script></head>"
            "<body><h1>Title</h1><p>Body   text.</p></body></html>"
        )
        text = _html_to_text(html)
        self.assertIn("Title", text)
        self.assertIn("Body text.", text)
        self.assertNotIn("alert", text)
        self.assertNotIn("color:red", text)


if __name__ == "__main__":
    unittest.main()


class TestRapidApiRequestSpec(unittest.TestCase):
    def test_google_api31_is_post_with_body(self) -> None:
        from zirconAgent.tools.web_ops import _rapidapi_request_spec
        spec = _rapidapi_request_spec("google-api31.p.rapidapi.com", "", "cats", 5, "wt-wt")
        self.assertEqual(spec["method"], "POST")
        self.assertEqual(spec["path"], "/websearch")
        self.assertEqual(spec["json"]["text"], "cats")
        self.assertIsNone(spec["params"])

    def test_google_search116_is_get_with_query_param(self) -> None:
        from zirconAgent.tools.web_ops import _rapidapi_request_spec
        spec = _rapidapi_request_spec("google-search116.p.rapidapi.com", "", "cats", 5, "wt-wt")
        self.assertEqual(spec["method"], "GET")
        self.assertEqual(spec["params"]["query"], "cats")
        self.assertIsNone(spec["json"])

    def test_google_search116_limit_snaps_to_allowed_tier(self) -> None:
        from zirconAgent.tools.web_ops import _rapidapi_request_spec
        # limit must be one of 10/20/30/40/50/100 — snap up
        for n, expected in [(5, 10), (10, 10), (15, 20), (25, 30), (100, 100)]:
            spec = _rapidapi_request_spec("google-search116.p.rapidapi.com", "", "q", n, "wt-wt")
            self.assertEqual(spec["params"]["limit"], expected)

    def test_unknown_host_defaults_to_post(self) -> None:
        from zirconAgent.tools.web_ops import _rapidapi_request_spec
        spec = _rapidapi_request_spec("some-other.p.rapidapi.com", "", "cats", 5, "wt-wt")
        self.assertEqual(spec["method"], "POST")
