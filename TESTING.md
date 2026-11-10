# Testing

From the repository root, run `python -m unittest discover -s tests -v` and `python ci_checks.py`. Both commands use the Python standard library. The workflow runs them on Ubuntu and Windows.

The integration test generates a two-transaction WAL with Python's `sqlite3`, copies it while the connection is idle, then scans the copy with the original database and sidecars absent. It checks the final commit ordinal and the prefix left after changing a page byte in the final frame.

Synthetic cases cover both checksum word orders, both page size limits, the file cap, empty and header-only snapshots, malformed headers, partial frames, zero page numbers, both salts, and rolling checksum failure. I/O checks cover descriptor validation, input access, unsafe spellings, and quiet output failures. The example checker verifies exact stdout, empty stderr, and all documented exits using the four bundled fixtures.
