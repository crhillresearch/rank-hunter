# Analysis Tools

Analysis begins after a curve has earned more attention.

The right tool depends on what is missing: points, independence, subgroup quality, alternative search geometry, or an upper bound.

## Decision guide

| Question | Tool |
| --- | --- |
| Need another rational point? | **Target**, **Quartics**, Family geometry |
| Have points but do not know whether they add rank? | **Independence** |
| Need to improve/check subgroup index? | **Saturation** |
| Want canonical-height geometry / reduced basis guidance? | **Lattices** |
| Want an upper bound or exact rank? | **Descent** |
| Want alternate point-search models? | **Quartics & Coverings** |

## Start from the live curve state

Before running anything expensive, open the curve and record:

- rigorous lower bound;
- rigorous upper bound;
- exact rank if already known;
- number of exact points;
- complete rigorous witness basis status;
- prior timeout/error history.

Do not repeat an expensive method that already hit a deterministic engine limit on the identical model unless you have changed something meaningful.

## Independence

Use Independence when new exact points are present but the rigorous lower bound has not risen.

The standard path tests candidates against the authoritative rigorous basis.

Hard-case strategies can precondition or saturate the basis, then retry exact certification.

See [Independence](independence.md).

## Saturation

Saturation asks whether the current subgroup is missing divisible points.

A nontrivial index can replace the basis with a better/saturated basis without increasing rank.

That can improve later independence, lattice, or descent work.

See [Saturation](saturation.md).

## Lattices and heights

Height-pairing data helps answer:

- Is the current basis badly conditioned?
- Which point combinations are short/long?
- Are there numerically suspicious near-relations?
- Which combinations are useful anchors for further search?

These are powerful diagnostics but numerical geometry alone is not proof of independence.

See [Lattices & Heights](lattices.md).

## Quartics and coverings

Pointed quartics and exact coverings give alternative rational-point search spaces.

A successful quartic hit matters only after exact map-back to the elliptic curve.

Timeouts in one quartic model do not imply the original curve has no additional points.

See [Quartics & Coverings](quartics.md).

## Descent and rank bounds

Use Descent when you want a rigorous upper bound.

Normal automatic rank-bounds behavior is PARI-first, with bounded fallback/escalation behavior implemented by the release.

If the upper bound meets the rigorous lower bound, exact rank is closed.

See [Descent & Rank Bounds](descent.md).

## Suggested workflow for a promising high-rank curve

A reasonable order is:

1. verify all newly discovered points exactly;
2. run Independence;
3. if the basis is awkward, try bounded Saturation;
4. build a height lattice for geometry;
5. use Target/Quartics if you still want more points;
6. run Descent when closing the interval is worth the cost.

This is a workflow suggestion, not a theorem. Some families have specialized geometry that should run earlier.

## Avoiding wasted compute

Before increasing a timeout by 10×, ask:

- Did the previous run time out or actually complete?
- Was it searching the right model?
- Was the dominant difficulty height, denominator, covering size, or engine limit?
- Does another exact transform make the same problem smaller?
- Is the current rigorous basis complete?

Longer compute is useful only when it attacks the actual bottleneck.

## Evidence rule

Every Analysis page is still subject to [What Counts as Proof?](../getting-started/evidence.md).
