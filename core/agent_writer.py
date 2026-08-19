from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger("agent.core.agent_writer")

AGENTS_MD_PROMPT = """\
You are analysing a software project to produce a concise "Source of Truth" document
for AI coding assistants. The document should be named `AGENTS.md` and placed at the
project root.

Given the project structure, configuration, and recent conversation, produce a document
that covers:

1. **Tech Stack** — languages, frameworks, key libraries, versions
2. **Architecture** — high-level directory layout, module purposes, data flow
3. **Key Conventions** — coding style, import patterns, naming, testing approach
4. **Gotchas** — non-obvious behaviours, important constraints, things an AI tends to get wrong
5. **Recent Changes** — based on git history, what's been worked on lately

Keep the document under 200 lines. Focus on information that would help an AI agent
understand the project *faster* than reading all the source files.

Respond with the FULL markdown content of the document. No JSON wrapping, no explanation.
"""


async def gather_project_context(repo_path: Path) -> str:
    parts: list[str] = []

    tree_lines: list[str] = []
    skip_dirs = {  # i just kept adding to this list whenever stuff broke
        ".git", "__pycache__", "node_modules", ".venv", "venv", ".tox",
        ".pytest_cache", ".mypy_cache", ".ruff_cache", ".hypothesis",
        "target", "dist", "build", "bin", "obj", "vendor", ".dart_tool",
        "coverage", ".zircon-code",
    }
    try:
        for i, entry in enumerate(sorted(repo_path.rglob("*"))):
            if i >= 200:
                tree_lines.append("  ... (more files)")
                break
            try:
                rel = entry.relative_to(repo_path)
                parts_list = rel.parts
                skip = False
                for p in parts_list:
                    if p.startswith(".") or p in skip_dirs or p == "__pycache__":
                        skip = True
                        break
                if skip:
                    continue
                if len(parts_list) > 3:
                    continue
                indent = "  " * (len(parts_list) - 1)
                suffix = "/" if entry.is_dir() else ""
                tree_lines.append(f"{indent}{rel.name}{suffix}")
            except (ValueError, OSError):
                continue
    except Exception:
        tree_lines.append("(unable to list)")

    if tree_lines:
        parts.append("## File Tree\n```\n" + "\n".join(tree_lines[:60]) + "\n```")

    config_paths = [
        "package.json", "pyproject.toml", "Cargo.toml", "go.mod",
        "requirements.txt", "setup.py", "setup.cfg",
        "tsconfig.json", "vite.config.ts", "next.config.js",
        "models.yaml", "zircon.json",
    ]
    config_blocks: list[str] = []
    for cf in config_paths:
        cf_path = repo_path / cf
        if cf_path.exists() and cf_path.is_file():
            try:
                content = cf_path.read_text(encoding="utf-8", errors="replace")[:1000]
                config_blocks.append(f"--- {cf} ---\n{content}")
            except Exception:
                pass
    if config_blocks:
        parts.append("## Config Files\n" + "\n\n".join(config_blocks))

    try:
        import subprocess
        from .proc_spawn import popen_kwargs
        result = subprocess.run(
            ["git", "log", "--oneline", "-10"],
            capture_output=True, text=True, cwd=str(repo_path), timeout=10,
            **popen_kwargs(),
        )
        if result.returncode == 0 and result.stdout.strip():
            parts.append("## Recent Git History\n```\n" + result.stdout.strip() + "\n```")
    except Exception:
        pass

    return "\n\n".join(parts)


async def generate_agents_md(
    repo_path: str | Path,
    llm_generate_fn: Any,  # async (messages) -> response with .content
    existing_md: str = "",
    messages_for_context: list[dict] | None = None,
) -> str:
    repo_path = Path(repo_path).resolve()
    context = await gather_project_context(repo_path)

    if existing_md:
        context += f"\n\n## Current AGENTS.md\n\n{existing_md[:2000]}"

    if messages_for_context:
        recent = messages_for_context[-5:]  # Last 5 messages
        conv_block = "\n".join(
            f"[{m.get('role', '?')}] {str(m.get('content', ''))[:300]}"
            for m in recent
        )
        context += f"\n\n## Recent Conversation\n{conv_block[:2000]}"

    messages = [
        {"role": "system", "content": AGENTS_MD_PROMPT},
        {"role": "user", "content": f"Generate AGENTS.md for this project:\n\n{context[:12000]}"},
    ]

    try:
        response = await llm_generate_fn(
            role="default",
            messages=messages,
            max_tokens=4096,
        )
        content = response.content.strip()

        if content.startswith("```"):
            lines = content.splitlines()
            if lines[0].strip().startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            content = "\n".join(lines).strip()

        agents_path = repo_path / "AGENTS.md"
        agents_path.write_text(content, encoding="utf-8")
        logger.info("Auto-generated AGENTS.md (%d bytes)", len(content))
        return content

    except Exception as e:
        logger.warning("Failed to auto-generate AGENTS.md: %s", e)
        return ""