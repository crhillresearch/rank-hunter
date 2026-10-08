# Nagao-Style Scoring

Rank Hunter uses prime-reduction scores to rank candidates before expensive rational-point work.

This page documents the built-in **General short-Weierstrass score** in 0.9.2. Family plugins may implement different scoring rules and must preserve their own provenance.

## Built-in short-model score

For

\[
E: y^2=x^3+Ax+B,
\]

and each good prime \(p\) below the configured bound, Rank Hunter computes \(\#E(\mathbf F_p)\) exactly by a character sum and accumulates

\[
S(E;B)=\sum_{\substack{p<B\\p\ne2,3\\p\nmid\Delta}}
\log\left(\frac{\#E(\mathbf F_p)}{p}\right).
\]

The implementation:

- uses primes \(p < B\);
- skips \(2\) and \(3\);
- skips primes of bad reduction;
- counts \(\#E(\mathbf F_p)\) exactly for the short model;
- stores the list of primes/term count and algorithm provenance.

## Important naming note

The project calls this a **Nagao-style** historical Rank Hunter score.

Core provenance explicitly records:

```text
published_mestre_nagao_formula = false
```

Do not cite this exact formula as if it were the standard published Mestre–Nagao statistic.

Family plugins may implement closer variants of published scores; inspect their metadata.

## Why reduction statistics correlate with rank

For a good prime,

\[
a_p=p+1-\#E(\mathbf F_p).
\]

High-rank search heuristics often look for aggregate patterns in \(a_p\), point counts, or related logarithmic quantities over many small primes.

The intuition is that reductions of curves with unusually rich rational structure can show statistically unusual behavior.

This is heuristic search guidance, not a theorem that a high score implies high rank.

## Bad reduction

Bad primes are skipped in the built-in short-model score.

That means two curves may use slightly different prime sets under the same bound.

The stored `primes_used` and `terms_used` are therefore part of the score provenance.

## Comparing scores

Only compare scores when the scoring algorithm/configuration is compatible.

At minimum check:

- algorithm/version;
- source mode;
- prime bound;
- prime convention;
- number of terms.

Do not compare a Family plugin's score numerically to the General score unless the formulas are intentionally compatible.

## Staged scoring

A common high-throughput workflow is:

1. low prime bound on a large population;
2. keep the best fraction;
3. higher prime bound on survivors;
4. exact point search only on the best candidates.

This spends exact modular arithmetic where it has the highest scheduling value.

## Score is not rank

A score can be useful even when it is “wrong” on an individual curve.

It should never directly write:

- rigorous lower bound;
- rigorous upper bound;
- exact rank.

If a scorer does that, it violates Rank Hunter's evidence boundary.

## Reproducibility

When exporting interesting candidates, preserve:

- exact model or Family parameter;
- scoring algorithm/version;
- prime bound;
- primes/terms used when available;
- score value;
- plugin/version if Family-specific.

## Source implementation

The built-in General score is implemented in `rank42.general_hunt_core.short_curve_nagao_score_details`.

See [Search](../manual/search.md) for how scores are used in candidate screening.
