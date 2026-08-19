import pytest
from zirconAgent.core.distiller import Distiller


@pytest.fixture
def distiller():
    return Distiller()


class TestAutoDetect:
    def test_detect_pytest(self, distiller):
        data = "test_main.py::test_add PASSED\ntest_main.py::test_sub FAILED\n=== 1 passed, 1 failed in 0.5s ==="
        assert distiller._detect_schema(data) == "pytest_output"

    def test_detect_shell(self, distiller):
        data = "some output\nExit code: 1"
        assert distiller._detect_schema(data) == "shell_output"

    def test_detect_generic(self, distiller):
        data = "just some random text"
        assert distiller._detect_schema(data) == "generic"


class TestDistillPytest:
    def test_basic(self, distiller):
        data = (
            "test_a.py::test_one PASSED\n"
            "test_a.py::test_two FAILED\n"
            "E   assert 1 == 2\n"
            "=== 1 passed, 1 failed in 0.5s ==="
        )
        result = distiller.distill(data, "pytest_output")
        assert "passed" in result.lower()
        assert "fail" in result.lower()

    def test_no_failures(self, distiller):
        data = "test_a.py::test_one PASSED\ntest_a.py::test_two PASSED\n=== 2 passed in 0.3s ==="
        result = distiller.distill(data, "pytest_output")
        assert "2 passed" in result or "passed: 2" in result.lower()


class TestDistillShell:
    def test_with_exit_code(self, distiller):
        data = "Compiling...\nDone.\nExit code: 0"
        result = distiller.distill(data, "shell_output")
        assert "0" in result

    def test_with_stderr(self, distiller):
        data = "output\nSTDERR:\nerror: something broke\nExit code: 1"
        result = distiller.distill(data, "shell_output")
        assert "error" in result.lower() or "STDERR" in result


class TestDistillLinter:
    def test_basic(self, distiller):
        data = "app.py:10:5: E999 SyntaxError\napp.py:20:1: F841 unused variable"
        result = distiller.distill(data, "linter_output")
        assert "E999" in result or "F841" in result


class TestGenericDistill:
    def test_short_data_unchanged(self, distiller):
        data = "hello world"
        assert distiller.distill(data) == data

    def test_long_data_truncated(self, distiller):
        data = "x" * 5000
        result = distiller.distill(data, target_tokens=100)
        assert len(result) < len(data)

    def test_empty_data(self, distiller):
        assert distiller.distill("") == ""


class TestDistillToSignal:
    def test_short_unchanged(self, distiller):
        data = "short output"
        assert distiller.distill_to_signal(data) == data

    def test_long_compressed(self, distiller):
        data = "\n".join(f"line {i}" for i in range(50))
        result = distiller.distill_to_signal(data)
        assert len(result) < len(data)


class TestObservationMasking:
    def test_mask_with_focus(self, distiller):
        data = "line about cats\nline about dogs\nline about cats again\nmore dogs\ncats here"
        result = distiller.mask_observation(data, "cats")
        assert "cats" in result.lower()
        assert len(result) <= len(data)

    def test_no_focus_returns_data(self, distiller):
        data = "some output"
        assert distiller.mask_observation(data) == data

    def test_short_data_unchanged(self, distiller):
        data = "short"
        assert distiller.mask_observation(data, "focus") == data
