# Changelog

All notable changes to Rank Hunter are documented here.

## [0.9.2] — 2026-10-08

**Initial Public Release**

Rank Hunter 0.9.2 is the first public release of the current Rank Hunter platform.
It brings together the accepted v0.9.x core, the audited official plugin
ecosystem, reproducible Axiom presentation, and the supported installation
workflow into one release-ready research environment.

### Added

- Added a one-command Ubuntu/Linux installer that prepares the supported runtime,
  initializes the database, builds the pinned point-search engines, installs the
  official plugin set, and leaves the checkout ready to launch.
- Added the initial audited official **20-Family** publication set through
  `rh-plugins`, covering Campbell, DMT, Elkies, Elkies–Klagsbrun,
  Fermigier–Mestre, Fibonacci, Kihara, Kloosterman, Mazur-torsion,
  Mestre/Fermigier, several Dujella–Peral families, and Nagao's
  `j = 1728` twist family.
- Added complete current search adapters for **Elkies X1092**, including the
  accepted family/search integration needed to use it through ordinary Rank
  Hunter workflows.
- Added **Fiber Atlas**, a read-only family-research Workspace with parameter
  landscapes, denominator/Farey views, rank-growth views, search-yield views,
  bounded large-family projections, and reproducible selection handoffs back to
  ordinary Rank Hunter search surfaces.
- Added proof-conservative Fiber Atlas family-baseline semantics: exact generic
  rank produces a true rank-jump label, while lower-bound-only families report
  excess above the recorded generic lower bound without overstating what has
  been proved.
- Added **Twist & Isogeny Lab**, a read-only Workspace for exact rational
  isogeny structure and bounded quadratic-twist neighborhoods, with exact
  arithmetic metadata, stored-evidence joins, retained-curve handoffs, and
  explicit separation between exact arithmetic, stored rank evidence,
  heuristics, and inconclusive timeouts.
- Added large-curve resilience to Twist & Isogeny Lab through bounded complete
  class timeouts and exact one-hop prime-degree isogeny probes that preserve
  completed results while leaving timed-out degrees unknown.
- Added a compact installed-plugin inventory to **Settings → About**.
- Bundled the **Axiom** theme directly with Rank Hunter Core so the accepted
  Light/Dark presentation ships reproducibly with the application.

### Changed

- Reworked the plugin-management surfaces so Family, Feature, and Workspace rows
  use clearer lifecycle/status presentation, disabled Families remain fully
  legible, Family detail actions match Extension detail actions, and redundant
  Workspace route chips / historical Extension clutter are removed.
- Simplified **Settings → Runtimes** around an immediate runtime-status summary,
  explicit required/optional executables, on-demand runtime tests, and separate
  time-budget controls while preserving the existing save-time health gate.
- Simplified **Settings → Services** so it owns service configuration only;
  System Diagnostics remains in its canonical System location.
- Made theme/navigation behavior reproducible from the bundled Axiom assets
  rather than depending on ad-hoc local presentation state.
- Refined **Jobs Results / Hunt Recap** presentation so retained research results
  stay readable without duplicating low-value parsed payload detail.
- Reconciled Family Pipeline candidate limits with the actual retained
  population, keeping candidate counts and downstream stage behavior consistent.
- Made Pipeline quartic-hit notices retention-aware so the UI distinguishes
  newly retained hits from rediscovered/non-retained results instead of
  overstating search yield.
- Changed Dashboard **Evidence Depth** counts to show cumulative numbers of
  retained curves that have reached each evidence stage while keeping the
  horizontal partition based on deepest completed stage.
- Improved Fiber Atlas large-database behavior by making expensive search-yield
  and bad-prime projections explicit/lazy and bounding visualization/table
  projections for families with thousands of stored fibers.
- Gave Twist & Isogeny Lab a clearer researcher-facing flow:
  **Source curve → Research mode → Selected object → Neighborhood results**,
  while keeping all arithmetic on-demand, cached, read-only, and outside core
  rank-proof ownership.

### Fixed

- Fixed **Power off Rank Hunter** so the server terminates cleanly and returns
  control to the launching terminal instead of hanging indefinitely at
  `Stopping...`.
- Fixed the Curves detail page duplicate **Next useful action** render that could
  raise a Streamlit duplicate-element-key failure.
- Fixed Extension breadcrumbs so hosted Workspace/Feature pages display their
  human page title instead of an internal `extension:...` route identifier.
- Fixed Family Pipeline candidate-top reconciliation so a configured top count
  no longer silently disagrees with the actual retained candidate population.
- Fixed Fiber Atlas large-family freezes caused by eager bad-prime parsing and
  unbounded trial division on very large stored prime values.
- Fixed Fiber Atlas rank-growth semantics so a specialization's own promoted
  lower bound is never reused as the family generic baseline.
- Fixed large-coefficient Twist & Isogeny Lab behavior so complete-class
  timeouts remain explicitly inconclusive and exact prime-degree probe results
  survive independently.
- Fixed a Sage/Python interoperability issue in the isogeny probe by converting
  probe degrees to Sage integers before exact isogeny checks.

## [0.9.1] — 2026-10-01

This release consolidates the accepted v0.9.1 work. Superseded or reverted visual experiments are omitted below unless they materially explain the final behavior.

### Added

