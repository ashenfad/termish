"""Test that monkeyfs filesystem implementations satisfy the termish protocol."""

import pytest

monkeyfs = pytest.importorskip("monkeyfs")

from termish.fs import FileSystem


class TestVirtualFSProtocol:
    """Test monkeyfs.VirtualFS against termish FileSystem protocol."""

    def test_isinstance(self):
        fs = monkeyfs.VirtualFS({})
        assert isinstance(fs, FileSystem)

    def test_read_write(self):
        fs = monkeyfs.VirtualFS({})
        fs.write("/hello.txt", b"world")
        assert fs.read("/hello.txt") == b"world"

    def test_write_append(self):
        fs = monkeyfs.VirtualFS({})
        fs.write("/f.txt", b"hello")
        fs.write("/f.txt", b" world", mode="a")
        assert fs.read("/f.txt") == b"hello world"

    def test_exists_isfile_isdir(self):
        fs = monkeyfs.VirtualFS({})
        fs.write("/dir/file.txt", b"data")
        assert fs.exists("/dir/file.txt")
        assert fs.isfile("/dir/file.txt")
        assert fs.isdir("/dir")
        assert not fs.isfile("/dir")
        assert not fs.isdir("/dir/file.txt")

    def test_stat(self):
        fs = monkeyfs.VirtualFS({})
        fs.write("/f.txt", b"12345")
        meta = fs.stat("/f.txt")
        assert meta.size == 5
        assert meta.is_dir is False

    def test_stat_directory(self):
        fs = monkeyfs.VirtualFS({})
        fs.mkdir("/d")
        meta = fs.stat("/d")
        assert meta.is_dir is True

    def test_mkdir_and_makedirs(self):
        fs = monkeyfs.VirtualFS({})
        fs.mkdir("/a")
        assert fs.isdir("/a")
        fs.makedirs("/b/c/d")
        assert fs.isdir("/b/c/d")

    def test_mkdir_parents(self):
        fs = monkeyfs.VirtualFS({})
        fs.mkdir("/x/y/z", parents=True)
        assert fs.isdir("/x/y/z")

    def test_remove(self):
        fs = monkeyfs.VirtualFS({})
        fs.write("/f.txt", b"data")
        fs.remove("/f.txt")
        assert not fs.exists("/f.txt")

    def test_rmdir(self):
        fs = monkeyfs.VirtualFS({})
        fs.mkdir("/empty")
        fs.rmdir("/empty")
        assert not fs.exists("/empty")

    def test_rename(self):
        fs = monkeyfs.VirtualFS({})
        fs.write("/old.txt", b"data")
        fs.rename("/old.txt", "/new.txt")
        assert not fs.exists("/old.txt")
        assert fs.read("/new.txt") == b"data"

    def test_list(self):
        fs = monkeyfs.VirtualFS({})
        fs.write("/a.txt", b"")
        fs.write("/b.txt", b"")
        fs.write("/sub/c.txt", b"")
        result = fs.list("/")
        assert "a.txt" in result
        assert "b.txt" in result
        assert "sub" in result

    def test_list_recursive(self):
        fs = monkeyfs.VirtualFS({})
        fs.write("/d/a.txt", b"")
        fs.write("/d/sub/b.txt", b"")
        result = fs.list("/d", recursive=True)
        assert "a.txt" in result
        assert any("b.txt" in r for r in result)

    def test_list_detailed(self):
        fs = monkeyfs.VirtualFS({})
        fs.write("/f.txt", b"hello")
        fs.mkdir("/d")
        infos = fs.list_detailed("/")
        names = [i.name for i in infos]
        assert "f.txt" in names
        assert "d" in names

    def test_glob(self):
        fs = monkeyfs.VirtualFS({})
        fs.write("/a.py", b"")
        fs.write("/b.py", b"")
        fs.write("/c.txt", b"")
        matches = fs.glob("/*.py")
        assert any("a.py" in m for m in matches)
        assert any("b.py" in m for m in matches)
        assert not any("c.txt" in m for m in matches)

    def test_getcwd_chdir(self):
        fs = monkeyfs.VirtualFS({})
        assert fs.getcwd() == "/"
        fs.makedirs("/a/b")
        fs.chdir("/a")
        assert fs.getcwd() == "/a"


