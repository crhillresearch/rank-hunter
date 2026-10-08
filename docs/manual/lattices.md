# Lattices & Heights

The canonical height pairing turns non-torsion Mordell–Weil points into a positive-definite lattice after quotienting torsion.

Rank Hunter uses this geometry to inspect basis quality and guide search. Numerical lattice data is not automatically an exact rank certificate.

## Build a lattice

```bash
sage -python -m rank42.lattice \
  --db rank42.db \
  --curve-id 123 \
  --source stored-generators \
  --precision 256
```

List saved lattices:

```bash
sage -python -m rank42.lattice --db rank42.db --list
```

Use `--help` for the current parser.

## What is stored

A saved lattice can include:

- source basis identity;
- precision;
- canonical heights;
- Gram/height-pairing matrix;
- determinant/regulator-like diagnostics where available;
- reduction/search metadata.

The exact stored schema evolves; treat the database record plus engine/version as the reproducibility unit.

## Canonical height

For a non-torsion point \(P\), the canonical height \(\hat h(P)\) behaves quadratically:

\[
\hat h(nP)=n^2\hat h(P).
\]

The bilinear height pairing is recovered from canonical heights and gives the Gram matrix for a basis.

## What lattice geometry can tell you

Numerically, it can help identify:

- badly conditioned bases;
- unusually short combinations;
- long generator directions;
- near-relations worth exact testing;
- candidate anchor combinations for alternative point searches.

## What it cannot prove by itself

A floating-point positive-definite Gram matrix is not the final exact independence certificate used by Rank Hunter.

Precision, conditioning, and enormous point heights can make numerical rank misleading.

Use numerical lattice results to schedule exact work.

## Reduction

Lattice reduction can produce a better-conditioned basis of the same subgroup.

This can improve:

- later height calculations;
- search anchors;
- relation search;
- covering/descent conditioning.

Reduction does not add rank by itself.

## Lattice holes / search vectors

Some research stages can construct candidate vectors from the lattice geometry, for example combinations near half-lattice holes.

These are search heuristics.

If such a vector produces a rational point through an exact construction, that point still enters the normal exact ledger and independence path.

## Relation attacks

When the height matrix suggests dependence or a short relation, use the exact relation tooling rather than declaring numerical dependence.

Example:

```bash
sage -python -m rank42.mw_relation_attack \
  --db rank42.db \
  --curve-id 123
```

## Precision

Increase precision when:

- entries vary by many orders of magnitude;
- the determinant is numerically unstable;
- near-relations are being investigated.

Higher precision costs time and does not fix a fundamentally poor basis; saturation/reduction may be more useful.

## Related documentation

- [Mathematics: Heights & Lattices](../mathematics/heights-lattices.md)
- [Independence](independence.md)
- [Saturation](saturation.md)
- [Quartics & Coverings](quartics.md)
