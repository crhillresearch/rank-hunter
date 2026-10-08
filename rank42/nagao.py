import math
import pickle
from pathlib import Path

from sage.all import prime_range


FAMILY_SCORE_ALGORITHM = "rh-family-table-nagao-v1"
FAMILY_SCORE_VERSION = 1
FAMILY_SCORE_FORMULA = "sum_log_cardinality_over_p"


def prime_table_count(prime_bound):
    """Return the number of prime tables needed for ``p < prime_bound``."""
    return sum(1 for _ in prime_range(2, int(prime_bound)))


def _table_count(primes, tables):
    return sum(1 for p in primes if p in tables)


def extend_score_tables(
    family,
    prime_bound,
    tables=None,
    *,
    progress=False,
    checkpoint_path=None,
    checkpoint_metadata=None,
    checkpoint_interval=25,
):
    """Extend ``tables`` to all primes p < ``prime_bound``.

    Each table[r] is log(#E_r(F_p)/p), or None for a specialization where the
    affine parameter is unusable/singular.  Keeping extension separate lets the
    staged sieve postpone expensive larger-prime precomputation until the
    candidate set has already been reduced.

    When ``checkpoint_path`` is supplied, partial tables are written there
    atomically as an explicitly incomplete cache.  The checkpoint is never
    written to a normal cache path, so an interrupted precompute cannot be
    mistaken for a complete table set.
    """
    if tables is None:
        tables = {}

    prime_bound = int(prime_bound)
    primes = [int(p0) for p0 in prime_range(2, prime_bound)]
    target_count = len(primes)
    current_count = _table_count(primes, tables)
    checkpoint_interval = max(1, int(checkpoint_interval))
    checkpoint_path = Path(checkpoint_path) if checkpoint_path is not None else None
    checkpoint_metadata = dict(checkpoint_metadata or {})
    last_checkpoint_count = -1
    completed = False

    def write_checkpoint(*, force=False):
        nonlocal last_checkpoint_count
        if checkpoint_path is None:
            return
        current = _table_count(primes, tables)
        if not force and current - last_checkpoint_count < checkpoint_interval:
            return
        metadata = dict(checkpoint_metadata)
        metadata.update(
            {
                "complete": False,
                "checkpoint": True,
                "prime_bound": prime_bound,
                "table_count": current,
                "target_table_count": target_count,
            }
        )
        save_score_tables(checkpoint_path, tables, metadata=metadata)
        last_checkpoint_count = current

    if progress:
        print(
            f"[precompute] {current_count}/{target_count} tables; "
            f"target p < {prime_bound}",
            flush=True,
        )

    try:
        for p in primes:
            if p in tables:
                continue
            hook = getattr(family, "nagao_score_table", None)
            if callable(hook):
                try:
                    table = list(hook(p))
                except Exception:
                    table = None
                if table is not None:
                    if len(table) != p:
                        raise ValueError(
                            f"family nagao_score_table({p}) returned {len(table)} entries; expected {p}"
                        )
                    tables[p] = table
                    current_count += 1
                    if progress and (
                        current_count == target_count
                        or current_count % checkpoint_interval == 0
                        or p < 25
                    ):
                        print(
                            f"[precompute] {current_count}/{target_count} tables; latest p={p} "
                            "(family fast table)",
                            flush=True,
                        )
                    write_checkpoint()
                    continue

            table = [None] * p
            for r in range(p):
                E = family.curve_mod_p(r, p)
                if E is None:
                    continue
                try:
                    n = int(E.cardinality())
                except Exception:
                    continue
                table[r] = math.log(n / p)
            tables[p] = table
            current_count += 1
            if progress and (
                current_count == target_count
                or current_count % checkpoint_interval == 0
                or p < 25
            ):
                print(
                    f"[precompute] {current_count}/{target_count} tables; latest p={p}",
                    flush=True,
                )
            write_checkpoint()
        write_checkpoint(force=True)
        completed = True
    finally:
        # KeyboardInterrupt and other exceptional exits still leave the last
        # consistent partial table set behind.  save_score_tables writes via a
        # sibling temporary file and replace(), so readers never see a torn
        # pickle.
        if not completed:
            write_checkpoint(force=True)
    return tables


def precompute_score_tables(family, prime_bound):
    return extend_score_tables(family, prime_bound)


