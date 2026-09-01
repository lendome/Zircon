# Multi-model configuration for the agent framework.
# Profiles are selected by ROLE, not by name. The router picks the best available.

# Optional: web search backend. Default is keyless DuckDuckGo ("ddg").
# Keyed backends give more reliable, higher-quality results:
# web_search:
#   rapidapi_key: "${RAPIDAPI_KEY}"
#   rapidapi_host: google-api31.p.rapidapi.com   # any RapidAPI SERP host
#   rapidapi_endpoint: /websearch                # its search path
#   rapidapi_region: wt-wt                        # optional
#   context7_api_key: "${CONTEXT7_API_KEY}"      # optional: raises lookup_docs rate limits
# Search runs through a RapidAPI SERP API. To swap providers, change host +
# endpoint to another RapidAPI SERP listing; the parser accepts the common
# result shapes (title/href|url/body|snippet).

profiles:
  advisor:
    base_url: "https://openrouter.ai/api/v1"
    api_key: "YOUR_API_KEY_HERE"
    model: "openai/gpt-5.6-sol-pro"
    supports_vision: true
    reasoning_effort: "max"
    max_tokens: 4096
    context_window: 128000
    supports_caching: false
    cost_input: 0.0
    cost_output: 0.0
    roles: ["advisor"]
    timeout: 180.0

  frontier:
    base_url: "https://openrouter.ai/api/v1"
    api_key: "YOUR_API_KEY_HERE"
    model: "minimax/minimax-m2.7"
    max_tokens: 8192
    context_window: 32000
    supports_caching: false
    cost_input: 0.0
    cost_output: 0.0
    roles: ["architect", "planner", "complex_debug", "frontier"]
    timeout: 120.0

  default:
    base_url: "https://openrouter.ai/api/v1"
    api_key: "YOUR_API_KEY_HERE"
    model: "minimax/minimax-m2.7"
    # Set true only when this endpoint/model accepts image_url message parts.
    supports_vision: false
    max_tokens: 8192
    context_window: 32000
    supports_caching: false
    cost_input: 0.0
    cost_output: 0.0
    roles: ["editor", "general", "chat", "default", "planner", "architect"]
    timeout: 120.0

  fast_apply:
    base_url: "https://openrouter.ai/api/v1"
    api_key: "YOUR_API_KEY_HERE"
    model: "minimax/minimax-m2.7"
    max_tokens: 4096
    context_window: 32000
    supports_caching: false
    roles: ["fast_rewrite", "speculative", "fast"]
    timeout: 120.0

  small:
    base_url: "https://openrouter.ai/api/v1"
    api_key: "YOUR_API_KEY_HERE"
    model: "minimax/minimax-m2.7"
    max_tokens: 2048
    context_window: 32000
    supports_caching: false
    roles: ["summarize", "classify", "mask", "distill", "small"]
    timeout: 120.0

# Router settings
router:
  default_role: "default"
  failover_enabled: true
  cost_tracking: true
  streaming: true
  streaming_fallback: true
  max_concurrent: 3
  max_retries: 4
  retry_base_delay: 1.0
  retry_max_delay: 32.0
  rate_limit_delay: 0.5
  role_priority:
    advisor: ["advisor", "frontier", "default"]
    architect: ["frontier", "default"]
    planner: ["frontier", "default"]
    editor: ["default", "frontier"]
    default: ["default", "frontier"]
    fast: ["fast_apply", "default"]
    distill: ["small", "default"]
    summarize: ["small", "default"]
