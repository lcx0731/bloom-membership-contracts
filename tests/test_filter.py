import hashlib
import itertools
from fractions import Fraction

import pytest

from bloom_contracts import BloomFilter, Config, choose_config
from bloom_contracts.audit import deduplicate_filter_only, deduplicate_with_confirmation


def test_bit_layout_matches_independent_set_of_hash_positions():
    config = Config(5, 3, 123)
    keys = [b"", b"a", b"\x00\xff", b"same", b"same"]
    reference = [set() for _ in range(config.slices)]
    bloom = BloomFilter(config)
    for key in keys:
        bloom.add(key)
        for j in range(config.slices):
            frame = config.seed.to_bytes(8, "little") + bytes([j]) + len(key).to_bytes(2, "little") + key
            value = hashlib.blake2b(frame, digest_size=8, person=b"PBloomSlot-v1").digest()
            reference[j].add(int.from_bytes(value, "little") % config.width)
    expected = bytes(sum(1 << b for b in range(8) if 8*byte+b in occupied)
                     for occupied in reference for byte in range(config.width//8))
    assert bloom.to_bytes()[14:-32] == expected
    assert bloom.occupancy() == tuple(map(len, reference))
    assert all(bloom.might_contain(key) for key in keys)


def test_empty_and_duplicate_insertion_contracts():
    bloom = BloomFilter(Config(4, 2))
    assert bloom.occupancy() == (0, 0) and bloom.layout_false_positive() == 0
    assert not bloom.might_contain(b"a")
    bloom.add(b"a")
    once = bloom.to_bytes()
    for _ in range(20): bloom.add(b"a")
    assert once == bloom.to_bytes() and bloom.occupancy() == (1, 1)


def test_union_laws_and_equivalence_to_building_all_keys():
    config = Config(8, 4, 9)
    filters = [BloomFilter(config) for _ in range(3)]
    all_keys = BloomFilter(config)
    for i in range(300):
        key = str(i).encode()
        filters[i%3].add(key)
        all_keys.add(key)
    a, b, c = filters
    a_before = a.to_bytes()
    assert a.union(b).union(c).to_bytes() == all_keys.to_bytes()
    assert a.union(b).to_bytes() == b.union(a).to_bytes()
    assert a.union(a).to_bytes() == a_before
    assert a.union(b.union(c)).to_bytes() == all_keys.to_bytes()
    assert a.to_bytes() == a_before
    assert all(all_keys.might_contain(str(i).encode()) for i in range(300))
    for other in [Config(7, 4, 9), Config(8, 3, 9), Config(8, 4, 10)]:
        with pytest.raises(ValueError): a.union(BloomFilter(other))


def test_small_ideal_hash_probability_by_exact_enumeration():
    width = 16
    probabilities = [Fraction(len({a, b}), width) for a, b in itertools.product(range(width), repeat=2)]
    one_slice = sum(probabilities) / len(probabilities)
    assert one_slice == 1 - Fraction(15, 16)**2
    assert Config(4, 3).expected_false_positive(2) == pytest.approx(float(one_slice**3), abs=1e-16)
    # For a fixed layout, enumerate all query-coordinate tuples instead.
    occupied = [{0, 1, 3}, {2, 5}]
    positive = sum(a in occupied[0] and b in occupied[1]
                   for a, b in itertools.product(range(width), repeat=2))
    assert Fraction(positive, width**2) == Fraction(3*2, width**2)


def test_capacity_choice_is_minimal_within_supported_family():
    config = choose_config(4000, .01)
    assert config.expected_false_positive(4000) <= .01
    for p in range(4, 21):
        for k in range(1, 17):
            candidate = Config(p, k)
            if (candidate.payload_bytes, k, p) < (config.payload_bytes, config.slices, config.power):
                assert candidate.expected_false_positive(4000) > .01
    assert config.expected_false_positive(8000) > .01


def test_exact_confirmation_prevents_false_positive_data_loss():
    events = [str(i//2).encode() for i in range(400)]
    config = Config(4, 2, 7)  # Deliberate saturation makes this a strong control.
    safe, lookups = deduplicate_with_confirmation(events, config)
    unsafe = deduplicate_filter_only(events, config)
    assert safe == list(dict.fromkeys(events))
    assert len(unsafe) < len(safe)
    assert len(unsafe) == len(set(unsafe))
    assert 0 < lookups < len(events)


@pytest.mark.parametrize("args", [(3, 2), (21, 2), (4, 0), (4, 17), (True, 2), (4, 2, -1), (4, 2, 2**64)])
def test_invalid_config(args):
    with pytest.raises(ValueError): Config(*args)


def test_invalid_keys_and_capacity_requests():
    bloom = BloomFilter(Config(4, 2))
    for key in ["text", bytearray(b"x"), 12, b"x"*4097]:
        before = bloom.to_bytes()
        with pytest.raises(ValueError): bloom.add(key)
        with pytest.raises(ValueError): bloom.might_contain(key)
        assert bloom.to_bytes() == before
    for n, target in [(0, .01), (True, .01), (100, 0), (100, float("nan")), (100, True)]:
        with pytest.raises(ValueError): choose_config(n, target)
    with pytest.raises(ValueError, match="cannot be met"):
        choose_config(2000000, 1e-9)
