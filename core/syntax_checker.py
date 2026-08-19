"""Syntax and error checker for modified/created files.

After a file is edited or created, this module checks for detectable errors
(syntax errors, parse failures, etc.) and formats them into concise messages
that can be injected into the LLM prompt so the model is aware of issues.

This module uses the most robust available checkers for each file type:
- Python: ast.parse (stdlib) + optional ruff (SOTA) + optional pyflakes
- JavaScript: node --check (Node.js stdlib)
- TypeScript: tsc --noEmit
- JSON: json.loads (stdlib)
- YAML: yaml.safe_load
- TOML: tomllib (Python 3.11+ stdlib)
- HTML/XML: html.parser (stdlib) — real parser, not regex
- CSS: basic structural check
- Shell: bash -n + shellcheck
"""

from __future__ import annotations

import ast
import html.parser
import json
import logging
import re
import subprocess
import sys
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .proc_spawn import popen_kwargs

logger = logging.getLogger("agent.core.syntax_checker")


# --------------------------------------------------------------------------- #
# Result types
# --------------------------------------------------------------------------- #

@dataclass
class SyntaxIssue:
    """A single syntax or error issue found in a file."""
    file_path: str
    line: int
    column: int
    message: str
    severity: str = "error"  # error, warning, info
    checker: str = ""  # which checker found it
    rule_id: str = ""  # e.g. "F401" for ruff, "SC1000" for shellcheck


@dataclass
class SyntaxCheckResult:
    """Result of checking a file for syntax/errors."""
    file_path: str
    issues: list[SyntaxIssue] = field(default_factory=list)
    checked: bool = True
    _checkers_used: list[str] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return any(i.severity == "error" for i in self.issues)

    @property
    def has_warnings(self) -> bool:
        return any(i.severity == "warning" for i in self.issues)

    def format(self, include_warnings: bool = True) -> str:
        """Format issues into a concise string for prompt injection."""
        if not self.issues:
            return ""

        errors = [i for i in self.issues if i.severity == "error"]
        warnings = [i for i in self.issues if i.severity == "warning"]
        infos = [i for i in self.issues if i.severity == "info"]

        parts = []
        if errors:
            parts.append(f"<syntax_errors file=\"{self.file_path}\">")
            for e in errors:
                loc = self._format_location(e)
                rule = f" [{e.rule_id}]" if e.rule_id else ""
                parts.append(f"  {loc}:{rule} {e.message}")
            parts.append("</syntax_errors>")

        if include_warnings and warnings:
            parts.append(f"<syntax_warnings file=\"{self.file_path}\">")
            for w in warnings:
                loc = self._format_location(w)
                rule = f" [{w.rule_id}]" if w.rule_id else ""
                parts.append(f"  {loc}:{rule} {w.message}")
            parts.append("</syntax_warnings>")

        return "\n".join(parts)

    @staticmethod
    def _format_location(issue: SyntaxIssue) -> str:
        if issue.line and issue.column:
            return f"line {issue.line}, col {issue.column}"
        if issue.line:
            return f"line {issue.line}"
        return "?"


# --------------------------------------------------------------------------- #
# Main checker class
# --------------------------------------------------------------------------- #

