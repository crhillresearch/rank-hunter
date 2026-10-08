# Data Model

Rank Hunter stores durable research state in SQLite. The default database is `rank42.db`.

This page documents the conceptual model. Column-level schema evolves through migrations, so developers should inspect the release schema when writing migrations or direct SQL.

## Core entity relationships

```text
Campaign
  ├── Candidate Pools
  │     └── Candidates
  │            └── retained Curve (optional)
  ├── Jobs / Pipeline Runs
  └── research context

Curve
  ├── Points
  ├── Rank Evidence
  ├── Lattices
  ├── Quartic Searches / Coverings
  ├── arithmetic metadata
  └── Jobs / provenance
```

## Curves

A curve row is durable scientific identity.

Important concepts include:

- exact five-term Weierstrass model;
- family/parameter;
- status;
- plugin/family provenance;
- native fiber/chart identity;
- cached/display metadata.

Do not use a candidate row as the authoritative curve model.

## Points

Points are attached to one curve.

Conceptually important fields include:

- rational `x`, `y`;
- exact-verification flag;
- source/role;
- independence status;
- rigorous-independent flag;
- search reference;
- plugin/provenance metadata.

The point table can contain exact points that are not part of the rigorous witness basis.

## Rank evidence

Rank evidence records proof-oriented engine results independently of the curve's display state.

Evidence can include:

- rigorous lower;
- rigorous upper;
- exact rank;
- engine and version;
- evidence type;
- status;
- exact model identity;
- points used/found;
- options;
- assumptions;
- elapsed time;
- metadata.

Core reduces evidence into the current curve research state.

## Current rank state

The live curve state conceptually contains:

- `rigorous_lower`;
- `rigorous_upper`;
- `exact_rank`;
- inconsistency/conflict state.

If an upper bound is lower than the rigorous lower bound, the state is inconsistent and should be investigated.

## Candidate Pools

Pool rows describe a persistent population and its generator provenance.

Important concepts:

- name/id;
- plugin/version;
- Family spec;
- generation JSON;
- file/adapter/manifest fingerprints;
- source path;
- candidate count;
- status/timestamps.

## Candidates

Candidate rows are ordered members of one pool.

They can store:

- rank order;
- parameter;
- optional source coordinates;
- score;
- prime score provenance;
- queue status;
- linked curve id;
- native Family/fiber identity;
- chart identity;
- plugin metadata.

Deleting a pool removes its candidate queue rows but does not delete retained curves.

## Native fibers and charts

Family search can distinguish a mathematical fiber from a particular search chart/parameterization.

This prevents two exact chart representations of the same Family fiber from being mistaken for unrelated scientific objects.

Where available, provenance can include:

- native family key;
- native parameter;
- native family spec;
- chart id;
- chart parameter;
- chart map fingerprint.

## Jobs

Jobs are operational records.

They should answer:

- what command/worker was launched;
- with which configuration;
- when;
- under which campaign/pool/curve;
- status;
- log/result.

A job result may contain scientific evidence, but job completion itself is not evidence.

## Campaigns

Campaigns group related research work.

They provide context/provenance, not rank proof.

An active campaign can be pinned in the UI and associated with newly launched work.

## Pipeline state

Pipeline persistence separates:

- reusable stage definition/configuration;
- Pipeline Run;
- per-candidate stage state;
- strategy substeps;
- child derivations.

This supports resume and makes transformed populations auditable.

## Lattices

Lattice records persist height-pairing work tied to a curve and a basis/source.

Treat precision, source basis, and engine metadata as part of the result identity.

## Quartic searches

Pointed-quartic work persists search model identity, anchor information, search bounds/status, hits, and map-back diagnostics.

This allows timeout/partial coverage to remain distinguishable from completed zero-hit searches.

## Coverings

A generic exact covering record can include:

- curve id;
- quartic coefficients;
- exact map expressions back to the curve;
- optional lattice/hole provenance;
- schema version;
- metadata/status.

Storage of an exact map does not automatically certify a Selmer-class claim.

## Settings

User/runtime settings live in the settings tables and are validated against the typed settings registry.

Application state and dispatcher heartbeat state are intentionally separate from user settings.

## Plugin state

Plugin state records whether a plugin is enabled/disabled/invalid and stores validation information.

Scientific provenance on curves/pools also records plugin version/fingerprints so later plugin upgrades do not erase origin information.

## External catalogs / corpora

Reference/catalog data is stored separately from local rigorous evidence.

Importing or browsing an external rank label should not silently mutate local proof state.

## Direct SQL

Direct SQL is useful for debugging and research, but follow these rules:

1. back up the database first;
2. do not hand-edit rigorous rank fields to “fix” a result;
3. prefer append/promotion APIs for evidence;
4. use migrations for schema changes;
5. preserve foreign-key integrity;
6. distinguish orchestration cleanup from scientific deletion.

## Database backup

For serious work, back up before:

- release upgrades;
- migrations;
- large imports;
- manual SQL;
- recovery operations.

Prefer SQLite's backup mechanism or copy only while writes are stopped.

## Developer rule

When adding a new scientific engine, persist:

- exact input model;
- engine/version;
- configuration;
- status;
- timeout/error classification;
- exact returned objects;
- proof assumptions;
- elapsed time.

Do not reduce a rich scientific result to one mutable integer column.
