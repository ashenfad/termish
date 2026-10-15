"""A filesystem error as a terminal shows it."""

_TEXT: dict[type[OSError], str] = {
    FileNotFoundError: "No such file or directory",
    IsADirectoryError: "Is a directory",
    NotADirectoryError: "Not a directory",
    FileExistsError: "File exists",
    PermissionError: "Permission denied",
}


def fs_error_text(e: OSError) -> str:
    """``No such file or directory`` and the like for ``e``; its own
    ``strerror`` (or message) for any other kind."""
    for kind, text in _TEXT.items():
        if isinstance(e, kind):
            return text
    return e.strerror or str(e)
