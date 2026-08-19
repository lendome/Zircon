from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("agent.core.project_classifier")


PROJECT_CATEGORIES = {
    "antibot_backend": {
        "label": "Anti-bot / Anti-cheat backend",
        "description": (
            "Server-side anti-bot, anti-cheat, or anti-automation system. "
            "May include heuristics, fingerprinting, rate-limiting, captcha integration, "
            "behavioural analysis, and client verification."
        ),
        "keywords": [
            "antibot", "anti-bot", "anticheat", "anti-cheat", "bot detection",
            "fingerprint", "rate limit", "captcha", "hcaptcha", "turnstile",
            "behaviour", "behavior", "heuristic", "bot mitigation",
        ],
    },
    "antibot_frontend": {
        "label": "Anti-bot / Anti-cheat frontend",
        "description": (
            "Client-side (browser or app) anti-bot protection layer. "
            "May include JS challenges, canvas fingerprinting, WebGL checks, "
            "mouse-movement analysis, and integrity verification."
        ),
        "keywords": [
            "challenge", "js challenge", "fingerprint", "client integrity",
            "webgl", "canvas", "mouse move", "bot frontend",
        ],
    },
    "web_frontend": {
        "label": "Web frontend",
        "description": (
            "User-facing web application built with a framework such as "
            "React, Vue, Svelte, Angular, or plain HTML/CSS/JS."
        ),
        "keywords": [
            "react", "vue", "svelte", "angular", "nextjs", "nuxt",
            "frontend", "ui", "component", "tailwind", "bootstrap",
            "html", "css", "javascript", "typescript",
        ],
    },
    "api_backend": {
        "label": "API / Backend service",
        "description": (
            "Server-side API or backend service. May use frameworks like "
            "FastAPI, Flask, Django, Express, or similar."
        ),
        "keywords": [
            "api", "backend", "fastapi", "flask", "django", "express",
            "rest", "graphql", "endpoint", "route", "controller",
        ],
    },
    "ai_backend": {
        "label": "AI / ML backend",
        "description": (
            "Backend service focused on AI/ML inference, model serving, "
            "agent orchestration, or prompt processing."
        ),
        "keywords": [
            "ai", "llm", "model", "agent", "inference", "embedding",
            "prompt", "vector", "rag", "chain", "openai", "anthropic",
        ],
    },
    "cli_tool": {
        "label": "CLI tool / Utility",
        "description": (
            "Command-line tool, automation script, or developer utility. "
            "Typically has a main entry point and processes arguments."
        ),
        "keywords": [
            "cli", "command line", "argparse", "click", "typer",
            "console", "terminal", "script", "utility",
        ],
    },
    "library_package": {
        "label": "Library / Package",
        "description": (
            "Reusable library or package meant to be imported by other projects. "
            "Has a public API and may be published to a package registry."
        ),
        "keywords": [
            "library", "package", "sdk", "client", "sdk", "wrapper",
            "pypi", "npm", "crates.io", "nuget",
        ],
    },
    "fullstack_web": {
        "label": "Full-stack web application",
        "description": (
            "Application with both frontend and backend components, "
            "often in a monorepo or with separate client/server directories."
        ),
        "keywords": [
            "fullstack", "full stack", "monorepo", "client",
            "server", "app", "web application",
        ],
    },
    "game_mod": {
        "label": "Game mod / Scripting",
        "description": (
            "Modification, script, or extension for a video game. "
            "Uses the game's scripting language or SDK."
        ),
        "keywords": [
            "game", "mod", "script", "sqf", "lua", "arma", "minecraft",
            "plugin", "addon", "extension",
        ],
    },
    "data_pipeline": {
        "label": "Data pipeline / ETL",
        "description": (
            "Data processing, ETL, or analytics pipeline. "
            "Processes, transforms, or moves data between systems."
        ),
        "keywords": [
            "etl", "pipeline", "data", "spark", "kafka", "airflow",
            "dbt", "data processing", "analytics",
        ],
    },
    "mobile_app": {
        "label": "Mobile application",
        "description": (
            "Mobile app for Android, iOS, or cross-platform frameworks "
            "like React Native, Flutter, or Kotlin Multiplatform."
        ),
        "keywords": [
            "android", "ios", "flutter", "react native", "kotlin",
            "swift", "mobile", "app",
        ],
    },
    "devops_infra": {
        "label": "DevOps / Infrastructure",
        "description": (
            "Infrastructure-as-code, CI/CD configuration, Docker/K8s "
            "manifests, or deployment automation."
        ),
        "keywords": [
            "docker", "kubernetes", "k8s", "terraform", "ansible",
            "ci/cd", "github actions", "gitlab ci", "deployment",
            "infrastructure", "helm",
        ],
    },
    "other_generic": {
        "label": "Generic / Uncategorised",
        "description": (
            "A project that does not clearly match any of the above "
            "categories. Apply general-purpose coding best practices."
        ),
        "keywords": [],
    },
}