- Added a full **Quartics research workbench** with curve-level intelligence, rank-impact accounting, proof-aware point inventories, selected-covering research cards, search-efficiency/funnel views, raw audit data, and direct Independence handoff.
- Added exact quartic-model fingerprints and deterministic binary-quartic arithmetic projections, including normalized coefficient identity plus exact `I`, `J`, and discriminant where applicable.
- Added conservative Quartics local-evidence projection, bounded search-coverage/overlap accounting, empirical-yield summaries, search-depth views, and evidence-driven next-attack guidance without treating finite search failure as proof.
- Added Quartics exact-model comparison, provenance/reproduction chains, portable replay recipes, point-discovery attribution, new/pre-existing/rediscovered classification, point-height quality views, richer sorting/filtering, and research-accounting summaries.
- Added immutable Quartics discovery observations plus Pipeline Run / Candidate / Stage / pointed-attempt identity so replay/cache behavior and historical provenance remain inspectable.
- Added a shared active-curve selector/filter experience across the specialist Analysis surfaces that use one active curve, while preserving page-specific exceptions.
- Added always-visible Raw Job logs with isolated live refresh so active logs can update without polling/re-running the entire page.
- Added a normal-flow page-end runway for long research pages without reintroducing the rejected global fixed runway experiment.
- Added a researcher **Library ingestion workbench** with a durable Library vault, bounded artifact previews, explicit field-mapping drafts, normalized projections, guarded deletion, Candidate Pool promotion, exact mapped-point validation, Library-wide browsing, and explicit Curve/model handoff.
- Added a dedicated curve portrait built from stored exact points and a torsion-aware Hall of Fame / record view.
- Added bounded batch Curve Arithmetic backfill for missing retained-curve arithmetic, with explicit progress/failure accounting rather than render-time computation.
- Added ICARM Catalog torsion filtering and an `Only best` view while preserving external-reference semantics.
- Added first-class Dashboard torsion research coverage, distribution, torsion × exact-rank summaries, record views, and anomaly/attention signals sourced from authoritative local state.
- Added **Analysis → Saturation** as a dedicated classical Mordell–Weil saturation workbench with bounded prime ranges, prime-by-prime ladder mode, exact basis provenance, persisted index/regulator evidence, and Jobs-owned execution/log handoff. Saturation remains rank-neutral by itself.
- Added Jobs-owned **Auto scheduling** using the same frozen Pipeline launch preparation as immediate Auto runs, preserving Campaign/Pipeline provenance and scheduler ownership.
- Added core UI-server **Restart** and **Power** controls with confirmation dialogs and one core server-lifecycle owner.
- Added canonical page breadcrumbs, shared responsive/overflow safeguards, semantic Dark-mode native-control styling, and appearance-aware chart/custom-component palette handoff.
- Added first-party Rank Hunter application/icon assets and the shared route/page icon infrastructure used by the refined shell.

### Changed

- Rebuilt **Curves** into a faster research inventory with Campaign filtering, Browse / Hall of Fame / Import navigation, Campaign-style drill-in/back behavior, filter-aware research charts, improved arithmetic/catalog grids, richer Overview arithmetic, evidence consolidation, and a real research-history timeline.
- Restored **Points** to visible navigation while keeping Curves/Analysis handoffs and scientific ledger ownership intact.
- Simplified Campaign lifecycle presentation to one primary play/activate action, active-only sticky context, and a clearer active Campaign landing highlight.
- Moved Plugins into **Manage**, simplified plugin navigation, and later rebuilt the manager around compact line tabs, row-oriented Family/Feature/Workspace summaries, explicit lifecycle/validation status, dedicated About pages, visible research/provenance sections, and separate Features vs Workspaces landing groups.
- Reorganized **Settings** into compact `Runtimes / Services / About` subviews, kept expensive health probes explicit, retained ICARM credential ownership under Services, and kept Queue/Dispatcher/Scheduler lifecycle controls Jobs-owned.
- Reworked the Dashboard reading order, restored the research Landscape charts, made Attention bounded/resolvable, consolidated the supporting rail, integrated research-owner actions into Research Frontier, and simplified the Leaderboard torsion filter.
- Restored truly global `ALL` Dashboard research charts while allowing simple Candidate Pool narrowing without silently redefining the global population.
- Absorbed the standalone Case Board into Curves and retired the separate Research Case UI while preserving the Campaign Research Notebook.
- Hardened Dashboard/Jobs dispatcher truth around persistent-service state plus runtime heartbeat, including degraded/offline distinctions and stale-service self-healing.
- Simplified **Auto** into a one-button-first research surface, then refined it into `Auto Search` with Family/Torsion target selection, aligned Family/Variant and Goal/Retention rows, explicit `Schedule`, and `Start Auto Search` / `Schedule Auto Search` actions.
- Kept Candidate Pool maintenance/transfer ownership in Candidates → Pools and removed duplicate Family Search maintenance UI.
- Canonicalized **Candidates** under Search, fixed legacy handoffs/rerun state, switched Generate / Pools to compact line tabs, removed the second Browse / Import & Export tab row, and replaced it with an inline `Import/Export` action plus a Back-to-Pools transfer subpage.
- Switched Curves, Libraries, Catalogs, Plugins, Settings, and other peer subviews toward compact line navigation instead of full-width page-button navigation where appropriate.
- Centered/narrowed General Search and refined Search/Auto layout without changing Pipeline execution ownership.
- Changed Jobs post-navigation spacing to the same small breathing room used elsewhere in Manage.
- Compacted Pipeline module-category expanders by constraining module-row component hosts to their actual 54px row height, removing blank vertical blocks when later categories open.
- Removed the user-facing **Parsed scientific result** JSON presentation while preserving structured parsing, dynamic stats, Hunt Recaps, Raw logs, and stored evidence.
- Made Pipeline Search Target/Modules surfaces use semantic card backgrounds and made no-Campaign Pipeline Runs explicitly show global run history.
- Moved Database **Legacy repair** into a collapsed disclosure while preserving every warning, maintenance lock, rollback path, and explicit repair action.
- Standardized the release presentation around Axiom Light/Dark semantics, appearance-aware native controls/tables/charts/components, and the finalized shared Material route/action icon set.

### Fixed

