# Heights & Mordell–Weil Lattices

The canonical height \(\hat h\) is the natural quadratic height on the Mordell–Weil group.

For non-torsion \(P\),

\[
\hat h(nP)=n^2\hat h(P).
\]

The associated bilinear pairing is

\[
\langle P,Q\rangle=
\frac{1}{2}
\left(
\hat h(P+Q)-\hat h(P)-\hat h(Q)
\right).
\]

For independent points modulo torsion, this pairing gives a positive-definite Gram matrix.

## Lattice model

A rank-\(r\) subgroup with basis \(P_1,\dots,P_r\) becomes a Euclidean lattice through the height pairing.

The Gram matrix is

\[
G_{ij}=\langle P_i,P_j\rangle.
\]

Its determinant is related to the regulator of the chosen subgroup.

## Basis quality

Two bases can span the same subgroup while having very different numerical quality.

A poor basis may contain huge combinations and make:

- height computation;
- relation search;
- descent;
- point-centered geometry

more difficult.

Lattice reduction seeks a better basis of the same subgroup.

## Numerical precision

Canonical-height computations are high-precision real calculations.

For large-height points or nearly dependent bases, low precision can produce unstable determinants/eigenvalues.

Increase precision before drawing geometric conclusions.

## Numerical rank is not proof

A numerical Cholesky/Gram test is a screen.

It can suggest that points are independent, but Rank Hunter's rigorous lower bound uses the exact certificate path.

This separation matters most in high rank, where condition numbers can be extreme.

## Search geometry

Lattices are useful for choosing combinations of known points as search anchors.

Examples include:

- short pair combinations;
- reduced-basis vectors;
- half-lattice-hole directions.

These choices can make transformed point coordinates much smaller.

Whether they actually expose a new rational point remains a search question.

## Saturation

If a basis is unsaturated, the lattice describes a sublattice of the true Mordell–Weil lattice.

Saturation can replace it with a larger same-rank subgroup and change regulator/index relationships.

## Regulator and rank

A nonzero numerical regulator is not, by itself, the Rank Hunter proof that rank equals the matrix dimension.

The exact points and independence certificate provide the lower bound; the lattice describes their geometry.

## See also

- [Manual: Lattices & Heights](../manual/lattices.md)
- [Mathematical Independence](independence.md)
- [Manual: Saturation](../manual/saturation.md)
