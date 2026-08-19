# `llm/` — LLM Layer

Routes requests to configured AI providers with structured output parsing.

| Module | Purpose |
|--------|---------|
| `router.py` | `ModelRouter` — multi-provider dispatch (OpenAI, Anthropic, Ollama), rate-limit handling, streaming |
| `prompts.py` | All system prompts: agent styles, planner, gatekeeper, sub-agents, swarm, history summarizer |
| `structured.py` | JSON extraction from LLM responses (regex, markdown code block, direct parse) |