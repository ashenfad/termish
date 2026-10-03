"""``/dev/null`` as a redirect target: it discards and reads empty, and
it is never a file in the filesystem.

``2>/dev/null`` always discarded. ``>`` and ``>>`` wrote a file named
``/dev/null`` instead, so a command an agent silenced left that file in
its tree, where every later listing and diff found it.
"""

import pytest

from termish import execute
from termish.errors import TerminalError
from termish.fs import MemoryFS


@pytest.fixture
def fs():
    return MemoryFS()


def test_stdout_to_dev_null_discards(fs):
    assert execute("echo hi > /dev/null; echo ok", fs) == "ok\n"
    assert not fs.exists("/dev/null")


def test_append_to_dev_null_discards(fs):
    assert execute("echo hi >> /dev/null; echo ok", fs) == "ok\n"
    assert not fs.exists("/dev/null")


def test_both_streams_to_dev_null(fs):
    out = execute("cat /missing > /dev/null 2>&1; echo exit=$?", fs)
    assert out == "exit=1\n"
    assert not fs.exists("/dev/null")


def test_a_pipeline_into_dev_null_keeps_its_exit_code(fs):
    fs.write("/a.txt", b"x\n")
    assert execute("cat /a.txt > /dev/null && echo ok", fs) == "ok\n"
    with pytest.raises(TerminalError):
        execute("cat /missing > /dev/null", fs)
    assert not fs.exists("/dev/null")


def test_reading_dev_null_is_empty(fs):
    assert execute("cat < /dev/null; echo ok", fs) == "ok\n"
    assert execute("wc -c < /dev/null", fs).strip() == "0"
    assert not fs.exists("/dev/null")


def test_another_file_still_writes(fs):
    execute("echo hi > /out.txt", fs)
    assert fs.read("/out.txt") == b"hi\n"


@pytest.mark.parametrize("target", ["/dev/./null", "/dev//null", "/tmp/../dev/null"])
def test_any_spelling_of_dev_null_discards(fs, target):
    assert execute(f"echo hi > {target}; echo ok", fs) == "ok\n"
    assert not fs.exists("/dev/null")


def test_a_relative_spelling_of_dev_null_discards(fs):
    out = execute("cd / && echo hi > dev/null && wc -c < dev/null", fs)
    assert out.strip() == "0"
    assert not fs.exists("/dev/null")
