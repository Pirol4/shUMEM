#!/usr/bin/env python3
"""Mean DRAM bandwidth of a pcm_memory.sh CSV between two instants.

pcm_summary.sh averages the samples above a write threshold, which fails for
a system that writes less than the threshold under load (dyn-128 writes
~10-20 MB/s, CLAUDE.md 19.10). This one averages by time instead: the
samples whose timestamp lies in [start, end], given as Unix epoch seconds,
e.g. the traffic window of one rate as harness/sweep.sh records it.

Usage (on dut, where the CSV and its local-time timestamps were written):
  harness/pcm_window.py /mydata/exp/pcm-mem_dyn-128.csv 1760900000 1760900060
Prints: n=<samples> read=<MB/s> write=<MB/s> (write min=… max=…)
"""
import csv
import sys
import time

# pcm-memory -nc -csv: two header rows, then Date, Time, ..., and the System
# Read and Write totals in columns 12 and 13 (CLAUDE.md 17.1).
DATE_COL, TIME_COL, READ_COL, WRITE_COL = 0, 1, 11, 12
HEADER_ROWS = 2


def sample_epoch(date, clock):
    """PCM stamps samples in local time, to the millisecond."""
    seconds, _, millis = clock.partition(".")
    stamp = time.mktime(time.strptime(date + " " + seconds, "%Y-%m-%d %H:%M:%S"))
    return stamp + (int(millis) / 1000.0 if millis else 0.0)


def window_samples(path, start, end):
    with open(path, newline="") as f:
        rows = list(csv.reader(f))[HEADER_ROWS:]
    for row in rows:
        try:
            t = sample_epoch(row[DATE_COL].strip(), row[TIME_COL].strip())
            read, write = float(row[READ_COL]), float(row[WRITE_COL])
        except (IndexError, ValueError):
            continue    # a partial last line when PCM is stopped mid-write
        if start <= t <= end:
            yield read, write


def main():
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    path, start, end = sys.argv[1], float(sys.argv[2]), float(sys.argv[3])
    samples = list(window_samples(path, start, end))
    if not samples:
        print("n=0 no samples in window")
        return
    reads = [r for r, _ in samples]
    writes = [w for _, w in samples]
    print("n=%d read=%.1f write=%.1f (write min=%.1f max=%.1f) MB/s"
          % (len(samples), sum(reads) / len(reads), sum(writes) / len(writes),
             min(writes), max(writes)))


if __name__ == "__main__":
    main()