class TestIsolatedFSProtocol:
    """Test monkeyfs.IsolatedFS against termish FileSystem protocol."""

    def test_isinstance(self, tmp_path):
        fs = monkeyfs.IsolatedFS(str(tmp_path))
        assert isinstance(fs, FileSystem)

    def test_read_write(self, tmp_path):
        fs = monkeyfs.IsolatedFS(str(tmp_path))
        fs.write("hello.txt", b"world")
        assert fs.read("hello.txt") == b"world"

    def test_write_append(self, tmp_path):
        fs = monkeyfs.IsolatedFS(str(tmp_path))
        fs.write("f.txt", b"hello")
        fs.write("f.txt", b" world", mode="a")
        assert fs.read("f.txt") == b"hello world"

    def test_exists_isfile_isdir(self, tmp_path):
        fs = monkeyfs.IsolatedFS(str(tmp_path))
        fs.write("dir/file.txt", b"data")
        assert fs.exists("dir/file.txt")
        assert fs.isfile("dir/file.txt")
        assert fs.isdir("dir")
        assert not fs.isfile("dir")
        assert not fs.isdir("dir/file.txt")

    def test_stat(self, tmp_path):
        fs = monkeyfs.IsolatedFS(str(tmp_path))
        fs.write("f.txt", b"12345")
        meta = fs.stat("f.txt")
        assert meta.size == 5
        assert meta.is_dir is False

    def test_stat_directory(self, tmp_path):
        fs = monkeyfs.IsolatedFS(str(tmp_path))
        fs.mkdir("d")
        meta = fs.stat("d")
        assert meta.is_dir is True

    def test_mkdir_and_makedirs(self, tmp_path):
        fs = monkeyfs.IsolatedFS(str(tmp_path))
        fs.mkdir("a")
        assert fs.isdir("a")
        fs.makedirs("b/c/d")
        assert fs.isdir("b/c/d")

    def test_mkdir_parents(self, tmp_path):
        fs = monkeyfs.IsolatedFS(str(tmp_path))
        fs.mkdir("x/y/z", parents=True)
        assert fs.isdir("x/y/z")

    def test_remove(self, tmp_path):
        fs = monkeyfs.IsolatedFS(str(tmp_path))
        fs.write("f.txt", b"data")
        fs.remove("f.txt")
        assert not fs.exists("f.txt")

    def test_rmdir(self, tmp_path):
        fs = monkeyfs.IsolatedFS(str(tmp_path))
        fs.mkdir("empty")
        fs.rmdir("empty")
        assert not fs.exists("empty")

    def test_rename(self, tmp_path):
        fs = monkeyfs.IsolatedFS(str(tmp_path))
        fs.write("old.txt", b"data")
        fs.rename("old.txt", "new.txt")
        assert not fs.exists("old.txt")
        assert fs.read("new.txt") == b"data"

    def test_list(self, tmp_path):
        fs = monkeyfs.IsolatedFS(str(tmp_path))
        fs.write("a.txt", b"")
        fs.write("b.txt", b"")
        fs.mkdir("sub")
        result = fs.list(".")
        assert "a.txt" in result
        assert "b.txt" in result
        assert "sub" in result

    def test_list_recursive(self, tmp_path):
        fs = monkeyfs.IsolatedFS(str(tmp_path))
        fs.write("d/a.txt", b"")
        fs.write("d/sub/b.txt", b"")
        result = fs.list("d", recursive=True)
        assert "a.txt" in result
        assert any("b.txt" in r for r in result)

    def test_list_detailed(self, tmp_path):
        fs = monkeyfs.IsolatedFS(str(tmp_path))
        fs.write("f.txt", b"hello")
        fs.mkdir("d")
        infos = fs.list_detailed(".")
        names = [i.name for i in infos]
        assert "f.txt" in names
        assert "d" in names

    def test_glob(self, tmp_path):
        fs = monkeyfs.IsolatedFS(str(tmp_path))
        fs.write("a.py", b"")
        fs.write("b.py", b"")
        fs.write("c.txt", b"")
        matches = fs.glob("*.py")
        assert any("a.py" in m for m in matches)
        assert any("b.py" in m for m in matches)
        assert not any("c.txt" in m for m in matches)

    def test_getcwd_chdir(self, tmp_path):
        fs = monkeyfs.IsolatedFS(str(tmp_path))
        assert fs.getcwd() == "/"
        fs.makedirs("a/b")
        fs.chdir("a")
        assert fs.getcwd() == "/a"
