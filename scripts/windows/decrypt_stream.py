#!/usr/bin/env python3
"""Stream-decode one huge SQLCipher v4 db without loading it into memory.

decrypt_all.py reads the whole db (plus its WAL) into RAM before decrypting,
which dies with MemoryError on a 676 MB message_0.db. This variant:

  * walks the WAL frame-by-frame to learn which page numbers were committed
    (without materialising the merged image),
  * then decrypts page by page, pulling each page from the main file or from
    the WAL, and streams the plaintext straight to disk.

Same crypto as decrypt_all.py: PBKDF2-HMAC-SHA512 256000 iterations, AES-256-CBC,
HMAC-SHA512 page auth, reserve 80 bytes, page size 4096.

Usage: python decrypt_stream.py <raw_key_hex> <src.db> <dst.db>
"""
import hashlib
import hmac
import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "common"))
from crypto_backend import aes_cbc_decrypt as aes_cbc_dec

PAGE, RESERVE, SALT_SZ = 4096, 80, 16
KDF_ITER = 256000


def wal_committed_map(wal_path, page_size=PAGE):
    """Return {page_number: (wal_offset, frame_len)} for the last committed txn."""
    if not os.path.exists(wal_path):
        return {}, None
    size = os.path.getsize(wal_path)
    if size < 32:
        return {}, None
    with open(wal_path, "rb") as fh:
        header = fh.read(32)
        magic, version, psize = struct.unpack(">III", header[:12])
        if magic not in (0x377F0682, 0x377F0683) or version != 3007000 or psize != page_size:
            return {}, None
        byteorder = "<" if magic == 0x377F0682 else ">"
        generation = header[16:24]

        def checksum(data, state):
            first, second = state
            for left, right in struct.iter_unpack(byteorder + "II", data):
                first = (first + left + second) & 0xFFFFFFFF
                second = (second + right + first) & 0xFFFFFFFF
            return first, second

        state = checksum(header[:24], (0, 0))
        if state != struct.unpack(">II", header[24:32]):
            return {}, None

        frame_size = page_size + 24
        pending, committed = {}, {}
        committed_size = None
        offset = 32
        while offset + frame_size <= size:
            fh.seek(offset)
            raw = fh.read(frame_size)
            if len(raw) < frame_size:
                break
            hdr, page = raw[:24], raw[24:]
            if hdr[8:16] != generation:
                break
            page_number, commit_size = struct.unpack(">II", hdr[:8])
            if not 1 <= page_number <= 0xFFFFFFFE:
                break
            state = checksum(hdr[:8] + page, state)
            if state != struct.unpack(">II", hdr[16:24]):
                break
            pending[page_number] = (offset + 24, len(page))
            if commit_size:
                committed.update(pending)
                pending.clear()
                committed = {n: v for n, v in committed.items() if n <= commit_size}
                committed_size = commit_size
            offset += frame_size
    return committed, committed_size


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) < 3:
        print("usage: decrypt_stream.py <raw_key_hex> <src.db> <dst.db>")
        return 2
    raw = bytes.fromhex(argv[0].strip())
    src, dst = argv[1], argv[2]

    main_size = os.path.getsize(src)
    if main_size < PAGE:
        print("ERR: source too small")
        return 1

    with open(src, "rb") as fh:
        page1 = fh.read(PAGE)
    salt = page1[:SALT_SZ]
    enc = hashlib.pbkdf2_hmac("sha512", raw, salt, KDF_ITER, 32)
    mac_salt = bytes(v ^ 0x3A for v in salt)
    mac_key = hashlib.pbkdf2_hmac("sha512", enc, mac_salt, 2, 32)

    wal_map, commit_size = wal_committed_map(src + "-wal")
    total_pages = commit_size or (main_size // PAGE)
    print(f"src={src}")
    print(f"main_pages={main_size // PAGE} wal_pages={len(wal_map)} total={total_pages}")

    parent = os.path.dirname(dst)
    if parent:
        os.makedirs(parent, exist_ok=True)

    tmp = dst + ".part"
    with open(src, "rb") as mfh, open(tmp, "wb") as out:
        if commit_size is None:
            out.write(b"SQLite format 3\x00")

        for number in range(1, total_pages + 1):
            loc = wal_map.get(number)
            if loc is not None:
                with open(src + "-wal", "rb") as wfh:
                    wfh.seek(loc[0])
                    page = wfh.read(loc[1])
            else:
                mfh.seek((number - 1) * PAGE)
                page = mfh.read(PAGE)
            if len(page) != PAGE:
                print(f"ERR: short page {number}")
                return 1

            offset = SALT_SZ if number == 1 else 0
            expected = hmac.new(
                mac_key, page[offset:PAGE - 64] + struct.pack("<I", number), hashlib.sha512
            ).digest()
            if not hmac.compare_digest(expected, page[PAGE - 64:PAGE]):
                print(f"ERR: HMAC mismatch on page {number}")
                return 1

            rstart = PAGE - RESERVE
            iv = page[rstart:rstart + 16]
            if number == 1:
                # Page 1 carries the salt in bytes 0..15; the ciphertext starts
                # at SALT_SZ and the plaintext header continues right there.
                plain = aes_cbc_dec(enc, iv, page[SALT_SZ:rstart])
                if not (plain[0] == 0x10 and plain[1] == 0x00 and plain[4] == 0x50
                        and plain[5] == 0x40 and plain[7] == 0x20):
                    print(f"ERR: page 1 header invalid: {plain[:8].hex()}")
                    return 1
                out.write(b"SQLite format 3\x00")
                out.write(plain)
                out.write(page[rstart:])
            else:
                plain = aes_cbc_dec(enc, iv, page[:rstart])
                out.write(plain)
                out.write(page[rstart:])

            if number % 20000 == 0:
                print(f"  ... {number}/{total_pages} pages", flush=True)

    os.replace(tmp, dst)
    print(f"DONE -> {dst} ({os.path.getsize(dst)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
