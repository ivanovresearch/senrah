"""
tests/unit/test_diff_files.py — parse_diff_files (changed paths from a diff).

Covers the header forms GitHub's `.diff` media type emits: plain modify,
add/delete, rename (b-side wins), binary (no ---/+++ hunks), quoted paths,
dedup, and empty input.
"""

from __future__ import annotations

from senrah.ingester.diff_files import parse_diff_files

MODIFY = """\
diff --git a/src/app/main.py b/src/app/main.py
index 1111111..2222222 100644
--- a/src/app/main.py
+++ b/src/app/main.py
@@ -1,3 +1,4 @@
+import sys
"""

MULTI = """\
diff --git a/src/a.cs b/src/a.cs
index 1111111..2222222 100644
--- a/src/a.cs
+++ b/src/a.cs
@@ -1 +1 @@
-x
+y
diff --git a/tests/a_test.cs b/tests/a_test.cs
new file mode 100644
index 0000000..3333333
--- /dev/null
+++ b/tests/a_test.cs
@@ -0,0 +1 @@
+t
diff --git a/old/dead.cs b/old/dead.cs
deleted file mode 100644
index 4444444..0000000
--- a/old/dead.cs
+++ /dev/null
@@ -1 +0,0 @@
-gone
"""

RENAME = """\
diff --git a/src/before.cs b/src/after.cs
similarity index 97%
rename from src/before.cs
rename to src/after.cs
"""

BINARY = """\
diff --git a/assets/logo.png b/assets/logo.png
index 1111111..2222222 100644
Binary files a/assets/logo.png and b/assets/logo.png differ
"""

QUOTED = '''\
diff --git "a/docs/with space.md" "b/docs/with space.md"
index 1111111..2222222 100644
--- "a/docs/with space.md"
+++ "b/docs/with space.md"
@@ -1 +1 @@
-a
+b
'''


class TestParseDiffFiles:
    def test_single_modify(self) -> None:
        assert parse_diff_files(MODIFY) == ["src/app/main.py"]

    def test_multi_add_delete(self) -> None:
        assert parse_diff_files(MULTI) == [
            "src/a.cs",
            "tests/a_test.cs",
            "old/dead.cs",
        ]

    def test_rename_takes_new_path(self) -> None:
        assert parse_diff_files(RENAME) == ["src/after.cs"]

    def test_binary_file_included(self) -> None:
        # Binary entries have no ---/+++ hunk lines; the diff --git header
        # is the only place they are named.
        assert parse_diff_files(BINARY) == ["assets/logo.png"]

    def test_quoted_path_unwrapped(self) -> None:
        assert parse_diff_files(QUOTED) == ["docs/with space.md"]

    def test_empty_input(self) -> None:
        assert parse_diff_files("") == []

    def test_dedup_preserves_order(self) -> None:
        assert parse_diff_files(MODIFY + MODIFY) == ["src/app/main.py"]

    def test_diff_body_lines_ignored(self) -> None:
        # A '+diff --git ...' ADDED LINE inside a hunk must not be parsed.
        sneaky = MODIFY + "+diff --git a/fake.py b/fake.py\n"
        assert parse_diff_files(sneaky) == ["src/app/main.py"]
