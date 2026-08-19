"""Intelligent error routing — routes errors to appropriate model tiers.

This module implements "Intelligent Model Routing" from the blueprint:

- Minor syntax errors (missing semicolons, unbalanced brackets) → cheap fast model
- Complex semantic errors (undefined names, logical flaws) → top-tier model
- Compiler/linter errors → cheap model for first-pass fix, falls back to top-tier
- Runtime test failures → top-tier model (requires full understanding)

Cost savings: Minor errors account for ~60% of edit-fix cycles. Routing them
to a cheap model saves ~60-80% on those iterations.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from .syntax_integration import is_minor_error
from .types import TierConfig

logger = logging.getLogger("agent.core.error_router")


class ErrorCategory:
    """Classification of error severity and complexity."""

    MINOR_SYNTAX = "minor_syntax"
    """Trivial syntax fix: missing colon, unbalanced parens, etc. Cheap model can fix."""

    MODERATE_SYNTAX = "moderate_syntax"
    """Multi-line structural errors. Top-tier model preferred."""

    SEMANTIC = "semantic"
    """Logic errors, undefined names, type mismatches. Must use top-tier model."""

    RUNTIME = "runtime"
    """Test failures, runtime exceptions. Must use top-tier model."""

    LINT = "lint"
    """Linter warnings (unused imports, style). Cheap model is fine."""


@dataclass
class ErrorInfo:
    """Information about a detected error for routing decisions."""

    file_path: str
    errors: str  # Formatted error text
    category: str = ErrorCategory.MINOR_SYNTAX
    line_count: int = 0
    error_count: int = 0
    contains_undefined_names: bool = False
    is_linter_warning: bool = False
    attempt_count: int = 0

    @property
    def is_minor(self) -> bool:
        """True if this error can be sent to a cheap model."""
        return self.category == ErrorCategory.MINOR_SYNTAX or \
               self.category == ErrorCategory.LINT

    @property
    def needs_top_tier(self) -> bool:
        """True if this error requires a top-tier model."""
        return self.category in (ErrorCategory.SEMANTIC, ErrorCategory.RUNTIME)


def classify_error(
    file_path: str,
    error_text: str,
    error_count: int = 1,
    attempt_count: int = 0,
) -> ErrorInfo:
    """Classify an error into a category for model routing.

    Analyses the error text and determines the appropriate model tier.
    """
    lower = error_text.lower()
    errors = error_text
    line_count = error_text.count("\n") + 1

    # Detect linter warnings (not errors)
    is_lint = "<syntax_warnings" in error_text
    has_undefined = "undefined" in lower or "not defined" in lower or \
                    "F821" in error_text or "F822" in error_text or "F823" in error_text

    if is_lint:
        return ErrorInfo(
            file_path=file_path,
            errors=errors,
            category=ErrorCategory.LINT,
            line_count=line_count,
            error_count=error_count,
            is_linter_warning=True,
            attempt_count=attempt_count,
        )

    if has_undefined:
        return ErrorInfo(
            file_path=file_path,
            errors=errors,
            category=ErrorCategory.SEMANTIC,
            line_count=line_count,
            error_count=error_count,
            contains_undefined_names=True,
            attempt_count=attempt_count,
        )

    # Check if it's a runtime/test error
    if any(kw in lower for kw in (
        "traceback", "assertionerror", "runtimeerror",
        "test failed", "assert", "segmentation fault",
        "stack trace", "exception in thread",
    )):
        return ErrorInfo(
            file_path=file_path,
            errors=errors,
            category=ErrorCategory.RUNTIME,
            line_count=line_count,
            error_count=error_count,
            attempt_count=attempt_count,
        )

    # Check if it's moderate (multi-line, structural)
    if error_count > 3 or line_count > 5:
        return ErrorInfo(
            file_path=file_path,
            errors=errors,
            category=ErrorCategory.MODERATE_SYNTAX,
            line_count=line_count,
            error_count=error_count,
            attempt_count=attempt_count,
        )

    # Default: minor syntax
    return ErrorInfo(
        file_path=file_path,
        errors=errors,
        category=ErrorCategory.MINOR_SYNTAX,
        line_count=line_count,
        error_count=error_count,
        attempt_count=attempt_count,
    )


def select_model_role(error_info: ErrorInfo) -> str:
    """Select the LLM role to use based on error classification.

    Returns:
        "fast" for minor errors, "editor"/"default" for moderate/semantic errors.
    """
    if error_info.is_minor:
        return "fast"
    return "default"


def select_fix_role(error_info: ErrorInfo) -> str:
    """Select the role for the fix attempt based on error info and retry count."""
    if error_info.attempt_count >= 1:
        # On retry, escalate to top-tier model
        return "editor"
    if error_info.needs_top_tier:
        return "editor"
    if error_info.is_minor:
        return "fast"
    return "default"