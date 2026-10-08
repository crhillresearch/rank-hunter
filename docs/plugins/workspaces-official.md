# Official Features & Workspaces

Official extensions add optional search/research surfaces without placing their mathematics directly in Rank Hunter Core.

The installed collection/version is authoritative; this page describes the release set at a high level.

## Feature: Symmetry Reducer

Uses exact Family symmetries to avoid searching parameter regions known to be equivalent under the declared transformation.

Purpose:

- reduce duplicate candidate work;
- preserve a canonical representative;
- attach symmetry provenance.

A symmetry reduction changes search geometry/population. It does not prove rank.

## Workspace: Curve Explorer

Interactive curve/point visualization and exact rational-point construction displays.

Use it for inspection and teaching/research visualization.

Browser-side displays are not a substitute for core exact evidence.

## Workspace: Fiber Atlas

Family/fiber research surface for exploring parameter landscapes, rank growth, search yield, and handoff into Target/Pipelines.

Useful for comparing where a Family search is succeeding or stalling.

A visual cluster is a search signal, not proof that nearby fibers share rank.

## Workspace: Leaderboard Compare

Compares local Rank Hunter curves with synchronized high-rank leaderboard/reference data.

External leaderboard rank claims remain reference data until reproduced or imported through the local evidence path.

Use this workspace to answer:

- Is my curve already known?
- Which known records are nearby?
- Which Family/source produced a record?
- Where does local evidence differ from external metadata?

## Workspace: Twist & Isogeny Lab

Research surface for exact rational isogeny-class work and bounded quadratic-twist neighborhoods.

The workspace separates:

- exact curve/isogeny construction;
- heuristic search over neighborhoods;
- rigorous evidence promotion.

A promising twist score is not rank evidence.

## Enable / disable

Open **Plugins → Extensions**.

Enabled Workspace pages appear in Rank Hunter's Workspace navigation.

## Operational rule

Workspaces should launch long arithmetic as jobs rather than performing it during Streamlit render.

If a Workspace appears frozen, check **Jobs** before assuming the browser thread is doing the science.

## Version/provenance

Record both:

- Rank Hunter core version;
- extension/plugin version.

When a Workspace launches a Family/core job, the resulting job/curve should retain the relevant provenance independently of the UI page.
