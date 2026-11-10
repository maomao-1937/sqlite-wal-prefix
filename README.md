# sqlite-wal-prefix

Find how far a saved SQLite write-ahead log (WAL) can be read as a valid frame sequence. See where scanning stops and which valid frame carries the last commit marker. The shareable JSON report contains counts, an ordinal and fixed reason labels.

## Quickstart

Use Python 3.11 or later. From the repository root, run the bundled binary examples.

```sh
python sqlite_wal_prefix.py examples/complete.wal
python sqlite_wal_prefix.py examples/empty.wal
python sqlite_wal_prefix.py examples/checksum-tail.wal
python sqlite_wal_prefix.py examples/invalid-header.wal
```

The examples exit `0`, `0`, `1`, and `2`, respectively. In the checksum-tail example the first two frames validate, and the second frame is a commit marker.

```json
{"status":"rejected_tail","end_reason":"checksum_mismatch","valid_frames":2,"commit_markers":1,"last_commit_ordinal":2}
```

For your own data, run `python sqlite_wal_prefix.py SNAPSHOT` on a saved, stable copy of the WAL. If taking a copy from an open SQLite connection, keep writers and checkpoint activity idle during the copy. The snapshot can contain database page data. Share the report and keep the raw WAL private.

| Exit | Standard output |
| --- | --- |
| `0` | `ok` for a valid header and frame sequence or a zero-byte capture. `--help` prints usage. |
| `1` | `rejected_tail` for a valid header followed by an incomplete or invalid frame. |
| `2` | `{"status":"invalid"}` for a malformed or unsupported header, invalid arguments, or a read failure. Unavailable stdout also returns `2` quietly. |

## Report fields

- `valid_frames` counts the sequential valid frames before the stopping point. `commit_markers` counts the valid frames with a nonzero database-size field. `last_commit_ordinal` is the one-based position of the last such frame or `null`. Valid frames can follow the last commit marker.
- `end_reason` is `empty` for zero bytes or `complete` at a frame boundary, including a valid header with zero frames. Otherwise the check stops at the first `partial_frame`, `salt_mismatch`, `page_zero`, or `checksum_mismatch`. A full frame is checked for salts, page number, then rolling checksum.
- The input is one local regular file of at most 16 MiB. The header uses format `3007000`, either supported magic value, and a power-of-two page size from 512 to 65,536 bytes. The magic selects the checksum input order. Stored fields and checksum words are big-endian. [SQLite WAL file format](https://www.sqlite.org/fileformat2.html#walformat)
- The parser reads the captured WAL alone and treats page bytes as checksum input. Its scope is the captured prefix and its commit markers. A WAL reset can leave stale physical frames with mismatched salts. [SQLite WAL reset](https://www.sqlite.org/fileformat2.html#walreset)
- Path checks apply to the supplied spelling. The filesystem resolves links and mounts.

## 中文说明

这个工具读取保存的 SQLite WAL 快照，找出从文件开头起连续通过校验的帧。报告给出有效帧数、提交标记数、最后一个提交帧的序号和停止原因。序号从 1 开始。有效帧也可能出现在最后一个提交标记之后。

使用 Python 3.11 或更新版本。运行 `python sqlite_wal_prefix.py SNAPSHOT`，输入为不超过 16 MiB 的本地常规文件。对在线数据库取样时，复制期间需要让写入和检查点操作保持空闲。报告省略路径、页数据、盐值和校验和。WAL 重置后可能留下旧帧，遇到盐值不匹配时只报告扫描停止的位置和原因。

退出码 `0` 表示扫描完整、文件为空或帮助显示成功，`1` 表示头部有效但遇到无效或不完整的帧，`2` 表示参数、头部、读取或输出失败。工具只读取指定的快照文件，页字节仅用于校验和计算。

[测试](TESTING.md) · [调研](RESEARCH.md) · [MIT 许可证](LICENSE)
