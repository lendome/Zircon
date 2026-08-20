from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, AsyncIterator

import httpx

from ..core.types import LLMResponse, ModelProfile, StreamChunk, ToolCall, ToolCallDelta
from ..core.config import RouterConfig
from ..llm.structured import extract_json

logger = logging.getLogger("agent.llm.router")


def _normalize_usage(data: dict) -> dict:
    """Normalize OpenAI-compatible usage while preserving provider extensions."""
    usage = data.get("usage", {})
    result = dict(usage) if isinstance(usage, dict) else {}
    for source in (usage, data):
        if not isinstance(source, dict):
            continue
        for key in ("cost", "total_cost", "total_cost_usd"):
            if source.get(key) is not None:
                result["cost"] = source[key]
                return result
    return result


class ModelRouter:
    _TRANSIENT_RETRY_ATTEMPTS = 3
    _TRANSIENT_RETRY_DELAY_SECONDS = 3.0

    def __init__(self, config: RouterConfig):
        self.config = config
        self._profiles: dict[str, ModelProfile] = {}
        for p in config.profiles:
            self._profiles[p.name] = p
        self._last_request_time: float = 0.0
        self.reasoning_effort: str = "medium"
        self.session_cost_usd: float = 0.0
        self._nitro_mode: bool = False
        # Fast mode routes to the highest-throughput providers (OpenRouter
        # "nitro"). Seeded from config; also toggleable at runtime / via the CLI.
        self._fast_mode: bool = bool(getattr(config, "fast_mode", False))

    @property
    def fast_mode(self) -> bool:
        return self._fast_mode

    def set_fast_mode(self, enabled: bool) -> None:
        self._fast_mode = bool(enabled)

    @property
    def nitro_mode(self) -> bool:
        return self._nitro_mode

    def set_nitro_mode(self, enabled: bool) -> None:
        self._nitro_mode = bool(enabled)

    def reset_session_cost(self) -> None:
        self.session_cost_usd = 0.0

    def _record_cost(self, usage: dict) -> None:
        try:
            cost = float(usage.get("cost", 0.0))
        except (TypeError, ValueError):
            return
        if cost > 0:
            self.session_cost_usd += cost

    @staticmethod
    def _request_headers(profile: ModelProfile) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if profile.api_key:
            headers["Authorization"] = f"Bearer {profile.api_key}"
        return headers

    @staticmethod
    def _request_max_tokens(profile: ModelProfile, requested: int | None) -> int:
        """Keep caller budgets within the profile's known provider limit."""
        return min(requested or profile.max_tokens, profile.max_tokens)

    def select(self, role: str) -> list[ModelProfile]:
        candidates = self.config.role_priority.get(role, [])
        result = []
        for name in candidates:
            if name in self._profiles:
                result.append(self._profiles[name])
        if not result:
            default = self._profiles.get("default")
            if default:
                result.append(default)
        for p in self._profiles.values():
            if p not in result and (role in p.roles or "default" in p.roles):
                result.append(p)
        return result

    def set_role_profile(self, role: str, profile_name: str) -> None:
        """Make a profile the first candidate for a role at runtime."""
        if profile_name not in self._profiles:
            raise ValueError(f"Unknown model profile: {profile_name}")
        candidates = [name for name in self.config.role_priority.get(role, []) if name != profile_name]
        self.config.role_priority[role] = [profile_name, *candidates]

    @property
    def context_window(self) -> int:
        """Return the largest context_window across all profiles (default 128K)."""
        max_cw = 0
        for p in self._profiles.values():
            if p.context_window > max_cw:
                max_cw = p.context_window
        return max_cw or 128000

    def profile_context_window(self, profile_name: str) -> int:
        """Get context_window for a specific profile, or fall back to default."""
        p = self._profiles.get(profile_name)
        if p:
            return p.context_window
        return 128000

    def _is_non_retryable(self, error: Exception) -> bool:
        """Check if an error should NOT be retried (e.g. auth failures, bad requests)."""
        if isinstance(error, httpx.HTTPStatusError):
            return error.response.status_code in (400, 401, 403)
        return False

    @classmethod
    def _retry_attempts(cls, configured_attempts: int) -> int:
        """Guarantee enough attempts to recover from transient transport failures."""
        return max(configured_attempts, cls._TRANSIENT_RETRY_ATTEMPTS)

    @staticmethod
    def _is_transient_transport_error(error: Exception) -> bool:
        """Return whether a connection interruption or timeout is safe to retry."""
        return isinstance(error, (httpx.TimeoutException, httpx.TransportError))

    async def generate(
        self,
        role: str,
        messages: list[dict],
        tools: list[dict] | None = None,
        max_tokens: int | None = None,
        disable_reasoning: bool = False,
        progress_callback=None,
        tool_choice: Any = None,
    ) -> LLMResponse:
        candidates = self.select(role)
        last_error: Exception | None = None
        logger.debug("generate(role=%s, candidates=[%s], msgs=%d)", role,
                      ", ".join(p.name for p in candidates), len(messages))

        attempts = self._retry_attempts(self.config.max_retries)
        for profile in candidates:
            for attempt in range(attempts):
                try:
                    await self._enforce_rate_limit()
                    if progress_callback:
                        attempt_label = f" ({attempt+1}/{attempts})" if attempt > 0 else ""
                        progress_callback(f"⏳ Contacting {profile.model}{attempt_label}...")
                    t0 = time.monotonic()
                    resp = await self._call(profile, messages, tools, max_tokens, disable_reasoning=disable_reasoning, tool_choice=tool_choice)
                    self._record_cost(resp.usage)
                    dt = time.monotonic() - t0
                    logger.info("response from %s: %d tokens in %.1fs (role=%s)",
                                resp.model, resp.output_tokens, dt, role)
                    if progress_callback:
                        progress_callback(f"✅ Response from {resp.model} ({resp.output_tokens} tokens in {dt:.1f}s)")
                    return resp
                except (httpx.TransportError, httpx.HTTPStatusError) as e:
                    # Build a full, detailed error message from the provider response
                    error_detail = str(e)
                    if isinstance(e, httpx.HTTPStatusError):
                        status = e.response.status_code
                        body = e.response.text
                        error_detail = f"HTTP {status} ({profile.model}): {body}"
                        # Truncate for logs/progress only if very long, but keep full in RuntimeError
                        log_detail = error_detail[:500] if len(error_detail) > 500 else error_detail
                        logger.warning("Provider error on %s: %s", profile.name, log_detail)
                    else:
                        log_detail = error_detail

                    # 400/401/403 should NOT be retried — fail immediately
                    if self._is_non_retryable(e):
                        logger.error("Non-retryable error on %s: %s", profile.name, log_detail)
                        if progress_callback:
                            progress_callback(f"❌ {profile.model}: {log_detail[:200]}")
                        raise RuntimeError(
                            f"All models failed for role '{role}': {error_detail}"
                        ) from e

                    last_error = e
                    delay = (
                        self._TRANSIENT_RETRY_DELAY_SECONDS
                        if self._is_transient_transport_error(e)
                        else min(
                            self.config.retry_base_delay * (2 ** attempt),
                            self.config.retry_max_delay,
                        )
                    )
                    logger.warning("retry %s attempt %d: %s (%.1fs wait)",
                                   profile.name, attempt, log_detail, delay)
                    if progress_callback:
                        progress_callback(f"⚠️ {profile.model} attempt {attempt+1} failed: {log_detail[:100]} — retrying in {delay:.0f}s...")
                    await asyncio.sleep(delay)

        # If we exhausted all profiles, build the final error with the full detail
        final_detail = str(last_error)
        if isinstance(last_error, httpx.HTTPStatusError):
            final_detail = f"HTTP {last_error.response.status_code}: {last_error.response.text}"
        raise RuntimeError(f"All models failed for role '{role}': {final_detail}")

    async def generate_stream(
        self,
        role: str,
        messages: list[dict],
        tools: list[dict] | None = None,
        max_tokens: int | None = None,
        disable_reasoning: bool = False,
        progress_callback=None,
    ) -> AsyncIterator[StreamChunk]:
        candidates = self.select(role)
        last_error: Exception | None = None
        attempts = self._retry_attempts(self.config.max_retries)
        for profile in candidates:
            for attempt in range(attempts):
                try:
                    await self._enforce_rate_limit()
                    if progress_callback:
                        attempt_label = f" ({attempt+1}/{attempts})" if attempt > 0 else ""
                        progress_callback(f"⏳ Streaming {profile.model}{attempt_label}...")
                    async for chunk in self._call_stream(profile, messages, tools, max_tokens, disable_reasoning=disable_reasoning):
                        if chunk.done and chunk.usage:
                            self._record_cost(chunk.usage)
                        yield chunk
                    if progress_callback:
                        progress_callback(f"✅ Stream from {profile.model} complete")
                    return
                except (httpx.TransportError, httpx.HTTPStatusError) as e:
                    # Build a full, detailed error message from the provider response
                    error_detail = str(e)
                    if isinstance(e, httpx.HTTPStatusError):
                        status = e.response.status_code
                        body = e.response.text
                        error_detail = f"HTTP {status} ({profile.model}): {body}"
                        log_detail = error_detail[:500] if len(error_detail) > 500 else error_detail
                        logger.warning("Provider stream error on %s: %s", profile.name, log_detail)
                    else:
                        log_detail = error_detail

                    if self._is_non_retryable(e):
                        logger.error("Non-retryable stream error on %s: %s", profile.name, log_detail)
                        if progress_callback:
                            progress_callback(f"❌ {profile.model}: {log_detail[:200]}")
                        raise RuntimeError(
                            f"All models failed for role '{role}': {error_detail}"
                        ) from e

                    delay = (
                        self._TRANSIENT_RETRY_DELAY_SECONDS
                        if self._is_transient_transport_error(e)
                        else min(
                            self.config.retry_base_delay * (2 ** attempt),
                            self.config.retry_max_delay,
                        )
                    )
                    logger.warning("stream retry %s attempt %d: %s (%.1fs wait)",
                                   profile.name, attempt, log_detail, delay)
                    if progress_callback:
                        progress_callback(f"⚠️ {profile.model} streaming attempt {attempt+1} failed: {log_detail[:100]} — retrying in {delay:.0f}s...")
                    await asyncio.sleep(delay)
                    continue
        # If we exhausted all profiles, build the final error with full detail
        final_detail = str(last_error) if last_error else "All providers failed without specific error"
        if isinstance(last_error, httpx.HTTPStatusError):
            final_detail = f"HTTP {last_error.response.status_code}: {last_error.response.text}"
        raise RuntimeError(f"All models failed for role '{role}': {final_detail}")

    async def _call(
        self,
        profile: ModelProfile,
        messages: list[dict],
        tools: list[dict] | None,
        max_tokens: int | None,
        disable_reasoning: bool = False,
        tool_choice: Any = None,
    ) -> LLMResponse:
        payload = self._build_payload(profile, messages, tools, max_tokens, stream=False, disable_reasoning=disable_reasoning, tool_choice=tool_choice)
        async with httpx.AsyncClient(timeout=profile.timeout) as client:
            resp = await client.post(
                f"{profile.base_url}/chat/completions",
                headers=self._request_headers(profile),
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()

        choice = data["choices"][0]
        message = choice["message"]
        content = message.get("content") or ""
        reasoning_content = message.get("reasoning_content") or ""
        tool_calls = self._parse_tool_calls(message.get("tool_calls", []))
        finish_reason = choice.get("finish_reason", "") or ""

        return LLMResponse(
            content=content,
            reasoning_content=reasoning_content,
            tool_calls=tool_calls,
            model=data.get("model", profile.model),
            usage=_normalize_usage(data),
            finish_reason=finish_reason,
        )

    async def _call_stream(
        self,
        profile: ModelProfile,
        messages: list[dict],
        tools: list[dict] | None,
        max_tokens: int | None,
        disable_reasoning: bool = False,
    ) -> AsyncIterator[StreamChunk]:
        payload = self._build_payload(profile, messages, tools, max_tokens, stream=True, disable_reasoning=disable_reasoning)
        collected_content = ""
        collected_tool_calls: dict[int, dict] = {}
        model_name = profile.model
        last_finish_reason = ""

        async with httpx.AsyncClient(timeout=httpx.Timeout(profile.timeout, read=profile.timeout)) as client:
            async with client.stream(
                "POST",
                f"{profile.base_url}/chat/completions",
                headers=self._request_headers(profile),
                json=payload,
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data_str = line[6:].strip()
                    if data_str == "[DONE]":
                        break

                    try:
                        chunk = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue

                    model_name = chunk.get("model", model_name)
                    choices = chunk.get("choices", [])
                    if not choices:
                        continue

                    delta = choices[0].get("delta", {})
                    fr = choices[0].get("finish_reason")
                    if fr:
                        last_finish_reason = fr

                    if "content" in delta and delta["content"]:
                        collected_content += delta["content"]
                        yield StreamChunk(text=delta["content"], model=model_name)

                    for rkey in ("reasoning_content", "reasoning"):
                        if rkey in delta and delta[rkey]:
                            yield StreamChunk(reasoning=delta[rkey], model=model_name)

                    if "tool_calls" in delta:
                        for tc_delta in delta["tool_calls"]:
                            idx = tc_delta.get("index", 0)
                            if idx not in collected_tool_calls:
                                collected_tool_calls[idx] = {
                                    "id": "",
                                    "name": "",
                                    "arguments": "",
                                }
                            entry = collected_tool_calls[idx]
                            if tc_delta.get("id"):
                                entry["id"] = tc_delta["id"]
                            fn = tc_delta.get("function", {})
                            if fn.get("name"):
                                entry["name"] += fn["name"]
                            if fn.get("arguments"):
                                entry["arguments"] += fn["arguments"]
                            # Emit the assembled-so-far fragment immediately so
                            # downstream consumers can render tool calls live.
                            yield StreamChunk(
                                tool_call_delta=ToolCallDelta(
                                    index=idx,
                                    id=entry["id"],
                                    name=entry["name"],
                                    arguments=entry["arguments"],
                                ),
                                model=model_name,
                            )

                    usage = _normalize_usage(chunk)
                    if usage:
                        yield StreamChunk(done=True, model=model_name, usage=usage)

        if collected_content or collected_tool_calls:
            tool_calls = []
            for idx in sorted(collected_tool_calls.keys()):
                tc = collected_tool_calls[idx]
                args = ModelRouter._parse_arguments(tc.get("arguments", ""))
                tool_calls.append(ToolCall(id=tc["id"], name=tc["name"], arguments=args))

            yield StreamChunk(
                done=True,
                model=model_name,
                usage={},
                tool_calls=tool_calls if tool_calls else None,
                finish_reason=last_finish_reason,
            )

    @staticmethod
    def _normalize_message_roles(messages: list[dict]) -> list[dict]:
        """Sanitize system-message placement for strict providers (Anthropic).

        Anthropic rejects a ``system``-role message that appears anywhere
        other than the leading run (unless it follows a ``user`` message or an
        ``assistant`` message ending in a server tool result, or uses the
        directive-only form ``content: []`` with ``output_config``). The tool
        loop appends mid-conversation system notes (advisor feedback, budget
        nudges, execution-state blocks, history summaries), which produced
        HTTP 400 "messages.N: role 'system' must follow a 'user' message".

        Normalization, preserving every message and its content:
        - The leading run of system messages is kept as-is (always valid).
        - A later system message that directly follows a ``user`` message is
          kept as-is (valid position).
        - Directive-only system messages (``content: []``) are kept as-is
          (valid at any position).
        - Any other system message is folded into the preceding ``user``
          message when possible, otherwise emitted as a ``user`` message —
          but never inserted between an assistant tool_call and its tool
          results (that pairing must stay intact).
        """
        if not messages:
            return messages

        def _as_user(msg: dict) -> dict:
            converted = dict(msg)
            converted["role"] = "user"
            return converted

        def _merge_into_user(target: dict, msg: dict) -> None:
            extra = msg.get("content")
            if extra in (None, ""):
                return
            base = target.get("content")
            if isinstance(base, str) and isinstance(extra, str):
                target["content"] = base + "\n\n" + extra
            elif base in (None, "") and isinstance(extra, str):
                target["content"] = extra
            elif isinstance(base, list) and isinstance(extra, str):
                base.append({"type": "text", "text": extra})
            elif isinstance(base, str) and isinstance(extra, list):
                target["content"] = [{"type": "text", "text": base}, *extra]
            elif isinstance(base, list) and isinstance(extra, list):
                base.extend(extra)
            else:
                # Unknown content shapes — fall back to a textual merge so
                # nothing is silently dropped.
                target["content"] = f"{base or ''}\n\n{extra}"

        # Fast path: already valid (nothing to do) — avoid copying.
        saw_non_system = False
        needs_fix = False
        for i, m in enumerate(messages):
            if not isinstance(m, dict):
                continue
            role = m.get("role")
            if role == "system" and saw_non_system:
                content = m.get("content")
                prev_role = messages[i - 1].get("role") if i > 0 and isinstance(messages[i - 1], dict) else None
                directive_only = content == [] and m.get("output_config") is not None
                if prev_role != "user" and not directive_only:
                    needs_fix = True
                    break
            elif role != "system":
                saw_non_system = True
        if not needs_fix:
            return messages

        out: list[dict] = []
        pending: list[dict] = []  # system messages awaiting safe placement

        def _flush_pending() -> None:
            for p in pending:
                if out and out[-1].get("role") == "user":
                    _merge_into_user(out[-1], p)
                else:
                    out.append(_as_user(p))
            pending.clear()

        header_done = False
        for m in messages:
            if not isinstance(m, dict):
                _flush_pending()
                out.append(m)
                continue
            role = m.get("role")
            if role == "system" and not header_done:
                out.append(m)  # leading system run stays verbatim
                continue
            if role != "system":
                header_done = True
                if role == "tool":
                    # Keep assistant(tool_calls) -> tool results paired:
                    # defer pending system messages until the tool run ends.
                    out.append(m)
                    continue
                _flush_pending()
                out.append(m)
                continue
            # Mid-conversation system message.
            content = m.get("content")
            if content == [] and m.get("output_config") is not None:
                _flush_pending()
                out.append(m)  # directive-only form is valid anywhere
                continue
            if out and out[-1].get("role") == "user":
                _merge_into_user(out[-1], m)
                continue
            pending.append(m)
        _flush_pending()
        return out

    def _build_payload(
        self,
        profile: ModelProfile,
        messages: list[dict],
        tools: list[dict] | None,
        max_tokens: int | None,
        stream: bool = False,
        disable_reasoning: bool = False,
        tool_choice: Any = None,
    ) -> dict:
        payload: dict = {
            "model": self._request_model(profile),
            "messages": self._normalize_message_roles(messages),
            "max_tokens": self._request_max_tokens(profile, max_tokens),
            "stream": stream,
        }
        if tools:
            payload["tools"] = [{"type": "function", "function": t} for t in tools]
            if tool_choice is not None:
                payload["tool_choice"] = tool_choice
        if disable_reasoning:
            if "anthropic" in profile.model:
                payload["thinking"] = {"type": "disabled"}
            if "claude" in profile.model:
                payload["thinking"] = {"type": "disabled"}
        # OpenRouter unified reasoning control. A per-profile setting wins;
        # otherwise the router-global reasoning_effort applies. Omitted only
        # when nothing is set (defers to the model's native default).
        reasoning: dict = {}
        effort = getattr(profile, "reasoning_effort", None)
        enabled = getattr(profile, "reasoning_enabled", None)
        if disable_reasoning and enabled is None:
            enabled = False
        if enabled is False:
            reasoning["enabled"] = False
        elif effort:
            reasoning["effort"] = effort
        elif not disable_reasoning and self.reasoning_effort and self.reasoning_effort != "none":
            reasoning["effort"] = self.reasoning_effort
        if reasoning:
            payload["reasoning"] = reasoning
        if self._fast_mode and self._supports_nitro(profile):
            # Sort providers by throughput (equivalent to the ":nitro" slug
            # suffix). Only meaningful for OpenRouter-style multi-provider
            # endpoints; harmless to omit elsewhere.
            provider = dict(payload.get("provider") or {})
            provider["sort"] = "throughput"
            payload["provider"] = provider
        return payload

    def _request_model(self, profile: ModelProfile) -> str:
        """Return the model slug adjusted for active OpenRouter Nitro mode."""
        model = profile.model
        if self._nitro_mode and self._supports_nitro(profile) and not model.endswith(":nitro"):
            return f"{model}:nitro"
        return model

    @staticmethod
    def _supports_nitro(profile: ModelProfile) -> bool:
        """Throughput sorting only applies to OpenRouter's aggregated API."""
        return "openrouter.ai" in (profile.base_url or "")

    @staticmethod
    def _parse_arguments(raw: str) -> dict:
        if not raw or not raw.strip():
            return {}
        text = raw.strip()

        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return parsed
            if isinstance(parsed, str):
                return ModelRouter._parse_arguments(parsed)
        except json.JSONDecodeError:
            pass

        cleaned = text
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            lines = [l for l in lines if not l.startswith("```")]
            cleaned = "\n".join(lines).strip()
            try:
                parsed = json.loads(cleaned)
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                pass

        extracted = extract_json(text)
        if isinstance(extracted, dict):
            return extracted

        return {}

    @staticmethod
    def _parse_tool_calls(raw: list[dict]) -> list[ToolCall]:
        result = []
        for tc in raw:
            fn = tc.get("function", {})
            args = ModelRouter._parse_arguments(fn.get("arguments", "{}"))
            result.append(ToolCall(
                id=tc.get("id", ""),
                name=fn.get("name", ""),
                arguments=args,
            ))
        return result

    async def _enforce_rate_limit(self):
        if self.config.rate_limit_delay > 0:
            elapsed = time.monotonic() - self._last_request_time
            if elapsed < self.config.rate_limit_delay:
                wait = self.config.rate_limit_delay - elapsed
                # Instrumentation: how much wall-clock this self-throttle costs.
                self._throttle_wait_total = getattr(self, "_throttle_wait_total", 0.0) + wait
                await asyncio.sleep(wait)
        self._last_request_time = time.monotonic()
