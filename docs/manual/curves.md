# Curves & Points

**Curves** is the authoritative research inventory for elliptic curves Rank Hunter has retained.

**Points** is the exact rational-point ledger attached to those curves.

The two are deliberately separate because “point exists” and “point contributes to rank” are different claims.

## Curve identity

A retained curve has an exact stored five-term Weierstrass model

\[
[a_1,a_2,a_3,a_4,a_6].
\]

Do not identify a curve only by a pretty-printed short model in a screenshot. Research provenance should refer to the stored exact model and curve id.

Depending on origin, a curve may also carry:

- Family/plugin id and version;
- Family spec and file fingerprints;
- parameter;
- native Family/fiber identity;
- chart/transform information;
- candidate-pool origin;
- arithmetic metadata.

## Research state

The important live rank fields are conceptually:

- rigorous lower bound;
- rigorous upper bound;
- exact rank when the interval closes;
- evidence-conflict flag.

The live research state is reduced from evidence. It is not just the last engine's textual output.

If a later job times out, earlier rigorous evidence remains.

## Point ledger

A point row can distinguish:

- exact verification;
- discovery source;
- role;
- independence status;
- whether it is part of the rigorous witness basis;
- search reference/provenance;
- plugin metadata.

A point may be exact but still have unknown independence.

## Duplicate/sign handling

Many elliptic-curve workflows encounter equivalent representations of the same group direction, for example \(P\) and \(-P\).

Search/certification code canonicalizes points where appropriate so raw discovery count should not be confused with independent generator count.

## Rigorous witness basis

When core needs the current rigorous basis for downstream work, it loads the authoritative witness set rather than guessing from every exact point in the table.

This matters for:

- pointed quartic search;
- known-subgroup descent;
- saturation;
- lattice construction;
- hard-case independence.

If the stored witness basis is incomplete, downstream stages may correctly refuse to run.

## External/catalog data

External catalogs and read-only Libraries are reference material.

A catalog entry may tell you that a curve is historically known at high rank. It does not automatically mutate the local rigorous state.

If exact coordinates are imported, Rank Hunter can verify and store them through the local evidence path.

## Provenance

Before publishing or exporting a curve, preserve:

- curve id;
- exact model;
- Family/plugin identity and version;
- parameter/native fiber identity;
- exact independent points used for the lower bound;
- relevant rank-evidence ids/engines;
- any model transport used by external tools.

This is more useful than preserving only a final rank number.

## Evidence conflicts

If Rank Hunter detects a rigorous upper bound below the existing lower bound, treat the curve as having an evidence conflict.

Do not “fix” the display manually.

Inspect:

1. model identity;
2. point transport;
3. evidence engine;
4. plugin/import provenance;
5. timestamps and prior evidence.

## Candidate history

A curve linked from a Candidate Pool is independent of the pool after retention.

Deleting a pool should not delete the scientific curve state.

## What to do from a curve

Use:

- **Target** for more points;
- **Independence** when point contribution is unclear;
- **Saturation** when subgroup index matters;
- **Lattices** for canonical-height geometry;
- **Quartics** for alternative point-search models;
- **Descent** for rigorous upper bounds.

See [Analysis Tools](analysis.md).

## Reporting a lower bound

A defensible statement should identify the exact point evidence, for example:

> Rank Hunter verified and certified 17 independent rational points on the stored curve, proving rank ≥ 17.

A screenshot showing “17 points found” is not the same statement.
