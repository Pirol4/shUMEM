"""Latency of TRex's latency streams, per pg_id and merged, from one window.

TRex reports each latency stream (one pg_id) with a coarse histogram of one
significant digit (10 us buckets below 100 us, 100 us buckets up to 1 ms...)
and, when the server runs with --hdrh, an HDR histogram at 1 us resolution
(3 significant digits; base64 in json["hdrh"], TRex src/time_histogram.cpp).
Percentiles come from the HDR histogram when there is one, else from the
coarse one as the bucket's lower bound ("within one bucket"). The encoded HDR
histogram is kept, so runs can be merged later and percentiles recomputed
over all of them (analysis/eval_report.py).
"""

PERCENTILES = (50.0, 90.0, 99.0, 99.9, 99.99)
# TRex's settings for its HDR histograms (time_histogram.cpp).
HDRH_RANGE = (1, 10 * 1000 * 1000, 3)

try:
    from hdrh.histogram import HdrHistogram
except ImportError:          # checked by the caller when HDR data appears
    HdrHistogram = None


def percentile_key(p):
    """50.0 -> 'p50', 99.9 -> 'p999', 99.99 -> 'p9999'."""
    return "p" + ("%g" % p).replace(".", "")


def decode_hdrh(encoded):
    if HdrHistogram is None:
        raise RuntimeError("TRex sends HDR histograms but the hdrh module is missing: "
                           "sudo pip3 install hdrhistogram (setup/trex-setup.sh does it)")
    return HdrHistogram.decode(encoded)


def empty_hdrh():
    return HdrHistogram(*HDRH_RANGE)


def coarse_total(histogram):
    return sum(histogram.values())


def coarse_percentile(histogram, fraction):
    """Lower bound of the bucket holding the `fraction` quantile, or None."""
    total = coarse_total(histogram)
    if not total:
        return None
    seen = 0
    for bucket in sorted(histogram, key=float):
        seen += histogram[bucket]
        if seen >= fraction * total:
            return float(bucket)
    return None


def merge_snapshots(snapshots):
    """One latency entry from per-segment snapshots of the same pg_id.

    Whether TRex keeps a latency stream's counters when the stream is removed
    and added again is not documented; if every snapshot holds at least as
    many samples as the one before, they are cumulative and the last is the
    window; otherwise each segment started from zero and they are summed.
    """
    snapshots = [s for s in snapshots if s]
    if not snapshots:
        return {}
    totals = [coarse_total(s.get("latency", {}).get("histogram", {})) for s in snapshots]
    if all(b >= a for a, b in zip(totals, totals[1:])):
        return snapshots[-1]
    histogram, worst, weighted, count, hdrh = {}, None, 0.0, 0, None
    for snap in snapshots[:-1]:     # the last one repeats the final segment
        lat = snap.get("latency", {})
        for bucket, n in lat.get("histogram", {}).items():
            histogram[bucket] = histogram.get(bucket, 0) + n
        n = coarse_total(lat.get("histogram", {}))
        if lat.get("average") is not None:
            weighted += lat["average"] * n
            count += n
        if lat.get("total_max") is not None:
            worst = max(worst or 0, lat["total_max"])
        if lat.get("hdrh"):
            hdrh = hdrh or empty_hdrh()
            hdrh.add(decode_hdrh(lat["hdrh"]))
    merged = {"histogram": histogram, "total_max": worst,
              "average": weighted / count if count else None}
    if hdrh is not None:
        merged["hdrh"] = hdrh.encode().decode()
    return {"latency": merged, "err_cntrs": snapshots[-1].get("err_cntrs", {})}


def summarize(entry, tx=None, rx=None):
    """Mean, min, max and percentiles of one latency entry, plus its histogram."""
    lat = entry.get("latency", {})
    coarse = lat.get("histogram", {})
    record = {"avg_us": lat.get("average"), "min_us": lat.get("total_min"),
              "max_us": lat.get("total_max"), "jitter_us": lat.get("jitter"),
              "dropped": entry.get("err_cntrs", {}).get("dropped"),
              "tx": tx, "rx": rx, "hdrh": lat.get("hdrh")}
    if lat.get("hdrh"):
        hist = decode_hdrh(lat["hdrh"])
        record.update(source="hdrh", samples=hist.get_total_count())
        for p in PERCENTILES:
            record[percentile_key(p)] = hist.get_value_at_percentile(p)
    else:
        record.update(source="coarse", samples=coarse_total(coarse))
        for p in PERCENTILES:
            record[percentile_key(p)] = coarse_percentile(coarse, p / 100.0)
    return record


def aggregate(records):
    """All latency streams of a window as one: merged histogram, weighted mean."""
    records = [r for r in records if r.get("samples")]
    if not records:
        return {}
    total = sum(r["samples"] for r in records)
    merged = {"samples": total,
              "avg_us": sum((r["avg_us"] or 0) * r["samples"] for r in records) / total,
              "min_us": min((r["min_us"] for r in records if r["min_us"] is not None),
                            default=None),
              "max_us": max((r["max_us"] for r in records if r["max_us"] is not None),
                            default=None),
              "jitter_us": None,
              "dropped": sum(r["dropped"] or 0 for r in records)}
    if all(r["source"] == "hdrh" for r in records):
        hist = empty_hdrh()
        for r in records:
            hist.add(decode_hdrh(r["hdrh"]))
        merged.update(source="hdrh", hdrh=hist.encode().decode())
        for p in PERCENTILES:
            merged[percentile_key(p)] = hist.get_value_at_percentile(p)
    else:
        # Without HDR the only aggregate percentile that holds is the worst
        # stream's; label it as such rather than invent a merged one.
        merged.update(source="coarse-max", hdrh=None)
        for p in PERCENTILES:
            key = percentile_key(p)
            merged[key] = max((r[key] for r in records if r[key] is not None), default=None)
    return merged