- Fixed Candidate Pools tab/rerun state so clicked navigation wins over stale session state and legacy Import / Export handoffs still land on the canonical Pools owner.
- Fixed Curves inventory performance with bounded/faster landing queries and follow-up optimizations, without moving scientific computation into page render.
- Fixed Curves grid-state/column-order and detail-flow inconsistencies, including restoring the intended Next Action placement before curve actions.
- Fixed Dashboard interaction clutter, status wording/capitalization, Leaderboard filter behavior, and plugin-health presentation while preserving filtering semantics.
- Fixed dispatcher liveness reporting when the DB heartbeat mirror lags the real runtime heartbeat; active-but-unhealthy service states now report degraded rather than falsely offline.
- Fixed Dark-mode sidebar arrows, search/text/date/time/native controls, tables, popup/select surfaces, page-nav states, Campaign/Pipeline custom components, and Quartics/Lattices charts without regressing Light mode.
- Fixed responsive layout/overflow problems across ordinary column rows, Campaign headers, Curves grids, DataFrames, and embedded component surfaces.
- Fixed breadcrumb clipping/spacing edge cases, including Campaigns nesting and the literal escaped-chevron rendering bug.
- Fixed Auto scheduling/launch presentation regressions while keeping immediate launches Pipeline-owned and durable schedules Jobs-owned.
- Fixed Plugins manager lifecycle-key collisions and historical/unavailable presentation while preserving validation, provenance, trust, and archive/restore semantics.
- Fixed Jobs results regressions caused by stale source contracts after Raw log/Parsed-result ownership changes; production Results/Jobs ownership remains unchanged.
- Fixed the Pipeline module expander blank-space regression without changing module order, parameters, serialization, validation, or execution.
- Fixed Candidates transfer-page hierarchy so Import/Export is explicit and aligned with Stored pools rather than presented as a second tab layer.


## [0.9.0] — 2026-09-28

Major architecture, research-workflow, Pipeline, scientific-state, and release-hardening checkpoint. This entry describes the accepted implementation delivered in the v0.9.0 series.

### Added

- Added one authoritative **Curve Research State** projection for rigorous lower bounds, rigorous upper bounds, exact rank, rank conflicts, conditional bounds, and compatibility-field projection.
- Added one authoritative **Curve Arithmetic** state that separates locally computed/persisted invariants from external ICARM/LMFDB reference values and retains explicit provenance/conflict state.
- Added typed **Candidate ranking provenance** so heuristic candidate scores retain their ranking kind, source, configuration, and reproducibility metadata.
- Added shared exact-point/rank promotion services so point verification, independence, saturation, rigorous lower-bound promotion, and exact-rank closure use the same evidence semantics.
- Added shared **Launch Context** resolution with durable Campaign inheritance, plugin/variant identity, target/source metadata, Feature ids, and source-pool provenance.
- Added durable Pipeline definition revisions, deterministic content hashes, immutable Run snapshots, schema/catalog fingerprints, runtime provenance, and drift detection.
- Added durable Pipeline editor draft recovery and dirty-state tracking without weakening the protected Pipeline editor UX.
- Added typed Pipeline Run Manifests that freeze core, catalog, plugin, Family, adapter, Feature, executable, and source fingerprints needed for reproducibility.
- Added durable branch/checkpoint state for resumable deep/constructive Pipeline strategies and transformed-child lineage.
- Added explicit timeout-envelope/budget planning for expensive Geometry and deep-strategy stages without presenting those envelopes as ETAs.
- Added typed artifacts and verifier-owned contracts for constructive section-height, trace/division, square-condition, transform, isogeny-transfer, and related derived-curve stages.
- Added durable covering/search attempt records, branch outcome aggregation, map-back diagnostics, stop reasons, and bounded failure samples for plugin/geometry research stages.
- Added process-isolated research hooks/workers so plugin Geometry, higher-descent, p-adic, analytic-upper, and other expensive scientific paths cannot silently bypass host timeout/failure boundaries.
- Added **Conditional Analytic Rank Upper** as Pipeline module 56 using a bounded Mestre–Bober/Sage analytic-rank upper; GRH-conditional evidence remains separate from rigorous algebraic rank.
- Added **Cubic Class-Group / 2-Selmer Bound** as Pipeline module 57 with Brumer–Kramer conditional and unconditional proof modes, including separate conditional Mordell–Weil upper evidence.
- Added **Cassels–Tate Selmer Refinement** as Pipeline module 58.
- Added **Isogeny Descent** as Pipeline module 59.
- Added **Covering Minimization & Reduction** as Pipeline module 60.
- Added **Local Solubility & Covering Height Planner** as Pipeline module 61.
- Added **Specialization Injectivity Certificate** as Pipeline module 62.
- Added a **Research Inbox / Case Board** Analysis Work Center for active research questions, unresolved proof/search state, and next-action planning.
- Added one semantic active Analysis-curve context shared by the Work Center and specialist Analysis pages while retaining legacy session keys as compatibility inputs.
- Added a deterministic **Analysis Planner** and reusable method-history/budget/basis-fingerprint services instead of page-specific next-step logic.
- Added a dedicated **Rank Proof** workbench for rigorous rank intervals, upper-bound attempts, and exact-rank closure work.
- Re-scoped **Independence** around exact-point dependence and finding a new Mordell–Weil direction rather than acting as a second rank-proof page.
- Added an **MW Geometry** workbench backed by Point Ledger/lattice state, with exact-candidate handoff to Independence and a conservative numerical scheduling hint back to Target.
- Added a persisted **Quartics** Analysis workbench sharing the same exact quartic/covering services used by Pipeline execution.
- Added a durable **Research Timeline** and lightweight researcher-controlled case state without inventing mathematical evidence from human workflow labels.
- Added family-level `family_evidence` reduction for rigorous generic-rank certificates, conflict handling, and certified specialization rank-jump analysis.
- Added a Campaign-aware Analysis handoff while keeping Campaign ownership under Manage.
- Added a Campaign **Research Notebook** built on the append-only Campaign journal rather than a second note authority.
- Added reproducible **Curve Research Bundles** with authoritative rank/arithmetic state, exact points/witness provenance, Pipeline Run Manifests, plugin/family/runtime fingerprints, optional-artifact status, and local-vs-reference arithmetic separation.
- Added **Campaign Research Bundles** preserving Campaign/Run/Job/Curve/Notebook/Landscape relationships and selective Notebook handoff.
- Added **Landscape** as the comparative research environment, with authoritative dimension definitions, reproducible/frozen cohorts, saved analyses, family/native parameter identity, configurable prime-cluster features, and Dashboard handoff.
- Added manager/detail Plugin UIs for **Families** and **Extensions/Features**, including search/filter/sort, lifecycle/health status, structured Validation, provenance, source paths, variants/presets/capabilities/Libraries, pages/hooks, and trust disclosure.
- Added the versioned **FamilyAdapterV1** public Plugin SDK, `ExtensionContextV1`, `FeatureContextV1`, and a compatibility bridge for legacy module-style Family adapters.
- Added versioned Feature-hook and Extension-navigation contracts with canonical ids, compatibility aliases, host-side canonicalization, and explicit deprecated-alias reporting in validation.
- Added non-destructive Plugin **Archive / Restore**, fingerprint-aware **Needs revalidation**, Superseded handling, and read-only **Unavailable** historical provenance when an installed package disappears.
- Added a shared cross-surface Family preset contract spanning Candidates, Family Search, and Target, including separate option-schema validation and durable applied-preset provenance with exact effective settings and Modified state.
- Added explicit executable-code **Trust & execution** disclosure for Family/Extension/Feature Plugins; validation is a trust boundary, not a security sandbox.
- Added a separate presentation-only Theme contract with schema validation, path containment, native Streamlit theme validation, shared startup/runtime resolution, and Active/Selected restart semantics.
- Added shared release-link constants for Plugin and Theme install affordances so page code no longer owns distribution URLs.
- Added a bounded shared database-check service for read-only PRAGMAs and conservative Advanced SQL classification.
- Added one shared **maintenance lock** covering destructive/maintenance operations and dispatcher claim/start paths.
- Added schema-aware backup validation, guarded restore, automatic pre-restore backup, and bounded browser-download behavior.
- Added explicit **Fast / Deep** Diagnostics release states plus a long-running Full Database Verification maintenance Job for release-critical verification.
- Added Queue, Dispatcher, Scheduler, migration, settings, evidence, and plugin-boundary health to Diagnostics while keeping Diagnostics read-only.
- Added a typed Settings registry/runtime resolver and separated application state such as the current Campaign from ordinary settings storage.
- Added lazy Dashboard **Overview / Research / Operations** views, authoritative snapshot services, a cross-system Attention Feed, exact/bounded Operations aggregates, and owner-page navigation.
- Added a bounded `dashboard.after_header` Feature-host contract limiting Dashboard contributions to compact passive regions.
- Added a shared Sage-preloaded Streamlit launcher so the UI starts with Sage initialized on the process main thread while retaining Streamlit compatibility.