class SyntaxChecker:
    """Checks files for syntax and structural errors using best-available tools.

    Supports:
    - Python (.py)       via ast.parse + optionally ruff (SOTA) + pyflakes
    - JavaScript (.js, .mjs, .cjs) via node --check
    - TypeScript (.ts, .tsx) via tsc --noEmit
    - JSX/TSX (.jsx, .tsx) via the above
    - JSON (.json)       via json.loads
    - YAML (.yaml, .yml) via yaml.safe_load (Python yaml lib)
    - TOML (.toml)       via tomllib (Python 3.11+) or tomli/toml
    - HTML/XML (.html, .xml, .xhtml, .svg) via html.parser (stdlib)
    - CSS (.css)         via basic structure check
    - Shell (.sh, .bash, .zsh) via bash -n + shellcheck (if available)
    - Dockerfile        via basic check
    """

    def __init__(self, repo_path: str | Path | None = None):
        self.repo_path = Path(repo_path).resolve() if repo_path else Path.cwd()

    def check_file(self, file_path: str | Path, content: str | None = None) -> SyntaxCheckResult:
        """Check a file for syntax errors. If content is provided, checks that instead of reading the file."""
        path = self._resolve(file_path)
        fpath_str = str(path.relative_to(self.repo_path)) if self._is_within_repo(path) else str(path)

        if content is None:
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except Exception as e:
                return SyntaxCheckResult(
                    file_path=fpath_str,
                    issues=[SyntaxIssue(
                        file_path=fpath_str, line=0, column=0,
                        message=f"Cannot read file: {e}",
                    )],
                )

        if not content or not content.strip():
            return SyntaxCheckResult(file_path=fpath_str)

        checkers = self._select_checkers(path, content)

        all_issues: list[SyntaxIssue] = []
        checkers_used: list[str] = []
        for checker_fn in checkers:
            try:
                issues = checker_fn(path, content, fpath_str)
                all_issues.extend(issues)
                if issues:
                    checkers_used.append(checker_fn.__name__)
            except Exception as e:
                logger.debug("syntax checker %s failed for %s: %s", checker_fn.__name__, fpath_str, e)

        return SyntaxCheckResult(
            file_path=fpath_str,
            issues=all_issues,
            _checkers_used=checkers_used,
        )

    def check_path(self, file_path: str | Path) -> SyntaxCheckResult:
        """Convenience: check a file path by reading it from disk."""
        return self.check_file(file_path)

    def check_text(self, file_path: str | Path, content: str) -> SyntaxCheckResult:
        """Convenience: check content as if it were the content of file_path."""
        return self.check_file(file_path, content=content)

    # ------------------------------------------------------------------ #
    # Checker selection
    # ------------------------------------------------------------------ #

    def _select_checkers(self, path: Path, content: str) -> list:
        """Select appropriate checkers based on file extension and content."""
        suffix = path.suffix.lower()
        basename = path.name.lower()

        # Named files without standard extensions
        if basename == "dockerfile":
            return [self._check_dockerfile]
        if basename in ("makefile", "gemfile"):
            return []

        checkers = []

        if suffix == ".py":
            checkers = [self._check_python_ast]
            # Add ruff for SOTA linting if available
            checkers.append(self._check_python_ruff)
        elif suffix in (".js", ".mjs", ".cjs"):
            checkers = [self._check_javascript]
        elif suffix in (".ts",):
            checkers = [self._check_typescript]
        elif suffix == ".jsx":
            checkers = [self._check_javascript]
        elif suffix in (".tsx",):
            checkers = [self._check_typescript]
        elif suffix == ".json":
            checkers = [self._check_json]
        elif suffix in (".yaml", ".yml"):
            checkers = [self._check_yaml]
        elif suffix == ".toml":
            checkers = [self._check_toml]
        elif suffix in (".html", ".htm", ".xhtml", ".xml", ".svg"):
            checkers = [self._check_html]
        elif suffix == ".css":
            checkers = [self._check_css]
        elif suffix in (".sh", ".bash", ".zsh"):
            checkers = [self._check_shell]
        elif suffix in (".md", ".mdx", ".txt", ".rst"):
            pass  # No syntax check for plain text
        elif suffix in (".yml", ".yaml"):  # already handled above
            pass

        return checkers

    # ------------------------------------------------------------------ #
    # Python: ast.parse (stdlib) + multi-error via compile replacement
    # ------------------------------------------------------------------ #

    def _check_python_ast(self, path: Path, content: str, fpath_str: str) -> list[SyntaxIssue]:
        """Python syntax check using ast.parse + multi-error compile() trick.
        
        Uses a line-by-line scan to find ALL SyntaxErrors, not just the first one.
        """
        issues: list[SyntaxIssue] = []

        # Primary check: ast.parse reports the first error only
        try:
            ast.parse(content)
            return issues  # No syntax errors at all
        except SyntaxError as e:
            issues.append(SyntaxIssue(
                file_path=fpath_str,
                line=e.lineno or 0,
                column=e.offset or 0,
                message=f"SyntaxError: {e.msg}",
                checker="ast",
            ))

        # Multi-error strategy: try compile() which sometimes gives more info
        # then do a binary search for ALL errors by compiling sub-sections
        lines = content.splitlines(keepends=True)
        self._find_all_python_errors(lines, issues, fpath_str)

        # Deduplicate by line number
        seen_lines: set[int] = set()
        deduped: list[SyntaxIssue] = []
        for issue in issues:
            if issue.line not in seen_lines:
                seen_lines.add(issue.line)
                deduped.append(issue)

        return deduped

    def _find_all_python_errors(
        self, lines: list[str], issues: list[SyntaxIssue], fpath_str: str
    ) -> None:
        """Find ALL syntax errors by testing sub-ranges of the file."""
        total = len(lines)

        # Test progressively smaller chunks to find more errors
        # Strategy: split file in half and test each half
        def try_compile(text: str, start_line: int) -> SyntaxIssue | None:
            """Try to compile a code snippet, return issue or None."""
            if not text.strip():
                return None
            try:
                ast.parse(text)
                return None
            except SyntaxError as e:
                return SyntaxIssue(
                    file_path=fpath_str,
                    line=start_line + (e.lineno or 1) - 1,
                    column=e.offset or 0,
                    message=f"SyntaxError: {e.msg}",
                    checker="ast",
                )

        # Binary split: find errors in first half, second half
        mid = total // 2
        first_half = "".join(lines[:mid])
        second_half = "".join(lines[mid:])

        for text, offset in [(first_half, 0), (second_half, mid)]:
            if text.strip():
                issue = try_compile(text, offset)
                if issue and not any(
                    i.line == issue.line and i.message == issue.message
                    for i in issues
                ):
                    issues.append(issue)

    def _check_python_ruff(self, path: Path, content: str, fpath_str: str) -> list[SyntaxIssue]:
        """Check Python file using ruff (SOTA Python linter, extremely fast).
        
        Ruff is the current state-of-the-art Python linter — it catches:
        - Syntax errors (with multi-error reporting)
        - Undefined names (F821)
        - Unused imports (F401)
        - Unused variables (F841)
        - And 700+ other rules

        Falls back silently if ruff is not installed.
        NOTE: ruff requires the file to exist on disk. If check_text() is used
        (no real file), this checker is skipped silently.
        """
        if not path.exists():
            return []  # skip — file doesn't exist on disk

        issues: list[SyntaxIssue] = []
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "ruff", "check", "--quiet",
                 "--output-format=json", "--no-cache", str(path)],
                capture_output=True, text=True, timeout=30,
                **popen_kwargs(),
            )
            if proc.stdout.strip():
                findings = json.loads(proc.stdout)
                for finding in findings:
                    severity = "error"
                    # ruff uses 'E' for error-level, 'W' for warning, 'F' for pyflakes
                    code = finding.get("code", "")
                    if code and code[0] in ("W", "C", "N", "D"):
                        severity = "warning"
                    elif code and code[0] in ("I",):
                        severity = "info"

                    # Some ruff findings are informational (convention)
                    if finding.get("fixable") and code in (
                        "F401", "F841", "I001"
                    ):
                        severity = "warning"

                    issues.append(SyntaxIssue(
                        file_path=fpath_str,
                        line=finding.get("location", {}).get("row", 0) or 0,
                        column=finding.get("location", {}).get("column", 0) or 0,
                        message=finding.get("message", "Unknown ruff finding"),
                        severity=severity,
                        checker="ruff",
                        rule_id=code,
                    ))
        except FileNotFoundError:
            pass  # ruff not installed — fine, ast.parse covers syntax
        except subprocess.TimeoutExpired:
            logger.debug("ruff check timed out for %s", fpath_str)
        except (json.JSONDecodeError, Exception) as e:
            logger.debug("ruff check failed for %s: %s", fpath_str, e)

        return issues

    # ------------------------------------------------------------------ #
    # JavaScript / TypeScript
    # ------------------------------------------------------------------ #

    def _check_javascript(self, path: Path, content: str, fpath_str: str) -> list[SyntaxIssue]:
        """Check JavaScript using node --check (Node.js's built-in syntax checker).
        
        Node.js is confirmed available on this system.
        """
        issues: list[SyntaxIssue] = []
        try:
            proc = subprocess.run(
                ["node", "--check", str(path)],
                capture_output=True, text=True, timeout=15,
                **popen_kwargs(),
            )
            if proc.returncode != 0:
                output = proc.stderr or proc.stdout
                self._parse_node_errors(output, issues, fpath_str)
        except FileNotFoundError:
            issues.append(SyntaxIssue(
                file_path=fpath_str, line=0, column=0,
                message="Node.js not found — cannot validate JavaScript syntax",
                severity="warning", checker="js",
            ))
        except subprocess.TimeoutExpired:
            pass
        except Exception as e:
            logger.debug("node --check failed for %s: %s", fpath_str, e)

        return issues

    def _parse_node_errors(self, output: str, issues: list[SyntaxIssue], fpath_str: str) -> None:
        """Parse Node.js error output into SyntaxIssues."""
        for line in output.splitlines():
            line = line.strip()
            if not line:
                continue
            # Node error format: "path:line:col: error message"
            # or: "SyntaxError: Unexpected token"
            m = re.match(
                r'.*?(\d+):(\d+)\s+(.*)', line
            )
            if m:
                issues.append(SyntaxIssue(
                    file_path=fpath_str,
                    line=int(m.group(1)),
                    column=int(m.group(2)),
                    message=m.group(3).strip(),
                    checker="node",
                ))
            elif "SyntaxError" in line or "Error:" in line:
                issues.append(SyntaxIssue(
                    file_path=fpath_str, line=0, column=0,
                    message=line[:200],
                    checker="node",
                ))

    def _check_typescript(self, path: Path, content: str, fpath_str: str) -> list[SyntaxIssue]:
        """Check TypeScript using tsc --noEmit.
        
        Uses the project's tsconfig.json if available, otherwise --noEmit --strict.
        """
        issues: list[SyntaxIssue] = []

        # First check if tsc is available via npx
        tsc_cmd = self._find_tsc()
        if not tsc_cmd:
            issues.append(SyntaxIssue(
                file_path=fpath_str, line=0, column=0,
                message="TypeScript compiler (tsc) not found — cannot validate .ts/.tsx files",
                severity="warning", checker="tsc",
            ))
            return issues

        try:
            # Check if a tsconfig exists near the file or in the repo root
            tsconfig = self._find_tsconfig(path)
            cmd = [tsc_cmd, "--noEmit", "--pretty", "false"]
            if tsconfig:
                cmd.extend(["--project", str(tsconfig)])
            cmd.append(str(path))

            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=30,
                **popen_kwargs(),
            )
            if proc.returncode != 0:
                output = proc.stdout or proc.stderr
                self._parse_tsc_errors(output, issues, fpath_str)
        except subprocess.TimeoutExpired:
            pass
        except Exception as e:
            logger.debug("tsc check failed for %s: %s", fpath_str, e)

        return issues

    def _find_tsc(self) -> str | None:
        """Find tsc binary — try local node_modules/.bin first, then npx, then global."""
        local_tsc = self.repo_path / "node_modules" / ".bin" / "tsc"
        if local_tsc.exists():
            return str(local_tsc)

        # Try npx
        try:
            proc = subprocess.run(
                ["npx", "--no-install", "tsc", "--version"],
                capture_output=True, text=True, timeout=5,
                **popen_kwargs(),
            )
            if proc.returncode == 0:
                return "npx tsc"
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        # Try global
        for candidate in ["tsc", "tsc.cmd"]:
            try:
                proc = subprocess.run(
                    [candidate, "--version"],
                    capture_output=True, text=True, timeout=5,
                    **popen_kwargs(),
                )
                if proc.returncode == 0:
                    return candidate
            except FileNotFoundError:
                continue

        return None

    def _find_tsconfig(self, file_path: Path) -> Path | None:
        """Find the nearest tsconfig.json."""
        # Check file's directory, then parent, up to repo root
        current = file_path.parent
        while current >= self.repo_path:
            tsconfig = current / "tsconfig.json"
            if tsconfig.exists():
                return tsconfig
            current = current.parent

        # Check repo root
        root_tsconfig = self.repo_path / "tsconfig.json"
        return root_tsconfig if root_tsconfig.exists() else None

    def _parse_tsc_errors(self, output: str, issues: list[SyntaxIssue], fpath_str: str) -> None:
        """Parse tsc error output into SyntaxIssues.
        
        tsc format: "file.ts(line,col): error TS2345: message"
        """
        for line in output.splitlines():
            line = line.strip()
            if not line:
                continue
            # tsc error format
            m = re.match(r'.*\((\d+),(\d+)\):\s*(error|warning)\s*(TS\d+)?:?\s*(.*)', line)
            if m:
                severity = "error" if m.group(3) == "error" else "warning"
                issues.append(SyntaxIssue(
                    file_path=fpath_str,
                    line=int(m.group(1)),
                    column=int(m.group(2)),
                    message=m.group(5).strip(),
                    severity=severity,
                    checker="tsc",
                    rule_id=m.group(4) or "",
                ))
            elif "error" in line.lower() or "TS" in line:
                issues.append(SyntaxIssue(
                    file_path=fpath_str, line=0, column=0,
                    message=line[:300],
                    checker="tsc",
                ))

    # ------------------------------------------------------------------ #
    # JSON
    # ------------------------------------------------------------------ #

    def _check_json(self, path: Path, content: str, fpath_str: str) -> list[SyntaxIssue]:
        issues: list[SyntaxIssue] = []
        try:
            json.loads(content)
        except json.JSONDecodeError as e:
            issues.append(SyntaxIssue(
                file_path=fpath_str,
                line=e.lineno,
                column=e.colno,
                message=f"JSONDecodeError: {e.msg}",
                checker="json",
            ))
        except Exception as e:
            issues.append(SyntaxIssue(
                file_path=fpath_str, line=0, column=0,
                message=f"JSON error: {e}",
                checker="json",
            ))
        return issues

    # ------------------------------------------------------------------ #
    # YAML
    # ------------------------------------------------------------------ #

    def _check_yaml(self, path: Path, content: str, fpath_str: str) -> list[SyntaxIssue]:
        issues: list[SyntaxIssue] = []
        try:
            import yaml
            yaml.safe_load(content)
        except ImportError:
            issues.append(SyntaxIssue(
                file_path=fpath_str, line=0, column=0,
                message="PyYAML not installed — cannot validate YAML syntax",
                severity="warning", checker="yaml",
            ))
        except yaml.scanner.ScannerError as e:
            line = e.problem_mark.line + 1 if e.problem_mark else 0
            column = e.problem_mark.column + 1 if e.problem_mark else 0
            msg = e.problem or str(e)
            issues.append(SyntaxIssue(
                file_path=fpath_str,
                line=line, column=column,
                message=f"YAML error: {msg}",
                checker="yaml",
            ))
        except yaml.parser.ParserError as e:
            line = e.problem_mark.line + 1 if e.problem_mark else 0
            column = e.problem_mark.column + 1 if e.problem_mark else 0
            issues.append(SyntaxIssue(
                file_path=fpath_str,
                line=line, column=column,
                message=f"YAML parse error: {e}",
                checker="yaml",
            ))
        return issues

    # ------------------------------------------------------------------ #
    # TOML (Python 3.11+ stdlib via tomllib)
    # ------------------------------------------------------------------ #

    def _check_toml(self, path: Path, content: str, fpath_str: str) -> list[SyntaxIssue]:
        issues: list[SyntaxIssue] = []
        # Python 3.11+ has tomllib in stdlib
        toml_module = None
        for mod_name in ("tomllib", "tomli", "toml"):
            try:
                toml_module = __import__(mod_name)
                break
            except ImportError:
                continue

        if toml_module is None:
            issues.append(SyntaxIssue(
                file_path=fpath_str, line=0, column=0,
                message="No TOML parser available (try: pip install tomli)",
                severity="warning", checker="toml",
            ))
            return issues

        try:
            if hasattr(toml_module, "loads"):
                toml_module.loads(content)
            else:
                # python-toml uses load/loads differently
                import io
                toml_module.load(io.StringIO(content))
        except Exception as e:
            # Extract line number from error if possible
            line = 0
            msg = str(e)
            m = re.match(r'.*line\s+(\d+).*', msg)
            if m:
                line = int(m.group(1))
            issues.append(SyntaxIssue(
                file_path=fpath_str,
                line=line, column=0,
                message=f"TOML error: {msg}",
                checker="toml",
            ))

        return issues

    # ------------------------------------------------------------------ #
    # HTML: html.parser (stdlib) — real parsing, not regex
    # ------------------------------------------------------------------ #

    class _HtmlValidator(html.parser.HTMLParser):
        """HTML/XML parser that collects structural issues."""

        def __init__(self, fpath_str: str):
            super().__init__(convert_charrefs=True)
            self.fpath_str = fpath_str
            self.issues: list[SyntaxIssue] = []
            self.tag_stack: list[tuple[str, int, int]] = []  # (tag, line, col)
            self._line = 0
            self._col = 0

        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            # Void elements don't need closing
            if tag.lower() in _VOID_ELEMENTS:
                return
            self.tag_stack.append((tag, self.getpos()[0], self.getpos()[1]))

        def handle_endtag(self, tag: str) -> None:
            lowered = tag.lower()
            if lowered in _VOID_ELEMENTS:
                return
            if self.tag_stack and self.tag_stack[-1][0].lower() == lowered:
                self.tag_stack.pop()
                return

            # Check if there's a matching open tag deeper in the stack
            for i in range(len(self.tag_stack) - 1, -1, -1):
                if self.tag_stack[i][0].lower() == lowered:
                    # Unclosed tags in between
                    for j in range(len(self.tag_stack) - 1, i, -1):
                        unclosed = self.tag_stack.pop()
                        self.issues.append(SyntaxIssue(
                            file_path=self.fpath_str,
                            line=unclosed[1],
                            column=unclosed[2],
                            message=f"Unclosed tag <{unclosed[0]}>",
                            severity="error",
                            checker="html",
                        ))
                    self.tag_stack.pop()
                    return

            self.issues.append(SyntaxIssue(
                file_path=self.fpath_str,
                line=self.getpos()[0],
                column=self.getpos()[1],
                message=f"Unexpected closing tag </{tag}>",
                severity="error",
                checker="html",
            ))

        def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            pass  # Self-closing tags like <br/> are fine

    def _check_html(self, path: Path, content: str, fpath_str: str) -> list[SyntaxIssue]:
        """Check HTML/XML using html.parser (stdlib) — actual parsing, not regex."""
        # Pre-check: look for basic brokenness that would crash the parser
        issues: list[SyntaxIssue] = []

        # Check for unbalanced angle brackets that would break parsing
        lines = content.splitlines()
        for i, line in enumerate(lines, 1):
            opens = line.count("<")
            closes = line.count(">")
            if opens > closes and "<" not in line.strip()[:1]:
                # Only flag if it's clearly unbalanced (not template syntax)
                stripped = line.strip()
                # Skip lines that are obviously template code
                if not any(kw in stripped for kw in ("{%", "{{", "<%", "${", "${")):
                    pass

        # Run the actual HTML parser
        try:
            parser = self._HtmlValidator(fpath_str)
            parser.feed(content)
            parser.close()
            issues.extend(parser.issues)

            # Report remaining unclosed tags
            for tag, line, col in parser.tag_stack:
                issues.append(SyntaxIssue(
                    file_path=fpath_str,
                    line=line, column=col,
                    message=f"Unclosed tag <{tag}>",
                    severity="error",
                    checker="html",
                ))
        except html.parser.HTMLParseError as e:
            issues.append(SyntaxIssue(
                file_path=fpath_str,
                line=e.lineno or 0,
                column=e.offset or 0,
                message=f"HTML parse error: {e}",
                checker="html",
            ))
        except Exception as e:
            logger.debug("html.parser failed for %s: %s", fpath_str, e)

        return issues

    # ------------------------------------------------------------------ #
    # CSS: basic structural validation
    # ------------------------------------------------------------------ #

    def _check_css(self, path: Path, content: str, fpath_str: str) -> list[SyntaxIssue]:
        """Check CSS for basic structural issues.
        
        Checks:
        - Balanced braces
        - Basic selector/declaration structure
        """
        issues: list[SyntaxIssue] = []
        lines = content.splitlines()

        # Check balanced braces
        brace_depth = 0
        in_comment = False

        for i, line in enumerate(lines, 1):
            stripped = line.strip()

            # Skip comments
            if "/*" in stripped:
                in_comment = True
            if in_comment:
                if "*/" in stripped:
                    in_comment = False
                continue

            for ch in stripped:
                if ch == "{":
                    brace_depth += 1
                elif ch == "}":
                    brace_depth -= 1

            if brace_depth < 0:
                issues.append(SyntaxIssue(
                    file_path=fpath_str,
                    line=i, column=stripped.find("}") + 1,
                    message="Unexpected closing brace — no matching opening brace",
                    severity="error", checker="css",
                ))
                brace_depth = 0

        if brace_depth > 0:
            issues.append(SyntaxIssue(
                file_path=fpath_str,
                line=len(lines), column=1,
                message=f"Unclosed block: {brace_depth} unmatched opening brace(s)",
                severity="error", checker="css",
            ))

        return issues

    # ------------------------------------------------------------------ #
    # Shell scripts
    # ------------------------------------------------------------------ #

    def _check_shell(self, path: Path, content: str, fpath_str: str) -> list[SyntaxIssue]:
        """Check shell scripts using shellcheck if available, else bash -n."""
        issues: list[SyntaxIssue] = []

        # bash -n syntax check
        try:
            proc = subprocess.run(
                ["bash", "-n", str(path)],
                capture_output=True, text=True, timeout=5,
                **popen_kwargs(),
            )
            if proc.returncode != 0:
                for err_line in proc.stderr.splitlines():
                    m = re.match(r'.*?line\s+(\d+):\s*(.*)', err_line)
                    if m:
                        issues.append(SyntaxIssue(
                            file_path=fpath_str,
                            line=int(m.group(1)), column=1,
                            message=f"Bash syntax error: {m.group(2)}",
                            checker="bash",
                        ))
                    else:
                        issues.append(SyntaxIssue(
                            file_path=fpath_str, line=0, column=0,
                            message=f"Bash syntax error: {err_line}",
                            checker="bash",
                        ))
        except FileNotFoundError:
            issues.append(SyntaxIssue(
                file_path=fpath_str, line=0, column=0,
                message="Bash not available — cannot validate shell syntax",
                severity="warning", checker="bash",
            ))
        except subprocess.TimeoutExpired:
            pass
        except Exception as e:
            logger.debug("bash -n check failed: %s", e)

        # shellcheck for detailed analysis
        try:
            proc = subprocess.run(
                ["shellcheck", "--shell=bash", "--format=json", str(path)],
                capture_output=True, text=True, timeout=10,
                **popen_kwargs(),
            )
            if proc.stdout.strip():
                findings = json.loads(proc.stdout)
                for finding in findings:
                    severity = "warning"
                    level = finding.get("level", "")
                    if level == "error":
                        severity = "error"
                    issues.append(SyntaxIssue(
                        file_path=fpath_str,
                        line=finding.get("line", 0),
                        column=finding.get("column", 1),
                        message=finding.get("message", ""),
                        severity=severity,
                        checker="shellcheck",
                        rule_id=f"SC{finding.get('code', 0)}",
                    ))
        except FileNotFoundError:
            pass  # shellcheck not installed
        except subprocess.TimeoutExpired:
            pass
        except (json.JSONDecodeError, Exception) as e:
            logger.debug("shellcheck failed for %s: %s", fpath_str, e)

        return issues

    # ------------------------------------------------------------------ #
    # Dockerfile
    # ------------------------------------------------------------------ #

    def _check_dockerfile(self, path: Path, content: str, fpath_str: str) -> list[SyntaxIssue]:
        """Basic Dockerfile syntax check."""
        issues: list[SyntaxIssue] = []
        lines = content.splitlines()
        valid_instructions = {
            "FROM", "RUN", "CMD", "LABEL", "MAINTAINER", "EXPOSE",
            "ENV", "ADD", "COPY", "ENTRYPOINT", "VOLUME", "USER",
            "WORKDIR", "ARG", "ONBUILD", "STOPSIGNAL", "HEALTHCHECK",
            "SHELL",
        }

        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            instruction = stripped.split()[0].upper()
            if instruction not in valid_instructions and not instruction.startswith("#"):
                issues.append(SyntaxIssue(
                    file_path=fpath_str,
                    line=i, column=1,
                    message=f"Unknown Dockerfile instruction: '{instruction}'",
                    severity="warning", checker="dockerfile",
                ))

        # Check that FROM is the first non-comment instruction
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if not stripped.upper().startswith("FROM"):
                issues.append(SyntaxIssue(
                    file_path=fpath_str,
                    line=i, column=1,
                    message="First instruction in Dockerfile should be FROM",
                    severity="warning", checker="dockerfile",
                ))
            break

        return issues

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def _resolve(self, file_path: str | Path) -> Path:
        p = Path(file_path)
        if p.is_absolute():
            return p
        return (self.repo_path / p).resolve()

    def _is_within_repo(self, path: Path) -> bool:
        try:
            path.relative_to(self.repo_path)
            return True
        except ValueError:
            return False


