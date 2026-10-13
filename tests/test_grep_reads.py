"""How grep reads the files it searches: in batches through ``read_many``
when the filesystem has it, file by file when it does not, and without
asking per entry whether a listed path is a directory."""

from collections import Counter

import pytest

from termish.errors import TerminalError
from termish.fs import MemoryFS
from termish.interpreter import execute_script
from termish.interpreter.commands import search
from termish.parser import to_script


class CountingFS:
    """A MemoryFS that counts the calls made to it, as a filesystem that
    fetches remotely pays a round trip for each. ``batched=False`` hides
    ``read_many``, as a filesystem without it would."""

    def __init__(self, batched: bool = True) -> None:
        self._fs = MemoryFS()
        self._batched = batched
        self.calls: Counter[str] = Counter()
        self.batches: list[list[str]] = []

    def __getattr__(self, name: str):
        if name == "read_many" and not self._batched:
            raise AttributeError(name)
        attr = getattr(self._fs, name)
        if not callable(attr):
            return attr

        def counted(*args, **kwargs):
            self.calls[name] += 1
            if name == "read_many":
                self.batches.append(list(args[0]))
            return attr(*args, **kwargs)

        return counted

    def seed(self, files: dict[str, str]) -> None:
        for path, text in files.items():
            self._fs.write(path, text.encode())
        self.calls.clear()
        self.batches.clear()


TREE = {
    "/src/a.py": "def foo(): pass\n",
    "/src/b.py": "class Bar: pass\n",
    "/src/sub/c.py": "def baz(): pass\n",
    "/src/sub/deeper/d.txt": "nothing here\n",
}


def run(fs, command: str) -> str:
    return execute_script(to_script(command), fs)


class TestBatched:
    def test_one_listing_and_one_read_many(self):
        fs = CountingFS()
        fs.seed(TREE)
        output = run(fs, "grep -rn def src")
        assert output == "src/a.py:1:def foo(): pass\nsrc/sub/c.py:1:def baz(): pass\n"
        assert fs.calls["list"] == 1
        assert fs.calls["read_many"] == 1
        assert fs.calls["read"] == 0
        # The operand is asked about once; the six entries under it are not.
        assert fs.calls["isdir"] == 1

    def test_no_operand_asks_nothing_per_entry(self):
        fs = CountingFS()
        fs.seed(TREE)
        fs.chdir("/src")
        fs.calls.clear()
        output = run(fs, "grep -r def")
        assert output == "./a.py:def foo(): pass\n./sub/c.py:def baz(): pass\n"
        assert fs.calls == Counter({"list": 1, "read_many": 1})

    def test_large_tree_reads_in_bounded_batches(self, monkeypatch):
        monkeypatch.setattr(search, "_READ_BATCH", 2)
        fs = CountingFS()
        fs.seed({f"/d/f{i}.txt": f"line {i}\n" for i in range(5)})
        output = run(fs, "grep -r 'line [024]' d")
        assert output == "d/f0.txt:line 0\nd/f2.txt:line 2\nd/f4.txt:line 4\n"
        assert [len(batch) for batch in fs.batches] == [2, 2, 1]
        assert fs.calls["read"] == 0

    def test_filters_run_before_the_read(self):
        fs = CountingFS()
        fs.seed(TREE)
        run(fs, "grep -r --include='*.py' --exclude-dir=sub def src")
        assert fs.batches == [["src/a.py", "src/b.py"]]

    def test_missing_operand_still_reports_after_earlier_output(self):
        fs = CountingFS()
        fs.seed({"/a.txt": "hit\n"})
        with pytest.raises(TerminalError) as exc:
            run(fs, "grep hit a.txt missing.txt")
        assert exc.value.exit_code == 2
        assert "missing.txt: No such file or directory" in str(exc.value)

    def test_a_failing_read_many_falls_back_to_reading_each_file(self):
        fs = CountingFS()
        fs.seed(TREE)

        def broken(paths):
            raise ConnectionError("store unreachable")

        fs._fs.read_many = broken
        output = run(fs, "grep -rl def src")
        assert output == "src/a.py\nsrc/sub/c.py\n"
        assert fs.calls["read_many"] == 1
        assert fs.calls["read"] == 6  # each of the six entries, as without it

    def test_keys_spelled_otherwise_fall_back_to_reading_each_file(self):
        """A read_many that keys its answer by absolute path breaks the
        contract; grep reads file by file rather than skip every file."""
        fs = CountingFS()
        fs.seed(TREE)
        inner = fs._fs
        fs._fs.read_many = lambda paths: {
            inner._resolve(p): inner._files[inner._resolve(p)]
            for p in paths
            if inner.isfile(p)
        }
        output = run(fs, "grep -rl def src")
        assert output == "src/a.py\nsrc/sub/c.py\n"
        assert fs.calls["read"] == 6


