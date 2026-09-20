"""
Tests for the printf builtin.

Everything goes through ``execute`` so the real tokenizer decides what
FORMAT looks like by the time printf sees it: single quotes keep a
backslash literal, so ``printf '%s\\n' x`` hands printf a two-character
``\\n`` that printf itself must interpret.
"""

import pytest

from termish import execute
from termish.errors import TerminalError
from termish.fs import MemoryFS


@pytest.fixture
def fs():
    return MemoryFS()


class TestConversions:
    def test_string(self, fs):
        assert execute(r"printf '%s' hello", fs) == "hello"

    def test_decimal(self, fs):
        assert execute(r"printf '%d' 42", fs) == "42"

    def test_decimal_i_alias(self, fs):
        assert execute(r"printf '%i' -7", fs) == "-7"

    def test_signed_and_based_numbers(self, fs):
        assert execute(r"printf '%d %d %d' +5 0x1f 010", fs) == "5 31 8"

    def test_char_takes_first_character(self, fs):
        assert execute(r"printf '%c' xyz", fs) == "x"

    def test_percent_literal(self, fs):
        assert execute(r"printf '100%%'", fs) == "100%"

    def test_mixed(self, fs):
        assert execute(r"printf '%s|%d|%c|%%' hi 42 xyz", fs) == "hi|42|x|%"

    def test_no_trailing_newline_is_added(self, fs):
        assert execute("printf x", fs) == "x"


class TestEscapes:
    def test_newline_and_tab(self, fs):
        assert execute(r"printf 'a\tb\n'", fs) == "a\tb\n"

    def test_backslash(self, fs):
        # The tokenizer keeps both characters inside single quotes, so
        # printf receives `\\` and collapses it to one backslash.
        assert execute(r"printf '\\'", fs) == "\\"

    def test_carriage_return_and_friends(self, fs):
        assert execute(r"printf '\r\a\b\f\v'", fs) == "\r\a\b\f\v"

    def test_octal_with_leading_zero(self, fs):
        assert execute(r"printf '\0101'", fs) == "A"

    def test_octal_without_leading_zero(self, fs):
        assert execute(r"printf '\101\102'", fs) == "AB"

    def test_unknown_escape_keeps_backslash(self, fs):
        assert execute(r"printf 'a\qb'", fs) == r"a\qb"

    def test_escapes_are_not_interpreted_in_arguments(self, fs):
        # That would be %b, which is not supported: an argument is data.
        assert execute(r"printf '%s' 'a\nb'", fs) == r"a\nb"

    def test_double_quoted_format(self, fs):
        # Double quotes collapse `\\` but leave `\n` alone, so printf gets
        # the same two-character escape it gets from single quotes.
        assert execute(r'printf "%s\n" hi', fs) == "hi\n"


class TestFormatReuse:
    def test_one_conversion_per_cycle(self, fs):
        assert execute(r"printf '%s\n' a b c", fs) == "a\nb\nc\n"

    def test_two_conversions_per_cycle(self, fs):
        assert execute(r"printf '%s=%s\n' a 1 b 2", fs) == "a=1\nb=2\n"

    def test_three_conversions_per_cycle(self, fs):
        assert execute(r"printf '%s %s %s|' a b c d e f", fs) == "a b c|d e f|"

    def test_partial_last_cycle_fills_with_empty(self, fs):
        assert execute(r"printf '%s %s %s|' a b c d e", fs) == "a b c|d e |"

    def test_format_without_conversions_prints_once(self, fs):
        # bash prints the format once and ignores the arguments, rather
        # than repeating it per argument.
        assert execute(r"printf 'hi\n' a b c", fs) == "hi\n"


class TestMissingArguments:
    def test_string_consumes_empty(self, fs):
        assert execute(r"printf '[%s]'", fs) == "[]"

    def test_decimal_consumes_zero(self, fs):
        assert execute(r"printf '%d\n'", fs) == "0\n"

    def test_char_consumes_empty(self, fs):
        assert execute(r"printf '[%c]'", fs) == "[]"


