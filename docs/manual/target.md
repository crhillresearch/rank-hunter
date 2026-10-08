# Target Search

Target Search is for one retained curve that has earned more compute.

Broad Search asks “which candidates deserve attention?” Target asks “how do I push this exact curve further?”

## Preconditions

Use Target after the curve has been retained and you can identify:

- exact stored model;
- current rigorous rank interval;
- exact point ledger;
- Family/plugin provenance when applicable;
- previous search attempts.

If you only have an unsearched parameter in a Candidate Pool, stay in Search/Pipelines.

## Typical reasons to target a curve

- strong rigorous lower bound;
- unusually high Family/Nagao score plus exact point evidence;
- Family-specific geometry worth exploring;
- direct search found points but appears height-limited;
- you want a different denominator band/chart;
- you want to continue from a known rigorous basis.

## What Target preserves

Target Search does not start from a blank curve.

It can reuse durable state such as:

- exact points;
- rigorous witness basis;
- Family provenance;
- prior search history;
- current lower/upper bounds.

A later failed Target job does not reduce the known rank.

## Search configuration

Depending on the active Family/plugin and core capabilities, Target may expose controls for:

- point-search height stages;
- chart count;
- denominator bands;
- CPU/GPU ratpoints;
- hard timeouts;
- Family-specific worker options;
- covering/geometry strategies;
- exact-certificate budgets.

Use the smallest experiment that answers the current bottleneck.

## Diagnose before deepening

Before raising every budget, ask what blocked the previous search.

### Direct search timed out everywhere

More height may simply increase the same infeasible search.

Try a transformed model, covering, or Family-specific geometry.

### Many exact points but no rank growth

Run Independence before searching for more points.

### Basis is poorly conditioned

Try Saturation/Lattices before feeding it into more geometry.

### Descent timed out

That says nothing about existence of another point. Decide whether the goal is “find another generator” or “prove an upper bound.”

## Denominator bands

Changing the denominator band can expose rational points missed by an integral search.

But denominator is only one coordinate-complexity axis. A point with denominator 1 can still have an enormous numerator/height.

Use measured search geometry rather than assuming “higher denominator = deeper point.”

## Family-specific Target Search

Family plugins can provide dedicated Target workers.

These may know exact sections, special coordinates, coverings, or transformations unavailable to generic core search.

Plugin output still enters the same exact evidence path.

## Stopping criteria

Stop a Target experiment when:

- target rank is reached;
- the bounded region completes dry;
- the stage times out and the same geometry is no longer justified;
- another method is now more appropriate.

A target rank is a control condition, not a prediction that the curve has that rank.

## After Target

When a new point is found:

1. verify exact storage;
2. run/inspect independence;
3. update the rigorous lower bound only through certification;
4. decide whether to keep searching or switch to rank bounds.

## Related documentation

- [Search](search.md)
- [Independence](independence.md)
- [Quartics & Coverings](quartics.md)
- [Descent & Rank Bounds](descent.md)
