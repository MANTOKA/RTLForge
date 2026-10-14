import pytest

from verilog_agent.benchmarks.rs_reference import (
    GF,
    Field,
    bm,
    decode,
    encode,
    ribm,
    syndromes,
    vectors,
)


def test_book_examples_30_31():
    f = Field(3, 0b1011)

    def a(e):
        return f.power(2, e)

    syndrome = [0, a(1), a(2), a(6)]
    assert bm(syndrome, f) == ([1, a(1), a(3)], [0, a(1)])
    assert ribm(syndrome, f) == ([a(2), a(3), a(5)], [a(6), a(4)])
    word = [a(1), a(4), a(2), a(2), a(4), 1]
    assert decode(word, "bm", 4, f) == [a(1), a(4)]
    assert decode(word, "ribm", 4, f) == [a(1), a(4)]


def test_field_with_independent_log_table():
    # Separate shift/reduction implementation, no Field.mul used for the oracle.
    exp, log, x = [], {}, 1
    for i in range(1023):
        assert x not in log
        exp.append(x)
        log[x] = i
        x = ((x << 1) ^ (0x409 if x & 512 else 0)) & 1023
    assert x == 1
    for a in range(1, 1024):
        assert GF.mul(a, GF.inv(a)) == 1
        for b in (0, 1, 2, 17, 511, 1023):
            expected = 0 if b == 0 else exp[(log[a] + log[b]) % 1023]
            assert GF.mul(a, b) == expected


def test_rs272257_roundtrips():
    for case in vectors(per_error=3):
        assert len(encode(case["message"])) == 272
        assert syndromes(encode(case["message"])) == [0] * 15
        for algorithm in ("bm", "ribm"):
            assert decode(case["received"], algorithm) == case["message"]


def test_reject_invalid_algorithm_and_zero_inverse():
    with pytest.raises(ZeroDivisionError):
        GF.inv(0)
    with pytest.raises(ValueError):
        decode([0] * 272, "other")
