# Search

Search turns a large population of possible curves or family parameters into a smaller set worth retaining and analyzing.

The main rule is to separate **candidate ranking** from **rank proof**. Search is allowed to be aggressive; evidence promotion is not.

## Search modes

### Family search

Use **Search → Family** when an installed Family plugin describes the curve construction.

A Family plugin can provide:

- exact specialization model;
- modular point-count routine;
- candidate-generation controls;
- known exact generic sections;
- Family Search worker;
- Target Search worker;
- PGL2 charts or other exact transforms;
- presets and recommended budgets.

The plugin owns family-specific mathematics. Core owns persistence, orchestration, and proof boundaries.

### General search

Use **Search → General** when the search is not tied to a Family plugin.

For short Weierstrass models \(y^2=x^3+Ax+B\), the General Hunt scoring path can compute exact small-prime point counts and a project-specific Nagao-style score. The score is stored with provenance and is used for ranking, not proof.

See [Nagao Scoring](../mathematics/nagao.md).

### Auto

Use **Auto** when you want a prebuilt strategy rather than choosing Pipeline stages manually.

Auto is orchestration, not a different evidence standard.

## Candidate generation vs. point search

Candidate generation should usually be much cheaper than rational-point search.

A common workflow is:

```text
generate 100,000 parameters
→ retain best 1,000 by cheap score
→ search points on 100
→ exact-certify retained curves
→ deep-search the best few
```

The actual numbers depend on the Family and hardware.

## Point-search bounds

Bounded point searches may restrict:

- rational height;
- denominator band;
- affine chart;
- point-search model;
- per-search timeout;
- CPU/GPU backend.

A completed no-hit search is local to those bounds.

For rational \(x=a/b\) in lowest terms, Rank Hunter's direct ratpoints integrations use bounded numerator/denominator regions defined by the selected model/search chart. Changing chart can change the apparent coordinate height dramatically.

## Search models and exact map-back

Some searches operate on a transformed model or chart.

A hit only becomes useful when Rank Hunter can map it back exactly to the stored elliptic curve and verify the resulting rational point.

This is especially important for:

- minimal-model point searches;
- Family transforms;
- quartic/covering searches;
- isogeny or twist workflows;
- PGL2 parameter charts.

## CPU and GPU ratpoints

Rank Hunter can use the vendored CPU ratpoints implementation and, when installed and validated, the CUDA GPU backend.

Backend choice affects speed. It does not affect the proof standard.

Use **Diagnostics** and **Settings** to confirm which executable is active.

## Timeouts

Every expensive search should be thought of as:

> search this exact region with this exact engine for at most this long.

A timeout means the region was not fully resolved under that budget.

Do not record “0 points” merely because the process timed out.

## Retention

A search may materialize or retain a curve when it produces enough durable evidence or meets the configured retention policy.

Candidate rows and curve rows are separate. A Candidate Pool can be deleted without deleting retained scientific curves.

## Re-running a population

If you want to compare two strategies, reuse the same Candidate Pool when possible.

That preserves a common population and makes differences attributable to search configuration rather than candidate generation.

Use a new Pipeline Run or Job for the new strategy so stage provenance remains separate.

## Resume

Candidate pools store per-candidate state. Pipeline runs and jobs store their own execution state.

Prefer resuming the same durable object when the system supports it rather than recreating an equivalent-looking run from scratch.

## When to switch to Target

Use **Target** instead of broad Search when:

- only one or a few curves remain interesting;
- you want much larger point-search budgets;
- you need denominator-band or family-specific geometry;
- you want to continue from an existing exact basis.

See [Target Search](target.md).

## When to stop searching and certify

Once a curve has a useful exact lower bound, additional brute-force point search may be less valuable than:

- independence cleanup;
- saturation;
- descent/rank bounds;
- lattice analysis;
- coverings.

Use [Analysis Tools](analysis.md) to choose the next step.