SAFEGUARD_PREAMBLE = """\
## CRITICAL SAFEGUARD — Do Not Break Existing Code
The following domain guidance is provided to **inform** your approach, NOT to
override the user's explicit request or existing project code.

- **Do NOT proactively "fix" or refactor existing code** unless the user's
  task explicitly asks for it. If the project already has hardcoded values,
  specific patterns, or security stances in place, leave them alone.
  Changing them without being asked can silently break the project.
- **Do NOT introduce new best-practice patterns** (e.g. env variables for
  secrets, config files for constants, obfuscation, logging, error handling)
  unless the user's task directly requests them or they are strictly required
  to complete the task.
- **Respect the existing codebase.** If the project uses inline config,
  raw strings, or non-idiomatic patterns, assume those were deliberate
  decisions. Only suggest changes when the task demands it and the user
  has consented.
- **If in doubt, ask.** If a change would alter the project's security model,
  architecture, or existing behaviour beyond the scope of the task, flag
  it to the user rather than silently making the change.

When applying any domain-specific guidance below, always weigh it against
this safeguard. The user's explicit task is your primary directive.

"""

ADAPTIVE_PROMPT_ADDITIONS: dict[str, str] = {
    "antibot_backend": """\
## ANTI-BOT / ANTI-CHEAT DOMAIN CONTEXT
This project is an **anti-bot or anti-cheat backend system**. The following principles apply:

1. **Never trust the client.** Always validate, re-compute, or signature-check every input that originates client-side. Assume the client may be compromised or lying.
2. **Security through obscurity is a valid layer.** Avoid writing overly clean, standardised code paths for security-critical logic. Obfuscate variable names, randomise execution order, and avoid obvious patterns in heuristics where practical.
3. **Kernel / low-level awareness.** If working at Ring-0 or with system drivers, be extremely cautious with memory management, thread synchronisation, and edge cases. A mistake can crash the entire system (BSOD). Do not use unsafe patterns without explicit warning.
4. **Behavioural heuristics require nuance.** Do NOT write rigid hard-coded thresholds (e.g. `if speed > X: ban`). Instead, implement statistical or adaptive approaches: rolling averages, z-scores, percentile-based outlier detection. False positives are worse than false negatives — prioritise not banning legitimate users.
5. **Zero-day awareness.** Your knowledge has a cutoff date. Do not assume you know about the latest bypass methods. When asked about emerging threats, note the limitation and suggest testing/telemetry-based detection instead of claiming to patch unknown exploits.
6. **Polymorphism matters.** Where feasible, generate or support polymorphic code — randomised execution paths, encrypted strings, and variable mutation — to frustrate reverse-engineering of client-side protection.
7. **Protocol integrity.** Ensure server-side code validates protocol-level invariants: sequence numbers, timestamps, checksums, and operation costs. Do not assume the client respects the protocol.
8. **Balance innovation.** Keep the core system open to extension so new detection methods can be added without rewriting the entire engine. The prompt is a guide, not a cage.

When in doubt about a security trade-off, prefer the safer, more verifiable path and flag the concern explicitly.""",

    "antibot_frontend": """\
## ANTI-BOT FRONTEND DOMAIN CONTEXT
This project is a **client-side anti-bot protection layer**. The following principles apply:

1. **Obfuscation is essential.** Client-side code will be fully visible to attackers. Use meaningful obfuscation (string encryption, control-flow flattening, dead-code insertion) to slow reverse-engineering. Do not rely on "clean" readable code for the challenge layer.
2. **Canvas/WebGL fingerprinting.** If implementing fingerprinting, combine multiple signals (canvas, WebGL, audio, fonts, WebRTC) and hash them together. Avoid single-signal checks that are easily spoofed.
3. **Mouse-movement analysis.** Do not use hard thresholds (e.g. `if delta < X`). Use statistical models: compare distributions, look for lack of micro-adjustments, and account for human variability.
4. **Challenge diversity.** Generate varied challenges — different algorithms, different payload structures — so that a bypass for one challenge does not break all of them.
5. **Never embed server secrets.** Client-side code should never contain API keys, signing secrets, or any value that would let an attacker impersonate the server.
6. **Defence in depth.** Layer multiple independent checks so that bypassing one still leaves others active.
7. **Performance matters.** Anti-bot checks must not degrade the user experience. Keep challenge computation under reasonable time limits and degrade gracefully on slow devices.
8. **Fallback behaviour.** Provide fallback for users who fail challenges legitimately (accessibility, privacy extensions). Block only after multiple confirmed failures.""",

    "web_frontend": """\
## WEB FRONTEND CONTEXT
This project is a **general web frontend**. The following principles apply:
1. Prioritise responsive design and accessibility (a11y).
2. Follow the framework's idiomatic patterns (components, hooks, composables).
3. Keep bundle size in mind — lazy-load where appropriate.
4. Write semantic HTML and use CSS responsibly.
5. Handle loading, empty, and error states for every component.
6. Ensure state management is clean and predictable.""",

    "api_backend": """\
## API BACKEND CONTEXT
This project is an **API or backend service**. The following principles apply:
1. Follow RESTful or GraphQL conventions consistently.
2. Validate all inputs at the boundary (Pydantic, marshmallow, etc.).
3. Implement proper error handling with structured error responses.
4. Use dependency injection / middleware for cross-cutting concerns (auth, logging, rate-limiting).
5. Write tests for all endpoints, including error paths.
6. Document the API (OpenAPI, docstrings, or similar).
7. Consider idempotency for mutating operations.
8. Never expose internal stack traces or implementation details.""",

    "ai_backend": """\
## AI / ML BACKEND CONTEXT
This project is an **AI/ML backend or agent system**. The following principles apply:
1. Handle token budgets and context windows carefully — summarise or truncate when necessary.
2. Implement robust error handling for LLM API calls (rate limits, timeouts, fallbacks).
3. Never hardcode prompts in business logic — keep them configurable.
4. Log all LLM interactions for debugging and auditing.
5. Consider streaming responses for better UX.
6. Implement safety guardrails — never expose raw model output without validation.
7. Support tool/function calling patterns in a structured way.
8. Cache embeddings and model responses where appropriate to reduce costs.""",

    "cli_tool": """\
## CLI TOOL CONTEXT
This project is a **CLI tool or utility**. The following principles apply:
1. Provide clear --help output and argument validation.
2. Use sensible defaults and honour environment variables.
3. Handle non-zero exit codes appropriately.
4. Support both interactive and non-interactive (pipe-friendly) modes where applicable.
5. Include progress indicators for long-running operations.
6. Write coloured/structured output for readability, but offer `--no-color` flag.
7. Handle SIGINT gracefully — clean up temporary files.""",

    "library_package": """\
## LIBRARY / PACKAGE CONTEXT
This project is a **reusable library or package**. The following principles apply:
1. Design a clean, minimal public API. Keep internals private.
2. Write comprehensive type hints and docstrings.
3. Provide README with installation, quick-start, and API reference.
4. Ship tests and ensure they pass in CI.
5. Follow semantic versioning. Document breaking changes.
6. Avoid hard dependencies — make optional features truly optional.
7. Support both sync and async APIs where applicable.
8. Ensure backwards compatibility within a major version.""",

    "fullstack_web": """\
## FULL-STACK CONTEXT
This project is a **full-stack web application**. The following principles apply:
1. Maintain clean separation between frontend and backend code.
2. Define a clear API contract (shared types, OpenAPI spec, or GraphQL schema).
3. Handle CORS, authentication, and session management properly.
4. Ensure the frontend gracefully handles backend errors and loading states.
5. Consider the full data flow: client → validation → business logic → persistence → response.
6. Write integration tests that cover the full stack where critical paths are involved.
7. Keep shared logic (types, validation) in a common module if the project structure allows.""",

    "game_mod": """\
## GAME MOD / SCRIPTING CONTEXT
This project is a **game modification or script**. The following principles apply:
1. Follow the game's API and scripting conventions exactly.
2. Minimise performance impact — game mods run in real-time.
3. Avoid hard-coded paths or IDs — use configuration where possible.
4. Provide clear installation instructions.
5. Handle multiplayer/server contexts carefully — avoid desyncs.
6. Test with the specific game version(s) the mod targets.""",

    "data_pipeline": """\
## DATA PIPELINE CONTEXT
This project is a **data pipeline or ETL system**. The following principles apply:
1. Make pipelines idempotent — replaying should produce the same result.
2. Handle schema evolution gracefully.
3. Log all processing steps with timing information.
4. Implement error handling with retries and dead-letter queues.
5. Partition data appropriately for parallel processing.
6. Monitor pipeline health — alert on failures or data drift.
7. Document data schemas and transformation logic.""",

    "mobile_app": """\
## MOBILE APP CONTEXT
This project is a **mobile application**. The following principles apply:
1. Handle offline state gracefully — cache data locally.
2. Optimise for battery life and network usage.
3. Follow platform-specific design guidelines (Material Design / HIG).
4. Handle different screen sizes and orientations.
5. Implement proper permission handling.
6. Write unit tests for business logic and UI tests for critical flows.
7. Keep the app responsive — move heavy work off the main thread.""",

    "devops_infra": """\
## DEVOPS / INFRASTRUCTURE CONTEXT
This project is **infrastructure or DevOps tooling**. The following principles apply:
1. Make everything declarative and repeatable.
2. Never hardcode secrets — use environment variables, secrets managers, or vaults.
3. Pin dependency versions in all Dockerfiles and configs.
4. Document the architecture and deployment process.
5. Include health checks and readiness probes.
6. Plan for disaster recovery — backup and restore procedures.
7. Keep images/artifacts as small as possible (multi-stage builds, minimal base images).
8. Use infrastructure-as-code principles: version everything, review all changes.""",

    "other_generic": """\
## GENERAL CONTEXT
This project does not match a specific specialised category. Apply general-purpose best practices:
1. Read before editing. Understand the codebase before making changes.
2. Follow existing patterns and conventions in the code.
3. Write clean, maintainable code with appropriate tests.
4. Document non-obvious design decisions.
5. Consider error handling, edge cases, and performance.
6. Prioritise backward compatibility where possible.""",
}


