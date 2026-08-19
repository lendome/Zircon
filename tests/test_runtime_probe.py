import asyncio
import pytest

from zirconAgent.core.runtime_probe import (
    AUTO_PREVIEW_MAX_CHARS,
    extract_local_urls,
    extract_artifacts,
    extract_background_pids,
    extract_facts,
    is_local_url,
    normalize_probe_url,
    probe_url,
    RuntimeProbe,
    truncate_preview,
)


class TestUrlExtraction:
    def test_extracts_localhost_with_path(self):
        text = "  VITE v6.4.3  Local:   http://localhost:5173/"
        assert extract_local_urls(text) == ["http://localhost:5173/"]

    def test_extracts_127_and_normalizes_wildcard(self):
        urls = extract_local_urls("Running on http://0.0.0.0:8000 (Press CTRL+C to quit)")
        assert urls == ["http://0.0.0.0:8000"]
        assert normalize_probe_url(urls[0]) == "http://127.0.0.1:8000/"

    def test_extracts_ipv6_loopback(self):
        urls = extract_local_urls("ready on http://[::1]:3000/")
        assert urls == ["http://[::1]:3000/"]
        assert normalize_probe_url(urls[0]) == "http://[::1]:3000/"

    def test_extracts_ipv6_wildcard(self):
        urls = extract_local_urls("ready on http://[::]:3000")
        assert urls == ["http://[::]:3000"]
        assert normalize_probe_url(urls[0]) == "http://[::1]:3000/"

    def test_dedupes_repeated_urls(self):
        text = "a http://localhost:5173/ b http://localhost:5173/ c"
        assert extract_local_urls(text) == ["http://localhost:5173/"]

    def test_ignores_public_urls(self):
        # Public IPs/hostnames are NOT auto-probed.
        text = "see https://example.com and http://8.8.8.8:53"
        assert extract_local_urls(text) == []

    def test_includes_private_ranges(self):
        text = "api at http://192.168.1.10:8080 and http://10.0.0.5:9090"
        assert set(extract_local_urls(text)) == {
            "http://192.168.1.10:8080",
            "http://10.0.0.5:9090",
        }

    def test_strips_trailing_punctuation(self):
        text = "listening on http://localhost:3000."
        assert extract_local_urls(text) == ["http://localhost:3000"]

    def test_is_local_url_helpers(self):
        assert is_local_url("http://localhost:5173/")
        assert is_local_url("http://127.0.0.1:8000")
        assert is_local_url("http://0.0.0.0:8000")
        assert not is_local_url("https://github.com")
        assert not is_local_url("ftp://localhost:21")


class TestArtifactExtraction:
    def test_extracts_windows_exe_path(self):
        text = "C:\\Users\\VIP\\Documents\\webapp\\todo-app\\src-tauri\\target\\debug\\todo-app.exe"
        arts = extract_artifacts(text)
        assert any(a.endswith("todo-app.exe") for a in arts)

    def test_extracts_relative_exe(self):
        text = "Built target/release/myapp.exe"
        assert any("myapp.exe" in a for a in extract_artifacts(text))

    def test_extracts_msi(self):
        text = "Installer: dist/Todo App_1.0.0_x64_en-US.msi"
        assert any(a.endswith(".msi") for a in extract_artifacts(text))

    def test_ignores_source_files(self):
        assert extract_artifacts("edited src/app.py and utils.ts") == []


class TestFactsExtraction:
    def test_extract_facts_combines(self):
        text = (
            "Background job started: bg_3\n"
            "Local: http://localhost:5173/\n"
            "Built target/release/app.exe\n"
            "Exit code: 0"
        )
        f = extract_facts(text)
        assert f.urls == ["http://localhost:5173/"]
        assert any("app.exe" in a for a in f.artifacts)
        assert f.background_pids == ["bg_3"]
        assert f.exit_code == 0

    def test_extract_background_pids(self):
        assert extract_background_pids("use shell_poll(pid='bg_7')") == ["bg_7"]


class TestProbe:
    @pytest.mark.asyncio
    async def test_probe_unreachable_port_returns_error(self):
        # Pick an unused high port; expect connection refused / timeout style.
        result = await probe_url("http://127.0.0.1:1/", timeout=1.0)
        assert result.ok is False
        assert result.error
        assert "127.0.0.1:1" in result.probe_url

    @pytest.mark.asyncio
    async def test_runtime_probe_dedupes_across_calls(self):
        probe = RuntimeProbe()
        first = await probe.probe_new("see http://127.0.0.1:1/")
        second = await probe.probe_new("again http://127.0.0.1:1/ and http://127.0.0.1:2/")
        assert len(first) == 1
        # Second call only probes the new port (1/); port 1 already seen.
        assert len(second) == 1
        assert second[0].probe_url.startswith("http://127.0.0.1:2")


def test_truncate_preview_caps_automatic_body_and_suggests_fetch_url():
    preview = truncate_preview("x" * (AUTO_PREVIEW_MAX_CHARS + 1))

    assert preview.startswith("x" * AUTO_PREVIEW_MAX_CHARS)
    assert "use fetch_url" in preview


if __name__ == "__main__":
    asyncio.run(probe_url("http://127.0.0.1:1/", timeout=1.0))
