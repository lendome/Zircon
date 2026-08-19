from __future__ import annotations

import difflib


def make_unified_diff(
    path: str,
    old_content: str,
    new_content: str,
    max_lines: int = 50,
) -> str:
    old_lines = old_content.splitlines(keepends=True)
    new_lines = new_content.splitlines(keepends=True)

    diff = list(difflib.unified_diff(
        old_lines,
        new_lines,
        fromfile=f"a/{path}",
        tofile=f"b/{path}",
        lineterm="",
    ))

    if not diff:
        return ""

    if len(diff) > max_lines:
        truncated = diff[:max_lines]
        remaining = len(diff) - max_lines
        truncated.append(f"... ({remaining} more lines)\n")
        return "\n".join(truncated)

    return "\n".join(diff)


def colorize_diff(diff_text: str) -> str:
    GREEN = "\033[32m"
    RED = "\033[31m"
    CYAN = "\033[36m"
    DIM = "\033[2m"
    RESET = "\033[0m"

    lines = diff_text.split("\n")
    colored = []
    for line in lines:
        if line.startswith("+++") or line.startswith("---"):
            colored.append(f"{DIM}{line}{RESET}")
        elif line.startswith("@@"):
            colored.append(f"{CYAN}{line}{RESET}")
        elif line.startswith("+"):
            colored.append(f"{GREEN}{line}{RESET}")
        elif line.startswith("-"):
            colored.append(f"{RED}{line}{RESET}")
        else:
            colored.append(line)
    return "\n".join(colored)
