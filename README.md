# Rank Hunter

**Elliptic-curve search, investigation, and certification in one place.**

Rank Hunter grew out of a practical problem: finding a promising elliptic curve is only part of the job. After that, you still need to know where it came from, what was searched, which points are exact, what has actually been proved, and what is worth trying next.

Rank Hunter keeps that work together.

It combines family and general curve search, rational-point discovery, exact point verification, Mordell–Weil independence work, rank bounds, quartics and coverings, search pipelines, job history, external catalogs, and plugin-based research tools in one Streamlit application.

The current public release is **Rank Hunter 0.9.2**.

Visit [RankHunter.net](https://rankhunter.net).

## What you can do

- Search parameterized elliptic-curve families for promising specializations.
- Run general searches when you are not working from a family.
- Build reusable search pipelines from screening, point-search, geometry, descent, and certification stages.
- Push a specific curve through deeper Target Search when it deserves more attention.
- Verify rational points exactly and track where they came from.
- Certify independent points and maintain rigorous lower-bound evidence.
- Run descent and rank-bound tools without confusing timeouts with mathematical results.
- Explore quartics, coverings, lattices, heights, local arithmetic, isogenies, twists, and other classical research tools.
- Keep candidate pools, curves, jobs, results, campaigns, and research history in one local database.
- Compare local results with external references such as the ICARM Elliptic Curve Rank Leaderboard without treating external data as local proof.
- Add family-specific mathematics and optional research workspaces through plugins.

## Proof status matters

Rank Hunter is built to search aggressively without overstating the mathematics.

A high score is useful. It is not a proof. A point found numerically is interesting. It does not increase rank until it has been checked and certified through the appropriate exact path.

| Rank Hunter state | What it means |
| --- | --- |
| Heuristic / numerical signal | Useful for deciding where to search next. Not proof. |
| Exact rational point | The point has been checked exactly on the curve. |
| Rigorous lower bound | Enough exact independent points have been certified to prove `rank >= r`. |
| Rigorous upper bound | A rigorous method has proved `rank <= r`. |
| Exact rank | The rigorous lower and upper bounds meet. |
| Timeout / engine failure | Inconclusive. It is not evidence that a point or higher rank does not exist. |

That distinction is carried through the UI, stored evidence, and search history.

## Installation

### Ubuntu / Linux

Rank Hunter expects a working **SageMath** installation or another Python executable that can import `sage.all`.

Clone the public repository and run the installer from inside the checkout:

```bash
git clone https://github.com/crhillresearch/rank-hunter.git
cd rank-hunter
bash install.sh
```

The full installer:

- checks the scientific Python/Sage runtime;
- creates the Rank Hunter UI environment;
- initializes or migrates the local SQLite database;
- initializes the pinned `ratpoints` and `ratpoints-gpu` submodules;
- builds and smoke-tests CPU ratpoints;
- builds GPU ratpoints when a usable CUDA toolchain/device is available;
- installs the official Rank Hunter plugin collection from the pinned `crhillresearch/rh-plugins` `v0.9.2` tag.

For a lighter install that skips native ratpoints builds:

```bash
bash install.sh --lite
```

If Sage is not the Python currently on your `PATH`, point the installer at it directly:

```bash
bash install.sh --science-python /path/to/sage/python
```

### Windows

Download [RankHunter-Setup-x64.exe](https://github.com/crhillresearch/rank-hunter/releases/latest/download/RankHunter-Setup-x64.exe) from the latest public GitHub Release. The installer configures or reuses WSL2 and SageMath as needed, then installs Rank Hunter.

### Launch

```bash
bash scripts/run-ui.sh
```

Rank Hunter will open or create `rank42.db` in the checkout unless you configure another database path.

## A first hunt

A normal workflow looks roughly like this:

1. Open **Search** and choose a general search or an installed Family.
2. Generate or load a Candidate Pool.
3. Run a search or Pipeline to narrow the field.
4. Open interesting retained curves under **Curves**.
5. Use **Target Search** when one curve deserves a deeper point hunt.
6. Use **Analysis** tools to inspect exact points, independence, heights, coverings, local arithmetic, and rank evidence.
7. Run rigorous rank-bound methods when you are ready to try to close the rank interval.
8. Review the complete job/result history before sharing a claim.

You do not need to use every stage. Rank Hunter is designed so cheap search heuristics can narrow a large population before expensive exact work begins.

## Plugins

Rank Hunter Core stays deliberately generic. Family equations, published sections, family-specific search tricks, and optional specialist workspaces live in plugins rather than being hard-coded into the application.

The official plugin collection is installed automatically by `install.sh` from:

`https://github.com/crhillresearch/rh-plugins`

Plugins can provide:

- elliptic-curve Families;
- Family Search and Target Search adapters;
- search geometry and exact transforms;
- read-only research Libraries;
- optional Features and full research Workspaces.

Plugin results still pass through Rank Hunter's normal exact-evidence rules. A plugin cannot turn a heuristic score into a rigorous rank claim simply by reporting it.

## CPU and GPU point search

Rank Hunter supports the classical `ratpoints` engine and the CUDA-based `ratpoints-gpu` implementation when available.

CPU point search is the normal fallback and does not require a GPU. GPU search requires a compatible NVIDIA/CUDA environment and a successful native build.

The exact upstream revisions are pinned as Git submodules under `vendor/`.

## Your data stays local

The working research database is local SQLite state. It is not included in the source release.

Rank Hunter stores the information needed to make a research trail useful later: exact curve models, points, evidence records, search/job metadata, plugin provenance, commands, and related artifacts where applicable.

Research databases, exports, and local runtime state are ignored by Git.

## Project boundaries

Rank Hunter is a **classical elliptic-curve research environment**. Its supported public release path is built around SageMath, PARI/eclib functionality available through the supported runtime, exact rational arithmetic, ratpoints, descent/rank-bound methods, and explicit proof-aware persistence.

## License

Rank Hunter is released under the **GNU General Public License v3.0**. See [LICENSE](LICENSE).

Third-party projects and adapted/inspired components retain their own licenses and acknowledgements. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
