import pytest

from zirconAgent.core.completion_gate import (
    ExecutionState,
    classify_task,
    evaluate_completion,
)


def state(task="build it as an exe"):
    return ExecutionState(task=task)


class TestClassifyTask:
    def test_build_package_keyword(self):
        cats = classify_task(state("package the app as an .exe installer"))
        assert "build" in cats

    def test_server_keyword(self):
        cats = classify_task(state("run the flask web server"))
        assert "server" in cats

    def test_implementation_keyword(self):
        cats = classify_task(state("add a login feature"))
        assert "implementation" in cats

    def test_no_categories_for_qa(self):
        assert classify_task(state("what does this function do")) == set()


class TestBuildEvidence:
    def test_blocks_without_artifact_or_build(self):
        st = state("build it as an exe")
        v = evaluate_completion(st, has_text_response=True)
        assert not v.accept
        assert "build_artifact_or_successful_build" in v.missing
        assert "artifact" in v.nudge.lower()

    def test_accepts_with_artifact(self):
        st = state("build it as an exe")
        st.add_artifacts(["dist/app.exe"])
        v = evaluate_completion(st, has_text_response=True)
        assert v.accept

    def test_accepts_with_successful_build_command(self):
        st = state("build it as an exe")
        st.add_command("cargo build --release", exit_code=0, ok=True)
        v = evaluate_completion(st, has_text_response=True)
        assert v.accept

    def test_does_not_accept_on_failed_build(self):
        st = state("build it as an exe")
        st.add_command("cargo build --release", exit_code=1, ok=False)
        v = evaluate_completion(st, has_text_response=True)
        assert not v.accept

    def test_nudge_once_does_not_accept_unresolved_evidence(self):
        st = state("build it as an exe")
        v1 = evaluate_completion(st, has_text_response=True)
        assert not v1.accept
        v2 = evaluate_completion(st, has_text_response=True)
        assert not v2.accept
        assert "build_artifact_or_successful_build" in v2.missing
        assert not v2.nudge


class TestServerEvidence:
    def test_blocks_when_server_started_but_unreachable(self):
        st = state("run the web server")
        st.server_started = True
        from zirconAgent.core.runtime_probe import ProbeResult
        st.probe_results.append(
            ProbeResult(advertised_url="http://localhost:8000", probe_url="http://127.0.0.1:8000",
                        ok=False, error="connection refused")
        )
        v = evaluate_completion(st, has_text_response=True)
        assert not v.accept
        assert "reachable_server_url" in v.missing

    def test_accepts_when_server_reachable(self):
        st = state("run the web server")
        st.server_started = True
        from zirconAgent.core.runtime_probe import ProbeResult
        st.probe_results.append(
            ProbeResult(advertised_url="http://localhost:8000", probe_url="http://127.0.0.1:8000",
                        ok=True, status_code=200, content_type="text/html")
        )
        v = evaluate_completion(st, has_text_response=True)
        assert v.accept

    def test_later_success_supersedes_earlier_probe_failure(self):
        from zirconAgent.core.runtime_probe import ProbeResult

        st = state("run the web server")
        st.server_started = True
        st.record_probe_result(ProbeResult(
            advertised_url="http://localhost:8000",
            probe_url="http://127.0.0.1:8000",
            ok=False,
            error="connection refused",
        ))
        st.record_probe_result(ProbeResult(
            advertised_url="http://localhost:8000",
            probe_url="http://127.0.0.1:8000",
            ok=True,
            status_code=200,
        ))

        assert evaluate_completion(st, has_text_response=True).accept
        assert len(st.probe_results) == 2

    def test_no_server_requirement_when_no_server_started(self):
        st = state("run the web server")
        # No server was actually started; don't gate on reachability.
        v = evaluate_completion(st, has_text_response=True)
        assert v.accept


class TestReadonlyTask:
    def test_qa_accepted_without_evidence(self):
        st = state("what does foo() do")
        v = evaluate_completion(st, has_text_response=True)
        assert v.accept
        assert v.missing == []
