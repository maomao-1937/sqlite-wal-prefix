# Research

SQLite documents the WAL header, frame layout, cumulative checksum, and salt changes after a WAL reset. Its frame decoder also rejects page number zero and checks frame salts before the page number. The tool reports the stopping point of this offline scan. [SQLite WAL format and checksums](https://www.sqlite.org/fileformat2.html#walformat), [SQLite frame decoder](https://github.com/sqlite/sqlite/blob/master/src/wal.c)

One Litestream report describes database writes reusing WAL space with the same byte size. Another describes snapshot compaction reading through a commit beyond an advertised WAL boundary. These reports show why a size check alone cannot identify the current valid frame sequence, and why the frame-prefix end and last commit marker are separate quantities. [Litestream issue 1037](https://github.com/benbjohnson/litestream/issues/1037), [Litestream issue 1490](https://github.com/benbjohnson/litestream/issues/1490)
