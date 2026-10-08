# Changelog

## 1.1.14 — 2026-10-01

- Removed disabled-Family content opacity overrides so names, descriptions, and capability tags keep normal visual strength while lifecycle state remains visible through status indicators and badges.

## 1.1.13 — 2026-10-01

- Replaced Auto's schedule popover affordance with a compact Material schedule toggle beside the launch action.
- Kept the accessible Schedule Auto label while presenting the control as an icon-only Axiom button whose normal primary/secondary states show whether scheduling is active.

## 1.1.12 — 2026-10-01

- Aligned the Dashboard Leaderboard torsion menu to the top edge of the shared title row.
- Moved the torsion-menu button presentation fully into Axiom and retired the stale selectbox filter styling.

## 1.1.11 — 2026-10-01

- Moved Dashboard section-title presentation into Axiom-owned CSS instead of core runtime styling.
- Matched Dashboard section titles to the existing Research Frontier typography and divider while keeping status/health badges independently styled.

## 1.1.10 — 2026-09-30

- Increased Dark sidebar collapse/expand control contrast with a visible primary-accent border/icon and hover state.
- Tightened the CSS-drawn select-chevron spacing slightly after live review.
- Tightened Leaderboard title-to-rank spacing and moved the torsion selector below the rank/sync block, directly above the ICARM entry action.
- Coordinated with core's stronger AgGrid iframe surface overrides to eliminate remaining white grid canvases in Dark mode.

## 1.1.9 — 2026-09-30

- Added breathing room to the CSS-drawn select chevron without reintroducing a separate endcap box.
- Moved the Dashboard Leaderboard torsion filter slightly lower to separate it from the section title and rank display.
- Coordinated with core's dynamic Attention height: 0–3 items size naturally; 4+ retain the fixed scroll region.

## 1.1.8 — 2026-09-30

- Replaced the problematic select icon glyph with a CSS-drawn light chevron on the dark field, eliminating the gray-square artifact.
- Added warning/critical/info Attention dot colors consistent with Dashboard Recent Activity.
- Removed retired Explore Research heading styling after core reduced that area to buttons only.

## 1.1.7 — 2026-09-30

- Changed Dark select endcaps from a separate gray square to the same dark field surface with a light chevron.
- Tightened widget-help glyph overrides while keeping tooltip bodies unchanged.
- Coordinated with core's hidden Landscape pool label/help row and appearance-aware Light heatmap ramp.

## 1.1.6 — 2026-09-30

- Forced current Streamlit dropdown-arrow and widget-help glyph shapes onto muted Dark semantic colors instead of inherited white.
- Restyled EXPLORE RESEARCH as a Research Frontier-style subsection heading.
- Coordinated with core's restrained Axiom Research Landscape chart palette to remove the previous default/rainbow chart colors.

## 1.1.5 — 2026-09-30

- Fixed current React-Aria select Open/endcap buttons and widget help tooltips in Dark mode.
- Simplified the Dashboard ICARM leaderboard to one LEADERBOARD title with the torsion selector directly underneath.
- Wrapped Research Landscape in one section card, flattened the four chart subregions, and normalized chart-panel heights.
- Restyled Torsion Distribution as a compact evidence-segment chart, moved Torsion Follow-up into the right compact slot, and made Torsion Records full width.

## 1.1.4 — 2026-09-30

- Hardened Dark-mode selectbox trigger/endcap, sidebar collapse/expand, LinkButton, and newly added Dashboard torsion surfaces against Light-only Streamlit chrome.
- Darkened primary-action gradients while preserving the existing Light appearance.
- Added theme-owned compact styling for the Dashboard ICARM torsion filter beneath the Leaderboard heading.
- Coordinated with core semantic-palette propagation so AgGrid and custom Pipeline component iframes can render the active Axiom appearance instead of falling back to Light.

## 1.1.3 — 2026-09-29

- Restored the reference sidebar telemetry rhythm: full-width open semantic surface, deliberate internal padding, readable row spacing, and larger labels/details.
- Removed the later dashboard-stat cascade that reintroduced a bordered compact telemetry card.
- Preserved thin status tracks, bold values, and the bar-free I/O Wait row in Light and Dark.

## 1.1.2 — 2026-09-29

- Restyled sidebar system telemetry as a compact semantic readout with no extra card chrome.
- Tightened CPU/GPU/RAM progress tracks and aligned bold tabular values with muted secondary detail.
- Preserved status-colored fills and the bar-free one-line I/O Wait row.
- Added the same compact treatment to Axiom Dark while retaining its Primer-derived semantic tokens.

## 1.1.1 — 2026-09-29