CLASSIFIER_SYSTEM_PROMPT = """\
You are a project classifier. Given the file tree, config files, and source samples of a repository, determine the single best category for the project.

Respond with ONLY valid JSON:
{"category": "<category_key>", "reason": "<brief reason>"}

Available categories:
{category_list}

Choose the most specific category that matches. If none fit well, use "other_generic".
"""


def _gather_project_context(repo_path: Path) -> str:
    parts: list[str] = []

    tree_lines: list[str] = []
    try:
        for i, entry in enumerate(sorted(repo_path.rglob("*"))):
            if i >= 80:
                tree_lines.append("  ... (more files)")
                break
            try:
                rel = entry.relative_to(repo_path)
                parts_list = rel.parts
                skip = False
                for p in parts_list:
                    if p.startswith(".") or p in (
                        "__pycache__", "node_modules", ".git", "venv", ".venv",
                        "target", "dist", "build", "bin", "obj",
                    ):
                        skip = True
                        break
                if skip:
                    continue
                indent = "  " * (len(parts_list) - 1)
                suffix = f"/" if entry.is_dir() else ""
                tree_lines.append(f"{indent}{rel.name}{suffix}")
            except (ValueError, OSError):
                continue
    except Exception:
        tree_lines.append("(unable to list)")

    if tree_lines:
        parts.append("## File tree\n" + "\n".join(tree_lines))

    config_files = [
        "package.json", "pyproject.toml", "Cargo.toml", "go.mod",
        "CMakeLists.txt", "Makefile", "Dockerfile", "docker-compose.yml",
        ".github/workflows", "requirements.txt", "Pipfile",
        "setup.py", "setup.cfg", "build.gradle", "pom.xml",
        "Gemfile", "composer.json", "Project.xml", "mod.cpp",
        "tsconfig.json", "vite.config.ts", "next.config.js",
        ".env.example", "docker-compose.yaml",
    ]
    config_blocks: list[str] = []
    for cf in config_files:
        cf_path = repo_path / cf
        if cf_path.exists() and cf_path.is_file():
            try:
                content = cf_path.read_text(encoding="utf-8", errors="replace")[:2000]
                config_blocks.append(f"--- {cf} ---\n{content}")
            except Exception:
                pass
    if config_blocks:
        parts.append("## Config files\n" + "\n\n".join(config_blocks))

    source_samples: list[str] = []
    source_extensions = {
        ".py", ".js", ".ts", ".jsx", ".tsx", ".rs", ".go", ".java",
        ".cpp", ".c", ".h", ".cs", ".rb", ".php", ".swift", ".kt",
        ".lua", ".sqf", ".sh", ".yaml", ".yml", ".json",
    }
    try:
        candidate_files: list[Path] = []
        for f in repo_path.iterdir():
            if f.is_file() and f.suffix in source_extensions:
                candidate_files.append(f)
        for subdir in ("src", "lib", "app", "core", "api", "frontend", "backend", "client", "server"):
            sub_path = repo_path / subdir
            if sub_path.is_dir():
                try:
                    for f in sorted(sub_path.iterdir())[:3]:
                        if f.is_file() and f.suffix in source_extensions:
                            candidate_files.append(f)
                except Exception:
                    pass
        for f in candidate_files[:5]:
            try:
                content = f.read_text(encoding="utf-8", errors="replace")[:1000]
                rel = f.relative_to(repo_path)
                source_samples.append(f"--- {rel} ---\n{content}")
            except Exception:
                pass
    except Exception:
        pass
    if source_samples:
        parts.append("## Source samples\n" + "\n\n".join(source_samples))

    return "\n\n".join(parts)


