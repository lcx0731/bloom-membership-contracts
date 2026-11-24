"""Partitioned byte-key Bloom filter, using only the Python standard library."""

from dataclasses import dataclass
import hashlib
import hmac
import math
import struct


HEADER = struct.Struct("<4sBBQ")
MAGIC = b"PBF1"
CHECKSUM_BYTES = 32
MAX_PAYLOAD = 16 * (2**20 // 8)


def _integer(value, low, high, name):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{name} must be an integer in {low}..{high}")


@dataclass(frozen=True)
class Config:
    power: int
    slices: int
    seed: int = 1729

    def __post_init__(self):
        _integer(self.power, 4, 20, "power")
        _integer(self.slices, 1, 16, "slices")
        _integer(self.seed, 0, 2**64-1, "seed")

    @property
    def width(self):
        return 1 << self.power

    @property
    def payload_bytes(self):
        return self.slices * self.width // 8

    def expected_false_positive(self, distinct_items):
        """Ideal independent uniform hashes, averaged over insertion layouts."""
        _integer(distinct_items, 0, 2_000_000, "distinct_items")
        occupied_probability = -math.expm1(distinct_items * math.log1p(-1/self.width))
        return occupied_probability**self.slices


def choose_config(distinct_items, target=.01, seed=1729):
    """Smallest payload among the supported power-of-two slice designs.

    Ties prefer fewer hashes. The target is a model prediction, not a bound on
    an arbitrary workload or a claim of optimality over all Bloom variants.
    """
    _integer(distinct_items, 1, 2_000_000, "distinct_items")
    if type(target) not in (int, float) or not 1e-9 <= target <= .5:
        raise ValueError("target must be finite and in [1e-9,0.5]")
    candidates = [Config(p, k, seed) for p in range(4, 21) for k in range(1, 17)]
    feasible = [c for c in candidates if c.expected_false_positive(distinct_items) <= target]
    if not feasible:
        raise ValueError("target cannot be met within supported payload and slice limits")
    return min(feasible, key=lambda c: (c.payload_bytes, c.slices, c.power))


class BloomFilter:
    def __init__(self, config):
        if not isinstance(config, Config):
            raise ValueError("config must be a Config")
        self._config = config
        self._bits = bytearray(config.payload_bytes)

    @property
    def config(self):
        return self._config

    def _slots(self, key):
        if type(key) is not bytes or len(key) > 4096:
            raise ValueError("key must be bytes of length 0..4096")
        for part in range(self.config.slices):
            framed = struct.pack("<QBH", self.config.seed, part, len(key)) + key
            digest = hashlib.blake2b(framed, digest_size=8, person=b"PBloomSlot-v1").digest()
            yield int.from_bytes(digest, "little") & (self.config.width-1)

    def add(self, key):
        """Set bits. Duplicate additions are idempotent; no item count is kept."""
        for part, slot in enumerate(self._slots(key)):
            index = part * (self.config.width // 8) + slot // 8
            self._bits[index] |= 1 << (slot % 8)

    def might_contain(self, key):
        for part, slot in enumerate(self._slots(key)):
            index = part * (self.config.width // 8) + slot // 8
            if not self._bits[index] & (1 << (slot % 8)):
                return False
        return True

    def occupancy(self):
        stride = self.config.width // 8
        return tuple(sum(byte.bit_count() for byte in self._bits[j*stride:(j+1)*stride])
                     for j in range(self.config.slices))

    def layout_false_positive(self):
        """Ideal fresh uniform query coordinates, conditional on these bits."""
        return math.prod(count/self.config.width for count in self.occupancy())

    def union(self, other):
        """Return a new OR-union; identical configuration and seed are required."""
        if not isinstance(other, BloomFilter) or self.config != other.config:
            raise ValueError("union requires identical power, slice count, seed, and format")
        merged = BloomFilter(self.config)
        merged._bits[:] = bytes(a | b for a, b in zip(self._bits, other._bits))
        return merged

    def to_bytes(self):
        header = HEADER.pack(MAGIC, self.config.power, self.config.slices, self.config.seed)
        body = header + self._bits
        return body + hashlib.sha256(body).digest()

    @classmethod
    def from_bytes(cls, data):
        """Strict bounded decoder. Checksum detects corruption, not forgery."""
        if type(data) is not bytes or not HEADER.size+CHECKSUM_BYTES <= len(data) <= HEADER.size+MAX_PAYLOAD+CHECKSUM_BYTES:
            raise ValueError("invalid encoded size or type")
        magic, power, slices, seed = HEADER.unpack_from(data)
        if magic != MAGIC:
            raise ValueError("unsupported filter format")
        config = Config(power, slices, seed)
        if len(data) != HEADER.size+config.payload_bytes+CHECKSUM_BYTES:
            raise ValueError("payload length does not match header")
        body, digest = data[:-CHECKSUM_BYTES], data[-CHECKSUM_BYTES:]
        if not hmac.compare_digest(hashlib.sha256(body).digest(), digest):
            raise ValueError("checksum mismatch")
        result = cls(config)
        result._bits[:] = body[HEADER.size:]
        return result