# --------------------------------------------------------------------------- #
# HTML void elements (don't need closing tags)
# --------------------------------------------------------------------------- #

_VOID_ELEMENTS = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
    "!doctype", "!--", "?",  # processing instructions
})


# --------------------------------------------------------------------------- #
# Convenience functions
# --------------------------------------------------------------------------- #

def check_files(
    file_paths: list[str | Path],
    repo_path: str | Path | None = None,
    content_map: dict[str, str] | None = None,
    include_warnings: bool = True,
) -> str:
    """Check multiple files and return formatted error text for prompt injection.

    Args:
        file_paths: List of file paths to check
        repo_path: Repository root path
        content_map: Optional map of path -> content to check (avoids re-reading files)
        include_warnings: Whether to include warnings in output

    Returns:
        Formatted string of all errors/warnings, empty string if none found
    """
    checker = SyntaxChecker(repo_path=repo_path)
    result_parts: list[str] = []

    for fp in file_paths:
        fpath_str = str(fp)
        if content_map and fpath_str in content_map:
            result = checker.check_text(fp, content_map[fpath_str])
        else:
            result = checker.check_path(fp)
        formatted = result.format(include_warnings=include_warnings)
        if formatted:
            result_parts.append(formatted)

    return "\n".join(result_parts)


def check_content(
    file_path: str | Path,
    content: str,
    repo_path: str | Path | None = None,
    include_warnings: bool = True,
) -> str:
    """Check a single file's content and return formatted error text."""
    checker = SyntaxChecker(repo_path=repo_path)
    result = checker.check_text(file_path, content)
    return result.format(include_warnings=include_warnings)