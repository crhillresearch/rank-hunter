# Third-Party Notices

Rank Hunter includes, adapts, or is informed by several third-party projects.
Those projects retain their own copyrights and licenses.

## ICARM Elliptic Curve Rank Leaderboard

Rank Hunter can use the public ICARM Elliptic Curve Rank Leaderboard at
`https://elliptic-rank.icarm.cloud/` as an external reference catalog. Synced
catalog data remains reference data and is kept separate from Rank Hunter's
local scientific evidence.

The exact lower-bound worker in `rank42/exact_lb_worker.py` adapts the
Cremona/Brumer quadratic-character independence-certificate implementation in
ICARM's public `src/verify.ts` source.

Upstream project:

- Project: ICARM `elliptic-rank`
- Source: `https://github.com/icarm/elliptic-rank`
- Adapted source file: `src/verify.ts`
- Upstream license: Apache License 2.0
- License copy: `licenses/ICARM-APACHE-2.0.txt`
- Source reviewed for the Rank Hunter port: 2026-08-21

The Rank Hunter implementation is a Python/Sage/PARI port with Rank Hunter's
own timeout, JSON, provenance, database, and operator plumbing.

Rank Hunter also follows the leaderboard site's requested acknowledgement for
use of its database: ICARM, the NSF Institute for Computer-Aided Reasoning in
Mathematics, supported by NSF Grant DMS 2425401.

No synchronized ICARM database snapshot is bundled as Rank Hunter's canonical
dataset. Live catalog data is retrieved separately and remains external
reference material.

## Vendored rational-point search engines

Rank Hunter pins the following upstream projects as Git submodules under
`vendor/`:

- **ratpoints** — Michael Stoll — GPL-2.0-or-later
  - Source: `https://github.com/MichaelStollBayreuth/ratpoints`
- **ratpoints-gpu** — wgxli — GPL-2.0-or-later
  - Source: `https://github.com/wgxli/ratpoints-gpu`

These remain separate upstream works. Their copyright and license files remain
inside their respective submodule trees. Rank Hunter records exact submodule
commits rather than downloading moving branch heads at runtime.

## elliptic-rank-search

Rank Hunter's pointed-quartic search escalation was informed by the public
`elliptic-rank-search` project by Valery Asiryan, particularly its use of
point-centred quartic models, bounded/diverse Mordell–Weil anchor portfolios,
and reuse of exact observations in later search geometry.

Upstream project:

- Project: `asiryan/elliptic-rank-search`
- Source: `https://github.com/asiryan/elliptic-rank-search`
- Copyright: 2025-2026 Valery Asiryan
- Upstream license: MIT
- License copy: `licenses/ELLIPTIC-RANK-SEARCH-MIT.txt`
- Source reviewed for this implementation: 2026-09-18

The underlying projection formulas are classical. Rank Hunter integrates the
search strategy independently with its own persistence, ratpoints backends,
Campaign/Pipeline provenance, exact map-back checks, and independence
certification.

No upstream rank claim or stored witness is imported as Rank Hunter proof
evidence. Points found through these search paths still require exact
verification and Rank Hunter's ordinary certification process before they can
increase a rigorous lower bound.
