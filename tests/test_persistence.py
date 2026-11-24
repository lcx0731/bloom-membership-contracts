import hashlib
import json
import subprocess
import sys

import pytest

from bloom_contracts import BloomFilter, Config
from bloom_contracts.core import HEADER, MAX_PAYLOAD


def test_round_trip_retains_membership_and_portable_header(tmp_path):
    config = Config(9, 5, 2**63+123)
    original = BloomFilter(config)
    for i in range(500): original.add(f"key-{i}".encode())
    data = original.to_bytes()
    assert data[:14] == b"PBF1" + bytes([9, 5]) + config.seed.to_bytes(8, "little")
    path = tmp_path / "filter.pbf"
    path.write_bytes(data)
    restored = BloomFilter.from_bytes(path.read_bytes())
    assert restored.to_bytes() == data
    assert restored.config == config
    assert all(restored.might_contain(f"key-{i}".encode()) for i in range(500))
    # A new process must agree; built-in randomized hash() is never used.
    code = "from pathlib import Path; from bloom_contracts import BloomFilter; import sys; b=BloomFilter.from_bytes(Path(sys.argv[1]).read_bytes()); assert all(b.might_contain(f'key-{i}'.encode()) for i in range(500))"
    subprocess.run([sys.executable, "-c", code, str(path)], cwd=tmp_path, check=True)


def test_decoder_rejects_truncation_trailing_data_and_corruption():
    data = BloomFilter(Config(4, 2)).to_bytes()
    for end in range(len(data)):
        with pytest.raises(ValueError): BloomFilter.from_bytes(data[:end])
    for offset in range(len(data)):
        changed = bytearray(data)
        changed[offset] ^= 1
        with pytest.raises(ValueError): BloomFilter.from_bytes(bytes(changed))
    with pytest.raises(ValueError): BloomFilter.from_bytes(data+b"x")
    with pytest.raises(ValueError): BloomFilter.from_bytes(bytearray(data))
    with pytest.raises(ValueError): BloomFilter.from_bytes(b"x"*(MAX_PAYLOAD+47))


def test_checksum_does_not_authenticate_the_inserted_set():
    original = BloomFilter(Config(4, 2))
    original.add(b"present")
    body = original.to_bytes()[:-32]
    altered = body[:HEADER.size] + bytes(original.config.payload_bytes)
    restored = BloomFilter.from_bytes(altered+hashlib.sha256(altered).digest())
    assert not restored.might_contain(b"present")
    # Accepted by design: a valid checksum says nothing about writer identity.


def test_installed_cli_audit_and_invalid_settings(tmp_path):
    cmd = [sys.executable, "-m", "bloom_contracts.cli", "--items", "500", "--queries", "5000"]
    result = subprocess.run(cmd, cwd=tmp_path, capture_output=True, text=True, check=True)
    report = json.loads(result.stdout)
    assert report["false_negatives"] == 0
    assert report["observed_fpr"] < .03  # Broad fixed-workload sanity check.
    assert report["serialized_bytes"] == report["payload_bytes"] + 46
    cmd[4] = "0"
    bad = subprocess.run(cmd, cwd=tmp_path, capture_output=True, text=True)
    assert bad.returncode == 2 and "error:" in bad.stderr
