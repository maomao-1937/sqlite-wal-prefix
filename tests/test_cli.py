"""Test prefix scans against synthetic and real SQLite WAL snapshots."""

from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import struct
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "sqlite_wal_prefix.py"
LIMIT = 16 * 1024 * 1024
INVALID = {"status": "invalid"}
SALT = struct.pack(">II", 0x1312ACF1, 0x86951106)


def checksum(data, order, initial=(0, 0)):
    a, b = initial
    for index in range(0, len(data), 8):
        a = (a + b + int.from_bytes(data[index:index + 4], order)) % (1 << 32)
        b = (b + a + int.from_bytes(data[index + 4:index + 8], order)) % (1 << 32)
    return a, b


def wal_header(order, page_size, *, magic=None, version=3007000):
    if magic is None:
        magic = 0x377F0682 if order == "little" else 0x377F0683
    prefix = struct.pack(">IIII", magic, version, page_size, 42) + SALT
    return prefix + struct.pack(">II", *checksum(prefix, order))


def wal(order="little", page_size=512, pages=((1, 0), (2, 2), (1, 0), (3, 3), (2, 0))):
    header = wal_header(order, page_size)
    rolling = struct.unpack(">II", header[-8:])
    frames = []
    for index, (page, commit) in enumerate(pages, 1):
        page_bytes = (bytes([index, 0x94, 0xDE, 0xF3]) * (page_size // 4))
        fields = struct.pack(">II", page, commit)
        rolling = checksum(fields + page_bytes, order, rolling)
        frames.append(fields + SALT + struct.pack(">II", *rolling) + page_bytes)
    return header + b"".join(frames)


def flip(data, offset):
    return data[:offset] + bytes([data[offset] ^ 1]) + data[offset + 1:]


def report(reason, frames=0, commits=0, last=None):
    return {
        "status": "ok" if reason in {"empty", "complete"} else "rejected_tail",
        "end_reason": reason,
        "valid_frames": frames,
        "commit_markers": commits,
        "last_commit_ordinal": last,
    }


def encode(document):
    return json.dumps(document, separators=(",", ":")).encode("ascii") + b"\n"


class CliTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.snapshot = self.root / "private-capture.wal"

    def run_cli(self, *args, **kwargs):
        return subprocess.run([sys.executable, "-B", str(CLI), *map(str, args)],
                              cwd=self.root, timeout=15, **kwargs)

    def expect(self, code, expected, *args):
        actual = self.run_cli(*(args or (self.snapshot,)), capture_output=True)
        output = encode(expected) if isinstance(expected, dict) else expected
        self.assertEqual((actual.returncode, actual.stdout, actual.stderr), (code, output, b""))

    def probe(self, data, code, expected):
        self.snapshot.write_bytes(data)
        self.expect(code, expected)

    def import_subject(self):
        sys.path.insert(0, str(ROOT))
        self.addCleanup(sys.path.pop, 0)
        import sqlite_wal_prefix as subject
        return subject

    def test_actual_sqlite_snapshot_with_writer_idle_and_sidecars_absent(self):
        database = self.root / "private-database.sqlite"
        with closing(sqlite3.connect(database)) as connection:
            connection.execute("PRAGMA page_size=512")
            self.assertEqual(connection.execute("PRAGMA journal_mode=WAL").fetchone()[0], "wal")
            connection.execute("PRAGMA wal_autocheckpoint=0")
            connection.execute("CREATE TABLE demo (item BLOB)")
            connection.commit()
            connection.execute("INSERT INTO demo VALUES (?)", (b"private-data" * 100,))
            connection.commit()
            self.assertFalse(connection.in_transaction)
            captured = database.with_name(database.name + "-wal").read_bytes()
        for suffix in ("", "-wal", "-shm"):
            database.with_name(database.name + suffix).unlink(missing_ok=True)

        page_size = int.from_bytes(captured[8:12], "big")
        frame_size = 24 + page_size
        frame_count, remainder = divmod(len(captured) - 32, frame_size)
        self.assertEqual(remainder, 0)
        commit_ordinals = []
        for ordinal in range(1, frame_count + 1):
            offset = 32 + (ordinal - 1) * frame_size + 4
            if captured[offset:offset + 4] != b"\0\0\0\0":
                commit_ordinals.append(ordinal)
        self.assertEqual(len(commit_ordinals), 2)
        self.assertEqual(commit_ordinals[-1], frame_count)
        self.probe(captured, 0, report("complete", frame_count, 2, frame_count))
        self.assertEqual(self.snapshot.read_bytes(), captured)
        last_frame = 32 + (frame_count - 1) * frame_size
        self.probe(flip(captured, last_frame + 24), 1,
                   report("checksum_mismatch", frame_count - 1, 1, commit_ordinals[0]))

    def test_both_checksum_orders_and_valid_frames_after_last_commit(self):
        for order, page_size in (("little", 512), ("big", 65536)):
            with self.subTest(order=order, page_size=page_size):
                data = wal(order, page_size)
                self.probe(data, 0, report("complete", 5, 2, 4))

    def test_complete_header_only_and_empty_are_distinct(self):
        self.probe(b"", 0, report("empty"))
        self.probe(wal_header("big", 1024), 0, report("complete"))
        self.probe(wal(pages=((1, 0), (2, 0))), 0, report("complete", 2))

    def test_malformed_header_has_no_prefix_report(self):
        good = wal_header("little", 512)
        invalid = [good[:count] for count in (1, 4, 16, 31)]
        invalid.extend(wal_header("little", size) for size in (0, 1, 256, 513, 65535, 131072))
        invalid.extend((wal_header("little", 512, magic=0x377F0681),
                        wal_header("little", 512, version=3007001),
                        wal_header("little", 512, magic=0x377F0683), flip(good, 24), flip(good, 15)))
        for data in invalid:
            with self.subTest(data=data.hex()):
                self.probe(data, 2, INVALID)

    def test_partial_tail_at_different_frame_offsets(self):
        data = wal()
        start = 32 + 2 * (24 + 512)
        for length in (1, 8, 23, 24, 25, 535):
            self.probe(data[:start + length], 1, report("partial_frame", 2, 1, 2))

    def test_stop_at_first_bad_frame_and_do_not_count_rejected_commits(self):
        for order in ("little", "big"):
            data = wal(order)
            start = 32 + 2 * (24 + 512)
            page_zero = wal(order, pages=((1, 0), (2, 2), (0, 7), (3, 3)))
            for changed, reason in ((page_zero, "page_zero"), (flip(data, start + 8), "salt_mismatch"),
                                    (flip(data, start + 12), "salt_mismatch"),
                                    (flip(data, start + 4), "checksum_mismatch"),
                                    (flip(data, start + 23), "checksum_mismatch"),
                                    (flip(data, start + 24 + 511), "checksum_mismatch")):
                with self.subTest(order=order, reason=reason):
                    self.probe(changed, 1, report(reason, 2, 1, 2))

    def test_rolling_checksum_rejects_independently_valid_frame(self):
        data = wal("big", pages=((1, 1),))
        another = wal("big", pages=((2, 2),))
        self.probe(data + another[32:], 1, report("checksum_mismatch", 1, 1, 1))

    def test_inclusive_capture_size_cap_and_real_read_limit(self):
        exact = wal_header("little", 512) + b"\0" * (LIMIT - 32)
        self.probe(exact, 1, report("salt_mismatch"))
        self.probe(exact + b"\0", 2, INVALID)
        subject = self.import_subject()
        metadata = SimpleNamespace(st_size=32, st_mode=self.snapshot.stat().st_mode)
        with patch.object(subject.os, "stat", return_value=metadata), \
             patch.object(subject.os, "fstat", return_value=metadata), \
             patch.object(subject, "emit", return_value=2) as emit:
            self.assertEqual(subject.main([str(self.snapshot)]), 2)
            self.assertEqual(emit.call_args.args, (encode(INVALID), 2))

    def test_only_the_capture_is_accessed(self):
        subject = self.import_subject()
        self.snapshot.write_bytes(wal("big"))
        raw = self.snapshot.read_bytes()
        regular_stat, regular_open = os.stat, os.open

        def one_stat(path):
            self.assertEqual(path, str(self.snapshot))
            return regular_stat(path)

        def one_open(path, flags):
            self.assertEqual(path, str(self.snapshot))
            return regular_open(path, flags)

        with patch.object(subject.os, "stat", side_effect=one_stat) as stat_call, \
             patch.object(subject.os, "open", side_effect=one_open) as open_call, \
             patch.object(subject, "emit", return_value=0) as emit:
            self.assertEqual(subject.main([str(self.snapshot)]), 0)
            self.assertEqual(emit.call_args.args, (encode(report("complete", 5, 2, 4)), 0))
        self.assertEqual((stat_call.call_count, open_call.call_count), (1, 1))
        self.assertEqual(self.snapshot.read_bytes(), raw)

    def test_unsafe_paths_fail_before_stat_and_open(self):
        subject = self.import_subject()
        unsafe = ("file:///private/wal", "https://private-host/wal", r"\/host/share", r"/\host\share",
                  r"\??\capture", r"\Device\capture", r"\Global??\capture", r"\dir\..\DeViCe\capture",
                  "/dir/../DosDevices/wal", r"\dir/../SystemRoot\wal", "/./GlobalRoot/wal",
                  "C:relative.wal", "C:/sample:stream", "x/AUX.wal", "x/LPT³.wal", "x/CON .wal",
                  "x. /wal", "x/.. /wal", "x/capture.wal/", "x\0wal", "-")
        for spelling in unsafe:
            with patch.object(subject.os, "stat", side_effect=AssertionError("unsafe stat")), \
                 patch.object(subject.os, "open", side_effect=AssertionError("unsafe open")), \
                 patch.object(subject, "emit", return_value=2) as emit:
                self.assertEqual(subject.main([spelling]), 2)
                self.assertEqual(emit.call_args.args, (encode(INVALID), 2))

    def test_replacement_descriptor_rejected_and_closed_before_read(self):
        subject = self.import_subject()
        self.snapshot.write_bytes(wal())
        real_open = os.open
        descriptors = []

        def substitute(path, flags):
            descriptors.append(real_open(os.devnull, flags))
            return descriptors[0]

        with patch.object(subject.os, "open", side_effect=substitute), \
             patch.object(subject.os, "fdopen", side_effect=AssertionError("read an unsafe descriptor")), \
             patch.object(subject, "emit", return_value=2) as emit:
            self.assertEqual(subject.main([str(self.snapshot)]), 2)
            self.assertEqual(emit.call_args.args, (encode(INVALID), 2))
        self.assertEqual(len(descriptors), 1)
        with self.assertRaises(OSError):
            os.fstat(descriptors[0])

    def test_usage_and_nonregular_file(self):
        self.expect(2, INVALID)
        self.expect(2, INVALID, self.root)
        self.expect(2, INVALID, self.snapshot, self.snapshot)
        process = self.run_cli(capture_output=True)
        self.assertEqual((process.returncode, process.stdout, process.stderr), (2, encode(INVALID), b""))
        if hasattr(os, "mkfifo"):
            os.mkfifo(self.snapshot)
            self.expect(2, INVALID)
        self.expect(0, b"Usage: python sqlite_wal_prefix.py SNAPSHOT\n", "--help")

    def test_closed_stdout_stays_quiet(self):
        self.snapshot.write_bytes(wal())
        for args in ((self.snapshot,), (), ("--help",), ("-h",)):
            reader, writer = os.pipe()
            os.close(reader)
            with os.fdopen(writer, "wb") as pipe:
                actual = self.run_cli(*args, stdout=pipe, stderr=subprocess.PIPE)
            self.assertEqual((actual.returncode, actual.stderr), (2, b""))


if __name__ == "__main__":
    unittest.main()
