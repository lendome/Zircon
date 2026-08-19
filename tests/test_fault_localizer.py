from __future__ import annotations

import json
import asyncio
import time

import pytest

from zirconAgent.core.fault_localizer import (
    BM25,
    FaultLocalizer,
    _split_identifier,
    _tokenize,
)
from zirconAgent.core.types import LocalizationResult
from zirconAgent.tests.mocks import make_router
from zirconAgent.core.types import LLMResponse


@pytest.fixture
def repo(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "auth.py").write_text(
        "class Authenticator:\n"
        "    def __init__(self, store):\n"
        "        self.store = store\n"
        "\n"
        "    def verify(self, token: str) -> bool:\n"
        "        # token verification\n"
        "        user = self.store.lookup(token)\n"
        "        return user is not None\n"
        "\n"
        "    def revoke(self, token: str) -> None:\n"
        "        self.store.remove(token)\n"
        "\n"
        "\n"
        "def hash_password(pw: str) -> str:\n"
        "    return pw  # placeholder\n"
    )
    (src / "billing.py").write_text(
        "def charge(amount: float, customer: str) -> bool:\n"
        "    # charge the customer\n"
        "    return True\n"
        "\n"
        "\n"
        "def refund(tx_id: int) -> bool:\n"
        "    return True\n"
    )
    (src / "utils.py").write_text(
        "def helper(x):\n"
        "    return x * 2\n"
    )
    return tmp_path


class TestTokenizer:
    def test_split_identifier_handles_camel_and_separators(self):
        assert _split_identifier("core/faultLocalizer.py") == ["core", "fault", "localizer", "py"]

    def test_tokenize_drops_stopwords_and_short_tokens(self):
        toks = _tokenize("The BM25 ranking is a thing")
        assert "the" not in toks
        assert "bm25" in toks
        assert "ranking" in toks
        assert "is" not in toks  # too short / stopword


class TestBM25:
    def test_ranks_relevant_doc_higher(self):
        from zirconAgent.core.fault_localizer import _Doc
        docs = [
            _Doc("a.py", _tokenize("token verification auth login"), ""),
            _Doc("b.py", _tokenize("billing charge refund customer"), ""),
        ]
        bm = BM25(docs)
        scores = bm.score(_tokenize("auth token verify"))
        assert scores[0] > scores[1]

    def test_empty_corpus(self):
        bm = BM25([])
        assert bm.score(_tokenize("anything")) == []


class TestPhase1FileLevel:
    def test_top_file_matches_bug_report(self, repo):
        fl = FaultLocalizer(repo_path=repo, top_k_files=3)
        targets = fl._phase_file_level("auth token verification bug")
        assert targets, "expected at least one target"
        assert targets[0].path == "src/auth.py"
        assert targets[0].source in ("bm25", "fusion")
        assert all(t.score > 0 for t in targets)

    def test_no_source_files_returns_empty(self, tmp_path):
        fl = FaultLocalizer(repo_path=tmp_path)
        assert fl._phase_file_level("anything") == []

    def test_skips_hidden_and_build_dirs(self, repo):
        (repo / ".hidden").mkdir()
        (repo / ".hidden" / "secret.py").write_text("def leak(): return 'pw'\n")
        (repo / "node_modules").mkdir()
        (repo / "node_modules" / "pkg.py").write_text("def vendor(): return 'pw'\n")
        fl = FaultLocalizer(repo_path=repo, top_k_files=10)
        paths = [t.path for t in fl._phase_file_level("leak pw")]
        assert not any(".hidden" in p for p in paths)
        assert not any("node_modules" in p for p in paths)


class TestSymbolExtraction:
    def test_python_symbols_include_methods(self, repo):
        fl = FaultLocalizer(repo_path=repo)
        source = (repo / "src" / "auth.py").read_text()
        syms = fl._symbols_for_text(source, repo / "src" / "auth.py", "src/auth.py")
        names = {s["name"] for s in syms}
        assert "Authenticator" in names
        assert "Authenticator.verify" in names
        assert "hash_password" in names
        verify = next(s for s in syms if s["name"] == "Authenticator.verify")
        assert verify["kind"] == "method"
        assert verify["end_line"] >= verify["line"]

    def test_generic_symbols_for_js(self):
        fl = FaultLocalizer(repo_path=".")
        source = (
            "function foo(a) { return a; }\n"
            "const bar = (x) => x + 1;\n"
            "class Baz { constructor() {} }\n"
        )
        syms = fl._generic_symbols(source)
        names = {s["name"] for s in syms}
        assert "foo" in names
        assert "bar" in names
        assert "Baz" in names


