"""Locate the valid frame prefix in a saved SQLite WAL snapshot."""

import json
import os
import posixpath
import re
import stat
import struct
import sys


LIMIT = 16 * 1024 * 1024
HEADER_SIZE = 32
FRAME_HEADER_SIZE = 24
FORMAT = 3007000
WORD_PAIRS = {0x377F0682: struct.Struct("<II"), 0x377F0683: struct.Struct(">II")}
HEADER = struct.Struct(">8I")
FRAME_HEADER = struct.Struct(">6I")
DEVICE = re.compile(r"CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|(?:COM|LPT)[0-9¹²³]")
NATIVE_ROOTS = {"??", "device", "dosdevices", "global??", "systemroot", "globalroot"}


def require_local(path):
    spelling = path.replace("\\", "/")
    if not spelling or spelling == "-" or spelling.startswith("//") or spelling.endswith("/"):
        raise ValueError
    if any(ord(char) < 32 for char in path):
        raise ValueError
    if spelling.startswith("/"):
        for form in (spelling, posixpath.normpath(spelling)):
            root = form[1:].partition("/")[0].rstrip(" .").lower()
            if root in NATIVE_ROOTS:
                raise ValueError
    tail = spelling[2:] if re.match(r"^[A-Za-z]:/", spelling) else spelling
    if ":" in tail:
        raise ValueError
    for part in tail.split("/"):
        if part in {"", ".", ".."}:
            continue
        stem = part.partition(".")[0].rstrip(" ").upper()
        if part.endswith((" ", ".")) or DEVICE.fullmatch(stem):
            raise ValueError


def require_regular(info):
    if not stat.S_ISREG(info.st_mode) or not 0 <= info.st_size <= LIMIT:
        raise ValueError


def read_snapshot(path):
    require_local(path)
    require_regular(os.stat(path))
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0)
    descriptor = os.open(path, flags)
    try:
        require_regular(os.fstat(descriptor))
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            data = stream.read(LIMIT + 1)
    finally:
        os.close(descriptor)
    if len(data) > LIMIT:
        raise ValueError
    return data


def checksum(data, word_pairs, seed=(0, 0)):
    first, second = seed
    for left, right in word_pairs.iter_unpack(data):
        first = (first + left + second) & 0xffffffff
        second = (second + right + first) & 0xffffffff
    return first, second


def report(end_reason, valid_frames=0, commit_markers=0, last_commit=None):
    return {
        "status": "ok" if end_reason in {"complete", "empty"} else "rejected_tail",
        "end_reason": end_reason,
        "valid_frames": valid_frames,
        "commit_markers": commit_markers,
        "last_commit_ordinal": last_commit,
    }


def inspect(data):
    if not data:
        return report("empty")
    if len(data) < HEADER_SIZE:
        raise ValueError

    view = memoryview(data)
    magic, version, page_size, _, salt1, salt2, check1, check2 = HEADER.unpack_from(view)
    word_pairs = WORD_PAIRS.get(magic)
    if word_pairs is None or version != FORMAT:
        raise ValueError
    if not 512 <= page_size <= 65536 or page_size & (page_size - 1):
        raise ValueError
    rolling = checksum(view[:24], word_pairs)
    if rolling != (check1, check2):
        raise ValueError

    frame_size = FRAME_HEADER_SIZE + page_size
    frames, commits, last_commit = 0, 0, None
    reason = "complete"
    for offset in range(HEADER_SIZE, len(view), frame_size):
        end = offset + frame_size
        if end > len(view):
            reason = "partial_frame"
            break
        page, db_size, frame_salt1, frame_salt2, check1, check2 = FRAME_HEADER.unpack_from(view, offset)
        if (frame_salt1, frame_salt2) != (salt1, salt2):
            reason = "salt_mismatch"
            break
        if page == 0:
            reason = "page_zero"
            break
        # The rolling chain includes page number, commit size and page bytes.
        rolling = checksum(view[offset:offset + 8], word_pairs, rolling)
        rolling = checksum(view[offset + FRAME_HEADER_SIZE:end], word_pairs, rolling)
        if rolling != (check1, check2):
            reason = "checksum_mismatch"
            break
        frames += 1
        if db_size:
            commits += 1
            last_commit = frames
    return report(reason, frames, commits, last_commit)


def emit(data, code):
    remaining = memoryview(data)
    try:
        while remaining:
            written = os.write(1, remaining)
            if written <= 0:
                return 2
            remaining = remaining[written:]
    except OSError:
        return 2
    return code


def main(args):
    if args in (["--help"], ["-h"]):
        return emit(b"Usage: python sqlite_wal_prefix.py SNAPSHOT\n", 0)
    try:
        path, = args
        summary = inspect(read_snapshot(path))
        code = int(summary["status"] == "rejected_tail")
    except (OSError, ValueError):
        summary, code = {"status": "invalid"}, 2
    data = json.dumps(summary, separators=(",", ":")).encode("ascii") + b"\n"
    return emit(data, code)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
