# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeCAD contributors.
"""Track L2 harness: sweep a corpus of real DWGs through the LibreDWG bridge.

For every .dwg: convert with dwg2dxf (patched vendor build), then load the
result with ezdxf recover and sanity-check the modelspace. Each failure is
classified and its signature recorded, so a thousand-file corpus reduces to
a short list of unique LibreDWG bugs to hunt.

Usage:
    python tools/dwg_bench.py <corpus_dir> [--out report.csv]
                              [--copy-fails <dir>] [--workers N]

Categories:
    OK             converted and loaded, modelspace has entities
    SEGFAULT       dwg2dxf died on a signal (rc < 0)
    TIMEOUT        dwg2dxf exceeded the per-file timeout
    NO_OUTPUT      dwg2dxf exited without producing a DXF
    LOAD_FAIL      ezdxf recover could not read the produced DXF
    EMPTY_SALVAGE  DXF loads but modelspace empty with a big entitydb
                   (structurally broken emission, BASE COTAHUASI class)
    EMPTY          modelspace genuinely empty (template files etc.)
"""
from __future__ import annotations

import argparse
import csv
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

TIMEOUT = 180  # seconds per conversion


def check_one(dwg_path_str: str, dwg2dxf: str) -> dict:
    dwg_path = Path(dwg_path_str)
    row = {
        "file": dwg_path.name,
        "size_mb": round(dwg_path.stat().st_size / 1e6, 1),
        "category": "?",
        "signature": "",
        "entities": 0,
        "seconds": 0.0,
    }
    t0 = time.perf_counter()
    tmp = Path(tempfile.mkdtemp(prefix="dwgbench-"))
    out_dxf = tmp / "out.dxf"
    try:
        try:
            proc = subprocess.run(
                [dwg2dxf, "-y", "-o", str(out_dxf), str(dwg_path)],
                capture_output=True, timeout=TIMEOUT,
                encoding="utf-8", errors="replace",
            )
        except subprocess.TimeoutExpired:
            row["category"] = "TIMEOUT"
            return row

        stderr_tail = ""
        for line in reversed((proc.stderr or "").strip().splitlines()):
            if "ERROR" in line or "Segmentation" in line:
                stderr_tail = line.strip()[:160]
                break

        if proc.returncode < 0:
            row["category"] = "SEGFAULT"
            row["signature"] = f"signal {-proc.returncode}: {stderr_tail}"
            return row
        if not out_dxf.is_file() or out_dxf.stat().st_size == 0:
            row["category"] = "NO_OUTPUT"
            row["signature"] = stderr_tail or f"rc={proc.returncode}"
            return row

        from ezdxf import recover

        try:
            doc, _aud = recover.readfile(out_dxf)
        except Exception as exc:
            row["category"] = "LOAD_FAIL"
            row["signature"] = f"{type(exc).__name__}: {str(exc)[:140]}"
            return row

        n_msp = len(doc.modelspace())
        row["entities"] = n_msp
        if n_msp == 0 and len(doc.entitydb) > 100:
            row["category"] = "EMPTY_SALVAGE"
            row["signature"] = stderr_tail
        elif n_msp == 0:
            row["category"] = "EMPTY"
        else:
            row["category"] = "OK"
        return row
    finally:
        row["seconds"] = round(time.perf_counter() - t0, 1)
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("corpus", type=Path)
    ap.add_argument("--out", type=Path, default=Path("dwg_bench_report.csv"))
    ap.add_argument("--copy-fails", type=Path, default=None)
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()

    from formats.dwg_bridge import find_dwg2dxf

    dwg2dxf = find_dwg2dxf()
    if dwg2dxf is None:
        print("dwg2dxf not found", file=sys.stderr)
        return 1
    print(f"converter: {dwg2dxf}", flush=True)

    files = sorted(args.corpus.rglob("*.dwg")) + sorted(args.corpus.rglob("*.DWG"))
    files = sorted(set(files))
    print(f"corpus: {len(files)} files", flush=True)

    rows: list[dict] = []
    counts: dict[str, int] = {}
    t0 = time.perf_counter()
    with ProcessPoolExecutor(max_workers=args.workers) as pool, \
         open(args.out, "w", newline="") as fh:
        writer = csv.DictWriter(
            fh, ["file", "size_mb", "category", "signature", "entities", "seconds"])
        writer.writeheader()
        futures = {pool.submit(check_one, str(f), str(dwg2dxf)): f for f in files}
        for n, fut in enumerate(as_completed(futures), 1):
            src = futures[fut]
            try:
                row = fut.result()
            except Exception as exc:  # worker itself died
                row = {"file": src.name, "size_mb": 0, "category": "HARNESS_ERROR",
                       "signature": str(exc)[:140], "entities": 0, "seconds": 0}
            rows.append(row)
            counts[row["category"]] = counts.get(row["category"], 0) + 1
            writer.writerow(row)
            fh.flush()
            if row["category"] not in ("OK", "EMPTY"):
                print(f"[{row['category']}] {row['file']} — {row['signature']}",
                      flush=True)
                if args.copy_fails:
                    dest = args.copy_fails / row["category"]
                    dest.mkdir(parents=True, exist_ok=True)
                    try:
                        shutil.copy2(src, dest / src.name)
                    except OSError:
                        pass
            if n % 100 == 0:
                print(f"--- {n}/{len(files)} ({time.perf_counter()-t0:.0f}s) "
                      f"{counts}", flush=True)

    print(f"\nDONE in {time.perf_counter()-t0:.0f}s: {counts}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
