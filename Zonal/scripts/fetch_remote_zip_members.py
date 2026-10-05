"""Fetch selected members from a large remote ZIP using HTTP range requests.

This utility exists so official source files can be archived without downloading
multi-gigabyte bundles. It reads the ZIP central directory, retrieves only the
requested compressed members, verifies CRC-32, and refuses to overwrite a
different local file.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import struct
import urllib.request
import zlib
from pathlib import Path


EOCD_SIGNATURE = b"PK\x05\x06"
CENTRAL_SIGNATURE = b"PK\x01\x02"
LOCAL_SIGNATURE = b"PK\x03\x04"


def _request(url: str, start: int | None = None, end: int | None = None) -> bytes:
    headers = {"User-Agent": "MEM-ETX7B3-source-adapter/1.0"}
    if start is not None:
        headers["Range"] = f"bytes={start}-{'' if end is None else end}"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read()


def _content_length(url: str) -> int:
    request = urllib.request.Request(
        url,
        method="HEAD",
        headers={"User-Agent": "MEM-ETX7B3-source-adapter/1.0"},
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return int(response.headers["Content-Length"])


def _central_directory(url: str) -> dict[str, dict[str, int]]:
    total_bytes = _content_length(url)
    tail_bytes = min(total_bytes, 4 * 1024 * 1024)
    tail_start = total_bytes - tail_bytes
    tail = _request(url, tail_start, total_bytes - 1)
    eocd_offset = tail.rfind(EOCD_SIGNATURE)
    if eocd_offset < 0:
        raise ValueError("ZIP end-of-central-directory record was not found")
    eocd = struct.unpack_from("<4s4H2LH", tail, eocd_offset)
    entries = int(eocd[4])
    directory_size = int(eocd[5])
    directory_offset = int(eocd[6])
    if directory_offset < tail_start:
        directory = _request(
            url,
            directory_offset,
            directory_offset + directory_size - 1,
        )
        cursor = 0
    else:
        directory = tail
        cursor = directory_offset - tail_start

    output: dict[str, dict[str, int]] = {}
    for _ in range(entries):
        if directory[cursor : cursor + 4] != CENTRAL_SIGNATURE:
            raise ValueError(f"Invalid central-directory record at byte {cursor}")
        values = struct.unpack_from("<4s6H3L5H2L", directory, cursor)
        filename_length = int(values[10])
        extra_length = int(values[11])
        comment_length = int(values[12])
        filename = directory[
            cursor + 46 : cursor + 46 + filename_length
        ].decode("utf-8")
        output[filename] = {
            "compression": int(values[4]),
            "crc32": int(values[7]),
            "compressed_bytes": int(values[8]),
            "uncompressed_bytes": int(values[9]),
            "local_header_offset": int(values[16]),
        }
        cursor += 46 + filename_length + extra_length + comment_length
    return output


def fetch_member(
    url: str,
    member: str,
    output: Path,
    directory: dict[str, dict[str, int]] | None = None,
) -> dict[str, object]:
    directory = _central_directory(url) if directory is None else directory
    if member not in directory:
        raise KeyError(f"Remote ZIP member is absent: {member}")
    record = directory[member]
    local_offset = int(record["local_header_offset"])
    header = _request(url, local_offset, local_offset + 29)
    if header[:4] != LOCAL_SIGNATURE:
        raise ValueError(f"Invalid local header for {member}")
    values = struct.unpack("<4s5H3L2H", header)
    filename_length = int(values[9])
    extra_length = int(values[10])
    data_offset = local_offset + 30 + filename_length + extra_length
    compressed_bytes = int(record["compressed_bytes"])
    payload = _request(url, data_offset, data_offset + compressed_bytes - 1)
    if len(payload) != compressed_bytes:
        raise ValueError(f"Incomplete range response for {member}")
    compression = int(record["compression"])
    if compression == 0:
        content = payload
    elif compression == 8:
        content = zlib.decompress(payload, -zlib.MAX_WBITS)
    else:
        raise ValueError(f"Unsupported ZIP compression method {compression}")
    if len(content) != int(record["uncompressed_bytes"]):
        raise ValueError(f"Uncompressed byte count failed for {member}")
    if zlib.crc32(content) & 0xFFFFFFFF != int(record["crc32"]):
        raise ValueError(f"CRC-32 verification failed for {member}")

    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() and output.read_bytes() != content:
        raise FileExistsError(f"Refusing to overwrite a different file: {output}")
    output.write_bytes(content)
    return {
        "url": url,
        "member": member,
        "output": str(output),
        "bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest().upper(),
        "crc32": f"{zlib.crc32(content) & 0xFFFFFFFF:08X}",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--member")
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--spec-csv",
        type=Path,
        help="CSV with member and output columns; the remote directory is read once.",
    )
    args = parser.parse_args()
    if args.spec_csv:
        if args.member or args.output:
            parser.error("--spec-csv cannot be combined with --member/--output")
        with args.spec_csv.open("r", encoding="utf-8-sig", newline="") as handle:
            specs = list(csv.DictReader(handle))
        directory = _central_directory(args.url)
        receipts = [
            fetch_member(
                args.url,
                record["member"],
                Path(record["output"]).resolve(),
                directory,
            )
            for record in specs
        ]
        print(json.dumps(receipts, indent=2))
    else:
        if not args.member or not args.output:
            parser.error("--member and --output are required without --spec-csv")
        receipt = fetch_member(args.url, args.member, args.output.resolve())
        print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    main()