### Changed

- Converged all **new Search execution** onto Pipeline infrastructure: Auto, General Search, FREE Family Search, Plugin Family Search, Target, and Candidate generation now launch through Pipeline-owned paths.
- Retained retired native General/Family/Target/Auto engines only as compatibility paths for historical same-work Resume; fresh Retry, new schedules, and fresh direct invocation are rejected.
- Extracted reusable science from legacy Search engines into shared services for PARI rigorous upper bounds, curve materialization/retention, family baselines, specialization seeds, classical coverings, plugin Geometry, and authoritative arithmetic/rank state.
- Made Plugin Search configuration variant-aware and declarative, with Pipeline-native Auto profiles and manifest-owned presets as the source of truth.
- Made Nagao rescoring repeatable for progressive screening pipelines and aligned high-rank family presets with multi-round survivor funnels.
- Hardened the audited 55-module Pipeline catalog across Candidates/Control, Primes, Arithmetic, Points, Geometry, Evidence, Transforms, Constructive methods, and compound Strategies, then expanded the catalog through module 62.
- Made candidate population-stage selection commits atomic and centralized candidate-stage monotonicity/contract validation.
- Made Corpus/Library filtering consistent for existing Family Candidate Pools and retained exact candidate/native-fiber identity through Pipeline processing.
- Made Stop Goal and deep-strategy decisions read authoritative Curve Research State rather than compatibility rank columns.
- Clarified Adaptive Denominator Ladder outcomes, budgets, survivor semantics, and exact-point inventory handling.
- Tightened Nagao/prime-score identity, prime-table cache completeness, score-domain provenance, and related cached-prime semantics.
- Made saturation, Exact Independence, Final Rigorous Upper, Height-Lattice, Evidence Depth, and LLL/exactness stages record truthful attempt/completion state instead of implying proof from incomplete work.
- Made transformed-curve stages preserve canonical derived-curve identity, score reset/lineage, bounded discovery, typed transform artifacts, child-materialization boundaries, and isogeny-transfer certification provenance.
- Made constructive family stages preserve candidate-local lineage, hard-isolated hooks, square-condition bindings/coverage, durable shell checkpoints, and real-family constructive chains.
- Reworked Search/Target/Analysis/Manage boundaries so **Pipeline owns execution**, **Jobs owns process lifecycle/results**, **Analysis owns interpretation/planning**, and **Curves/Point Ledger/rank evidence own permanent scientific state**.
- Moved scientific Results under **Manage → Jobs → Results** while retaining the legacy Results route as a compatibility alias to the same renderer.
- Removed lifecycle mutation controls from Results so Pause/Stop/Resume/Retry remain Jobs-owned.
- Normalized Pipeline pause/stop/resume/retry through one shared Manage lifecycle service and made Retry create a new Run while Resume continues the original Run.
- Made Queue arbitration—not page-local state—the owner of concurrency/resource decisions.
- Normalized Campaign ownership and Launch Context inheritance across Jobs, Pipelines, Search, Candidates, and research workflows.
- Froze Schedule execution semantics to saved revision/hash/runtime context and prevented later edits from mutating already-scheduled scientific intent.
- Split large Manage page responsibilities into smaller reusable components without replacing the protected Pipeline editor.
- Renamed researcher-facing **Corpora** to **Libraries** and **External Catalog** to **Catalogs / Reference Catalogs** while preserving internal compatibility names where appropriate.
- Reworked Research Library identity around family/native/chart-safe keys, typed ranking provenance, and visible cross-Library conflict semantics.
- Demoted **Point Ledger** from primary navigation to an advanced/contextual scientific ledger while preserving Curves/Analysis handoffs.
- Made local Curve Arithmetic authoritative for local science; ICARM/LMFDB values remain labeled external references and cannot block publication of a fresh exact local invariant.
- Kept top-level arithmetic display compatibility while exposing strict local authority, local provenance/completeness, display fallback, and separate local/reference conflicts.
- Rebuilt Analysis navigation around Work Center, Independence, Rank Proof, MW Geometry, Quartics, Landscape, Timeline, and Rank Jump instead of overlapping Analyze/Descent/Lattice responsibilities.
- Made Rank Jump use completed family-level theorem/evidence records rather than historical Plugin generic-rank metadata.
- Made manual attack and Independence decisions read the authoritative rank reducer instead of stale compatibility columns.
- Rebuilt Dashboard around bounded owner-composed snapshots instead of an all-in-one research page; comparative charts/tables moved to Landscape.
- Removed embedded Dashboard Search execution; Quick Actions are navigation-only.
- Reused one reduced rank-state pass for Dashboard Research/Attention and replaced large row scans with bounded rows plus exact aggregate counts.
- Kept Dashboard snapshot paths read-only and removed whole-page polling/rerun behavior; only compact live shell surfaces poll.
- Pinned **Active Campaign** globally in the shell and centralized global active-work controls.
- Normalized legacy routes before sidebar rendering and retained compatibility aliases without exposing duplicate primary destinations.
- Made shell completion notifications, active-work log tails, theme resolution, and plugin-manifest discovery bounded and rerun-safe.
- Kept Themes outside executable Plugin lifecycle; Themes use **Select / Active / Selected** semantics instead of Enable/Archive.
- Restricted Extension navigation to the explicit public v1 sections; System and Candidates remain core-owned.
- Required privileged Settings/Database Feature injection to satisfy both declared System privilege and a core-owned allowlist; the default third-party allowlist remains empty.
- Kept Plugin removal non-destructive: ordinary UI actions Disable/Archive/Restore rather than recursively deleting scientific package folders.
- Centralized Plugin/Theme install destinations; the later public repository rename was explicitly skipped during the audit.
- Centralized migration ownership, settings truth, maintenance ownership, database verification, and Diagnostics status semantics.
- Made Database Advanced SQL fail closed on unknown/mutable PRAGMA forms while retaining explicitly query-only inspection/check PRAGMAs.
- Made current-schema backup restore require the authoritative schema manifest while older supported backups remain migration-eligible.
- Separated fast bounded health checks from deep release verification so a fast pass cannot masquerade as completed deep verification.
- Kept System navigation at Diagnostics / Database / Settings and kept ordinary Extensions out of privileged System surfaces.
- Updated application-state ownership to use the canonical `current_campaign_id` key through `ui_application_state` rather than settings storage.