class TestFallback:
    def test_reads_each_file_and_asks_only_about_directories(self):
        fs = CountingFS(batched=False)
        fs.seed(TREE)
        output = run(fs, "grep -rn def src")
        assert output == "src/a.py:1:def foo(): pass\nsrc/sub/c.py:1:def baz(): pass\n"
        assert fs.calls["read_many"] == 0
        # Every entry is read once; the two directories fail and are asked
        # about, plus the operand. Before, all six entries were asked.
        assert fs.calls["read"] == 6
        assert fs.calls["isdir"] == 1 + 2

    def test_a_directory_read_that_raises_file_not_found_is_skipped(self):
        """Some filesystems (monkeyfs's VirtualFS) answer a read of a
        directory with FileNotFoundError, not IsADirectoryError."""

        class DirNotFoundFS(CountingFS):
            def read(self, path, offset=0, size=-1):
                if self._fs.isdir(path):
                    raise FileNotFoundError(path)
                return self._fs.read(path, offset, size)

        fs = DirNotFoundFS(batched=False)
        fs.seed(TREE)
        assert run(fs, "grep -rc def src") == (
            "src/a.py:1\nsrc/b.py:0\nsrc/sub/c.py:1\nsrc/sub/deeper/d.txt:0\n"
        )

    def test_files_without_match_lists_no_directories(self):
        fs = CountingFS(batched=False)
        fs.seed(TREE)
        assert run(fs, "grep -rL def src") == "src/b.py\nsrc/sub/deeper/d.txt\n"


@pytest.mark.parametrize("batched", [True, False])
class TestLabels:
    """A directory operand without -r labels its lines only when more than
    one file is searched, as before entries stopped being checked one by
    one for being directories."""

    def test_one_file_under_a_directory_is_unlabeled(self, batched):
        fs = CountingFS(batched)
        fs.seed({"/src/sub/only.py": "def foo(): pass\n"})
        assert run(fs, "grep def src") == "def foo(): pass\n"

    def test_two_files_under_a_directory_are_labeled(self, batched):
        fs = CountingFS(batched)
        fs.seed({"/src/sub/a.py": "def a\n", "/src/b.py": "def b\n"})
        assert run(fs, "grep def src") == "src/b.py:def b\nsrc/sub/a.py:def a\n"

    def test_quiet_exit_status(self, batched):
        fs = CountingFS(batched)
        fs.seed(TREE)
        assert run(fs, "grep -rq def src && echo yes") == "yes\n"
        assert run(fs, "grep -rq zzz src || echo no") == "no\n"


@pytest.fixture
def monkeyfs():
    return pytest.importorskip("monkeyfs")


class TestMonkeyfs:
    def test_a_mount_answers_a_tree_in_one_batch(self, monkeyfs):
        """Through a MountFS, the mounted backend's share of the tree
        arrives in one read_many: the remote case grep batches for."""
        backend = CountingFS()
        backend.seed({"/notes/a.md": "todo: x\n", "/notes/b.md": "done\n"})
        fs = monkeyfs.MountFS(monkeyfs.VirtualFS({}), {"/docs": backend})
        output = run(fs, "grep -r todo /docs")
        assert output == "/docs/notes/a.md:todo: x\n"
        assert backend.calls["read_many"] == 1
        assert backend.calls["read"] == 0

    def test_virtual_fs_directories_are_skipped(self, monkeyfs):
        fs = monkeyfs.VirtualFS({})
        for path, text in TREE.items():
            fs.write(path, text.encode())
        assert run(fs, "grep -rc def /src") == (
            "/src/a.py:1\n/src/b.py:0\n/src/sub/c.py:1\n/src/sub/deeper/d.txt:0\n"
        )
