#!/usr/bin/env python3
"""Dump exports of every DLL in the WeChat install and look for crypto/SQLCipher
symbols we could hook directly (PBKDF2, HMAC, sqlite3_key, sqlcipher_*).

Hooking PKCS5_PBKDF2_HMAC is the jackpot: its first argument is the raw
passphrase, i.e. the device-wide raw key we are after -- no guessing about
which SHA-512 implementation SQLCipher uses.
"""
import os
import struct
import sys


def parse_exports(path):
    try:
        data = open(path, "rb").read()
    except OSError:
        return []
    if data[:2] != b"MZ":
        return []
    pe_off = struct.unpack_from("<I", data, 0x3C)[0]
    if data[pe_off:pe_off + 4] != b"PE\0\0":
        return []
    coff = pe_off + 4
    machine, nsec, _ts, _sym, _nsym, _opt_size, _chars = struct.unpack_from("<HHIIIHH", data, coff)
    opt = coff + 20
    magic = struct.unpack_from("<H", data, opt)[0]
    if magic == 0x10B:
        dd = opt + 96
    elif magic == 0x20B:
        dd = opt + 112
    else:
        return []
    exp_rva, exp_size = struct.unpack_from("<II", data, dd)
    if not exp_rva:
        return []

    sections = []
    sec_off = opt + _opt_size
    for i in range(nsec):
        base = sec_off + i * 40
        name = data[base:base + 8].rstrip(b"\0").decode("latin1", "replace")
        vsize, vaddr, rsize, raddr = struct.unpack_from("<IIII", data, base + 8)
        sections.append((vaddr, vsize, raddr, rsize, name))

    def rva2off(rva):
        for vaddr, vsize, raddr, rsize, _ in sections:
            if vaddr <= rva < vaddr + max(vsize, rsize):
                return raddr + (rva - vaddr)
        return None

    exp_off = rva2off(exp_rva)
    if exp_off is None:
        return []
    (_flags, _ts, _maj, _min, name_rva, _ordbase, naddr, nnames,
     addr_rva, names_rva, ords_rva) = struct.unpack_from("<IIHHIIIIIII", data, exp_off)
    name_off = rva2off(name_rva)
    dll_name = ""
    if name_off:
        end = data.index(b"\0", name_off)
        dll_name = data[name_off:end].decode("latin1", "replace")
    addr_off = rva2off(addr_rva)
    names_off = rva2off(names_rva)
    ords_off = rva2off(ords_rva)
    if addr_off is None or names_off is None or ords_off is None:
        return []
    out = []
    for i in range(nnames):
        nrva = struct.unpack_from("<I", data, names_off + i * 4)[0]
        noff = rva2off(nrva)
        if noff is None:
            continue
        end = data.index(b"\0", noff)
        sym = data[noff:end].decode("latin1", "replace")
        ordv = struct.unpack_from("<H", data, ords_off + i * 2)[0]
        func_rva = struct.unpack_from("<I", data, addr_off + ordv * 4)[0]
        out.append((sym, func_rva))
    return dll_name, out


def main():
    roots = sys.argv[1:] or [r"<微信安装目录>\4.1.15.56"]
    files = []
    for root in roots:
        for dp, _dn, fn in os.walk(root):
            for f in fn:
                if f.lower().endswith((".dll", ".exe")):
                    files.append(os.path.join(dp, f))
    interesting = ("pbkdf2", "hmac", "sqlite3_key", "sqlcipher", "sqlite3_rekey",
                   "evp_", "pkcs5", "sha512", "sha256", "wcdb", "dbkey")
    hits = []
    total_syms = 0
    for path in files:
        res = parse_exports(path)
        if not res:
            continue
        dll_name, syms = res
        total_syms += len(syms)
        for sym, rva in syms:
            low = sym.lower()
            if any(k in low for k in interesting):
                hits.append((os.path.basename(path), sym, rva))
    print(f"scanned {len(files)} binaries, {total_syms} exported symbols")
    print(f"interesting exports: {len(hits)}\n")
    by_file = {}
    for f, s, r in hits:
        by_file.setdefault(f, []).append((s, r))
    for f, items in sorted(by_file.items()):
        print(f"--- {f} ({len(items)}) ---")
        for s, r in items[:60]:
            print(f"    {s}  rva=0x{r:x}")
        if len(items) > 60:
            print(f"    ... +{len(items) - 60} more")


if __name__ == "__main__":
    main()