class TestInvalidNumber:
    def test_substitutes_zero_and_warns(self, fs):
        # bash: the diagnostic goes to stderr, 0 is printed, the rest of
        # the script still runs, and printf's own exit status is 1.
        out = execute(r"printf '%d\n' abc; echo done", fs)
        assert out == "0\nprintf: abc: invalid number\ndone\n"

    def test_exit_status_is_one(self, fs):
        out = execute(r"printf '%d\n' abc; echo exit=$?", fs)
        assert out == "0\nprintf: abc: invalid number\nexit=1\n"

    def test_empty_argument_is_invalid(self, fs):
        out = execute(r"printf '%d' '' || true", fs)
        assert out == "0printf: : invalid number\n"

    @pytest.mark.parametrize("operand", ["08", "09", "-08", "+019"])
    def test_leading_zero_with_a_non_octal_digit_is_invalid(self, fs, operand):
        # bash: a leading zero makes the operand octal, and octal has no
        # 8 or 9, so this is an invalid number rather than decimal eight.
        out = execute(rf"printf '%d\n' {operand}; echo exit=$?", fs)
        assert out == f"0\nprintf: {operand}: invalid number\nexit=1\n"

    def test_leading_zero_octal_still_converts(self, fs):
        assert execute(r"printf '%d %d' 010 -010", fs) == "8 -8"

    def test_failure_is_the_script_outcome(self, fs):
        with pytest.raises(TerminalError) as exc:
            execute(r"printf '%d' abc", fs)
        assert exc.value.exit_code == 1
        assert exc.value.stderr == "printf: abc: invalid number"
        assert exc.value.partial_output == "0"


class TestEndOfOptions:
    def test_double_dash_is_dropped(self, fs):
        # bash: ``--`` ends the options, so the next word is the format.
        assert execute(r"printf -- '%s\n' x", fs) == "x\n"

    def test_double_dash_alone_is_a_usage_error(self, fs):
        with pytest.raises(TerminalError) as exc:
            execute("printf --", fs)
        assert "usage" in exc.value.message

    def test_double_dash_as_a_later_word_is_data(self, fs):
        assert execute(r"printf '%s|' -- x", fs) == "--|x|"


class TestUsageErrors:
    def test_no_format(self, fs):
        with pytest.raises(TerminalError) as exc:
            execute("printf", fs)
        assert "usage" in str(exc.value)
        assert exc.value.exit_code == 1

    @pytest.mark.parametrize(
        "script, named",
        [
            (r"printf '%f\n' 1.5", "%f"),
            (r"printf '%5d\n' 1", "%5d"),
            (r"printf '%-10s\n' a", "%-10s"),
            (r"printf '%b\n' 'a\nb'", "%b"),
            (r"printf '%x\n' 255", "%x"),
            (r"printf 'abc%'", "%"),
        ],
    )
    def test_unsupported_conversion(self, fs, script, named):
        with pytest.raises(TerminalError) as exc:
            execute(script, fs)
        assert str(exc.value).startswith(f"printf: {named}:")
        assert exc.value.exit_code == 1

    def test_unsupported_conversion_stops_the_script(self, fs):
        out = execute(r"printf '%q' x || echo rescued", fs)
        assert out == "printf: %q: unsupported conversion\nrescued\n"


class TestRedirectsAndPipes:
    def test_redirect_writes_exactly_the_bytes(self, fs):
        execute(r"printf 'line\n' > f", fs)
        assert fs.read("f") == b"line\n"

    def test_redirect_without_newline(self, fs):
        execute(r"printf '%s' abc > f", fs)
        assert fs.read("f") == b"abc"

    def test_append_redirect(self, fs):
        execute(r"printf 'a\n' > f", fs)
        execute(r"printf 'b\n' >> f", fs)
        assert fs.read("f") == b"a\nb\n"

    def test_pipe_into_cat(self, fs):
        assert execute("printf x | cat", fs) == "x"

    def test_pipe_into_wc(self, fs):
        assert execute(r"printf 'a\nb\n' | wc -l", fs).strip() == "2"


class TestExpansion:
    def test_variable_expands_before_printf_sees_it(self, fs):
        out = execute(r'printf "%s\n" "$X"', fs, env={"X": "value"})
        assert out == "value\n"

    def test_unset_variable_is_an_empty_argument(self, fs):
        assert execute(r'printf "[%s]" "$NOPE"', fs) == "[]"

    def test_percent_in_an_expanded_argument_is_data(self, fs):
        out = execute(r'printf "%s\n" "$X"', fs, env={"X": "50%"})
        assert out == "50%\n"