### Fixed

- Fixed remaining cases where heuristic, numerical, conditional, timeout, or failed-search results could be mistaken for rigorous mathematical evidence.
- Preserved stronger prior rank evidence across later timeouts, engine errors, unsupported routes, and inconclusive attempts.
- Prevented conditional analytic or conditional class-group/Selmer bounds from populating rigorous upper bounds or closing exact rank.
- Prevented external Catalog arithmetic from silently becoming local scientific authority or blocking publication of exact local arithmetic.
- Prevented conflicting reduced rank state from becoming the Dashboard global best-rank headline.
- Fixed stale decision-time reads of rank compatibility columns in Analysis workers by routing them through the authoritative reducer.
- Fixed family generic-rank evidence reduction so explicit completed exact-generic-rank certificates participate in lower/upper reduction and contradictory family evidence is quarantined.
- Fixed plugin Geometry/covering/p-adic/constructive stages so derivation failures, rejection samples, retry outcomes, map-back failures, coverage limits, and stop reasons remain visible instead of collapsing to misleading success/failure labels.
- Fixed transform stages so malformed/unverified Plugin artifacts cannot silently materialize child curves or transfer proof state.
- Fixed point-centered quartic and MW Growth Loop completion/coverage semantics so dry searches, map-back failures, and bounded exhaustion remain distinguishable.
- Fixed Pipeline stop/checkpoint/resume paths so branch-local progress survives restart without double-applying evidence or losing candidate lineage.
- Fixed candidate/pool selection paths so atomicity, resume offsets, survivor sets, and exact-point counts cannot drift across partial runs.
- Fixed stale Auto active-controls state after legacy UI cleanup by restoring the queued/running controllable-state contract without restoring retired native Auto launch/history code.
- Fixed the Sage/Streamlit startup path so Sage signal initialization occurs on the process main thread and the UI still runs under the selected scientific interpreter.
- Fixed Family Search startup use of shared setting resolution exposed during the Sage launcher work.
- Fixed Plugin manager visibility for missing packages, failed revalidation provenance, Unavailable filtering, and read-only historical interpretation.
- Fixed Plugin validation freshness so manifest/Family/adapter/variant-content drift is visible and failed revalidation does not erase the last successful identity/fingerprint record.
- Fixed Family/Extension manager detail so executable-code trust/source information and structured Validation are visible at the point of use.
- Fixed deprecated Feature-hook/navigation aliases so compatibility is explicit and validation reports which legacy ids an installed Plugin still uses.
- Fixed Theme loading so invalid packages are auditable but excluded from normal selection, path traversal is rejected, and non-theme Streamlit configuration cannot enter a Theme package.
- Fixed Advanced SQL parenthesized-PRAGMA bypasses such as mutable `journal_mode(...)`, `user_version(...)`, `foreign_keys(...)`, and similar forms.
- Fixed dispatcher maintenance safety so the claim/start primitive itself fails closed under an exclusive maintenance lease.
- Fixed backup validation so a database claiming the current schema version cannot pass restore validation with missing required current tables/columns/indexes.
- Fixed Diagnostics witness-basis sufficiency, snapshot freshness, Queue/Dispatcher/Scheduler health visibility, and Fast-vs-Deep readiness wording.
- Fixed stale/prevalidated Extension/Feature objects so privileged System placement is rejected again at render time before Plugin code loads.
- Fixed shell/database/UI refresh paths that could perform hidden cleanup, schema work, or expensive whole-page polling during ordinary rendering.
- Fixed stale application-state regression coverage to assert canonical `current_campaign_id` ownership and explicitly reject the retired `active_campaign_id` key.