def score_rational_used_primes(a, b, tables):
    """Return the exact prime tables contributing to the historical RH family score."""
    used = []
    for p, table in tables.items():
        p = int(p)
        if int(b) % p == 0:
            continue
        invb = pow(int(b) % p, -1, p)
        r = ((int(a) % p) * invb) % p
        if table[r] is None:
            continue
        used.append(p)
    return sorted(used)


def family_score_provenance(a, b, tables, prime_bound):
    primes = score_rational_used_primes(a, b, tables)
    return {
        "algorithm": FAMILY_SCORE_ALGORITHM,
        "algorithm_version": int(FAMILY_SCORE_VERSION),
        "formula": FAMILY_SCORE_FORMULA,
        "scorer": "family_prime_table",
        "scorer_version": int(FAMILY_SCORE_VERSION),
        "source_mode": "family_parameter_table",
        "prime_bound": int(prime_bound),
        "primes_used": primes,
        "terms_used": len(primes),
        "prime_convention": (
            "prime tables p < bound; no global 2/3 exclusion; "
            "exclude p dividing denominator and unusable/singular table entries"
        ),
        "published_mestre_nagao_formula": False,
    }


def score_rational(a, b, tables):
    """Score t=a/b using all supplied prime tables."""
    total = 0.0
    used = 0
    for p, table in tables.items():
        if b % p == 0:
            continue
        invb = pow(b % p, -1, p)
        r = ((a % p) * invb) % p
        val = table[r]
        if val is None:
            continue
        total += val
        used += 1
    return total, used


def iter_fixed_denominator_sieve(a_min, a_max, b, tables, *, block_size=1 << 20):
    """Yield vectorized Nagao scores for coprime a with fixed denominator b.

    For each prime the contribution is periodic in a, so a
    whole block of numerators is updated at once instead of performing a Python
    modular lookup separately for every (a,p) pair.

    Yields ``(a_values, scores, used_counts)`` numpy arrays after filtering to
    gcd(a,b)=1.
    """
    import numpy as np

    a_min = int(a_min)
    a_max = int(a_max)
    b = int(b)
    block_size = max(1, int(block_size))

    if abs(a_min) >= 2**62 or abs(a_max) >= 2**62 or abs(b) >= 2**62:
        raise ValueError("vectorized sieve currently requires signed 64-bit a,b")

    prepared = []
    for p, table in sorted(tables.items()):
        if b % p == 0:
            continue
        invb = pow(b % p, -1, p)

        # Reindex the t=r table by numerator residue a mod p for this fixed b.
        # Do it in NumPy rather than a Python loop: this setup is repeated for
        # every denominator and is otherwise a noticeable part of sieve cost.
        raw = np.array([np.nan if x is None else float(x) for x in table], dtype=np.float64)
        ares = np.arange(p, dtype=np.int64)
        ridx = np.remainder(ares * invb, p)
        picked = raw[ridx]
        valid = np.isfinite(picked).astype(np.int16)
        vals = np.nan_to_num(picked, nan=0.0)
        prepared.append((p, vals, valid))

    for lo in range(a_min, a_max + 1, block_size):
        hi = min(a_max + 1, lo + block_size)
        aa = np.arange(lo, hi, dtype=np.int64)
        scores = np.zeros(len(aa), dtype=np.float64)
        used = np.zeros(len(aa), dtype=np.int32)
        for p, vals, valid in prepared:
            residues = np.remainder(aa, p)
            scores += vals[residues]
            used += valid[residues]

        keep = np.gcd(aa, abs(b)) == 1
        yield aa[keep], scores[keep], used[keep]


def save_score_tables(path, tables, *, metadata=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"metadata": metadata or {}, "tables": tables}
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("wb") as f:
        pickle.dump(payload, f, protocol=pickle.HIGHEST_PROTOCOL)
    tmp.replace(path)


def load_score_tables(path):
    path = Path(path)
    with path.open("rb") as f:
        payload = pickle.load(f)
    if not isinstance(payload, dict) or "tables" not in payload:
        raise ValueError(f"invalid Nagao cache: {path}")
    return payload.get("tables") or {}, payload.get("metadata") or {}


def score_cache_is_complete(metadata):
    """Return whether metadata describes a usable complete score cache.

    Caches written before completion metadata was introduced remain compatible;
    only an explicit incomplete/checkpoint marker makes a cache unusable as a
    finished result.
    """
    metadata = metadata or {}
    return metadata.get("complete", True) is not False and not metadata.get("checkpoint", False)