def _build_category_list() -> str:
    lines: list[str] = []
    for key, cat in PROJECT_CATEGORIES.items():
        lines.append(f'  "{key}": {cat["label"]} — {cat["description"][:120]}')
    return "\n".join(lines)


async def classify_project(
    repo_path: Path,
    llm_generate_fn: Any,  # async (messages) -> LLMResponse
) -> tuple[str, str]:
    context = _gather_project_context(repo_path)
    logger.debug("Project context for classification:\n%s", context[:500])

    if not context.strip():
        logger.warning("Empty project context — falling back to generic classification.")
        return "other_generic", "No project files found to analyse."

    category_list = _build_category_list()
    prompt = CLASSIFIER_SYSTEM_PROMPT.format(category_list=category_list)
    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": f"Classify this project:\n\n{context[:8000]}"},
    ]

    try:
        response = await llm_generate_fn(
            role="default",
            messages=messages,
            max_tokens=256,
        )
        raw = response.content.strip()
        logger.debug("Classifier raw response: %s", raw[:500])
    except Exception as e:
        logger.warning("Classifier LLM call failed (%s) — falling back to heuristic.", e)
        return _heuristic_classify(repo_path, context)

    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        import re
        m = re.search(r"```(?:json)?\s*\n?({.*?})\s*\n?```", raw, re.DOTALL)
        if m:
            try:
                parsed = json.loads(m.group(1))
            except json.JSONDecodeError:
                parsed = None
        else:
            parsed = None

    if isinstance(parsed, dict):
        cat = parsed.get("category", "other_generic")
        reason = parsed.get("reason", "Classified by LLM.")
        if cat not in PROJECT_CATEGORIES:
            logger.warning("Unknown category '%s' from classifier — falling back.", cat)
            cat = "other_generic"
        return cat, reason

    logger.warning("Could not parse classifier response — falling back to heuristic.")
    return _heuristic_classify(repo_path, context)


def _heuristic_classify(repo_path: Path, context: str) -> tuple[str, str]:
    lowered = context.lower()
    scores: dict[str, int] = {}

    for cat_key, cat_info in PROJECT_CATEGORIES.items():
        score = 0
        for kw in cat_info.get("keywords", []):
            if kw.lower() in lowered:
                score += 1
        if score > 0:
            scores[cat_key] = score

    if scores:
        best = max(scores, key=scores.get)
        return best, f"Heuristic match ({scores[best]} keyword hits)."
    return "other_generic", "No heuristic match found."


def get_adaptive_prompt(category: str) -> str:
    return ADAPTIVE_PROMPT_ADDITIONS.get(
        category,
        ADAPTIVE_PROMPT_ADDITIONS["other_generic"],
    )


def inject_adaptive_prompt(base_prompt: str, category: str) -> str:
    addition = SAFEGUARD_PREAMBLE + get_adaptive_prompt(category)
    tool_marker = "## AVAILABLE TOOLS"
    if tool_marker in base_prompt:
        before, after = base_prompt.split(tool_marker, 1)
        return before + addition + "\n\n" + tool_marker + after
    return base_prompt + "\n\n" + addition