## [0.8.10]

### Added

- Added per-run **Hunt Recaps** to Results so ordinary family searches retain candidate-level findings even without an active campaign.
- Added exact native-fiber recovery and export when the recorded family source and reconstructed candidate count agree with the original hunt data.
- Added independently movable Dashboard research cards with persisted custom layout and reset-to-canonical behavior.
- Added compact global active-work and active-campaign surfaces that keep research context visible without dominating the page.

### Changed

- Hardened public-tree/release hygiene by removing stale hotfix manifests, temporary candidate exports, and other development-only artifacts.
- Continued the release-development line from the `0.8.9` frozen baseline.

## [0.8.9] — 2026-09-22

### Added

- Introduced the full **Builder** modular search system with declarative stage contracts, saved recipes, immutable run snapshots, per-candidate provenance, resumable execution, and pipeline-order validation.
- Added reusable Builder sources for family candidates, exact torsion targets, general curves, existing candidate pools, and fixed retained curves.
- Added prime/local arithmetic modules for cached prime tables, multiscale Frobenius scoring, local root numbers, bad-prime fingerprints, torsion mod-p sieving, and local-solubility sieving.
- Added reusable search heuristics including multi-value Mestre-Nagao, Frobenius persistence, small-point density, point-yield persistence, Selmer headroom, and explicit-formula indicators.
- Added repeatable survivor funnels and denominator search stages, including **Select Survivors**, **Denominator Band Search**, and **Adaptive Denominator Ladder**.
- Added **MW Growth Loop** for iterative point-search geometry driven by newly discovered exact points.
- Added transform/fan-out modules for quadratic twists, targeted twists, rank-jump/base-change searches, parameter pullbacks, isogeny walks, and covering/Selmer branches with durable parent-child lineage.
- Added constructive family modules for section-height shells, trace-section construction, forced bisection/division, and square-condition specialization.
- Added advanced research modules for higher descent, Selmer-element fan-out, p-adic covering search, full saturation/index recovery, height-lattice reduction, and surface/fibration switching.
- Added editable research presets including **Aggressive Rank Hunter**, **Specialization Trickster**, **Generator Breaker**, **Prime Sieve Funnel**, and **Geometry Grinder**.
- Added a dedicated **Pipelines** management view for editing and deleting saved recipes without destroying historical run snapshots.
- Added a **Large-Height Generator Hunt** strategy with search-first ordering, MW-feedback geometry, denominator-band search, bounded late saturation, and explicit substep runtime/status reporting.
- Added a bounded **Upper-Bound Rescue Ladder** using quick rigorous bounds, exact Q-isogenous retries, optional short 2-Selmer, known-basis coverings, and higher-descent hooks.
- Added large-curve failure classification and recovery paths for `MWRANK_SIZE_LIMIT`, `MWRANK_FAILURE`, `PARI_STACK`, and `TIMEOUT`.
- Added standalone bounded saturation of complete rigorous witness bases without requiring a successful full 2-descent.
- Added exact-torsion Auto searches across the 15 Mazur rational torsion possibilities with provider validation, record-aware goals, rigorous-lower-bound stop conditions, and retention floors.
- Added a universal geometry-first search path with two-stage Mestre-Nagao screening, exact family/torsion baselines, bounded rigorous elimination, plugin-native accelerators, generic point-centered quartics, affine fallback, and exact certification.
- Added read-only plugin corpus infrastructure, a global Corpus browser, corpus-aware candidate generation, Builder corpus stages, and family pipeline support for external databases such as `curves2`.
- Added or substantially advanced research plugins for Three Lanterns, the ICARM rank-31/curve-302 family, Elkies high-rank families, Mestre sextuple searches, and related high-rank family research.
- Added durable Campaign research context with global active-campaign state, campaign notes, provenance, Auto/Pipeline prefilling, history, progress views, and safe pause/release behavior.
- Added a durable queued job system with a dedicated dispatcher, resource-class concurrency, crash recovery, legacy queue adoption, process/service lifecycle handling, and queue management.
- Added idempotent scheduled-run occurrences, immutable schedule snapshots, retryable preparation failures, bounded catch-up, and atomic scheduled pipeline enqueue.

### Changed

- Renamed **Build Your Own** to **Builder** and rebuilt it as a blank-by-default composable pipeline workspace.
- Renamed **Auto Hunter** to **Auto** and folded Geometry into Auto as a peer workflow.
- Made Aggressive Rank Hunter capability-driven rather than family-hardcoded; optional family sections, covering fan-out, higher descent, and p-adic search are detected at runtime.
- Reordered aggressive search so point/generator discovery happens before expensive upper-bound work where appropriate.
- Made giant-curve point search tolerant of minimal-model failures through a native-model fast path and exact rational coefficient normalization.
- Reserved denominator-chart capacity around known exact section/witness x-coordinates instead of allowing small chart budgets to collapse to center-zero rescalings.
- Moved resource concurrency and scheduling guarantees out of Streamlit behavior and into the durable dispatcher.
- Kept family-specific symbolic mathematics in plugins while core handles exact QQ verification, persistence, lineage, and evidence reduction.

### Fixed