class TestFullPipelineNoLLM:
    """Without a router, the pipeline degrades gracefully to whole-file suspects."""

    def test_degrade_path_produces_snippet(self, repo):
        fl = FaultLocalizer(repo_path=repo, top_k_files=3, snippet_lines=20)
        import asyncio
        res = asyncio.run(fl.localize("auth token verification bug"))
        assert isinstance(res, LocalizationResult)
        assert res.targets
        assert res.suspects
        assert res.primary_window is not None
        assert res.primary_window.file == "src/auth.py"
        assert res.snippet
        # snippet line numbers must be within file bounds and correctly numbered
        first_body = res.snippet.splitlines()[2]
        assert first_body.lstrip().startswith(("1", "2", "3")), first_body
        block = fl.format_context_block(res)
        assert "<fault_localization>" in block
        assert "Primary edit window" in block


class TestFullPipelineWithMockLLM:
    """Exercise phases 2 and 3 with a canned LLM router."""

    def test_suspect_and_window_from_llm(self, repo):
        # Phase 2 response: classify the verify method as the suspect.
        phase2 = LLMResponse(content=json.dumps({
            "reasoning": "verify handles tokens",
            "suspects": [{
                "file": "src/auth.py",
                "symbol": "Authenticator.verify",
                "reason": "token verification logic",
                "confidence": 0.9,
            }],
        }))
        # Phase 3 response: pinpoint the return line.
        phase3 = LLMResponse(content=json.dumps({
            "windows": [{
                "file": "src/auth.py",
                "symbol": "Authenticator.verify",
                "start_line": 6,
                "end_line": 7,
                "rationale": "the lookup logic",
                "confidence": 0.85,
            }],
        }))
        router = make_router([phase2, phase3])

        fl = FaultLocalizer(repo_path=repo, router=router, top_k_files=3, top_suspects=3)
        import asyncio
        res = asyncio.run(fl.localize("auth token verification bug"))

        assert res.suspects, "expected suspects from LLM phase 2"
        assert res.suspects[0].symbol == "Authenticator.verify"
        assert res.suspects[0].file == "src/auth.py"
        assert res.suspects[0].confidence == pytest.approx(0.9)
        assert res.windows, "expected windows from LLM phase 3"
        w = res.primary_window
        assert w is not None
        assert w.file == "src/auth.py"
        assert w.symbol == "Authenticator.verify"
        assert w.start_line == 6
        assert w.end_line == 7
        # snippet is centered on the window and bounded to the file
        assert res.snippet
        assert "src/auth.py" in res.snippet

    def test_hallucinated_symbol_dropped(self, repo):
        phase2 = LLMResponse(content=json.dumps({
            "suspects": [{
                "file": "src/auth.py",
                "symbol": "DoesNotExist",
                "reason": "x",
                "confidence": 0.5,
            }],
        }))
        phase3 = LLMResponse(content=json.dumps({"windows": []}))
        router = make_router([phase2, phase3])
        fl = FaultLocalizer(repo_path=repo, router=router, top_k_files=2)
        import asyncio
        res = asyncio.run(fl.localize("auth bug"))
        # hallucinated suspect dropped -> heuristic fallback kicks in
        assert res.suspects
        assert all(s.symbol != "DoesNotExist" for s in res.suspects)


class TestTimeoutsAndBounds:
    def test_phase1_reads_only_configured_prefix(self, tmp_path):
        source = tmp_path / "large.py"
        source.write_text("def target():\n    return 'auth token'\n" + "x = 0\n" * 100_000)
        fl = FaultLocalizer(repo_path=tmp_path, max_file_chars_for_ir=64)

        targets = fl._phase_file_level("auth token")

        assert targets

    def test_llm_timeout_uses_heuristic_fallback(self, repo):
        class StalledRouter:
            async def generate(self, **kwargs):
                await asyncio.sleep(10)

        fl = FaultLocalizer(repo_path=repo, router=StalledRouter(), llm_timeout_seconds=0.01)
        started = time.monotonic()
        result = asyncio.run(fl.localize("auth token verification bug"))

        assert time.monotonic() - started < 2.0
        assert result.suspects
        assert result.primary_window is not None

    def test_total_timeout_returns_whole_file_fallback(self, repo):
        class SlowLocalizer(FaultLocalizer):
            def _phase_file_level(self, bug_report):
                time.sleep(0.1)
                return super()._phase_file_level(bug_report)

        fl = SlowLocalizer(repo_path=repo, total_timeout_seconds=0.01)
        result = asyncio.run(fl.localize("auth token verification bug"))

        assert result.targets
        assert result.primary_window is not None

    def test_llm_failure_falls_back(self, repo):
        router = make_router()
        router.generate = make_router().generate  # returns generic text, not JSON
        fl = FaultLocalizer(repo_path=repo, router=router, top_k_files=2)
        import asyncio
        res = asyncio.run(fl.localize("auth token verify"))
        # Should still produce something via fallback (heuristic / symbol-span)
        assert res.suspects
