"""Shared argument parser for terminal commands."""

import argparse

from faketerm.errors import TerminalError


class CommandArgParser(argparse.ArgumentParser):
    """ArgumentParser that raises TerminalError instead of exiting."""

    def error(self, message):
        raise TerminalError(f"{self.prog}: {message}")

    def exit(self, status=0, message=None):
        if status != 0:
            raise TerminalError(message or "Argument parsing failed")