- Prevented live Streamlit refreshes from rerunning schema/migration writes while scientific jobs are using SQLite.
- Treated engine limits, timeouts, and arithmetic failures as routing/inconclusive outcomes rather than negative mathematical evidence.
- Preserved evidence belonging to curves pruned by later pipeline stages.
- Prevented generic-family theorem metadata from being promoted automatically to specialization-level rank evidence.
- Prevented saturation index gains from being reported as rank gains.

## [0.8.8]

### Added

- Rebuilt **Analysis → Independence** as a research workbench with rigorous-basis inspection, candidate prioritization, exact-certificate history, diagnostics, runtime history, and JSON certificate export.
- Added sequential multi-point certification against a growing rigorous basis so maximal independent subsets can be recovered when a selected point is dependent.
- Persisted exact independence attempts in the generic rank-evidence ledger with model/basis fingerprints and point-level attempt history.
- Added a dedicated **Themes** manager under Plugins with preview metadata and one-active-theme behavior.
- Rebuilt the Dashboard around evidence-aware research telemetry including Best Rank, Curves, Exact Ranks, High-Rank Hits, Local Research Frontier, exact-rank distribution, parameter-space yield, high-rank neighborhood, exceptional-curve radar, conductor records, complexity records, torsion/rank, unresolved curves, MW lattices, and compact search launchers.

### Changed

- Removed theme selection from Settings so Settings focuses on runtime/scientific configuration.
- Kept exact ranks and lower-bound-only curves visually and semantically distinct throughout Dashboard research views.
- Moved dashboard chart implementations into dedicated research-chart components while preserving their evidence semantics.

## [0.8.7.1]

### Added

- Added a generic `rank_evidence` ledger for engine, model, options, runtime, assumptions, rigorous lower/upper bounds, and completion status.
- Added PARI-first rigorous rank-bound orchestration with isolated subprocess timeouts, explicit mwrank escalation, caching, and conservative evidence reduction.
- Added claim-aware family metadata separating historical generic-rank records from reconstructed, section-verified, and verified generic lower bounds.
- Added exact Family PGL2 parameter-chart contracts, native-fiber identity, native-parameter provenance, and exact generic-section mapping to stored models.
- Added organized plugin discovery for family/extension/feature development layouts with symlink/source deduplication.
- Added pinned vendored `ratpoints` and `ratpoints-gpu` runtime discovery/build support.
- Added structured Feature hooks for deterministic command transforms and candidate-priority/gating annotations.
- Added manifest-driven plugin and Extension icons with validation.

### Changed

- Separated Rank Hunter Core from bundled families, extensions, themes, databases, benchmarks, and release-package artifacts.
- Finalized the clean-core navigation structure around Search, Analysis, Data, Plugins, and System.
- Removed Workbench from core and preserved experimental workspace functionality as optional Extensions.
- Established stable semantic `rh-*` UI hooks for themes and plugins.
- Preserved family-specific mathematics behind plugin contracts rather than a hardcoded core family registry.
- Made PARI the default quick rigorous rank-bound engine while preserving mwrank as an explicit/fallback path.
- Required exact rank to be derived only when rigorous lower and upper bounds agree.
- Preserved `UNKNOWN` as distinct from rank zero.
- Advanced the database schema through the rank-evidence/native-fiber hotfix line without rewriting existing curve/candidate evidence.

### Fixed

- Prevented completed-but-gapped PARI bounds from silently triggering expensive mwrank runs unless escalation was requested.
- Prevented numerical height/Gram screens from being promoted as rigorous generator independence.
- Preserved prior scientific evidence across timeout/error/inconclusive engine attempts.
- Fixed family discovery for organized development checkouts and duplicate symlink/source manifests.
- Fixed direct family provenance so curve identity can survive candidate-pool cleanup.

## [0.8.7]

### Added

- Defined a Streamlit-free Feature hook contract for supported search transformations.
- Added compatibility bridges for functional search-command transformations during the hunt/search API transition.

### Changed

- Began the clean-core transition that culminated in `0.8.7.1`.
- Kept hunt-storage compatibility aliases while new code moved toward public helper APIs.

## [0.8.6.4]

### Added

- Added the functional Feature `search_command` bridge used by search overlays such as symmetry reduction.
- Established the semantic `rh-*` theming hook system as the supported presentation API.

### Changed

- Kept Themes presentation-only: CSS/layout may change appearance but not search, persistence, or evidence behavior.

## [0.8.6]

### Added

- Expanded Rank Hunter from family plugins into a broader plugin platform with Families, Features, and Extensions.
- Added direct curve provenance (`plugin_id`, `family_spec`) so family identity survives candidate-pool deletion.
- Added optional full-workspace Extensions and lightweight search-stage Feature hooks.

### Changed

- Mapped older `Resources` extension navigation into the Plugins area for compatibility.
- Moved generic shared behavior toward core APIs while keeping family formulas in plugins.

## [0.8.5]

### Added

- Moved family-specific mathematics into Family plugins, including the growing set of Kihara, Elkies, Mestre/Fermigier, Campbell, Kloosterman-related, and other research families.

### Changed

- Established the architectural rule that core knows **how to search a family**, while the Family plugin owns the family's equations, sections, maps, and specialized mathematics.

## [0.8.4]

### Changed

- Made candidate/job state the durable home for search misses and screening metadata.
- Stopped automatically polluting the Curves inventory with transient search-created rows that had no retained evidence.

### Fixed

- Preserved existing curve rows that already contained points, lattices, coverings, quartic hits, or other meaningful state when cleaning transient misses.

## [0.8.3] — [In-house audit]

Internal release. No reliable public-facing release notes were retained.

## [0.8.2] — [In-house audit]

Internal release. No reliable public-facing release notes were retained.

## [0.8.1] — [In-house audit]

Internal release. No reliable public-facing release notes were retained.

## [0.8.0]

### Added