- Rebased Axiom Dark on a GitHub Primer-style developer palette: `#0D1117` canvas, `#161B22` surfaces, `#21262D` raised/hover surfaces, `#30363D` borders, `#E6EDF3` primary text, and `#58A6FF` accent.
- Increased secondary/muted text contrast so labels, captions, helper copy, telemetry, and research metadata remain readable on dark surfaces.
- Forced sidebar group headers, navigation buttons, page buttons, expanders, tabs, tables, dialogs, popovers, code blocks, and cards off white/light surfaces in Dark mode.
- Fixed native select caret endcaps and number-input +/- controls so their button chrome uses the dark raised surface instead of white.
- Aligned the native Streamlit dark palette with the same Axiom semantic colors.

## 1.1.0 — 2026-09-29

- Added coordinated Light/Dark appearances inside the single Axiom package.
- Added dark semantic tokens plus a dark native Streamlit palette; Light remains the default.
- Added appearance-layer overrides for shared cards, controls, tables, dialogs, code blocks, charts, dashboard surfaces, and status states.
- Added compact styling for Rank Hunter's global Light/Dark navigation control.

## 1.0.22 — 2026-09-01

- Replaced the Axiom Rank Hunter mark with the supplied blue logo artwork while keeping the existing theme branding/preview path stable.
- Rebuilt the conductor-by-rank strip with an ICARM-style compact dark SVG surface: exact-rank records are bright blue and lower-bound-only records are lighter blue.
- Paired Certification with the new Record by Complexity table beneath the chart block and removed the legacy full-width Records surface.
- Limited Unresolved, Lattices, Exceptional, and complexity-record tables to an approximately five-row viewport with sticky headers and internal scrolling.

## 1.0.21 — 2026-09-01

- Added a compact full-primary-column conductor-record chart surface between the two dashboard chart rows.
- Gives the conductor strip deliberate top/bottom breathing room while overriding the tall equal-card geometry used by the four square research charts.
- Keeps the compact chart subtitle on one line at desktop widths and preserves responsive wrapping on narrow screens.

## 1.0.20 — 2026-09-01

- Removed the colored right-edge accent strips from the four top Dashboard metric cards.
- Reserved a common two-line chart-header height so all four research chart cards retain matching geometry at normal laptop widths, including the Exceptional Curve Radar / Outlier Map.
- Restyled the research sequence as full-width Torsion × Rank, Records, Unresolved, Lattices, and Exceptional sections below the two-column dashboard body.
- Moved Certification into the right rail above Pipeline, hides the verbose research descriptions, and presents compact Records / Certification / Exceptional labels.
- Updated the Pipeline throughput tile for the new `Per Hour` label and unit-free numeric value.

## 1.0.19 — 2026-09-01

- Added Axiom styling for the new Arithmetic Records, Rank Certification, and Exceptional Arithmetic dashboard sections, including compact record tables and evidence-category bars.
- Expanded the Research Frontier summary grid from five to six tiles with responsive 6→3→2 column behavior.
- Kept the six Pipeline statistics in a compact 3×2 grid and reduced the Throughput value size so full `candidates/sec` or `candidates/hr` units remain readable.
- Applied the same soft dashboard-section surface and hover hierarchy to the new arithmetic research regions.

## 1.0.18 — 2026-09-01

- Switched the compact dashboard search-mode styling to the new structural Candidate-pool/General-Hunt label row emitted by core, eliminating the old negative-margin toggle folding hack.
- Keeps the Family/General switch inline with the visible first-control label and hides only the duplicate native Candidate-pool visual label.
- Pipeline now relies on the real core `PIPELINE` eyebrow rather than a theme-generated pseudo heading.

## 1.0.17 — 2026-09-01

- Removed the hidden style-only Streamlit layout box that was still leaving excess vertical space between the SEARCH eyebrow and Candidate pool.
- Kept the Family/General toggle centered on the Candidate pool label line after the spacing cleanup.
- Restored a blue `PIPELINE` eyebrow above the pipeline summary tiles.

## 1.0.16 — 2026-09-01

- Centered the compact Family/General search toggle vertically with the `Candidate pool` label.
- Removed the oversized blank band between the SEARCH section eyebrow and the first form control by collapsing Streamlit's extra toggle-row block gap while keeping the switch in normal flow.

## 1.0.15 — 2026-09-01

- Folded the compact Family/General search-mode switch into the first search-control label line so, in Family mode, it sits inline with `Candidate pool` like the Point-engine help affordance.
- Kept the switch in normal document flow with negative row spacing rather than absolute positioning, preserving the existing hover/focus explanation and avoiding the earlier escaped-toggle failure mode.

## 1.0.14 — 2026-09-01

