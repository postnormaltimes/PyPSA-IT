"""List ZIP members from a separately downloaded archive tail.

This is a read-only source-audit helper for large official archives.  It parses
the end-of-central-directory record and central-directory entries without
extracting or modifying the source archive.
"""

from __future__ import annotations

import argparse
import struct
from pathlib import Path


EOCD = b"PK\x05\x06"
CENTRAL = b"PK\x01\x02"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("tail", type=Path)
    parser.add_argument("--archive-size", type=int, required=True)
    parser.add_argument("--match", default="")
    args = parser.parse_args()

    blob = args.tail.read_bytes()
    eocd_pos = blob.rfind(EOCD)
    if eocd_pos < 0:
        raise SystemExit("EOCD signature not present in supplied tail")

    (
        _sig,
        _disk,
        _central_disk,
        _disk_entries,
        total_entries,
        central_size,
        central_offset,
        comment_len,
    ) = struct.unpack_from("<4s4H2LH", blob, eocd_pos)

    tail_start = args.archive_size - len(blob)
    rel = central_offset - tail_start
    if rel < 0 or rel + central_size > len(blob):
        raise SystemExit(
            f"Central directory is outside supplied tail: offset={central_offset}, "
            f"size={central_size}, tail_start={tail_start}"
        )

    print(
        f"entries={total_entries}\tcentral_offset={central_offset}\t"
        f"central_size={central_size}\tcomment_len={comment_len}"
    )
    pos = rel
    matched = 0
    for _ in range(total_entries):
        if blob[pos : pos + 4] != CENTRAL:
            raise SystemExit(f"Invalid central-directory signature at relative byte {pos}")
        fields = struct.unpack_from("<4s6H3L5H2L", blob, pos)
        compressed_size = fields[8]
        uncompressed_size = fields[9]
        name_len, extra_len, member_comment_len = fields[10:13]
        local_offset = fields[-1]
        name_start = pos + 46
        raw_name = blob[name_start : name_start + name_len]
        name = raw_name.decode("utf-8", errors="replace")
        if args.match.lower() in name.lower():
            print(
                f"{name}\tcompressed={compressed_size}\tuncompressed={uncompressed_size}"
                f"\tlocal_offset={local_offset}"
            )
            matched += 1
        pos = name_start + name_len + extra_len + member_comment_len

    print(f"matched={matched}")


if __name__ == "__main__":
    main()
