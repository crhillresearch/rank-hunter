# What Counts as Proof?

Rank Hunter separates **search evidence** from **mathematical proof**. This distinction affects the UI, the database, Pipeline stages, plugin contracts, and how results should be reported.

The safest rule is:

> A computation may decide where Rank Hunter searches without being allowed to change the rigorous rank state.

## Evidence levels

### Heuristic / numerical signal

Examples:

- Nagao-style score;
- prime-pattern score;
- numerical height-lattice geometry;
- a “promising” Family specialization;
- a search-model complexity score.

Use these signals to rank or schedule work.

They do not prove rank, independence, or the existence of an unseen point.

### Exact rational point

A point becomes exact evidence only after its rational coordinates are reconstructed and verified on the intended exact elliptic-curve model.

A point may have been discovered through:

- ratpoints;
- a Family section;
- a quartic/covering map;
- descent;
- a plugin worker;
- external import.

Discovery source does not by itself determine independence.

### Independent exact points

Exact point count is not Mordell–Weil rank.

Rank Hunter's independence path checks whether candidate points add directions beyond the current rigorous witness basis. Hard cases can use saturation-assisted strategies, but an inconclusive certificate is not converted into success.

### Rigorous lower bound

A rigorous lower bound \(r\) means Rank Hunter has evidence supporting

\[
\operatorname{rank} E(\mathbf Q) \ge r.
\]

Typically this comes from an exact certified independent subgroup of size \(r\).

The stored live lower bound is reduced from evidence; it is not supposed to be a manually trusted label.

### Rigorous upper bound

A rigorous upper bound \(u\) means an accepted rigorous method supports

\[
\operatorname{rank} E(\mathbf Q) \le u.
\]

Rank Hunter's rank pipeline is PARI-first in normal automatic mode, with bounded fallbacks depending on availability, failure mode, and whether explicit escalation was requested.

Upper-bound evidence is stored separately from lower-bound evidence.

### Exact rank

Exact rank is established when the rigorous interval closes:

\[
r_{\mathrm{lower}} = r_{\mathrm{upper}}.
\]

A statement such as “rank 12” should mean this equality has been established if it is being presented as exact rank.

If only the lower bound is known, write **rank ≥ 12**.

## What a timeout means

A timeout means only that the configured computation did not finish within its hard budget.

It does **not** mean:

- no point exists;
- the rank did not increase;
- a covering is insoluble;
- the candidate is bad;
- the upper bound equals the current lower bound.

Timeouts are intentionally preserved as inconclusive operational outcomes.

The same rule applies to worker crashes and engine errors.

## Common misreadings

| Observation | Incorrect conclusion | Correct interpretation |
| --- | --- | --- |
| 20 stored points | rank = 20 | points may be dependent |
| Nagao score is large | rank is high | candidate is heuristically interesting |
| numerical height matrix looks full rank | rigorous independence | use exact certification |
| saturation index is nontrivial | rank increased | subgroup/index changed; rank may be unchanged |
| point search found nothing | no rational point exists | none found in the bounded searched region |
| descent timed out | rank equals lower bound | unresolved |
| ICARM says rank 31 | local proof of rank 31 | external reference until evidence is reproduced/imported appropriately |
| plugin metadata says generic rank 17 | every specialization has exact rank 17 | specialization requires its own evidence |

## Evidence conflicts

Rank Hunter can detect a contradictory state, for example a rigorous upper bound below an already stored rigorous lower bound.

That should be treated as an evidence conflict requiring investigation, not automatically “resolved” by picking one number.

Possible causes include:

- wrong model transport;
- stale or malformed imported evidence;
- an engine/wrapper bug;
- mismatched provenance;
- a bad certificate.

The curve's history and evidence records should be inspected before further promotion.

## Generic families vs. specializations

A generic family may have known rational sections over \(\mathbf Q(T)\). Their specializations can provide rational points on a fiber, but Rank Hunter still verifies the specialized points on the exact fiber.

When a generic independence argument is being made from a good specialization, the proof direction must be explicit. Generic metadata by itself is not a substitute for specialized exact point verification.

## External data

Catalogs and read-only corpora are reference data.

Rank Hunter deliberately does not convert an external rank claim into local rigorous evidence merely because it appears in a trusted catalog.

External exact coordinates may be imported and checked, but the resulting local claim is based on the local verification path.

## Plugin boundary

Plugins may provide:

- equations;
- exact sections;
- search geometry;
- candidate scores;
- coverings;
- search workers;
- optional workspaces.

They do not bypass Rank Hunter's evidence rules.

A plugin cannot make a heuristic result rigorous simply by labeling it “rank.”

## How to report a curve

Prefer statements such as:

- “Rank Hunter certified 14 independent exact points, so rank ≥ 14.”
- “PARI produced a rigorous upper bound 14; together with the lower bound, rank = 14.”
- “The covering search timed out after 300 s; no conclusion was drawn.”
- “This specialization scored highly under the stored Nagao-style heuristic and was selected for deeper search.”

Avoid statements such as:

- “The rank is probably 18, so call it rank 18.”
- “No point was found, therefore there is no 18th generator.”
- “The external database says rank 20, therefore Rank Hunter proved rank 20.”

## Related documentation

- [Rank & Evidence](../mathematics/rank.md)
- [Independence](../manual/independence.md)
- [Descent & Rank Bounds](../manual/descent.md)
- [Data Model](../reference/data-model.md)