- Made SQLite the authoritative candidate-pool and search-state store; JSONL remained an import/export/reproducibility format rather than primary application state.
- Added a unified rational-point ledger with exact coordinates, provenance, verification state, generator/witness roles, and migration from legacy point storage.
- Added family-independent **Target Curve** search for direct point hunting on a single retained or supplied elliptic curve.
- Added modular Analysis/Data workspaces for Descent, Lattices & Heights, Independence, Curves, Points, Candidates, and Results.
- Added hard per-stage timeouts and safer scientific subprocess handling.

### Changed

- Separated candidate state, retained curve state, point evidence, and proof status into more explicit durable models.
- Required witness-backed exact points for rigorous lower-bound promotion.

### Fixed

- Reduced the chance that one pathological descent or point-search stage could block the entire application.
- Fixed Streamlit state issues involving non-session-safe database row objects.

## [0.7.14]

### Added

- Expanded External Catalog into a persistent subsystem with ICARM synchronization, local archival provenance, LMFDB checking, caching, and novelty/reference workflows.
- Added SQL-first LMFDB lookup with bounded HTTP fallback where available.
- Added more complete ICARM submission metadata and provenance handling.

### Changed

- Kept external catalog facts separate from locally reproduced mathematical evidence.
- Explicitly distinguished “not found in a catalog” from a mathematical claim of novelty.

## [0.7.13]

### Fixed

- Distinguished a genuinely empty bad-prime list from missing/uncomputed bad-prime metadata during ICARM submission preparation.
- Tightened submission/proof hygiene so incomplete arithmetic metadata could not masquerade as a complete packet.

## [0.7.12]

### Added

- Extended Möbius/PGL2 quartic search coordinates with subgroup-informed charts built from exact discovered fibers.
- Added free charts from small primitive integer PGL2 matrices independent of known points.

### Changed

- Treated alternate charts strictly as search-coordinate heuristics: they change where/how the equivalent genus-one curve is searched, not the proof status of the underlying curve.

## [0.7.11] — [In-house audit]

Internal release. No reliable public-facing release notes were retained.

## [0.7.10] — [In-house audit]

Internal release. No reliable public-facing release notes were retained.

## [0.7.9] — [In-house audit]

Internal release. No reliable public-facing release notes were retained.

## [0.7.8] — [In-house audit]

Internal release. No reliable public-facing release notes were retained.

## [0.7.7] — [In-house audit]

Internal release. No reliable public-facing release notes were retained.

## [0.7.6] — [In-house audit]

Internal release. No reliable public-facing release notes were retained.

## [0.7.5] — [In-house audit]

Internal release. No reliable public-facing release notes were retained.

## [0.7.4] — [In-house audit]

Internal release. No reliable public-facing release notes were retained.

## [0.7.3] — [In-house audit]

Internal release. No reliable public-facing release notes were retained.

## [0.7.2] — [In-house audit]

Internal release. No reliable public-facing release notes were retained.

## [0.7.1]

### Added

- Added the early **General Hunt** path for family-free short-Weierstrass searching.

### Changed

- Began moving General Hunt from uniform expensive searching toward a funnel: large pool → inexpensive Nagao/Mestre screening → shortlist → rational-point search → rigorous analysis.

## [0.7.0]

### Changed

- Made long-running candidate batches restartable from durable per-candidate completion markers instead of advancing by planned batch size.
- Derived resume offsets from completed work recorded in job state/logs.

### Fixed

- Prevented killed or failed batches from silently skipping candidates that had never actually been processed.

## [0.6.0]

### Added

- Added an optional adapter for the ICARM Elliptic Curve Rank Leaderboard.
- Added exact lower-bound verification work based on the Cremona/Brumer quadratic-character independence-certificate approach used by ICARM.
- Added Rank Hunter-specific timeouts, JSON/provenance handling, database integration, and operator plumbing around external verification.

### Changed

- Kept synchronized external catalog curves separate from local discovery/incumbent state.

## [0.5.0]

### Added

- Turned the Rank42 scripts into a persistent research application centered on `rank42.db`, a Streamlit control center, and CLI scientific workers.
- Integrated `ratpoints` as a core rational-point discovery engine alongside Sage, PARI/GP, and mwrank.
- Added persistent search/job output rather than treating terminal output as the only research record.
- Added high-rank family searching using Nagao/Mestre screening, exact PGL2/Möbius quartic transforms, exact inverse mapping, and known-section filtering.
- Added canonical-height Gram matrices as numerical independence screens.

### Changed

- Established the core design split between **discovery** and **certification**: point/search heuristics may suggest candidates, but proof is handled separately.

## [0.4.2]

### Added

- Added an exact data-driven 2-covering interface: family code can provide an exact quartic, exact rational map back to the elliptic curve, and search configuration while generic core handles execution.
- Added raw half-coset and Babai/nearest-plane Mordell-Weil lattice-hole heuristics.

### Changed

- Explicitly classified lattice-hole scores as search heuristics rather than closest-vector proofs.
- Formalized the pattern: family mathematics → generic search engine → exact map → independent verification.

## [0.4.1]

### Added

- Added data-only lattice-hole/quartic handoff scaffolding for future Mordell-Weil search directions.
- Standardized a reproducible hole/coset + exact quartic handoff record.

### Changed

- Refused to fabricate missing family-specific covering mathematics when an exact map was not known.

## [0.4.0]

### Added

- Added data-defined elliptic-curve families and the first generic family/search adapters.
- Began separating reusable search infrastructure from individual family equations.

## Prototype / Rank42

### Added

- Established the original WSL/Linux + SageMath research environment.
- Built the first terminal-driven elliptic-curve search workflow around Sage, PARI/GP, mwrank, and rational-point search tools.
- Began persisting research state in SQLite as the experiments outgrew one-off scripts.
- Adopted the **Rank42** name around the long-term goal of searching for exceptionally high-rank elliptic curves.

### Project direction

The prototype evolved from:

`generate → search points → estimate/prove rank`

into the Rank Hunter research pipeline:

`generate → screen → funnel → search → discover points → feed MW geometry → certify → bound → persist → resume`.
