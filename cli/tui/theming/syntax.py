"""
Syntax highlighting rules — maps parser scopes to theme colors.

Powers code blocks, the prompt textarea, and diff rendering. Rules cover
keywords, strings, numbers, types, markdown, diffs, errors, warnings, etc.

Thinking/reasoning blocks use a separate subtle syntax with reduced opacity.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .colors import Color, with_alpha
from .theme import Theme


@dataclass
class SyntaxStyle:
    fg: Color | None = None
    bg: Color | None = None
    bold: bool = False
    italic: bool = False
    underline: bool = False


@dataclass
class SyntaxRule:
    scopes: list[str]
    style: SyntaxStyle = field(default_factory=SyntaxStyle)


def get_syntax_rules(theme: Theme) -> list[SyntaxRule]:
    """Build syntax highlighting rules from a theme."""
    return [
        SyntaxRule(["comment"], SyntaxStyle(fg=theme.syntax_comment, italic=True)),
        SyntaxRule(["keyword"], SyntaxStyle(fg=theme.syntax_keyword, italic=True)),
        SyntaxRule(["string", "symbol"], SyntaxStyle(fg=theme.syntax_string)),
        SyntaxRule(["function", "constructor"], SyntaxStyle(fg=theme.syntax_function)),
        SyntaxRule(["number", "boolean"], SyntaxStyle(fg=theme.syntax_number)),
        SyntaxRule(["type", "module"], SyntaxStyle(fg=theme.syntax_type)),
        # Markdown
        SyntaxRule(["heading"], SyntaxStyle(fg=theme.markdown_heading, bold=True)),
        SyntaxRule(["link"], SyntaxStyle(fg=theme.markdown_link, underline=True)),
        SyntaxRule(["code"], SyntaxStyle(fg=theme.markdown_code)),
        # Diff
        SyntaxRule(["diff_added"], SyntaxStyle(fg=theme.diff_added)),
        SyntaxRule(["diff_removed"], SyntaxStyle(fg=theme.diff_removed)),
        SyntaxRule(["diff_context"], SyntaxStyle(fg=theme.diff_context)),
        # Errors / warnings
        SyntaxRule(["error"], SyntaxStyle(fg=theme.error, bold=True)),
        SyntaxRule(["warning"], SyntaxStyle(fg=theme.warning)),
        # Punctuation
        SyntaxRule(["operator", "punctuation"], SyntaxStyle(fg=theme.text_muted)),
        # Variable / identifier
        SyntaxRule(["variable", "identifier"], SyntaxStyle(fg=theme.text)),
    ]


def generate_subtle_syntax(theme: Theme) -> list[SyntaxRule]:
    """Generate dimmed syntax rules for thinking/reasoning blocks.

    Each rule's foreground color is reduced to theme.thinking_opacity.
    """
    rules = get_syntax_rules(theme)
    return [
        SyntaxRule(
            scopes=rule.scopes,
            style=SyntaxStyle(
                fg=with_alpha(rule.style.fg, theme.thinking_opacity) if rule.style.fg else None,
                bg=rule.style.bg,
                bold=rule.style.bold,
                italic=rule.style.italic,
                underline=rule.style.underline,
            ),
        )
        for rule in rules
    ]
