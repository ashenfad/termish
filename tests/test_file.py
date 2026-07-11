"""Tests for the ``file`` builtin."""

import pytest

from termish.errors import TerminalError
from termish.fs import MemoryFS
from termish.interpreter import execute_script
from termish.parser import to_script


@pytest.fixture
def fs():
    return MemoryFS()


class TestFile:
    def test_detects_gzip(self, fs):
        fs.write("/data.csv.gz", b"\x1f\x8b\x08\x00\x00\x00\x00\x00")
        out = execute_script(to_script("file /data.csv.gz"), fs)
        assert out == "/data.csv.gz: gzip compressed data\n"

    def test_detects_zip(self, fs):
        fs.write("/a.zip", b"PK\x03\x04\x00\x00")
        assert (
            execute_script(to_script("file /a.zip"), fs) == "/a.zip: Zip archive data\n"
        )

    def test_detects_pdf(self, fs):
        fs.write("/doc.pdf", b"%PDF-1.7\n...")
        assert (
            execute_script(to_script("file /doc.pdf"), fs) == "/doc.pdf: PDF document\n"
        )

    def test_detects_png(self, fs):
        fs.write("/img.png", b"\x89PNG\r\n\x1a\n\x00\x00")
        assert (
            execute_script(to_script("file /img.png"), fs)
            == "/img.png: PNG image data\n"
        )

    def test_detects_jpeg(self, fs):
        fs.write("/photo.jpg", b"\xff\xd8\xff\xe0\x00\x10")
        assert (
            execute_script(to_script("file /photo.jpg"), fs)
            == "/photo.jpg: JPEG image data\n"
        )

    def test_detects_elf(self, fs):
        fs.write("/bin", b"\x7fELF\x02\x01")
        assert execute_script(to_script("file /bin"), fs) == "/bin: ELF binary\n"

    def test_detects_posix_tar(self, fs):
        # `ustar` magic at offset 257 inside a 512-byte header block.
        buf = bytearray(512)
        buf[0:11] = b"archive.txt"
        buf[257:262] = b"ustar"
        fs.write("/a.tar", bytes(buf))
        assert (
            execute_script(to_script("file /a.tar"), fs)
            == "/a.tar: POSIX tar archive\n"
        )

    def test_detects_html_doctype(self, fs):
        fs.write("/page.html", b"<!DOCTYPE html><html><body>hi</body></html>")
        assert (
            execute_script(to_script("file /page.html"), fs)
            == "/page.html: HTML document\n"
        )

    def test_detects_html_case_insensitive_with_whitespace(self, fs):
        fs.write("/page.html", b"\n  <HTML>\n<body>x</body>\n</HTML>\n")
        assert (
            execute_script(to_script("file /page.html"), fs)
            == "/page.html: HTML document\n"
        )

    def test_empty_file(self, fs):
        fs.write("/zero", b"")
        assert execute_script(to_script("file /zero"), fs) == "/zero: empty\n"

    def test_ascii_text(self, fs):
        fs.write("/hello.txt", b"hello\nworld\n")
        assert (
            execute_script(to_script("file /hello.txt"), fs)
            == "/hello.txt: ASCII text\n"
        )

    def test_utf8_text(self, fs):
        fs.write("/greet.txt", "héllo\n".encode("utf-8"))
        assert (
            execute_script(to_script("file /greet.txt"), fs)
            == "/greet.txt: UTF-8 Unicode text\n"
        )

    def test_utf8_multibyte_crossing_1024_boundary(self, fs):
        # 1023 ASCII bytes followed by 'é' (b"\xc3\xa9") — the multi-byte
        # char straddles the 1024-byte mark. A 1024-byte sniff window
        # would truncate mid-char and raise UnicodeDecodeError; scanning
        # the full buffer correctly classifies as UTF-8.
        fs.write("/boundary.txt", b"a" * 1023 + b"\xc3\xa9")
        assert (
            execute_script(to_script("file /boundary.txt"), fs)
            == "/boundary.txt: UTF-8 Unicode text\n"
        )

    def test_long_file_with_trailing_non_ascii_is_not_ascii(self, fs):
        # Clean ASCII for the first 1024 bytes but a high byte at the
        # tail — must not be classified as "ASCII text".
        fs.write("/mostly_ascii.txt", b"a" * 2000 + "é".encode("utf-8"))
        out = execute_script(to_script("file /mostly_ascii.txt"), fs)
        assert out == "/mostly_ascii.txt: UTF-8 Unicode text\n"

    def test_binary_fallback_data(self, fs):
        # Five printable chars with a NUL — no magic match, binary fallback.
        fs.write("/blob", b"ab\x00cd")
        assert execute_script(to_script("file /blob"), fs) == "/blob: data\n"

    def test_directory(self, fs):
        fs.mkdir("/d")
        assert execute_script(to_script("file /d"), fs) == "/d: directory\n"

    def test_missing_path_errors(self, fs):
        with pytest.raises(TerminalError):
            execute_script(to_script("file /missing"), fs)

    def test_no_operands_errors(self, fs):
        with pytest.raises(TerminalError):
            execute_script(to_script("file"), fs)

    def test_multiple_paths_in_order(self, fs):
        fs.write("/a.gz", b"\x1f\x8b\x00")
        fs.write("/b.txt", b"hello")
        assert (
            execute_script(to_script("file /a.gz /b.txt"), fs)
            == "/a.gz: gzip compressed data\n/b.txt: ASCII text\n"
        )

    def test_agent_post_download_pattern(self, fs):
        # The exact transcript that prompted this work — make sure
        # `file FOO || true` survives even when FOO is missing (the
        # diagnostic still shows, as on a real terminal).
        out = execute_script(to_script("file /Electric_Vehicle.csv.gz || true"), fs)
        assert out.startswith("file: /Electric_Vehicle.csv.gz: ")
