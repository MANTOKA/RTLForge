"""Independent software oracle for the explicitly defined RS experiment.

Coefficients are low-degree first except received/codeword symbol streams.
BM follows Zhang Algorithm 2; RiBM uses the merged array of Figure 4.4.
This is test infrastructure, never an RTL-generation result.
"""

from __future__ import annotations

from dataclasses import dataclass
from random import Random


@dataclass(frozen=True)
class Field:
    bits: int = 10
    polynomial: int = 0x409

    def mul(self, a: int, b: int) -> int:
        result = 0
        while b:
            if b & 1:
                result ^= a
            b >>= 1
            a <<= 1
            if a & (1 << self.bits):
                a ^= self.polynomial
        return result

    def power(self, a: int, exponent: int) -> int:
        result = 1
        while exponent:
            if exponent & 1:
                result = self.mul(result, a)
            a = self.mul(a, a)
            exponent >>= 1
        return result

    def inv(self, a: int) -> int:
        if not a:
            raise ZeroDivisionError("zero has no finite-field inverse")
        return self.power(a, (1 << self.bits) - 2)

    def evaluate(self, coefficients: list[int], x: int) -> int:
        result = 0
        for coefficient in reversed(coefficients):
            result = self.mul(result, x) ^ coefficient
        return result

    def product(self, a: list[int], b: list[int]) -> list[int]:
        result = [0] * (len(a) + len(b) - 1)
        for i, x in enumerate(a):
            for j, y in enumerate(b):
                result[i + j] ^= self.mul(x, y)
        return result


GF = Field()


def encode(message: list[int], parity: int = 15, field: Field = GF) -> list[int]:
    """Systematic high-degree-first stream, roots alpha^1 through alpha^parity."""
    generator = [1]
    for j in range(1, parity + 1):
        generator = field.product(generator, [field.power(2, j), 1])
    dividend = [0] * parity + list(reversed(message))
    for degree in range(len(dividend) - 1, parity - 1, -1):
        lead = dividend[degree]
        for j, coefficient in enumerate(generator):
            dividend[degree - parity + j] ^= field.mul(lead, coefficient)
    return message + list(reversed(dividend[:parity]))


def syndromes(word: list[int], parity: int = 15, field: Field = GF) -> list[int]:
    return [field.evaluate(list(reversed(word)), field.power(2, j)) for j in range(1, parity + 1)]


def bm(syndrome: list[int], field: Field = GF) -> tuple[list[int], list[int]]:
    """Return normalized locator and ordinary evaluator, using 2t syndromes."""
    t = len(syndrome) // 2
    if len(syndrome) != 2 * t or not t:
        raise ValueError("KES requires a positive even syndrome count")
    locator = [1] + [0] * (2 * t)
    correction = [0, 1] + [0] * (2 * t - 1)
    length = 0
    for r in range(1, 2 * t + 1):
        delta = syndrome[r - 1]
        for i in range(1, length + 1):
            delta ^= field.mul(locator[i], syndrome[r - i - 1])
        old = locator[:]
        if delta:
            locator = [a ^ field.mul(delta, b) for a, b in zip(locator, correction)]
            if 2 * length < r:
                length = r - length
                inverse = field.inv(delta)
                correction = [field.mul(x, inverse) for x in old]
        correction = [0] + correction[:-1]
    if length > t:
        raise ValueError("locator degree exceeds t")
    locator = locator[: t + 1]
    return locator, field.product(locator, syndrome)[:t]


def ribm(syndrome: list[int], field: Field = GF) -> tuple[list[int], list[int]]:
    """Figure 4.4: 3t+1 uniform cells; output is locator and Omega_hat."""
    t = len(syndrome) // 2
    if len(syndrome) != 2 * t or not t:
        raise ValueError("KES requires a positive even syndrome count")
    delta = syndrome[:] + [0] * t + [1]
    theta = delta[:]
    gamma, k = 1, 0
    for _ in range(2 * t):
        discrepancy = delta[0]
        shifted = delta[1:] + [0]
        delta = [field.mul(gamma, a) ^ field.mul(discrepancy, b) for a, b in zip(shifted, theta)]
        if discrepancy and k >= 0:
            theta, gamma, k = shifted, discrepancy, -k - 1
        else:
            k += 1
    return delta[t : 2 * t + 1], delta[:t]


def decode(
    word: list[int], algorithm: str = "bm", parity: int = 15, field: Field = GF
) -> list[int]:
    if algorithm not in {"bm", "ribm"}:
        raise ValueError("unknown algorithm")
    t = parity // 2
    syndrome = syndromes(word, parity, field)
    if not any(syndrome):
        return word[:-parity]
    locator, evaluator = (bm if algorithm == "bm" else ribm)(syndrome[: 2 * t], field)
    degree = max((i for i, x in enumerate(locator) if x), default=-1)
    derivative = [locator[i] if i % 2 else 0 for i in range(1, len(locator))]
    corrected = word[:]
    roots = 0
    order = (1 << field.bits) - 1
    for index in range(len(word)):
        position = len(word) - 1 - index
        z = field.power(2, (-position) % order)
        if field.evaluate(locator, z) == 0:
            denominator = field.evaluate(derivative, z)
            if not denominator:
                raise ValueError("repeated root")
            magnitude = field.mul(field.evaluate(evaluator, z), field.inv(denominator))
            if algorithm == "ribm":
                magnitude = field.mul(magnitude, field.power(z, 2 * t))
            corrected[index] ^= magnitude
            roots += 1
    if roots != degree or any(syndromes(corrected, parity, field)):
        raise ValueError("uncorrectable or inconsistent codeword")
    return corrected[:-parity]


def vectors(seed: int = 20261014, per_error: int = 20) -> list[dict]:
    random = Random(seed)
    cases = []
    for errors in range(8):
        for trial in range(per_error):
            message = [random.randrange(1024) for _ in range(257)]
            if trial == 0:
                message = [0] * 257
            elif trial == 1:
                message = [1023] * 257
            elif trial == 2:
                message = list(range(257))
            clean = encode(message)
            received = clean[:]
            positions = random.sample(range(272), errors)
            if trial == 0 and errors:
                positions = [0] + list(range(272 - errors + 1, 272))
            for position in positions:
                received[position] ^= random.randrange(1, 1024)
            cases.append(
                {
                    "id": f"e{errors}_{trial:02d}",
                    "errors": errors,
                    "positions": positions,
                    "message": message,
                    "received": received,
                    "syndromes": syndromes(received),
                }
            )
    return cases