- Replaced the inner Top Rank caption with a blue `LEADERBOARD` eyebrow matching the other dashboard section labels.
- Tightened the compact Search header-to-Candidate-pool spacing by removing the extra toggle-row and form top gap.
- Rebalanced the dashboard body to align its primary research column beneath the first three top stat cards and the right rail beneath the fourth.

## 1.0.13 — 2026-09-01

- Removed the Top Rank gradient and standardized dashboard body sections on a soft surface lighter than the page background.
- Capitalized the Top Rank label visually, muted the leaderboard rank value, and added more spacing below the page-level metric row.
- Turned Research Frontier summary metrics into padded rounded white tiles on the soft section surface.
- Hid the compact Family Search text label, aligned its switch with the SEARCH heading, and added a theme-owned hover/focus tooltip explaining Family Search vs General Hunt.

## 1.0.12 — 2026-09-01

- Added a dedicated Axiom research-frontier stylesheet for the new live-database evidence strip.
- Styles the horizontal rank sequence with solid exact-rank markers, open lower-bound-only markers, per-rank counts, compact frontier metrics, legend, and strongest-curve row.

## 1.0.11 — 2026-09-01

- Returned Top Rank to Axiom's original cool-gray page tone and replaced the flat darker fill with a subtle diagonal dark-to-light gradient.
- Kept the Top Rank surface fully theme-owned through `--rh-dashboard-top-rank-bg` while preserving the transparent Streamlit inner wrapper.

## 1.0.10 — 2026-09-01

- Gave Top Rank a dedicated Axiom surface token (`--rh-dashboard-top-rank-bg`) at `#E6EAF2`, visibly darker than the page background instead of reusing the nearly identical navigation-hover color.
- Applies the surface directly to the semantic Top Rank region and keeps its internal Streamlit wrapper transparent so the theme-owned color shows through.

## 1.0.9 — 2026-09-01

- Targeted the bordered Top Rank ancestor with `:has(...)` so the visible ICARM card receives the exact navigation-hover surface color.
- Removed absolute positioning from the Family search switch; it now stays in normal Streamlit flow and is pulled onto the Search heading line with layout-safe spacing.

## 1.0.8 — 2026-09-01

- Fixed the Top Rank card surface selector so the actual bordered Streamlit wrapper uses the same `--rh-surface-hover` background as a hovered navigation item.
- Pulled the Family search toggle into the Search card header line so it no longer creates a large blank control row above Candidate pool.

## 1.0.7 — 2026-09-01

- Matched the dashboard Top Rank card background to the same `--rh-surface-hover` token used by hovered navigation items.
- Kept the compact dashboard search and research-card styling compatible with the latest core control layout.

## 1.0.6 — 2026-08-31

- Softened the ICARM leaderboard surface with a light tinted background and no redundant leaderboard heading.
- Updated dashboard chart-surface selectors for rank distribution, search progress, root-number/rank, and parameter-yield views.

## 1.0.5 — 2026-08-31

- Reduced the Rank Hunter sidebar mark from 96px to 84px for a quieter navigation header.
- Kept the compact research dashboard spacing compatible with the revised right-hand activity/search rail.

## 1.0.4 — 2026-08-31

- Simplified the ICARM leaderboard to a leaderboard label, top rank, direct entry link, and local-sync freshness.
- Quieted dashboard metric cards, added an in-card Results link for Jobs, and removed redundant status pills from the page strip.
- Moved four research charts directly under Local Research Frontier and added an evidence-coverage visualization.
- Added a compact functional dashboard search treatment for Family Search and General Hunt.
- Restyled Latest Activity for five recent jobs and kept pipeline/search controls dense in the right research rail.

## 1.0.3 — 2026-08-31

- Replaced the sidebar wordmark with the supplied Rank Hunter mark and kept it as the Dashboard control.
- Added dashboard styling for the local research frontier, ICARM leaderboard, compact pipeline cards, and three research charts.
- Added extension-surface spacing and denser three-column card support.

## 1.0.2 — 2026-08-31

- Fixed selected navigation hover/focus states so active items remain readable.
- Rebalanced the larger typography to restore the compact dashboard layout.
- Added explicit family status-dot geometry and refined generic-rank tiles.
- Turned the sidebar logo area into the native Dashboard hit target.

## 1.0.1 — 2026-08-31

- Switched Axiom branding to Rank Hunter's application wordmark.
- Removed the redundant Rank Hunter/version footer from the Axiom sidebar.
- Increased base, navigation, telemetry, dashboard, caption, and plugin typography for readability.

## 1.0.0 — 2026-08-31

- Initial Axiom theme.
- Added light Streamlit palette and compact white sidebar.
- Added cobalt active navigation and button treatments.
- Added dedicated styling for the redesigned Rank Hunter dashboard.
- Added flat plugin, telemetry, input, table, and card surfaces.
