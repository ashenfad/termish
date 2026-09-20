"""Tests for the FileSystem conformance kit."""

import pytest

from termish.fs import MemoryFS, check_filesystem


class TestMemoryFS:
    def test_memory_fs_conforms(self):
        check_filesystem(MemoryFS())

    def test_kit_cleans_up_after_itself(self):
        fs = MemoryFS()
        check_filesystem(fs)
        assert fs.list("/") == []
        assert fs.getcwd() == "/"

    def test_conforms_from_a_non_root_cwd(self):
        fs = MemoryFS()
        fs.makedirs("/home/agent")
        fs.chdir("/home/agent")
        check_filesystem(fs)
        assert fs.getcwd() == "/home/agent"
        assert fs.list("/home/agent") == []

    def test_rejects_a_filesystem_that_is_not_empty(self):
        fs = MemoryFS()
        fs.makedirs("/.termish-conformance")
        with pytest.raises(AssertionError, match="empty filesystem"):
            check_filesystem(fs)


class TestCatchesABadBackend:
    def test_discarding_the_range_fails(self):
        """A backend that takes offset/size and ignores them must not pass.

        This is the failure the kit exists for: whole-file reads all still
        return the right bytes, so nothing else catches it.
        """

        class WholeFileFS(MemoryFS):
            def read(self, path: str, offset: int = 0, size: int = -1) -> bytes:
                return super().read(path)

        with pytest.raises(AssertionError, match="^read:"):
            check_filesystem(WholeFileFS())

    def test_accepting_a_negative_offset_fails(self):
        class LenientFS(MemoryFS):
            def read(self, path: str, offset: int = 0, size: int = -1) -> bytes:
                return super().read(path, max(offset, 0), size)

        with pytest.raises(AssertionError, match="negative offset"):
            check_filesystem(LenientFS())

    def test_the_old_signature_fails_loudly(self):
        """A backend written against the whole-file signature raises TypeError."""

        class LegacyFS(MemoryFS):
            def read(self, path: str) -> bytes:  # type: ignore[override]
                return super().read(path)

        with pytest.raises(TypeError):
            check_filesystem(LegacyFS())
