"""Reproduce capacity, union, persistence, and duplicate-handling experiments."""

import argparse
from dataclasses import asdict
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from bloom_contracts import BloomFilter, choose_config
from bloom_contracts.audit import deduplicate_filter_only, deduplicate_with_confirmation


QUERY_COUNT = 40000


def absent_rate(bloom):
    count = sum(bloom.might_contain(f"absent:{i}".encode("ascii")) for i in range(QUERY_COUNT))
    return {"false_positives": count, "queries": QUERY_COUNT, "observed_fpr": count/QUERY_COUNT,
            "layout_fpr_ideal_hashes": bloom.layout_false_positive()}


def figure(metrics, output):
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10,
                         "axes.spines.right": False, "axes.spines.top": False,
                         "axes.titleweight": "bold", "axes.titlesize": 12,
                         "figure.facecolor": "#fafbf9", "axes.facecolor": "#fafbf9"})
    fig, axes = plt.subplots(2, 2, figsize=(12.8, 9.1))
    fig.subplots_adjust(left=.09, right=.96, bottom=.13, top=.85, hspace=.55, wspace=.31)
    fig.suptitle("A possible match still needs confirmation", x=.07, y=.97, ha="left", fontsize=23, weight="bold")
    fig.text(.07, .915, "Partitioned Bloom filter  |  5,120 payload bytes  |  Designed for 4,000 distinct keys at a 1% target",
             fontsize=11, color="#53616b")
    rows = metrics["capacity"]
    ax = axes[0, 0]
    x = [r["inserted_distinct_keys"] for r in rows]
    ax.plot(x, [r["expected_fpr_ideal_hashes"] for r in rows], color="#257f81", lw=2, label="Ideal insertion-average model")
    ax.plot(x, [r["layout_fpr_ideal_hashes"] for r in rows], "--", color="#6b7285", lw=1.5, label="Ideal query model for saved bits")
    ax.scatter(x, [r["observed_fpr"] for r in rows], color="#bf7838", s=35, zorder=3, label="Observed absent-query fraction")
    ax.axhline(.01, color="#a34d60", ls=":", label="Design target = 1%")
    ax.set(title="A  Fixed memory, increasing occupancy", xlabel="Distinct inserted keys", ylabel="False-positive fraction")
    ax.legend(frameon=False, fontsize=8.3, loc="upper left")

    ax = axes[0, 1]
    for index, count in enumerate([4000, 12000]):
        row = next(r for r in rows if r["inserted_distinct_keys"] == count)
        ax.bar(np.arange(5)+(index-.5)*.34, np.array(row["occupancy"])/8192,
               width=.34, color=["#257f81", "#bf7838"][index], label=f"{count:,} keys")
    ax.set_xticks(range(5), [f"Slice {j}" for j in range(5)])
    ax.set(title="B  Each slice fills independently in the model", ylabel="Fraction of bits set", ylim=(0, 1))
    ax.legend(frameon=False, fontsize=9)

    ax = axes[1, 0]
    union = metrics["union"]
    ax.bar([0, 1, 2], [union[key]["observed_fpr"] for key in ["left", "right", "merged"]],
           color=["#257f81", "#6d829c", "#bf7838"])
    ax.set_xticks([0, 1, 2], ["Left: 4,000", "Right: 4,000", "Union: 8,000"])
    ax.set(title="C  Valid union preserves keys, not the target", ylabel="Observed false-positive fraction")
    ax.axhline(.01, color="#a34d60", ls=":")
    ax.text(.03, .96, "Identical configuration and seed\nUnion bits equal a direct build on all 8,000 keys",
            transform=ax.transAxes, va="top", fontsize=9, color="#53616b")
    ax.set_ylim(0, max(union["merged"]["observed_fpr"]*1.4, .12))

    ax = axes[1, 1]
    dedup = metrics["deduplication"]
    counts = [dedup["exact_unique_count"], dedup["confirmed_output_count"], dedup["filter_only_output_count"]]
    bars = ax.bar([0, 1, 2], counts, color=["#6d829c", "#257f81", "#bf7838"])
    ax.set_xticks([0, 1, 2], ["Exact reference", "Confirm positives", "Filter alone"])
    ax.set(title="D  Filter-only deduplication loses new records", ylabel="Unique keys retained", ylim=(0, 5000))
    for bar, count in zip(bars, counts):
        ax.text(bar.get_x()+bar.get_width()/2, count+80, f"{count:,}", ha="center", fontsize=10)
    ax.text(.02, .97, f"Control intentionally exceeds its 1,000-key design\nFilter alone lost {dedup['lost_unique_keys']:,} distinct records",
            transform=ax.transAxes, va="top", fontsize=9, color="#53616b")
    for ax in axes.flat:
        ax.grid(axis="y", alpha=.13)
        ax.set_axisbelow(True)
    fig.text(.07, .05, "All keys are deterministic synthetic byte strings. Absent-query prefix is disjoint from insertion prefixes; 40,000 queries per filter.\n"
             "Hash-based probability curves are models, not adversarial guarantees. Payload size excludes Python object and exact-store memory.",
             fontsize=9, color="#53616b", linespacing=1.6)
    fig.savefig(output / "membership_audit.png", dpi=170)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("results"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    config = choose_config(4000, .01, seed=1729)
    metrics = {"data": "Deterministic synthetic byte keys; no external or personal data.",
               "config": asdict(config), "payload_bytes": config.payload_bytes,
               "probability_scope": "Expected and layout FPR assume ideal independent uniform hash coordinates. Observed fractions describe this fixed workload; no confidence guarantee is asserted.",
               "capacity": []}
    bloom, snapshots, count = BloomFilter(config), {}, 0
    for target in [0, 1000, 2000, 4000, 6000, 8000, 12000]:
        while count < target:
            bloom.add(f"insert:{count}".encode("ascii"))
            count += 1
        snapshots[target] = bloom.to_bytes()
        row = {"inserted_distinct_keys": target, "occupancy": list(bloom.occupancy()),
               "expected_fpr_ideal_hashes": config.expected_false_positive(target), **absent_rate(bloom)}
        row["false_negatives"] = sum(not bloom.might_contain(f"insert:{i}".encode("ascii")) for i in range(target))
        assert row["false_negatives"] == 0
        metrics["capacity"].append(row)
        print(f"keys={target:5} observed FPR={row['observed_fpr']:.6f} ideal={row['expected_fpr_ideal_hashes']:.6f}", flush=True)
    left = BloomFilter.from_bytes(snapshots[4000])
    right = BloomFilter(config)
    for i in range(4000, 8000): right.add(f"insert:{i}".encode("ascii"))
    merged = left.union(right)
    equivalent = merged.to_bytes() == snapshots[8000]
    assert equivalent
    path = args.output / "union_filter.pbf"
    path.write_bytes(merged.to_bytes())
    restored = BloomFilter.from_bytes(path.read_bytes())
    assert restored.to_bytes() == merged.to_bytes()
    assert all(restored.might_contain(f"insert:{i}".encode("ascii")) for i in range(8000))
    metrics["union"] = {"left": absent_rate(left), "right": absent_rate(right), "merged": absent_rate(restored),
                        "equals_direct_build": equivalent, "persisted_bytes": path.stat().st_size,
                        "retained_inserted_keys": 8000}
    events = []
    for i in range(4000):
        events.append(f"event:{i}".encode("ascii"))
        if i % 3 == 0: events.append(f"event:{i//2}".encode("ascii"))
    overloaded = choose_config(1000, .01, seed=1729)
    confirmed, lookups = deduplicate_with_confirmation(events, overloaded)
    unsafe = deduplicate_filter_only(events, overloaded)
    exact = list(dict.fromkeys(events))
    assert confirmed == exact
    metrics["deduplication"] = {"config": asdict(overloaded), "design_distinct_keys": 1000,
                                "input_events": len(events), "exact_unique_count": len(exact),
                                "confirmed_output_count": len(confirmed), "filter_only_output_count": len(unsafe),
                                "lost_unique_keys": len(exact)-len(unsafe), "exact_membership_lookups": lookups,
                                "membership_lookups_avoided": len(events)-lookups,
                                "exact_store_writes": len(confirmed),
                                "scope": "Sequential in-memory exact store retained; lookup counts are not latency or total-memory measurements."}
    (args.output / "metrics.json").write_text(json.dumps(metrics, indent=2, allow_nan=False)+"\n")
    figure(metrics, args.output)
    print(json.dumps(metrics["deduplication"], indent=2))
    print(f"Wrote metrics, figure, and synthetic union filter to {args.output}")


if __name__ == "__main__":
    main()
