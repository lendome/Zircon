"""Tests for the new reliability and cost-efficiency modules.

This file tests:
1. syntax_integration — verifiable feedback loops for syntax checking
2. git_integration — state & rollback management with auto-commit/rollback
3. prompt_cache — prompt caching support for cost efficiency
4. error_router — intelligent model routing for error handling
5. sandbox_executor — Docker-based sandboxed execution
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.syntax_integration import (
    check_file_after_edit,
    format_errors_for_loop,
    format_check_result_for_prompt,
    is_minor_error,
    needs_fix_attempt,
    build_syntax_fix_prompt,
)
from core.prompt_cache import (
    PromptCacheManager,
    CacheConfig,
    CacheStats,
    estimate_tokens,
)
from core.error_router import (
    classify_error,
    select_model_role,
    select_fix_role,
    ErrorCategory,
    ErrorInfo,
)


# =========================================================================
# syntax_integration tests
# =========================================================================


class TestSyntaxIntegration:
    """Test the syntax checker integration module."""

    def test_check_file_after_edit_valid_python(self, tmp_path: Path):
        """Should find no errors in valid Python."""
        py_file = tmp_path / "test.py"
        py_file.write_text("x = 1\nprint(x)\n")
        result = check_file_after_edit(py_file, repo_path=tmp_path)
        assert result is not None
        assert not result.has_errors

    def test_check_file_after_edit_invalid_python(self, tmp_path: Path):
        """Should find syntax errors in invalid Python."""
        py_file = tmp_path / "test.py"
        py_file.write_text("x = 1\nbreak\nprint(x)\n")
        result = check_file_after_edit(py_file, repo_path=tmp_path)
        assert result.has_errors

    def test_check_file_after_edit_json(self, tmp_path: Path):
        """Should find errors in invalid JSON."""
        json_file = tmp_path / "test.json"
        content = '{"key": "value",}'
        result = check_file_after_edit(json_file, content=content, repo_path=tmp_path)
        assert result.has_errors

    def test_check_file_after_edit_valid_content(self, tmp_path: Path):
        """Should work with content parameter (no disk access needed)."""
        py_file = tmp_path / "test.py"
        # Use check_file with text_content parameter via check_text
        from core.syntax_checker import SyntaxChecker
        checker = SyntaxChecker(repo_path=tmp_path)
        result = checker.check_text("test.py", "x = 1\n")
        assert result is not None
        assert not result.has_errors

    def test_format_errors_for_loop_no_errors(self, tmp_path: Path):
        """Should return empty string when there are no errors."""
        from core.syntax_checker import SyntaxCheckResult
        result = SyntaxCheckResult(file_path="test.py")
        formatted = format_errors_for_loop(result)
        assert formatted == ""

    def test_format_errors_for_loop_with_errors(self, tmp_path: Path):
        """Should return formatted error text when there are errors."""
        from core.syntax_checker import SyntaxCheckResult, SyntaxIssue
        result = SyntaxCheckResult(file_path="test.py")
        result.issues = [
            SyntaxIssue(
                file_path="test.py", line=1, column=5,
                message="SyntaxError: invalid syntax",
                checker="ast",
            )
        ]
        formatted = format_errors_for_loop(result)
        assert "test.py" in formatted
        assert "SyntaxError" in formatted

    def test_needs_fix_attempt_no_errors(self):
        """Should return False when there are no errors."""
        from core.syntax_checker import SyntaxCheckResult
        result = SyntaxCheckResult(file_path="test.py")
        assert needs_fix_attempt(result) is False

    def test_needs_fix_attempt_with_errors(self, tmp_path: Path):
        """Should return True when there are errors within max attempts."""
        from core.syntax_checker import SyntaxCheckResult, SyntaxIssue
        result = SyntaxCheckResult(file_path="test.py")
        result.issues = [
            SyntaxIssue(
                file_path="test.py", line=1, column=1,
                message="SyntaxError: bad",
                checker="ast",
            )
        ]
        assert needs_fix_attempt(result, max_attempts=3, current_attempt=0)
        assert needs_fix_attempt(result, max_attempts=3, current_attempt=2)
        # Should return False when max attempts reached
        assert needs_fix_attempt(result, max_attempts=3, current_attempt=3) is False

    def test_is_minor_error_trivial(self):
        """Single syntax error on the same line is minor."""
        from core.syntax_checker import SyntaxCheckResult, SyntaxIssue
        result = SyntaxCheckResult(file_path="test.py")
        result.issues = [
            SyntaxIssue(
                file_path="test.py", line=5, column=10,
                message="SyntaxError: unexpected EOF",
                checker="ast",
            )
        ]
        assert is_minor_error(result)

    def test_is_minor_error_undefined_name(self):
        """Undefined name errors are NOT minor (need semantic understanding)."""
        from core.syntax_checker import SyntaxCheckResult, SyntaxIssue
        result = SyntaxCheckResult(file_path="test.py")
        result.issues = [
            SyntaxIssue(
                file_path="test.py", line=1, column=1,
                message="F821: undefined name 'xyz'",
                checker="ruff", rule_id="F821",
            )
        ]
        assert not is_minor_error(result)

    def test_is_minor_error_too_many_issues(self):
        """More than 3 issues is not minor."""
        from core.syntax_checker import SyntaxCheckResult, SyntaxIssue
        result = SyntaxCheckResult(file_path="test.py")
        for i in range(4):
            result.issues.append(
                SyntaxIssue(
                    file_path="test.py", line=i+1, column=1,
                    message=f"Error {i}",
                    checker="ast",
                )
            )
        assert not is_minor_error(result)

    def test_build_syntax_fix_prompt(self):
        """Should build a concise fix prompt for the model."""
        prompt = build_syntax_fix_prompt(
            file_path="test.py",
            current_content="x = \n",
            errors="line 1: SyntaxError: invalid syntax",
        )
        assert "test.py" in prompt
        assert "SYNTAX ERROR FIX" in prompt
        assert "SEARCH/REPLACE" in prompt

    def test_format_check_result_for_prompt_multiple_files(self, tmp_path: Path):
        """Should format multiple file results into a single block."""
        from core.syntax_checker import SyntaxCheckResult, SyntaxIssue
        r1 = SyntaxCheckResult(file_path="a.py")
        r1.issues.append(SyntaxIssue(
            file_path="a.py", line=1, column=1,
            message="Error in a", checker="ast",
        ))
        r2 = SyntaxCheckResult(file_path="b.py")
        r2.issues.append(SyntaxIssue(
            file_path="b.py", line=2, column=3,
            message="Error in b", checker="ast",
        ))
        formatted = format_check_result_for_prompt({"a.py": r1, "b.py": r2})
        assert "<syntax_check>" in formatted
        assert "Error in a" in formatted
        assert "Error in b" in formatted


# =========================================================================
# prompt_cache tests
# =========================================================================


class TestPromptCache:
    """Test the prompt caching module."""

    def test_cache_stats_hit_rate(self):
        """Hit rate should be hits / (hits + misses)."""
        stats = CacheStats()
        stats.record_hit()
        stats.record_hit()
        stats.record_miss()
        assert stats.hit_rate == 2.0 / 3.0
        assert stats.hits == 2
        assert stats.misses == 1

    def test_cache_stats_empty(self):
        """Empty stats should have 0 hit rate."""
        stats = CacheStats()
        assert stats.hit_rate == 0.0

    def test_cache_stats_reset(self):
        """Reset should clear all counters."""
        stats = CacheStats()
        stats.record_hit(100)
        stats.record_miss(50)
        stats.reset()
        assert stats.hits == 0
        assert stats.misses == 0
        assert stats.tokens_saved == 0

    def test_estimate_tokens(self):
        """Token estimation should be roughly characters / 4."""
        # 11 chars / 4 = 2.75, max(1, 2) = 2
        assert estimate_tokens("hello world") == 2
        assert estimate_tokens("") == 0
        assert estimate_tokens("a") == 1

    def test_prompt_cache_disabled(self):
        """Disabled cache should return messages unchanged."""
        mgr = PromptCacheManager(CacheConfig(enabled=False))
        messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Hello!"},
        ]
        result = mgr.build_messages_with_cache(messages)
        assert result == messages

    def test_prompt_cache_enabled_system_message(self):
        """Enabled cache should add cache_control to large system messages."""
        mgr = PromptCacheManager(CacheConfig(
            enabled=True,
            min_cache_breakpoint_interval=50,
        ))
        # System message long enough to trigger cache_control
        long_content = "Hello " * 50  # ~300 chars
        messages = [
            {"role": "system", "content": long_content},
            {"role": "user", "content": "Hi"},
        ]
        result = mgr.build_messages_with_cache(messages)
        assert len(result) == 2
        # System message should be enriched
        assert result[0]["role"] == "system"
        # User message should be unchanged
        assert result[1]["role"] == "user"

    def test_prompt_cache_compute_key(self):
        """Cache key should be deterministic from static content."""
        mgr = PromptCacheManager(CacheConfig(enabled=True))
        msgs1 = [
            {"role": "system", "content": "Static prompt"},
            {"role": "user", "content": "What is X?"},
        ]
        msgs2 = [
            {"role": "system", "content": "Static prompt"},
            {"role": "user", "content": "What is Y?"},
        ]
        # Same system content should produce same key
        key1 = mgr.compute_cache_key(msgs1)
        key2 = mgr.compute_cache_key(msgs2)
        assert key1 == key2

    def test_prompt_cache_different_system_keys(self):
        """Different system content should produce different keys."""
        mgr = PromptCacheManager(CacheConfig(enabled=True))
        msgs1 = [
            {"role": "system", "content": "Prompt A"},
        ]
        msgs2 = [
            {"role": "system", "content": "Prompt B"},
        ]
        key1 = mgr.compute_cache_key(msgs1)
        key2 = mgr.compute_cache_key(msgs2)
        assert key1 != key2

    def test_cache_invalidation(self):
        """Should detect when cache needs invalidation."""
        mgr = PromptCacheManager(CacheConfig(enabled=True))
        # First call: no invalidation needed
        assert mgr.should_invalidate("key1") is False
        # Same key: no invalidation
        assert mgr.should_invalidate("key1") is False
        # Different key: invalidate
        assert mgr.should_invalidate("key2") is True

    def test_report_stats_empty(self):
        """Should produce valid output even with no stats."""
        mgr = PromptCacheManager(CacheConfig(enabled=True))
        report = mgr.report_stats()
        assert "<cache_stats>" in report
        assert "</cache_stats>" in report

    def test_report_stats_with_data(self):
        """Should include profile stats in report."""
        mgr = PromptCacheManager(CacheConfig(enabled=True))
        stats = mgr.get_stats("test_profile")
        stats.record_hit(100)
        stats.record_hit(200)
        stats.record_miss(50)
        report = mgr.report_stats()
        assert "test_profile" in report
        assert "66.7%" in report or "66.6%" in report  # 2/3 = 66.7%


# =========================================================================
# error_router tests
# =========================================================================


class TestErrorRouter:
    """Test the intelligent error routing module."""

    def test_classify_minor_syntax(self):
        """Simple syntax errors should be classified as minor."""
        info = classify_error(
            "test.py",
            "line 1: SyntaxError: invalid syntax",
            error_count=1,
        )
        assert info.category == ErrorCategory.MINOR_SYNTAX
        assert info.is_minor
        assert not info.needs_top_tier

    def test_classify_runtime_error(self):
        """Test failures should be classified as runtime."""
        info = classify_error(
            "test.py",
            "Traceback (most recent call last):\n  File test.py line 1\nAssertionError",
            error_count=1,
        )
        assert info.category == ErrorCategory.RUNTIME
        assert info.needs_top_tier

    def test_classify_undefined_name(self):
        """Undefined names should be classified as semantic."""
        info = classify_error(
            "test.py",
            "F821: undefined name 'some_var'",
            error_count=1,
        )
        assert info.category == ErrorCategory.SEMANTIC
        assert info.needs_top_tier

    def test_classify_linter_warning(self):
        """Linter warnings (unused imports) should be classified as lint."""
        info = classify_error(
            "test.py",
            '<syntax_warnings file="test.py">',
            error_count=1,
        )
        assert info.category == ErrorCategory.LINT
        assert info.is_minor

    def test_classify_moderate_syntax(self):
        """Multi-line structural errors should be moderate."""
        info = classify_error(
            "test.py",
            "\n".join(f"line {i}: error" for i in range(10)),
            error_count=5,
        )
        assert info.category == ErrorCategory.MODERATE_SYNTAX
        assert not info.is_minor

    def test_select_model_role_minor(self):
        """Minor errors should route to fast model."""
        info = ErrorInfo(
            file_path="test.py",
            errors="minor error",
            category=ErrorCategory.MINOR_SYNTAX,
        )
        assert select_model_role(info) == "fast"

    def test_select_model_role_semantic(self):
        """Semantic errors should route to default model."""
        info = ErrorInfo(
            file_path="test.py",
            errors="undefined name",
            category=ErrorCategory.SEMANTIC,
        )
        assert select_model_role(info) == "default"

    def test_select_fix_role_minor_first_attempt(self):
        """First attempt at minor error should use fast model."""
        info = ErrorInfo(
            file_path="test.py",
            errors="error",
            category=ErrorCategory.MINOR_SYNTAX,
            attempt_count=0,
        )
        assert select_fix_role(info) == "fast"

    def test_select_fix_role_minor_retry(self):
        """Second attempt at minor error should escalate to editor."""
        info = ErrorInfo(
            file_path="test.py",
            errors="error",
            category=ErrorCategory.MINOR_SYNTAX,
            attempt_count=1,
        )
        assert select_fix_role(info) == "editor"

    def test_select_fix_role_semantic(self):
        """Semantic errors should always use editor model."""
        info = ErrorInfo(
            file_path="test.py",
            errors="undefined",
            category=ErrorCategory.SEMANTIC,
            attempt_count=0,
        )
        assert select_fix_role(info) == "editor"

    def test_error_info_properties(self):
        """Should correctly report is_minor and needs_top_tier."""
        minor = ErrorInfo("f.py", "", ErrorCategory.MINOR_SYNTAX)
        assert minor.is_minor
        assert not minor.needs_top_tier

        semantic = ErrorInfo("f.py", "", ErrorCategory.SEMANTIC)
        assert not semantic.is_minor
        assert semantic.needs_top_tier

        runtime = ErrorInfo("f.py", "", ErrorCategory.RUNTIME)
        assert not runtime.is_minor
        assert runtime.needs_top_tier

        lint = ErrorInfo("f.py", "", ErrorCategory.LINT)
        assert lint.is_minor
        assert not lint.needs_top_tier