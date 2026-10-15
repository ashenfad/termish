"""``date``, and the strftime it formats with (shared with ``stat``).

Only the current time: no ``-d`` (parsing a date) and no setting the
clock. Times show in the host's local zone, or UTC with ``-u``.

Mirrors termish-ts's ``clock.ts``, conversion for conversion. The
conversions are done here rather than by ``time.strftime``, so an
unknown one stays as written on every platform.
"""

import re
from datetime import datetime, timezone

from termish.context import CommandContext, CommandResult
from termish.errors import TerminalError

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
MONTHS = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]

#: ``date`` with no format: ``Thu Oct 15 14:23:01 PDT 2026``.
DEFAULT_DATE_FORMAT = "%a %b %e %H:%M:%S %Z %Y"

#: ``-I``'s precisions, as formats.
ISO_FORMATS = {
    "date": "%Y-%m-%d",
    "hours": "%Y-%m-%dT%H%:z",
    "minutes": "%Y-%m-%dT%H:%M%:z",
    "seconds": "%Y-%m-%dT%H:%M:%S%:z",
}


def _offset_text(dt: datetime, colon: bool) -> str:
    """``+hhmm``, or ``+hh:mm`` with ``colon``."""
    delta = dt.utcoffset()
    minutes = 0 if delta is None else int(delta.total_seconds() // 60)
    sign = "-" if minutes < 0 else "+"
    hours, mins = divmod(abs(minutes), 60)
    return f"{sign}{hours:02d}{':' if colon else ''}{mins:02d}"


def _zone_name(dt: datetime, utc: bool) -> str:
    """The zone's short name (``PDT``, ``UTC``), or its offset where the
    platform has no name for it."""
    if utc:
        return "UTC"
    name = dt.tzname() or ""
    return name if re.fullmatch(r"[A-Z]{2,5}", name) else _offset_text(dt, False)


def moment(when: datetime, utc: bool) -> datetime:
    """``when`` (aware) in the local zone, or in UTC with ``utc``."""
    return when.astimezone(timezone.utc) if utc else when.astimezone()


def strftime(when: datetime, fmt: str, utc: bool) -> str:
    """Format ``when`` by ``fmt``'s strftime conversions.

    ``%Y %y %m %d %e %j %H %I %M %S %p %s %a %A %b %B %u %w %z %:z %Z
    %F %T %D %R %n %t %%``. Any other ``%`` sequence is kept as written.
    """
    dt = moment(when, utc)
    out: list[str] = []
    i = 0
    while i < len(fmt):
        ch = fmt[i]
        if ch != "%" or i == len(fmt) - 1:
            out.append(ch)
            i += 1
            continue
        code = fmt[i + 1]
        i += 2
        if code == ":" and i < len(fmt) and fmt[i] == "z":
            code = ":z"
            i += 1
        out.append(_convert(dt, code, utc))
    return "".join(out)


def _convert(dt: datetime, code: str, utc: bool) -> str:
    hour12 = dt.hour % 12 or 12
    match code:
        case "Y":
            return str(dt.year)
        case "y":
            return f"{dt.year % 100:02d}"
        case "m":
            return f"{dt.month:02d}"
        case "d":
            return f"{dt.day:02d}"
        case "e":
            return f"{dt.day:2d}"
        case "j":
            return f"{dt.timetuple().tm_yday:03d}"
        case "H":
            return f"{dt.hour:02d}"
        case "I":
            return f"{hour12:02d}"
        case "M":
            return f"{dt.minute:02d}"
        case "S":
            return f"{dt.second:02d}"
        case "p":
            return "AM" if dt.hour < 12 else "PM"
        case "s":
            return str(int(dt.timestamp() // 1))
        case "a":
            return DAYS[dt.weekday()][:3]
        case "A":
            return DAYS[dt.weekday()]
        case "b":
            return MONTHS[dt.month - 1][:3]
        case "B":
            return MONTHS[dt.month - 1]
        case "u":
            return str(dt.isoweekday())
        case "w":
            return str(dt.isoweekday() % 7)
        case "z":
            return _offset_text(dt, False)
        case ":z":
            return _offset_text(dt, True)
        case "Z":
            return _zone_name(dt, utc)
        case "F":
            return f"{dt.year}-{dt.month:02d}-{dt.day:02d}"
        case "T":
            return f"{dt.hour:02d}:{dt.minute:02d}:{dt.second:02d}"
        case "D":
            return f"{dt.month:02d}/{dt.day:02d}/{dt.year % 100:02d}"
        case "R":
            return f"{dt.hour:02d}:{dt.minute:02d}"
        case "n":
            return "\n"
        case "t":
            return "\t"
        case "%":
            return "%"
        case _:
            return f"%{code}"


def date(ctx: CommandContext) -> CommandResult | None:
    """``date [-u] [-I[PREC] | --iso-8601[=PREC] | -R | +FORMAT]``

    The current time: by default as ``Thu Oct 15 14:23:01 PDT 2026``;
    ``+FORMAT`` with strftime conversions; ``-I`` (``date``, ``hours``,
    ``minutes`` or ``seconds``) as ISO 8601; ``-R`` as RFC 5322. ``-u``
    shows UTC.
    """
    utc = False
    fmt: str | None = None

    def set_format(f: str) -> None:
        nonlocal fmt
        if fmt is not None:
            raise TerminalError("date: multiple output formats specified")
        fmt = f

    for arg in ctx.args:
        if arg in ("-u", "--utc", "--universal"):
            utc = True
        elif arg in ("-R", "--rfc-email"):
            set_format("%a, %d %b %Y %H:%M:%S %z")
        elif (
            arg.startswith("-I") or arg == "--iso-8601" or arg.startswith("--iso-8601=")
        ):
            prec = arg[2:] if arg.startswith("-I") else arg[len("--iso-8601=") :]
            iso = ISO_FORMATS.get(prec or "date")
            if iso is None:
                raise TerminalError(
                    f"date: invalid argument '{prec}' for --iso-8601"
                    " (use date, hours, minutes or seconds)"
                )
            set_format(iso)
        elif arg.startswith("+"):
            set_format(arg[1:])
        elif arg == "-d" or arg.startswith("--date"):
            raise TerminalError(
                "date: -d/--date is not supported; only the current time is shown"
            )
        elif arg.startswith("-"):
            raise TerminalError(f"date: unknown option: {arg}")
        else:
            raise TerminalError(f"date: setting the date is not supported: '{arg}'")
    now = datetime.now(timezone.utc)
    ctx.stdout.write(
        strftime(now, fmt if fmt is not None else DEFAULT_DATE_FORMAT, utc) + "\n"
    )
    return None
