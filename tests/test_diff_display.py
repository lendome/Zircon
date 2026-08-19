from zirconAgent.core.diff_display import make_unified_diff, colorize_diff


class TestMakeUnifiedDiff:
    def test_simple_edit(self):
        diff = make_unified_diff("test.py", "hello\nworld\n", "hello\nearth\n")
        assert "--- a/test.py" in diff
        assert "+++ b/test.py" in diff
        assert "-world" in diff
        assert "+earth" in diff

    def test_no_change(self):
        diff = make_unified_diff("x.py", "same\n", "same\n")
        assert diff == ""

    def test_addition(self):
        diff = make_unified_diff("x.py", "line1\n", "line1\nline2\n")
        assert "+line2" in diff

    def test_deletion(self):
        diff = make_unified_diff("x.py", "a\nb\nc\n", "a\nc\n")
        assert "-b" in diff

    def test_truncation(self):
        old = "\n".join(f"old line {i}" for i in range(100))
        new = "\n".join(f"new line {i}" for i in range(100))
        diff = make_unified_diff("big.py", old, new, max_lines=10)
        assert "more lines" in diff

    def test_empty_old(self):
        diff = make_unified_diff("new.py", "", "hello\n")
        assert "+hello" in diff

    def test_empty_new(self):
        diff = make_unified_diff("gone.py", "bye\n", "")
        assert "-bye" in diff


class TestColorizeDiff:
    def test_additions_green(self):
        result = colorize_diff("+added line")
        assert "\033[32m" in result  # GREEN

    def test_deletions_red(self):
        result = colorize_diff("-removed line")
        assert "\033[31m" in result  # RED

    def test_hunk_headers_cyan(self):
        result = colorize_diff("@@ -1,3 +1,4 @@")
        assert "\033[36m" in result  # CYAN

    def test_file_headers_dim(self):
        result = colorize_diff("--- a/test.py")
        assert "\033[2m" in result  # DIM

    def test_context_lines_unchanged(self):
        result = colorize_diff(" context line")
        assert "\033[" not in result  # no ANSI codes
