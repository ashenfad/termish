"""The small commands, where the shared cases can't reach: host commands
in ``help``/``which``/``command``, ``du -h``'s rounding, ``seq``'s limit
and ``strftime``'s conversions. Mirrors termish-ts's
builtins-small.test.ts.
"""

from datetime import datetime, timezone

import pytest

from termish.context import CommandContext
from termish.errors import TerminalError
from termish.fs import MemoryFS
from termish.interpreter import execute_script
from termish.interpreter.commands.clock import strftime
from termish.interpreter.commands.fsinfo import du_human
from termish.interpreter.commands.lines import SEQ_MAX
from termish.parser import to_script


def run(script: str, fs=None, **kwargs) -> str:
    return execute_script(to_script(script), fs or MemoryFS(), **kwargs)


def deploy(ctx: CommandContext) -> None:
    return None


deploy.description = "ship the app"  # type: ignore[attr-defined]


def quiet(ctx: CommandContext) -> None:
    return None


class TestHostCommands:
    commands = {"deploy": deploy, "quiet": quiet}

    def test_listed_by_help_with_a_description_when_they_have_one(self):
        out = run("help", commands=self.commands)
        assert any(
            line.split() == ["deploy", "ship", "the", "app"]
            for line in out.splitlines()
        )
        assert "quiet" in out.splitlines()

    def test_help_name_shows_the_description_or_says_there_is_none(self):
        assert run("help deploy", commands=self.commands) == "deploy - ship the app\n"
        assert (
            run("help quiet", commands=self.commands)
            == "quiet: a command of this host, with no help here\n"
        )

    def test_found_by_which_and_command(self):
        assert run("which deploy", commands=self.commands) == "deploy\n"
        assert (
            run("command -V deploy", commands=self.commands)
            == "deploy is a host command\n"
        )

    def test_override_a_builtins_help_when_they_take_its_name(self):
        def ls(ctx: CommandContext) -> None:
            return None

        ls.description = "a host ls"  # type: ignore[attr-defined]
        assert run("help ls", commands={"ls": ls}) == "ls - a host ls\n"


class TestDuHuman:
    def test_rounds_up_one_decimal_under_ten(self):
        assert du_human(0) == "0"
        assert du_human(1023) == "1023"
        assert du_human(1024) == "1.0K"
        assert du_human(1536) == "1.5K"
        assert du_human(1537) == "1.6K"
        assert du_human(10 * 1024) == "10K"
        assert du_human(10 * 1024 + 1) == "11K"
        assert du_human(1024 * 1024 - 1) == "1.0M"
        assert du_human(5 * 1024**3) == "5.0G"

    def test_shows_sizes_that_way(self):
        fs = MemoryFS()
        fs.write("/big", b"x" * 1536)
        assert run("du -h /big", fs) == "1.5K\t/big\n"


class TestSeq:
    def test_stops_at_its_limit(self):
        with pytest.raises(TerminalError, match="too many numbers"):
            run(f"seq {SEQ_MAX + 1}")


class TestStrftime:
    # 2026-03-05 07:08:09 UTC, a Thursday, day 64
    at = datetime(2026, 3, 5, 7, 8, 9, tzinfo=timezone.utc)

    def test_formats_each_conversion(self):
        assert (
            strftime(self.at, "%Y %y %m %d %e %j %H %I %M %S %p %u %w", True)
            == "2026 26 03 05  5 064 07 07 08 09 AM 4 4"
        )
        assert strftime(self.at, "%a %A %b %B", True) == "Thu Thursday Mar March"
        assert (
            strftime(self.at, "%F %T %D %R %z %:z %Z %s", True)
            == "2026-03-05 07:08:09 03/05/26 07:08 +0000 +00:00 UTC 1772694489"
        )
        assert strftime(self.at, "%n%t%%%Q", True) == "\n\t%%Q"

    def test_shows_12_for_noon_and_midnight_on_a_12_hour_clock(self):
        midnight = datetime(2026, 1, 1, 0, tzinfo=timezone.utc)
        noon = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
        assert strftime(midnight, "%I %p", True) == "12 AM"
        assert strftime(noon, "%I %p", True) == "12 PM"
