"""Offline tests for the BrowseComp benchmark harness (no network, no LLM)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_PARENT = _REPO_ROOT.parent
if str(_PARENT) not in sys.path:
    sys.path.insert(0, str(_PARENT))

from zirconAgent.benchmark.browsecomp import (
    decrypt,
    encrypt,
    extract_final_answer,
    load_dataset,
)


class TestDecryption(unittest.TestCase):
    def test_roundtrip(self) -> None:
        secret = "Who painted the ceiling? — Michelangelo (1512)"
        password = "canary-abc-123"
        self.assertEqual(decrypt(encrypt(secret, password), password), secret)

    def test_wrong_password_garbles(self) -> None:
        ct = encrypt("plaintext", "right")
        try:
            wrong = decrypt(ct, "wrong")
        except UnicodeDecodeError:
            return  # garbled to invalid utf-8: also acceptable
        self.assertNotEqual(wrong, "plaintext")


class TestDatasetLoading(unittest.TestCase):
    def test_load_synthetic_csv(self) -> None:
        canary = "BENCH-CANARY-1"
        q = encrypt("Which year did X happen?", canary)
        a = encrypt("1987", canary)
        csv_text = f"problem,answer,canary\n{q},{a},{canary}\n"
        rows = load_dataset(csv_text)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["question"], "Which year did X happen?")
        self.assertEqual(rows[0]["answer"], "1987")

    def test_rows_without_canary_skipped(self) -> None:
        csv_text = "problem,answer,canary\nabc,def,\n"
        self.assertEqual(load_dataset(csv_text), [])

    def test_undecryptable_rows_skipped(self) -> None:
        csv_text = "problem,answer,canary\nnot-base64!!!,also-bad,key\n"
        self.assertEqual(load_dataset(csv_text), [])


class TestAnswerExtraction(unittest.TestCase):
    def test_extracts_final_answer_line(self) -> None:
        out = "I searched around.\nFINAL ANSWER: Michelangelo\nCONFIDENCE: 90"
        self.assertEqual(extract_final_answer(out), "Michelangelo")

    def test_uses_last_final_answer(self) -> None:
        out = "FINAL ANSWER: draft\nmore thinking\nFINAL ANSWER: 1987\nCONFIDENCE: 70"
        self.assertEqual(extract_final_answer(out), "1987")

    def test_fallback_last_line(self) -> None:
        self.assertEqual(extract_final_answer("no marker\njust text"), "just text")
        self.assertEqual(extract_final_answer(""), "")


class TestGradeViaFunctionCall(unittest.TestCase):
    """Grading reads the verdict from a forced submit_verdict tool call —
    never from parsed prose."""

    def _router(self, tool_calls=None, raise_exc=None):
        import asyncio
        from zirconAgent.core.types import ToolCall

        class Router:
            _profiles = {}

            async def generate(self, role, messages, tools=None, max_tokens=0,
                               tool_choice=None, **kw):
                if raise_exc:
                    raise raise_exc
                class R:
                    pass
                r = R()
                r.tool_calls = tool_calls or []
                r.content = ""
                return r

        return Router()

    def test_reads_true_verdict(self) -> None:
        import asyncio
        from zirconAgent.benchmark.browsecomp import grade
        from zirconAgent.core.types import ToolCall
        router = self._router([ToolCall("1", "submit_verdict", {"correct": True})])
        self.assertTrue(asyncio.run(grade(router, "q", "gold", "pred")))

    def test_reads_false_verdict(self) -> None:
        import asyncio
        from zirconAgent.benchmark.browsecomp import grade
        from zirconAgent.core.types import ToolCall
        router = self._router([ToolCall("1", "submit_verdict", {"correct": False})])
        self.assertFalse(asyncio.run(grade(router, "q", "gold", "pred")))

    def test_empty_prediction_short_circuits(self) -> None:
        import asyncio
        from zirconAgent.benchmark.browsecomp import grade
        router = self._router([])  # generate would return no verdict
        self.assertFalse(asyncio.run(grade(router, "q", "gold", "")))

    def test_missing_verdict_raises_not_guesses(self) -> None:
        import asyncio
        from zirconAgent.benchmark.browsecomp import grade
        router = self._router(tool_calls=[])  # model never calls the tool
        with self.assertRaises(RuntimeError):
            asyncio.run(grade(router, "q", "gold", "some prediction"))


if __name__ == "__main__":
    unittest.main()
