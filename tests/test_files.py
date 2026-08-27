import base64
import hashlib
import zlib

import pytest

from leftbrain.files import files

SHA_ABC = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("LEFTBRAIN_FILE_ROOTS", str(tmp_path))
    (tmp_path / "abc.txt").write_bytes(b"abc")
    (tmp_path / "empty.bin").write_bytes(b"")
    big = bytes(range(256)) * 9000  # 2.3 MB: crosses the 1 MiB chunk boundary more than once
    (tmp_path / "big.bin").write_bytes(big)
    return tmp_path


def test_file_hash_known_digests(root):
    r = files("file_hash", path="abc.txt")
    assert r["ok"] and r["result"]["algo"] == "sha256" and r["result"]["hex"] == SHA_ABC and r["result"]["bytes"] == 3
    assert r["result"]["base64"] == base64.b64encode(bytes.fromhex(SHA_ABC)).decode()
    assert "matches" not in r["result"] and r["result"]["path"].endswith("abc.txt")
    assert files("file_hash", path="empty.bin")["result"]["hex"] == hashlib.sha256(b"").hexdigest()
    assert files("file_hash", path="abc.txt", algo="md5")["result"]["hex"] == "900150983cd24fb0d6963f7d28e17f72"
    assert files("file_hash", path="abc.txt", algo="sha1")["result"]["hex"] == "a9993e364706816aba3e25717850c26c9cd0d89d"
    assert files("file_hash", path="abc.txt", algo="blake2b")["result"]["hex"] == hashlib.blake2b(b"abc").hexdigest()
    crc = files("file_hash", path="abc.txt", algo="crc32")["result"]
    assert crc["hex"] == "352441c2" and crc["value"] == zlib.crc32(b"abc") and "base64" not in crc


def test_file_hash_streams_large_files(root):
    data = (root / "big.bin").read_bytes()
    r = files("file_hash", path="big.bin")["result"]
    assert r["hex"] == hashlib.sha256(data).hexdigest() and r["bytes"] == len(data)
    assert files("file_hash", path="big.bin", algo="crc32")["result"]["value"] == zlib.crc32(data)


def test_file_hash_verifies_expected(root):
    assert files("file_hash", path="abc.txt", expected=SHA_ABC)["result"]["matches"] is True
    assert files("file_hash", path="abc.txt", expected=SHA_ABC.upper())["result"]["matches"] is True
    # a line straight out of `sha256sum` / a CHECKSUMS file: the filename is stripped
    assert files("file_hash", path="abc.txt", expected=f"{SHA_ABC}  abc.txt")["result"]["matches"] is True
    assert files("file_hash", path="abc.txt", expected=f"{SHA_ABC} *abc.txt")["result"]["matches"] is True
    bad = files("file_hash", path="abc.txt", expected="0" * 64)
    assert bad["ok"] and bad["result"]["matches"] is False


def test_file_hash_refusals(root):
    assert files("file_hash", path="abc.txt", algo="sha999")["error"] == "invalid_input"
    assert files("file_hash", path="missing.txt")["error"] == "invalid_input"
    assert files("file_hash", path="/etc/hosts")["error"] == "forbidden"
    assert files("file_hash", path=".")["error"] == "invalid_input"  # a directory has no digest
