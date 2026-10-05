"""Extract one ZIP member from a byte-range segment starting at its local header."""

from __future__ import annotations

import argparse
import struct
import zlib
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("segment", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--compressed-size", type=int)
    args = parser.parse_args()

    blob = args.segment.read_bytes()
    if blob[:4] != b"PK\x03\x04":
        raise SystemExit("Segment does not start with a ZIP local-file header")
    (
        _sig,
        _version,
        flags,
        method,
        _mtime,
        _mdate,
        _crc,
        local_compressed_size,
        _uncompressed_size,
        name_len,
        extra_len,
    ) = struct.unpack_from("<4s5H3L2H", blob, 0)
    name = blob[30 : 30 + name_len].decode("utf-8", errors="replace")
    payload_start = 30 + name_len + extra_len
    compressed_size = args.compressed_size or local_compressed_size
    if not compressed_size:
        raise SystemExit("Compressed size absent from local header; pass --compressed-size")
    payload = blob[payload_start : payload_start + compressed_size]
    if len(payload) != compressed_size:
        raise SystemExit(
            f"Segment is incomplete: expected {compressed_size} compressed bytes, got {len(payload)}"
        )
    if method == 0:
        output = payload
    elif method == 8:
        output = zlib.decompress(payload, -15)
    else:
        raise SystemExit(f"Unsupported ZIP compression method {method}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(output)
    print(
        f"member={name}\tflags={flags}\tmethod={method}\t"
        f"compressed={compressed_size}\tuncompressed={len(output)}\toutput={args.output}"
    )


if __name__ == "__main__":
    main()
