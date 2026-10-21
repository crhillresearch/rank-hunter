"""Exact PGL2 coordinate changes for quartics over Q.

For x = (A z + B)/(C z + D), the affine quartic

    y^2 = f(x)

is transformed to

    Y^2 = (C z + D)^4 f((A z + B)/(C z + D)),
    Y = (C z + D)^2 y.

All arithmetic here uses :class:`fractions.Fraction`, making this module usable
without Sage and suitable for regression tests of the search-coordinate layer.

v0.7.12 extends the original base-anchor chart ranking with two additional
*search-coordinate* sources:

* subgroup-informed charts, built from exact non-base native quartic fibres
  already discovered for the specialization, mixed with the generic base
  fibres; and
* free charts, built from small primitive integer PGL2 matrices independent of
  known points.

These are search heuristics only.  They do not change the underlying genus-one
curve or any proof boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
import itertools
import math


def _q(x):
    return x if isinstance(x, Fraction) else Fraction(str(x))


def _trim(poly):
    out = list(poly)
    while len(out) > 1 and out[-1] == 0:
        out.pop()
    return out


def _add(p, q):
    n = max(len(p), len(q))
    out = [Fraction(0) for _ in range(n)]
    for i, x in enumerate(p):
        out[i] += x
    for i, x in enumerate(q):
        out[i] += x
    return _trim(out)


def _mul(p, q):
    out = [Fraction(0) for _ in range(len(p) + len(q) - 1)]
    for i, a in enumerate(p):
        for j, b in enumerate(q):
            out[i + j] += a * b
    return _trim(out)


def _pow_linear(a, b, n):
    out = [Fraction(1)]
    base = [_q(b), _q(a)]
    for _ in range(int(n)):
        out = _mul(out, base)
    return out


def evaluate(coefficients, x):
    x = _q(x)
    acc = Fraction(0)
    for c in reversed([_q(v) for v in coefficients]):
        acc = acc * x + c
    return acc


def rational_height_bits(x):
    """Bit-size proxy for a rational coordinate."""
    x = _q(x)
    return max(abs(x.numerator).bit_length(), x.denominator.bit_length())


def canonical_matrix_signature(A, B, C, D):
    """Canonical primitive integer representative of a PGL2(Q) matrix.

    Matrices that differ by a nonzero rational scalar have the same signature.
    This is used only to deduplicate search-coordinate charts.
    """
    vals = [_q(A), _q(B), _q(C), _q(D)]
    den = 1
    for x in vals:
        den = math.lcm(den, x.denominator)
    ints = [int(x * den) for x in vals]
    g = 0
    for x in ints:
        g = math.gcd(g, abs(x))
    if g:
        ints = [x // g for x in ints]
    for x in ints:
        if x:
            if x < 0:
                ints = [-v for v in ints]
            break
    return tuple(ints)


@dataclass(frozen=True)
class MobiusChart:
    A: Fraction
    B: Fraction
    C: Fraction
    D: Fraction
    alpha: Fraction | None
    beta: Fraction | None
    gamma: Fraction | None
    coefficients: tuple[Fraction, ...]
    max_coeff_bits: int
    sum_coeff_bits: int
    max_known_bits: int
    median_known_bits: int
    quality: float
    source: str = "base"
    source_detail: str | None = None

    @property
    def determinant(self):
        return self.A * self.D - self.B * self.C

    @property
    def matrix_signature(self):
        return canonical_matrix_signature(self.A, self.B, self.C, self.D)

    @property
    def chart_id(self):
        # Human-readable deterministic ID. Full exact matrix is persisted too.
        return (
            f"source={self.source};a={self.alpha};b={self.beta};g={self.gamma};"
            f"M={self.A},{self.B},{self.C},{self.D}"
        )

    def as_metadata(self):
        return {
            "source": self.source,
            "source_detail": self.source_detail,
            "alpha": None if self.alpha is None else str(self.alpha),
            "beta": None if self.beta is None else str(self.beta),
            "gamma": None if self.gamma is None else str(self.gamma),
            "matrix": [str(self.A), str(self.B), str(self.C), str(self.D)],
            "matrix_signature": list(self.matrix_signature),
            "max_coeff_bits": self.max_coeff_bits,
            "sum_coeff_bits": self.sum_coeff_bits,
            "max_known_bits": self.max_known_bits,
            "median_known_bits": self.median_known_bits,
            "quality": self.quality,
        }


def _matrix_anchor_images(A, B, C, D):
    """Return inverse images of z=0, infinity, 1 when finite."""
    A, B, C, D = map(_q, (A, B, C, D))
    alpha = B / D if D != 0 else None
    beta = A / C if C != 0 else None
    den = C + D
    gamma = (A + B) / den if den != 0 else None
    return alpha, beta, gamma


def _chart_metrics(coefficients, A, B, C, D, known_x=None):
    transformed = transform_coefficients(coefficients, A, B, C, D)
    # Match rank42.ratpoints.normalize_polynomial exactly for chart ranking:
    # D=lcm(denominators), then the integral search polynomial is D^2*f.
    den_lcm = 1
    for cc in transformed:
        den_lcm = math.lcm(den_lcm, cc.denominator)
    scale2 = den_lcm * den_lcm
    integral_coeffs = [int((cc * scale2).numerator) for cc in transformed]
    coeff_bits = [abs(c).bit_length() if c else 0 for c in integral_coeffs]

    known_bits = []
    for xx in known_x or []:
        zz = forward_x(A, B, C, D, xx)
        if zz is not None:
            known_bits.append(rational_height_bits(zz))
    known_bits.sort()
    max_known = known_bits[-1] if known_bits else 0
    med_known = known_bits[len(known_bits) // 2] if known_bits else 0
    max_coeff = max(coeff_bits) if coeff_bits else 0
    sum_coeff = sum(coeff_bits)
    # Lower is better. Coefficients dominate; known-point compression breaks ties.
    quality = float(max_coeff + 0.35 * max_known + 0.10 * med_known)
    return transformed, max_coeff, sum_coeff, max_known, med_known, quality


def chart_from_matrix(coefficients, A, B, C, D, *, known_x=None, source="free", source_detail=None):
    """Build a chart from an arbitrary nonsingular PGL2(Q) matrix."""
    A, B, C, D = map(_q, (A, B, C, D))
    if A * D - B * C == 0:
        raise ValueError("singular Mobius transform")
    transformed, max_coeff, sum_coeff, max_known, med_known, quality = _chart_metrics(
        coefficients, A, B, C, D, known_x=known_x
    )
    alpha, beta, gamma = _matrix_anchor_images(A, B, C, D)
    return MobiusChart(
        A=A, B=B, C=C, D=D,
        alpha=alpha, beta=beta, gamma=gamma,
        coefficients=tuple(transformed),
        max_coeff_bits=max_coeff,
        sum_coeff_bits=sum_coeff,
        max_known_bits=max_known,
        median_known_bits=med_known,
        quality=quality,
        source=str(source),
        source_detail=source_detail,
    )


def chart_from_three_points(coefficients, alpha, beta, gamma, known_x=None, *, source="base", source_detail=None):
    """Build the chart sending alpha->0, beta->infinity, gamma->1.

    The inverse coordinate map used by the transformed quartic is

        x = (A z + B)/(C z + D).
    """
    alpha, beta, gamma = map(_q, (alpha, beta, gamma))
    if len({alpha, beta, gamma}) != 3:
        raise ValueError("Mobius anchors must be distinct")

    k = (gamma - beta) / (gamma - alpha)
    # z = k (x-alpha)/(x-beta), hence x=(beta*z-k*alpha)/(z-k).
    A, B, C, D = beta, -k * alpha, Fraction(1), -k
    if A * D - B * C == 0:
        raise ValueError("singular Mobius transform")

    transformed, max_coeff, sum_coeff, max_known, med_known, quality = _chart_metrics(
        coefficients, A, B, C, D, known_x=known_x
    )
    return MobiusChart(
        A=A, B=B, C=C, D=D,
        alpha=alpha, beta=beta, gamma=gamma,
        coefficients=tuple(transformed),
        max_coeff_bits=max_coeff,
        sum_coeff_bits=sum_coeff,
        max_known_bits=max_known,
        median_known_bits=med_known,
        quality=quality,
        source=str(source),
        source_detail=source_detail,
    )


def transform_coefficients(coefficients, A, B, C, D):
    """Return coefficients of (Cz+D)^4 f((Az+B)/(Cz+D))."""
    coeffs = [_q(x) for x in coefficients]
    if len(coeffs) > 5:
        raise ValueError("expected degree at most four")
    coeffs += [Fraction(0)] * (5 - len(coeffs))
    A, B, C, D = map(_q, (A, B, C, D))
    if A * D - B * C == 0:
        raise ValueError("singular Mobius transform")

    out = [Fraction(0)]
    for i, fi in enumerate(coeffs):
        if fi == 0:
            continue
        term = _mul(_pow_linear(A, B, i), _pow_linear(C, D, 4 - i))
        term = [fi * x for x in term]
        out = _add(out, term)
    out += [Fraction(0)] * (5 - len(out))
    return tuple(out[:5])


def forward_x(A, B, C, D, x):
    """Map native x to chart z; return None for the chart point at infinity."""
    A, B, C, D, x = map(_q, (A, B, C, D, x))
    den = x * C - A
    if den == 0:
        return None
    return (B - x * D) / den


def inverse_x(A, B, C, D, z):
    A, B, C, D, z = map(_q, (A, B, C, D, z))
    den = C * z + D
    if den == 0:
        return None
    return (A * z + B) / den


def inverse_point(chart, z, Y):
    """Map a finite chart point (z,Y) back to the native affine quartic."""
    z, Y = _q(z), _q(Y)
    den = chart.C * z + chart.D
    if den == 0:
        return None
    x = (chart.A * z + chart.B) / den
    y = Y / (den * den)
    return x, y


def forward_point(chart, x, y):
    """Map a finite native point into this chart, or None at chart infinity."""
    x, y = _q(x), _q(y)
    z = forward_x(chart.A, chart.B, chart.C, chart.D, x)
    if z is None:
        return None
    den = chart.C * z + chart.D
    return z, den * den * y


def _chart_sort_key(c):
    return (
        c.quality, c.max_coeff_bits, c.max_known_bits,
        c.sum_coeff_bits, c.chart_id,
    )


def _dedupe_charts(charts):
    out = []
    seen = set()
    for chart in charts:
        sig = chart.matrix_signature
        if sig in seen:
            continue
        seen.add(sig)
        out.append(chart)
    return out


def _spread_rationals(values, limit):
    values = sorted({_q(x) for x in values}, key=lambda x: (rational_height_bits(x), x))
    limit = max(0, int(limit))
    if limit <= 0 or len(values) <= limit:
        return values
    if limit == 1:
        return [values[len(values) // 2]]
    # Deterministic spread across the entire observed height range rather than
    # keeping only the smallest discovered fibres.
    idxs = []
    for i in range(limit):
        idx = round(i * (len(values) - 1) / (limit - 1))
        if idx not in idxs:
            idxs.append(idx)
    return [values[i] for i in idxs]


def rank_charts(coefficients, known_x, *, anchor_pool=12, limit=8):
    """Enumerate and rank exact charts from low-height known x-coordinates.

    ``anchor_pool`` limits the combinatorial search to the lowest native-height
    known coordinates. Ordered triples are intentional because the roles
    0/infinity/1 are not symmetric computationally.
    """
    known = sorted({_q(x) for x in known_x}, key=lambda x: (rational_height_bits(x), x))
    if len(known) < 3:
        raise ValueError("need at least three distinct known x-coordinates")
    pool = known[: max(3, min(int(anchor_pool), len(known)))]
    charts = []
    for alpha, beta, gamma in itertools.permutations(pool, 3):
        try:
            charts.append(
                chart_from_three_points(
                    coefficients, alpha, beta, gamma, known_x=known,
                    source="base", source_detail="generic-base-fibre anchors",
                )
            )
        except (ZeroDivisionError, ValueError):
            continue
    charts = _dedupe_charts(charts)
    charts.sort(key=_chart_sort_key)
    return charts[: int(limit)]


def rank_subgroup_informed_charts(
    coefficients,
    base_x,
    discovered_x,
    *,
    base_anchor_pool=12,
    discovered_anchor_pool=16,
    limit=16,
):
    """Rank exact charts using at least one previously discovered non-base fibre.

    The name "subgroup-informed" describes the search design: these fibres were
    discovered while exploring the specialization's known Mordell--Weil
    landscape. Their subgroup membership is *not* inferred here. Exact rank
    claims remain the responsibility of the certificate layer.
    """
    base = sorted({_q(x) for x in base_x}, key=lambda x: (rational_height_bits(x), x))
    extra = {_q(x) for x in discovered_x} - set(base)
    if not extra:
        return []
    base_pool = base[: max(3, min(int(base_anchor_pool), len(base)))]
    extra_pool = _spread_rationals(extra, int(discovered_anchor_pool))
    pool = list(dict.fromkeys(base_pool + extra_pool))
    extra_set = set(extra_pool)
    known_for_score = list(dict.fromkeys(base + list(extra)))

    charts = []
    for alpha, beta, gamma in itertools.permutations(pool, 3):
        anchors = (alpha, beta, gamma)
        if not any(x in extra_set for x in anchors):
            continue
        n_extra = sum(x in extra_set for x in anchors)
        try:
            charts.append(
                chart_from_three_points(
                    coefficients, alpha, beta, gamma, known_x=known_for_score,
                    source="subgroup",
                    source_detail=f"mixed anchors with {n_extra} discovered non-base fibre(s)",
                )
            )
        except (ZeroDivisionError, ValueError):
            continue
    charts = _dedupe_charts(charts)
    charts.sort(key=_chart_sort_key)
    return charts[: int(limit)]


def _ordered_unique_rationals(values):
    out = []
    seen = set()
    for value in values or []:
        q = _q(value)
        if q in seen:
            continue
        seen.add(q)
        out.append(q)
    return out


def rank_exploratory_geometry_charts(
    coefficients,
    base_x,
    rigorous_x,
    exploratory_x,
    *,
    rigorous_anchor_pool=24,
    exploratory_anchor_pool=48,
    mix="balanced",
    limit=64,
):
    """Build exact charts from certified anchors plus geometry-only discoveries.

    exploratory_x is deliberately ordered by the caller: family adapters can
    prioritize recency, denominator band, height, or another discovery signal
    without teaching core about family-specific persistence. The returned
    charts are search geometry only. Nothing here promotes subgroup membership,
    independence, or rank evidence.

    The planner avoids cubic enumeration. It samples deterministic mixed
    patterns and then interleaves chart buckets by exploratory priority so a
    large anchor pool cannot be dominated by one easy rediscovery.
    """
    mix = str(mix or "balanced").strip().lower()
    if mix not in {"rigorous_heavy", "balanced", "exploratory_heavy"}:
        raise ValueError(f"unknown exploratory chart mix: {mix}")

    base = sorted({_q(x) for x in base_x or []}, key=lambda x: (rational_height_bits(x), x))
    rigorous = _ordered_unique_rationals(rigorous_x)
    certified = _ordered_unique_rationals(base + rigorous)
    certified = certified[: max(0, int(rigorous_anchor_pool))]

    exploratory = []
    blocked = set(certified)
    for x in _ordered_unique_rationals(exploratory_x):
        if x in blocked:
            continue
        exploratory.append(x)
        if len(exploratory) >= max(0, int(exploratory_anchor_pool)):
            break
    if not exploratory:
        return []

    score_x = _ordered_unique_rationals(base + certified + exploratory)
    fanout = {
        "rigorous_heavy": (8, 2, 0),
        "balanced": (6, 4, 2),
        "exploratory_heavy": (3, 6, 4),
    }[mix]
    rr_fanout, re_fanout, ee_fanout = fanout
    buckets = {i: [] for i in range(len(exploratory))}

    def add(primary, anchors, detail):
        if len(set(anchors)) != 3:
            return
        try:
            chart = chart_from_three_points(
                coefficients,
                anchors[0], anchors[1], anchors[2],
                known_x=score_x,
                source="exploratory",
                source_detail=detail,
            )
        except (ZeroDivisionError, ValueError):
            return
        buckets[int(primary)].append(chart)

    if len(certified) >= 2 and rr_fanout > 0:
        n = len(certified)
        for i, e in enumerate(exploratory):
            for step in range(1, min(rr_fanout, n - 1) + 1):
                r1 = certified[(i + step - 1) % n]
                r2 = certified[(i + step) % n]
                add(i, (r1, r2, e), "2 certified + 1 geometry-only anchor")
                add(i, (e, r1, r2), "2 certified + 1 geometry-only anchor")
                add(i, (r1, e, r2), "2 certified + 1 geometry-only anchor")

    if certified and len(exploratory) >= 2 and re_fanout > 0:
        ne = len(exploratory)
        nr = len(certified)
        for i, e1 in enumerate(exploratory):
            for step in range(1, min(re_fanout, ne - 1) + 1):
                e2 = exploratory[(i + step) % ne]
                r = certified[(i + step - 1) % nr]
                add(i, (r, e1, e2), "1 certified + 2 geometry-only anchors")
                add(i, (e1, r, e2), "1 certified + 2 geometry-only anchors")
                add(i, (e1, e2, r), "1 certified + 2 geometry-only anchors")

    if len(exploratory) >= 3 and ee_fanout > 0:
        ne = len(exploratory)
        for i, e1 in enumerate(exploratory):
            for step in range(1, min(ee_fanout, ne - 2) + 1):
                e2 = exploratory[(i + step) % ne]
                e3 = exploratory[(i + step + 1) % ne]
                add(i, (e1, e2, e3), "3 geometry-only anchors")

    for charts in buckets.values():
        charts.sort(key=_chart_sort_key)

    out = []
    seen = set()
    while len(out) < max(1, int(limit)) and any(buckets.values()):
        progressed = False
        for i in range(len(exploratory)):
            q = buckets[i]
            while q:
                chart = q.pop(0)
                sig = chart.matrix_signature
                if sig in seen:
                    continue
                seen.add(sig)
                out.append(chart)
                progressed = True
                break
            if len(out) >= max(1, int(limit)):
                break
        if not progressed:
            break
    return out

def rank_free_charts(coefficients, known_x, *, bound=3, limit=16):
    """Rank small primitive integer PGL2 matrices independent of known anchors."""
    bound = max(1, int(bound))
    vals = range(-bound, bound + 1)
    charts = []
    seen = set()
    for A, B, C, D in itertools.product(vals, repeat=4):
        if A == B == C == D == 0:
            continue
        if A * D - B * C == 0:
            continue
        sig = canonical_matrix_signature(A, B, C, D)
        if sig in seen:
            continue
        # Only retain the canonical sign representative while enumerating.
        if sig != (A, B, C, D):
            continue
        seen.add(sig)
        try:
            chart = chart_from_matrix(
                coefficients, A, B, C, D,
                known_x=known_x,
                source="free",
                source_detail=f"primitive integer matrix bound={bound}",
            )
        except ValueError:
            continue
        charts.append(chart)
    charts.sort(key=_chart_sort_key)
    return charts[: int(limit)]


def _weighted_interleave(base, subgroup, free, limit):
    """Interleave 1:2:1 so interrupted hybrid runs still sample all sources."""
    queues = {
        "base": list(base),
        "subgroup": list(subgroup),
        "free": list(free),
    }
    cycle = ("base", "subgroup", "subgroup", "free")
    out = []
    seen = set()
    while len(out) < int(limit) and any(queues.values()):
        progressed = False
        for name in cycle:
            q = queues[name]
            while q:
                chart = q.pop(0)
                sig = chart.matrix_signature
                if sig in seen:
                    continue
                seen.add(sig)
                out.append(chart)
                progressed = True
                break
            if len(out) >= int(limit):
                break
        if not progressed:
            break
    return out


def build_chart_plan(
    coefficients,
    base_x,
    discovered_x=None,
    *,
    strategy="hybrid",
    anchor_pool=12,
    discovered_anchor_pool=16,
    free_bound=3,
    limit=24,
):
    """Build a deterministic chart plan for one of four operator strategies.

    Strategies:
      ``base``      current generic-section anchor charts;
      ``subgroup``  charts requiring at least one discovered non-base fibre;
      ``free``      small primitive integer PGL2 matrices;
      ``hybrid``    adaptive 25% base / 50% subgroup / 25% free, with quota
                    transferred automatically when one source is unavailable.
    """
    strategy = str(strategy).strip().lower()
    if strategy not in {"base", "subgroup", "free", "hybrid"}:
        raise ValueError(f"unknown chart strategy: {strategy}")
    limit = max(1, int(limit))
    base_x = list({_q(x) for x in base_x})
    base_set = set(base_x)
    discovered_x = list({_q(x) for x in (discovered_x or []) if _q(x) not in base_set})
    score_x = list(dict.fromkeys(base_x + discovered_x))

    if strategy == "base":
        return rank_charts(coefficients, base_x, anchor_pool=anchor_pool, limit=limit)
    if strategy == "subgroup":
        return rank_subgroup_informed_charts(
            coefficients, base_x, discovered_x,
            base_anchor_pool=anchor_pool,
            discovered_anchor_pool=discovered_anchor_pool,
            limit=limit,
        ) if discovered_x else []
    if strategy == "free":
        return rank_free_charts(
            coefficients, score_x or base_x, bound=free_bound, limit=limit
        )

    # Generate enough candidates from each source to fill adaptive quotas after
    # cross-source PGL2 deduplication. The 1:2:1 interleave gives the requested
    # 25/50/25 mix when all three sources are available and automatically
    # transfers missing quota when a source is empty.
    candidate_limit = max(limit * 3, 24)
    base = rank_charts(
        coefficients, base_x, anchor_pool=anchor_pool, limit=candidate_limit
    )
    subgroup = rank_subgroup_informed_charts(
        coefficients, base_x, discovered_x,
        base_anchor_pool=anchor_pool,
        discovered_anchor_pool=discovered_anchor_pool,
        limit=candidate_limit,
    ) if discovered_x else []
    free = rank_free_charts(
        coefficients, score_x or base_x, bound=free_bound, limit=candidate_limit
    )
    return _weighted_interleave(base, subgroup, free, limit)

