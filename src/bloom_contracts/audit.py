"""Deterministic synthetic workload and safe/unsafe duplicate-handling examples."""

from dataclasses import asdict

from .core import BloomFilter, _integer, choose_config


def audit(items=4000, queries=20000, target=.01, seed=1729):
    _integer(items, 1, 50000, "items")
    _integer(queries, 1, 200000, "queries")
    config = choose_config(items, target, seed)
    bloom = BloomFilter(config)
    for i in range(items):
        bloom.add(f"insert:{i}".encode("ascii"))
    false_negatives = sum(not bloom.might_contain(f"insert:{i}".encode("ascii")) for i in range(items))
    # Prefixes certify that all queried keys are absent without consulting a set.
    false_positives = sum(bloom.might_contain(f"absent:{i}".encode("ascii")) for i in range(queries))
    return {"data": "Deterministic distinct synthetic ASCII keys; inserted and queried prefixes are disjoint.",
            "config": asdict(config), "items": items, "absent_queries": queries,
            "payload_bytes": config.payload_bytes, "serialized_bytes": len(bloom.to_bytes()),
            "occupancy": list(bloom.occupancy()), "expected_fpr_ideal_hashes": config.expected_false_positive(items),
            "layout_fpr_ideal_hashes": bloom.layout_false_positive(),
            "observed_false_positives": false_positives, "observed_fpr": false_positives/queries,
            "false_negatives": false_negatives}


def deduplicate_with_confirmation(events, config):
    """Toy exact store kept in memory; all updates occur in one sequential pass.

    Bloom negatives avoid a membership lookup, but every accepted key is still
    written to the exact store. This is not a total-memory reduction claim.
    """
    bloom, exact, output = BloomFilter(config), set(), []
    lookups = 0
    for key in events:
        if bloom.might_contain(key):
            lookups += 1
            if key in exact:
                continue
        exact.add(key)
        bloom.add(key)
        output.append(key)
    return output, lookups


def deduplicate_filter_only(events, config):
    """Intentionally unsafe control: a possible match discards a new record."""
    bloom, output = BloomFilter(config), []
    for key in events:
        if not bloom.might_contain(key):
            output.append(key)
            bloom.add(key)
    return output
