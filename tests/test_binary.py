"""Binary payloads survive pipes and redirects byte for byte.

Text decoding happens at each command's own boundary and once more for
the transcript; nothing that merely moves content decodes it.
"""

import gzip as gzip_module

import pytest

from termish import CommandContext, CommandResult, MemoryFS, execute

ALL_BYTES = bytes(range(256))


@pytest.fixture
def fs():
    fs = MemoryFS()
    fs.write("/bin.dat", ALL_BYTES)
    return fs


class TestRedirectsCarryBytes:
    def test_cat_to_file_is_byte_identical(self, fs):
        execute("cat bin.dat > copy.dat", fs)
        assert fs.read("/copy.dat") == ALL_BYTES

    def test_cat_through_a_pipe_is_byte_identical(self, fs):
        execute("cat bin.dat | cat > c2.dat", fs)
        assert fs.read("/c2.dat") == ALL_BYTES

    def test_tee_writes_bytes(self, fs):
        execute("cat bin.dat | tee copy.dat > /dev/null", fs)
        assert fs.read("/copy.dat") == ALL_BYTES

    def test_tee_append_writes_bytes(self, fs):
        execute("cat bin.dat | tee -a copy.dat > /dev/null", fs)
        execute("cat bin.dat | tee -a copy.dat > /dev/null", fs)
        assert fs.read("/copy.dat") == ALL_BYTES * 2

    def test_head_bytes(self, fs):
        execute("head -c 100 bin.dat > h.dat", fs)
        assert fs.read("/h.dat") == ALL_BYTES[:100]

    def test_tail_bytes(self, fs):
        execute("tail -c 100 bin.dat > t.dat", fs)
        assert fs.read("/t.dat") == ALL_BYTES[-100:]

    def test_head_bytes_from_stdin(self, fs):
        execute("cat bin.dat | head -c 10 > h.dat", fs)
        assert fs.read("/h.dat") == ALL_BYTES[:10]

    def test_tail_bytes_from_stdin(self, fs):
        execute("cat bin.dat | tail -c 10 > t.dat", fs)
        assert fs.read("/t.dat") == ALL_BYTES[-10:]

    def test_append_redirect_carries_bytes(self, fs):
        execute("cat bin.dat > copy.dat; cat bin.dat >> copy.dat", fs)
        assert fs.read("/copy.dat") == ALL_BYTES * 2

    def test_input_redirect_carries_bytes(self, fs):
        execute("cat < bin.dat > copy.dat", fs)
        assert fs.read("/copy.dat") == ALL_BYTES

    def test_crlf_survives_a_pipeline(self, fs):
        fs.write("/crlf.txt", b"a\r\nb\r\n")
        execute("cat crlf.txt | cat > copy.txt", fs)
        assert fs.read("/copy.txt") == b"a\r\nb\r\n"

    def test_heredoc_body_is_utf8(self, fs):
        execute("cat <<EOF > out.txt\ncafé\nEOF", fs)
        assert fs.read("/out.txt") == "café\n".encode()


class TestGzipComposes:
    def test_gzip_c_round_trips_through_a_redirect(self, fs):
        execute("gzip -c bin.dat > bin.gz", fs)
        assert gzip_module.decompress(fs.read("/bin.gz")) == ALL_BYTES

    def test_cat_piped_into_zcat_round_trips(self, fs):
        fs.write("/bin.gz", gzip_module.compress(ALL_BYTES))
        execute("cat bin.gz | zcat > out.dat", fs)
        assert fs.read("/out.dat") == ALL_BYTES

    def test_gzip_pipeline_round_trips(self, fs):
        execute("cat bin.dat | gzip -c | zcat > out.dat", fs)
        assert fs.read("/out.dat") == ALL_BYTES

    def test_gzip_d_reads_stdin(self, fs):
        fs.write("/bin.gz", gzip_module.compress(ALL_BYTES))
        execute("cat bin.gz | gzip -d > out.dat", fs)
        assert fs.read("/out.dat") == ALL_BYTES

    def test_gunzip_c_reads_stdin(self, fs):
        fs.write("/bin.gz", gzip_module.compress(b"hello\n"))
        assert execute("cat bin.gz | gunzip -c", fs) == "hello\n"

    def test_zcat_piped_into_wc_counts_decompressed_bytes(self, fs):
        fs.write("/bin.gz", gzip_module.compress(ALL_BYTES))
        assert execute("zcat bin.gz | wc -c", fs) == "256\n"


class TestWcCounts:
    def test_wc_c_counts_bytes_of_an_octal_escape(self, fs):
        assert execute(r"printf '\0101\n' | wc -c", fs) == "2\n"

    def test_wc_c_counts_bytes_not_characters(self, fs):
        assert execute("printf 'é\\n' | wc -c", fs) == "3\n"

    def test_wc_m_counts_characters(self, fs):
        assert execute("printf 'é\\n' | wc -m", fs) == "2\n"

    def test_wc_c_counts_every_byte_of_a_binary_file(self, fs):
        assert execute("wc -c bin.dat", fs) == "256 bin.dat\n"

    def test_wc_c_counts_binary_stdin(self, fs):
        assert execute("cat bin.dat | wc -c", fs) == "256\n"


class TestTranscriptIsDecodedOnce:
    def test_undecodable_bytes_become_replacement_chars(self, fs):
        out = execute("cat bin.dat", fs)
        assert isinstance(out, str)
        assert "�" in out

    def test_text_transcript_is_unchanged(self, fs):
        assert execute("echo café", fs) == "café\n"


class TestInjectedHandlers:
    def test_handler_writes_bytes_to_stdout_buffer(self, fs):
        def emit(ctx: CommandContext) -> CommandResult | None:
            ctx.stdout.buffer.write(ALL_BYTES)
            return None

        assert execute("emit | wc -c", fs, commands={"emit": emit}) == "256\n"

    def test_handler_reads_bytes_from_stdin_buffer(self, fs):
        def count(ctx: CommandContext) -> CommandResult | None:
            ctx.stdout.write(f"{len(ctx.stdin.buffer.read())}\n")
            return None

        assert execute("cat bin.dat | count", fs, commands={"count": count}) == "256\n"

    def test_handler_bytes_reach_a_redirect_intact(self, fs):
        def emit(ctx: CommandContext) -> CommandResult | None:
            ctx.stdout.buffer.write(ALL_BYTES)
            return None

        execute("emit > out.dat", fs, commands={"emit": emit})
        assert fs.read("/out.dat") == ALL_BYTES

    def test_text_and_bytes_interleave_in_write_order(self, fs):
        def mixed(ctx: CommandContext) -> CommandResult | None:
            ctx.stdout.write("a")
            ctx.stdout.flush()
            ctx.stdout.buffer.write(b"b")
            ctx.stdout.write("c")
            return None

        execute("mixed > out.txt", fs, commands={"mixed": mixed})
        assert fs.read("/out.txt") == b"abc"
