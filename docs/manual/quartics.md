# Quartics & Coverings

Quartic and covering models provide alternative spaces in which to search for rational points on an elliptic curve.

Rank Hunter's key rule is exact map-back:

> A point on a search model becomes elliptic-curve evidence only after the rational map back to the stored curve checks exactly.

## Pointed quartics

Given a known point \(P\) on a Weierstrass curve, Rank Hunter can project the elliptic curve through \(P\) to an exact quartic in a slope coordinate.

For

\[
y^2+a_1xy+a_3y=x^3+a_2x^2+a_4x+a_6,
\]

core uses the completed-square quantity

\[
V=2y+a_1x+a_3
\]

and constructs an exact quartic model attached to the anchor point.

Rational quartic hits are mapped back with exact formulas and verified again on the elliptic curve.

## Anchor portfolios

Pointed-quartic search can build a portfolio of anchors from:

- rigorous basis points;
- observed exact points;
- pair combinations;
- wider basis combinations;
- optional lattice-derived preferred vectors.

Anchor selection is a search heuristic. Exact map-back and independence remain separate.

## Quartic reduction

Core can send quartic models through an isolated PARI reduction worker.

If an exactly checked reduction transform is available, Rank Hunter searches the reduced model and composes the inverse transform during map-back.

If reduction does not complete, the raw quartic can remain searchable.

Reduction is coordinate optimization, not rank evidence.

## Direct quartic search

Example:

```bash
sage -python -m rank42.quartic_search \
  --db rank42.db \
  --curve-id 123 \
  --coefficients 1,0,-4,0,1 \
  --height 100000 \
  --timeout 300
```

Use `--help` for exact coefficient and mapping options in your release.

## Persisted workbench

Run a saved pointed-quartic search:

```bash
sage -python -m rank42.quartic_workbench_runner \
  --db rank42.db \
  --mode pointed \
  --search-id 17
```

Run a stored exact covering:

```bash
sage -python -m rank42.quartic_workbench_runner \
  --db rank42.db \
  --mode covering \
  --covering-id 8
```

Run covering-local planning:

```bash
sage -python -m rank42.quartic_workbench_runner \
  --db rank42.db \
  --mode covering-local \
  --curve-id 123
```

## Stored coverings

Rank Hunter's generic covering handoff stores:

- quartic coefficients;
- exact rational map expressions back to the curve;
- curve id;
- optional lattice/hole provenance;
- metadata.

Generic covering storage proves that the model/map were accepted as exact data. It does not automatically prove a Selmer-class statement unless that provenance is separately verified.

## Local solubility and search planning

Covering tools can test local conditions and attach heuristic search budgets.

A rigorous local obstruction can rule out rational points on that covering.

The converse is not true: local solubility does not guarantee a rational point.

Search-height recommendations are heuristics, not proven height bounds.

## Fan-out

A Pipeline or plugin can provide multiple exact coverings and search them independently.

Branch coverage matters.

If half the coverings timed out, the aggregate result should not be reported as a completed exhaustive search.

## Point discovery and rank promotion

After an exact covering hit maps back to \(E(\mathbf Q)\):

1. point is stored exact;
2. duplicate/sign handling occurs;
3. independence is checked;
4. rigorous lower bound rises only if a genuinely new direction is certified.

## When quartics are useful

Use quartic/covering search when:

- direct affine coordinates are too large;
- a Family supplies natural 2-coverings;
- known points give useful pointed projections;
- lattice geometry suggests promising anchors;
- descent produces covering data.

## When to stop

If oracle/diagnostic work shows that every transformed known generator remains many orders of magnitude outside practical ratpoints height, increasing the same bounded height modestly is unlikely to help.

Change geometry rather than only increasing time.

## Related documentation

- [Target Search](target.md)
- [Lattices & Heights](lattices.md)
- [Pipelines](pipelines.md)
