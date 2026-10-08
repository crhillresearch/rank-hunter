# Mathematical Independence

Let \(P_1,\dots,P_n\in E(\mathbf Q)\).

They are independent modulo torsion if

\[
a_1P_1+\cdots+a_nP_n\in E(\mathbf Q)_{\mathrm{tors}}
\]

with integers \(a_i\) implies every \(a_i=0\).

Independent non-torsion points give a rank lower bound.

## Why raw point count fails

A point search often rediscovers:

- negatives;
- multiples;
- sums/differences of known points;
- points in an unsaturated subgroup;
- the same rational point through several search models.

Therefore “number of exact point rows” is not rank.

## Height pairing

The canonical height gives a quadratic form on the free part.

For a proposed basis, the height-pairing matrix is positive definite when the points are independent modulo torsion.

Numerical height matrices are excellent diagnostics, but finite precision can be misleading for very large or nearly dependent points.

Rank Hunter does not use a floating-point positive-definite check as its final rigorous promotion rule.

## Exact certificate strategy

The exact independence implementation works against an authoritative rigorous basis and can use bounded arithmetic such as halvings/modular information and saturation-assisted fallback.

The exact implementation is versioned and should be treated as part of the certificate provenance.

## Saturation and independence

Suppose \(G\subset E(\mathbf Q)\) has finite index in its saturation \(G^{\mathrm{sat}}\).

Then

\[
\operatorname{rank}G=\operatorname{rank}G^{\mathrm{sat}}.
\]

Saturation may improve the basis without increasing rank.

This is why a nontrivial saturation index is not an extra generator.

## Generic sections

For sections \(P_i(T)\in E(\mathbf Q(T))\), a generic linear relation would survive specialization wherever the sections/model specialize properly.

Therefore a good specialization at which the section images are exactly independent can prove generic independence.

The reverse implication is not automatic: generic independence does not guarantee every specialization retains full independence.

## Dependence vs unresolved

An exact certificate can fail to finish.

“Unresolved” is not “dependent.”

Rank Hunter preserves this distinction with timeout/inconclusive statuses.

## Practical consequence

When a search finds a new exact point:

1. compare/canonicalize against existing exact points;
2. test it against the current rigorous witness basis;
3. only promote the lower bound after exact independence succeeds.

See [Manual: Independence](../manual/independence.md).
