import random

from featherdb.bloom import BloomFilter


def test_no_false_negatives_for_inserted_keys():
    rng = random.Random(0)
    keys = [f"key-{i}".encode() for i in range(500)]
    bf = BloomFilter(expected_items=len(keys), false_positive_rate=0.01)
    for k in keys:
        bf.add(k)
    for k in keys:
        assert bf.might_contain(k), f"false negative for {k!r} -- bloom filters must never do this"


def test_false_positive_rate_is_roughly_as_configured():
    rng = random.Random(1)
    inserted = [f"present-{i}".encode() for i in range(2000)]
    bf = BloomFilter(expected_items=len(inserted), false_positive_rate=0.01)
    for k in inserted:
        bf.add(k)

    absent = [f"absent-{rng.randrange(10**9)}".encode() for _ in range(5000)]
    false_positives = sum(1 for k in absent if bf.might_contain(k))
    rate = false_positives / len(absent)

    # Generous bound (configured for 1%) so this doesn't flake.
    assert rate < 0.05, f"false positive rate {rate:.4f} is far above the configured 1%"


def test_round_trips_through_bytes():
    bf = BloomFilter(expected_items=100, false_positive_rate=0.01)
    for i in range(50):
        bf.add(f"k{i}".encode())

    restored = BloomFilter.from_bytes(bf.to_bytes())
    assert restored.num_bits == bf.num_bits
    assert restored.num_hashes == bf.num_hashes
    for i in range(50):
        assert restored.might_contain(f"k{i}".encode())
