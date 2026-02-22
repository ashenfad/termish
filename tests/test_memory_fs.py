"""Tests for MemoryFS implementation."""

import pytest

from termish.fs import FileSystem, MemoryFS


class TestProtocolCompliance:
    def test_isinstance_check(self):
        fs = MemoryFS()
        assert isinstance(fs, FileSystem)


class TestBasicFileOps:
    def test_write_and_read(self):
        fs = MemoryFS()
        fs.write("/hello.txt", b"hello world")
        assert fs.read("/hello.txt") == b"hello world"

    def test_write_creates_parent_dirs(self):
        fs = MemoryFS()
        fs.write("/a/b/c.txt", b"deep")
        assert fs.isdir("/a")
        assert fs.isdir("/a/b")
        assert fs.read("/a/b/c.txt") == b"deep"

    def test_write_overwrite(self):
        fs = MemoryFS()
        fs.write("/f.txt", b"old")
        fs.write("/f.txt", b"new")
        assert fs.read("/f.txt") == b"new"

    def test_write_append(self):
        fs = MemoryFS()
        fs.write("/f.txt", b"hello")
        fs.write("/f.txt", b" world", mode="a")
        assert fs.read("/f.txt") == b"hello world"

    def test_read_nonexistent(self):
        fs = MemoryFS()
        with pytest.raises(FileNotFoundError):
            fs.read("/nope.txt")

    def test_read_directory_errors(self):
        fs = MemoryFS()
        fs.mkdir("/mydir")
        with pytest.raises(IsADirectoryError):
            fs.read("/mydir")

    def test_exists(self):
        fs = MemoryFS()
        assert not fs.exists("/x.txt")
        fs.write("/x.txt", b"")
        assert fs.exists("/x.txt")

    def test_isfile_isdir(self):
        fs = MemoryFS()
        fs.mkdir("/d")
        fs.write("/d/f.txt", b"")
        assert fs.isdir("/d")
        assert not fs.isfile("/d")
        assert fs.isfile("/d/f.txt")
        assert not fs.isdir("/d/f.txt")

    def test_remove(self):
        fs = MemoryFS()
        fs.write("/f.txt", b"data")
        fs.remove("/f.txt")
        assert not fs.exists("/f.txt")

    def test_remove_nonexistent(self):
        fs = MemoryFS()
        with pytest.raises(FileNotFoundError):
            fs.remove("/nope.txt")

    def test_rename(self):
        fs = MemoryFS()
        fs.write("/old.txt", b"data")
        fs.rename("/old.txt", "/new.txt")
        assert not fs.exists("/old.txt")
        assert fs.read("/new.txt") == b"data"


class TestDirectoryOps:
    def test_mkdir(self):
        fs = MemoryFS()
        fs.mkdir("/mydir")
        assert fs.isdir("/mydir")

    def test_mkdir_exist_ok(self):
        fs = MemoryFS()
        fs.mkdir("/mydir")
        fs.mkdir("/mydir", exist_ok=True)  # Should not raise
        with pytest.raises(FileExistsError):
            fs.mkdir("/mydir", exist_ok=False)

    def test_makedirs(self):
        fs = MemoryFS()
        fs.makedirs("/a/b/c")
        assert fs.isdir("/a")
        assert fs.isdir("/a/b")
        assert fs.isdir("/a/b/c")

    def test_rmdir_empty(self):
        fs = MemoryFS()
        fs.mkdir("/empty")
        fs.rmdir("/empty")
        assert not fs.exists("/empty")

    def test_rmdir_nonempty_errors(self):
        fs = MemoryFS()
        fs.mkdir("/d")
        fs.write("/d/f.txt", b"")
        with pytest.raises(OSError):
            fs.rmdir("/d")

    def test_rmdir_nonexistent(self):
        fs = MemoryFS()
        with pytest.raises(FileNotFoundError):
            fs.rmdir("/nope")

    def test_list_basic(self):
        fs = MemoryFS()
        fs.write("/a.txt", b"")
        fs.write("/b.txt", b"")
        fs.mkdir("/sub")
        entries = fs.list("/")
        assert "a.txt" in entries
        assert "b.txt" in entries
        assert "sub" in entries

    def test_list_recursive(self):
        fs = MemoryFS()
        fs.write("/d/a.txt", b"")
        fs.write("/d/sub/b.txt", b"")
        entries = fs.list("/d", recursive=True)
        # Should include nested paths
        assert any("a.txt" in e for e in entries)
        assert any("b.txt" in e for e in entries)

    def test_list_detailed(self):
        fs = MemoryFS()
        fs.write("/f.txt", b"hello")
        fs.mkdir("/d")
        infos = fs.list_detailed("/")
        names = [i.name for i in infos]
        assert "f.txt" in names
        assert "d" in names
        # Check file info
        f_info = [i for i in infos if i.name == "f.txt"][0]
        assert f_info.size == 5
        assert not f_info.is_dir
        # Check dir info
        d_info = [i for i in infos if i.name == "d"][0]
        assert d_info.is_dir


class TestPathResolution:
    def test_relative_paths(self):
        fs = MemoryFS()
        fs.write("/f.txt", b"root")
        # cwd is / by default
        assert fs.read("f.txt") == b"root"

    def test_chdir(self):
        fs = MemoryFS()
        fs.makedirs("/a/b")
        fs.chdir("/a")
        assert fs.getcwd() == "/a"
        fs.chdir("b")
        assert fs.getcwd() == "/a/b"

    def test_chdir_nonexistent(self):
        fs = MemoryFS()
        with pytest.raises(FileNotFoundError):
            fs.chdir("/nope")

    def test_dotdot_resolution(self):
        fs = MemoryFS()
        fs.makedirs("/a/b")
        fs.write("/a/f.txt", b"up")
        fs.chdir("/a/b")
        assert fs.read("../f.txt") == b"up"

    def test_dot_resolution(self):
        fs = MemoryFS()
        fs.write("/f.txt", b"dot")
        assert fs.read("./f.txt") == b"dot"


class TestStat:
    def test_stat_file(self):
        fs = MemoryFS()
        fs.write("/f.txt", b"hello")
        meta = fs.stat("/f.txt")
        assert meta.size == 5
        assert not meta.is_dir
        assert meta.created_at
        assert meta.modified_at

    def test_stat_dir(self):
        fs = MemoryFS()
        fs.mkdir("/d")
        meta = fs.stat("/d")
        assert meta.is_dir
        assert meta.size == 0

    def test_stat_nonexistent(self):
        fs = MemoryFS()
        with pytest.raises(FileNotFoundError):
            fs.stat("/nope")


class TestGlob:
    def test_glob_star(self):
        fs = MemoryFS()
        fs.write("/a.py", b"")
        fs.write("/b.py", b"")
        fs.write("/c.txt", b"")
        matches = fs.glob("/*.py")
        assert "/a.py" in matches
        assert "/b.py" in matches
        assert "/c.txt" not in matches

    def test_glob_recursive(self):
        fs = MemoryFS()
        fs.write("/src/a.py", b"")
        fs.write("/src/sub/b.py", b"")
        fs.write("/src/sub/c.txt", b"")
        matches = fs.glob("/src/**/*.py")
        assert any("a.py" in m for m in matches)
        assert any("b.py" in m for m in matches)
        assert not any("c.txt" in m for m in matches)

    def test_glob_no_matches(self):
        fs = MemoryFS()
        assert fs.glob("/*.xyz") == []
