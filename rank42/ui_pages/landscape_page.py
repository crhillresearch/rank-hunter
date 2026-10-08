from __future__ import annotations

import streamlit as st

from rank42.landscape_dimensions import (
    evaluate_landscape_feature,
    get_landscape_dimension,
    landscape_dimensions,
    normalize_landscape_feature,
    resolve_curve_landscape_dimensions,
    resolve_frozen_candidate_landscape_dimensions,
)
from rank42.landscape_state import (
    build_landscape_cohort,
    get_landscape_analysis,
    list_landscape_analyses,
    save_landscape_analysis,
)
from rank42.ui_components import region, tabs
from .common import title


_SOURCE_LABELS = {
    "campaign": "Campaign",
    "family": "Family / variant",
    "candidate_pool": "Candidate Pool",
    "pipeline_run": "Pipeline Run",
    "explicit_curves": "Explicit curve ids",
}
_SOURCE_BY_LABEL = {label: kind for kind, label in _SOURCE_LABELS.items()}


def _table_exists(db, name):
    return db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (str(name),),
    ).fetchone() is not None


def _go_to(page):
    st.session_state["rh_page"] = str(page)


def _campaign_options(db):
    if not _table_exists(db, "research_campaigns"):
        return []
    return db.execute(
        """SELECT id,name,status,created_at,updated_at
           FROM research_campaigns
           ORDER BY CASE status
             WHEN 'active' THEN 0 WHEN 'paused' THEN 1
             WHEN 'completed' THEN 2 ELSE 3 END,
             updated_at DESC,id DESC
           LIMIT 500"""
    ).fetchall()


def _family_options(db):
    rows = db.execute(
        """SELECT native_family_key,MIN(family) AS family,
                  MIN(plugin_id) AS plugin_id,COUNT(*) AS curve_count
           FROM curves
           WHERE native_family_key IS NOT NULL AND native_family_key!=''
           GROUP BY native_family_key
           ORDER BY curve_count DESC,native_family_key
           LIMIT 1000"""
    ).fetchall()
    if rows:
        return [
            {
                "value": str(row["native_family_key"]),
                "label": (
                    f'{row["native_family_key"]} · {int(row["curve_count"]):,} curves'
                    + (f' · {row["plugin_id"]}' if row["plugin_id"] else "")
                ),
            }
            for row in rows
        ]

    fallback = db.execute(
        """SELECT family,COUNT(*) AS curve_count
           FROM curves
           GROUP BY family
           ORDER BY curve_count DESC,family
           LIMIT 1000"""
    ).fetchall()
    return [
        {
            "value": str(row["family"]),
            "label": f'{row["family"]} · {int(row["curve_count"]):,} curves',
            "legacy": True,
        }
        for row in fallback
    ]


def _pool_options(db):
    if not _table_exists(db, "candidate_pools"):
        return []
    return db.execute(
        """SELECT id,name,plugin_id,family_spec,candidate_count,status,updated_at
           FROM candidate_pools
           ORDER BY id DESC LIMIT 500"""
    ).fetchall()


def _run_options(db):
    if not _table_exists(db, "search_pipeline_runs"):
        return []
    return db.execute(
        """SELECT id,pipeline_name,status,candidates_total,candidates_done,created_at
           FROM search_pipeline_runs
           ORDER BY id DESC LIMIT 500"""
    ).fetchall()


def _parse_curve_ids(value):
    text = str(value or "").strip()
    if not text:
        return []
    values = []
    for token in text.replace("\n", ",").split(","):
        token = token.strip()
        if not token:
            continue
        values.append(int(token.lstrip("#")))
    return sorted(set(values))


def _cohort_controls(db, *, prefix, label):
    with region(f"landscape-{prefix}-cohort", border=True):
        st.subheader(label)
        source_label = st.selectbox(
            "Population source",
            list(_SOURCE_BY_LABEL),
            key=f"landscape-{prefix}-source-kind",
        )
        source_kind = _SOURCE_BY_LABEL[source_label]
        source = None

        if source_kind == "campaign":
            rows = _campaign_options(db)
            if rows:
                by_id = {int(row["id"]): row for row in rows}
                ids = list(by_id)
                selected = st.selectbox(
                    "Campaign",
                    ids,
                    format_func=lambda campaign_id: (
                        f'#{campaign_id} · {by_id[campaign_id]["name"]} · '
                        f'{by_id[campaign_id]["status"]}'
                    ),
                    key=f"landscape-{prefix}-campaign",
                )
                source = {"campaign_id": int(selected)}
            else:
                st.info("No Campaigns are stored yet.")

        elif source_kind == "family":
            options = _family_options(db)
            if options:
                values = [rec["value"] for rec in options]
                by_value = {rec["value"]: rec for rec in options}
                selected = st.selectbox(
                    "Family / variant",
                    values,
                    format_func=lambda value: by_value[value]["label"],
                    key=f"landscape-{prefix}-family",
                )
                if by_value[selected].get("legacy"):
                    source = {"family": selected}
                else:
                    source = {"native_family_key": selected}
            else:
                st.info("No retained curve families are stored yet.")

        elif source_kind == "candidate_pool":
            rows = _pool_options(db)
            if rows:
                by_id = {int(row["id"]): row for row in rows}
                ids = list(by_id)
                selected = st.selectbox(
                    "Candidate Pool",
                    ids,
                    format_func=lambda pool_id: (
                        f'#{pool_id} · {by_id[pool_id]["name"]} · '
                        f'{int(by_id[pool_id]["candidate_count"] or 0):,} candidates'
                    ),
                    key=f"landscape-{prefix}-pool",
                )
                source = {"pool_id": int(selected)}
            else:
                st.info("No Candidate Pools are stored yet.")

        elif source_kind == "pipeline_run":
            rows = _run_options(db)
            if rows:
                by_id = {int(row["id"]): row for row in rows}
                ids = list(by_id)
                selected = st.selectbox(
                    "Pipeline Run",
                    ids,
                    format_func=lambda run_id: (
                        f'#{run_id} · {by_id[run_id]["pipeline_name"]} · '
                        f'{by_id[run_id]["status"]} · '
                        f'{int(by_id[run_id]["candidates_done"] or 0):,}/'
                        f'{int(by_id[run_id]["candidates_total"] or 0):,}'
                    ),
                    key=f"landscape-{prefix}-run",
                )
                source = {"run_id": int(selected)}
            else:
                st.info("No Pipeline Runs are stored yet.")

        else:
            raw = st.text_input(
                "Curve ids",
                placeholder="2485, 2486, 2492",
                key=f"landscape-{prefix}-curve-ids",
                help="Comma-separated retained Curve ids.",
            )
            try:
                curve_ids = _parse_curve_ids(raw)
            except ValueError:
                curve_ids = []
                st.error("Curve ids must be integers.")
            if curve_ids:
                source = {"curve_ids": curve_ids}

        f1, f2 = st.columns(2, gap="small")
        minimum = f1.number_input(
            "Minimum rigorous lower",
            min_value=0,
            value=0,
            step=1,
            key=f"landscape-{prefix}-minimum-rank",
            help="0 means no rank filter. Positive values use authoritative Curve Research State.",
        )
        only_with_curve = f2.checkbox(
            "Only retained curves",
            value=False,
            key=f"landscape-{prefix}-only-retained",
            help="Useful for Candidate Pool / Pipeline Run cohorts that include unretained candidates.",
        )

        filters = {}
        if int(minimum) > 0:
            filters["minimum_rigorous_lower"] = int(minimum)
        if only_with_curve:
            filters["only_with_curve"] = True

        return source_kind, source, filters


def _feature_controls(prefix="primary"):
    with region(f"landscape-{prefix}-feature", border=True):
        st.subheader("Descriptive feature")
        st.caption(
            "Optional cohort annotation only. Features are exploratory configuration, "
            "not rank evidence or automatic Pipeline rules."
        )
        enabled = st.checkbox(
            "Add a feature",
            value=False,
            key=f"landscape-{prefix}-feature-enabled",
        )
        if not enabled:
            return []

        labels = {
            "bad_prime_contains": "Bad-prime membership",
            "bad_prime_superset": "Bad-prime set contains all",
            "root_number_equals": "Root number equals",
            "denominator_divisible_by": "Native denominator divisible by",
        }
        kind = st.selectbox(
            "Feature kind",
            list(labels),
            format_func=lambda value: labels[value],
            key=f"landscape-{prefix}-feature-kind",
        )

        if kind == "bad_prime_contains":
            prime = st.number_input(
                "Prime",
                min_value=2,
                value=2,
                step=1,
                key=f"landscape-{prefix}-feature-prime",
            )
            return [{"kind": kind, "prime": int(prime)}]

        if kind == "bad_prime_superset":
            raw = st.text_input(
                "Primes",
                value="2,3",
                key=f"landscape-{prefix}-feature-primes",
                help="Comma-separated prime values.",
            )
            try:
                primes = [int(value.strip()) for value in raw.split(",") if value.strip()]
            except ValueError:
                st.error("Prime sets must contain integers.")
                return []
            return [{"kind": kind, "primes": primes}]

        if kind == "root_number_equals":
            value = st.selectbox(
                "Root number",
                [-1, 1],
                key=f"landscape-{prefix}-feature-root-number",
            )
            return [{"kind": kind, "value": int(value)}]

        prime = st.number_input(
            "Prime",
            min_value=2,
            value=2,
            step=1,
            key=f"landscape-{prefix}-feature-denominator-prime",
        )
        return [{"kind": kind, "prime": int(prime)}]


def _feature_label(feature):
    feature = normalize_landscape_feature(feature)
    kind = feature["kind"]
    if kind == "bad_prime_contains":
        return f'Bad primes contain {feature["prime"]}'
    if kind == "bad_prime_superset":
        return "Bad primes contain {" + ",".join(str(x) for x in feature["primes"]) + "}"
    if kind == "root_number_equals":
        return f'Root number = {feature["value"]}'
    return f'Native denominator divisible by {feature["prime"]}'


def _feature_dimension_ids(feature_definitions):
    required = set()
    for raw in feature_definitions or []:
        feature = normalize_landscape_feature(raw)
        if feature["kind"] in {"bad_prime_contains", "bad_prime_superset"}:
            required.add("arithmetic.bad_primes")
        elif feature["kind"] == "root_number_equals":
            required.add("arithmetic.root_number")
        else:
            required.add("parameter.denominator")
    return required


def _analysis_rows(
    db,
    cohort,
    dimension_ids,
    feature_definitions=None,
    *,
    cohort_label="Primary",
    max_rows=250,
):
    """Build a bounded UI projection from one frozen R5 cohort."""
    selected = [str(value) for value in dimension_ids]
    candidate_ids = [value for value in selected if value.startswith("candidate.")]
    curve_ids = [value for value in selected if not value.startswith("candidate.")]
    feature_definitions = list(feature_definitions or [])
    feature_dims = _feature_dimension_ids(feature_definitions)
    curve_needed = sorted(set(curve_ids) | feature_dims)

    members = list(cohort.get("members") or [])[: int(max_rows)]
    linked_curve_ids = sorted({
        int(member["curve_id"])
        for member in members
        if member.get("curve_id") is not None
    })
    curve_values = {}
    if curve_needed:
        for curve_id in linked_curve_ids:
            curve_values[curve_id] = resolve_curve_landscape_dimensions(
                db,
                curve_id,
                dimension_ids=curve_needed,
            )

    specs = {dimension_id: get_landscape_dimension(dimension_id) for dimension_id in selected}
    normalized_features = [normalize_landscape_feature(raw) for raw in feature_definitions]
    rows = []

    for member in members:
        curve_id = member.get("curve_id")
        combined = {}
        if curve_id is not None:
            combined.update(curve_values.get(int(curve_id), {}))
        if candidate_ids and member.get("candidate_id") is not None:
            frozen_candidate = resolve_frozen_candidate_landscape_dimensions(member)
            combined.update({
                dimension_id: frozen_candidate[dimension_id]
                for dimension_id in candidate_ids
            })

        row = {
            "Cohort": str(cohort_label),
            "Member": (
                f'Candidate #{int(member["candidate_id"])}'
                if member.get("candidate_id") is not None
                else f'Curve #{int(curve_id)}'
                if curve_id is not None
                else str(member.get("parameter") or "—")
            ),
            "Curve": "—" if curve_id is None else f"#{int(curve_id)}",
            "_provenance": {},
            "_rigor": {},
        }
        for dimension_id in selected:
            spec = specs[dimension_id]
            record = combined.get(dimension_id)
            row[spec["label"]] = None if record is None else record.get("value")
            row["_provenance"][dimension_id] = (
                {} if record is None else dict(record.get("provenance") or {})
            )
            row["_rigor"][dimension_id] = spec["rigor_class"]

        if normalized_features:
            if curve_id is None:
                for feature in normalized_features:
                    row[_feature_label(feature)] = None
            else:
                for feature in normalized_features:
                    row[_feature_label(feature)] = evaluate_landscape_feature(
                        feature,
                        combined,
                    )
        rows.append(row)
    return rows


def _dimension_controls(source_kind):
    registry = landscape_dimensions()
    allowed = []
    for rec in registry:
        is_candidate = rec["id"].startswith("candidate.")
        if is_candidate and source_kind not in {"candidate_pool", "pipeline_run"}:
            continue
        allowed.append(rec)

    by_id = {rec["id"]: rec for rec in allowed}
    options = list(by_id)
    defaults = (
        ["candidate.ranking_value", "rank.rigorous_lower"]
        if source_kind in {"candidate_pool", "pipeline_run"}
        else ["rank.rigorous_lower", "arithmetic.log_conductor"]
    )
    defaults = [value for value in defaults if value in by_id]

    selected = st.multiselect(
        "Registered dimensions",
        options,
        default=defaults,
        format_func=lambda dimension_id: (
            f'{by_id[dimension_id]["label"]} · '
            f'{by_id[dimension_id]["rigor_class"]}'
        ),
        key="landscape-dimensions",
        help="Dimensions come from the versioned Landscape registry; arbitrary SQL expressions are not accepted.",
    )
    return selected


def _display_rows(rows):
    return [
        {key: value for key, value in row.items() if not key.startswith("_")}
        for row in rows
    ]


def _render_dimension_provenance(dimension_ids):
    with st.expander("Dimension provenance", expanded=False):
        records = []
        for dimension_id in dimension_ids:
            spec = get_landscape_dimension(dimension_id)
            records.append(
                {
                    "Dimension": spec["label"],
                    "Rigor class": spec["rigor_class"],
                    "Authority": spec["source_authority"],
                    "Provenance policy": spec["provenance_policy"],
                    "Version": spec["version"],
                }
            )
        st.dataframe(records, hide_index=True, width="stretch")


def _dimension_altair_type(spec):
    value_type = str(spec["value_type"])
    if "integer" in value_type or "real" in value_type:
        return "Q"
    return "N"


def _render_analysis_preview(
    db,
    primary_cohort,
    *,
    comparison_cohort=None,
    dimension_ids,
    feature_definitions=None,
):
    feature_definitions = list(feature_definitions or [])
    primary_rows = _analysis_rows(
        db,
        primary_cohort,
        dimension_ids,
        feature_definitions,
        cohort_label="Primary",
    )
    comparison_rows = (
        []
        if comparison_cohort is None
        else _analysis_rows(
            db,
            comparison_cohort,
            dimension_ids,
            feature_definitions,
            cohort_label="Comparison",
        )
    )
    rows = primary_rows + comparison_rows

    with region("landscape-analysis-preview", border=True):
        st.subheader("Exploratory comparison")
        st.caption(
            "Descriptive, numerical, and heuristic relationships shown here are exploratory. "
            "Landscape does not promote rank evidence or turn an observed association into proof."
        )
        st.caption(
            f'Primary frozen cohort: {int(primary_cohort.get("member_count") or 0):,} members · '
            f'{str(primary_cohort.get("member_hash") or "")[:12]}…'
            + (
                f' · Comparison: {int(comparison_cohort.get("member_count") or 0):,} members · '
                f'{str(comparison_cohort.get("member_hash") or "")[:12]}…'
                if comparison_cohort is not None
                else ""
            )
        )

        if not rows:
            st.info("The selected frozen cohort has no members to preview.")
            return

        if len(dimension_ids) >= 2:
            import altair as alt
            import pandas as pd

            x_id, y_id = dimension_ids[:2]
            x_spec = get_landscape_dimension(x_id)
            y_spec = get_landscape_dimension(y_id)
            frame = pd.DataFrame(_display_rows(rows))
            x_label = x_spec["label"]
            y_label = y_spec["label"]
            if x_label in frame.columns and y_label in frame.columns:
                plot = frame.dropna(subset=[x_label, y_label])
                if not plot.empty:
                    chart = (
                        alt.Chart(plot)
                        .mark_circle(filled=True, opacity=0.68, size=72)
                        .encode(
                            x=alt.X(
                                f"{x_label}:{_dimension_altair_type(x_spec)}",
                                title=x_label,
                            ),
                            y=alt.Y(
                                f"{y_label}:{_dimension_altair_type(y_spec)}",
                                title=y_label,
                            ),
                            color=alt.Color("Cohort:N", title="Cohort"),
                            tooltip=[
                                alt.Tooltip("Cohort:N"),
                                alt.Tooltip("Member:N"),
                                alt.Tooltip("Curve:N"),
                                alt.Tooltip(f"{x_label}:{_dimension_altair_type(x_spec)}"),
                                alt.Tooltip(f"{y_label}:{_dimension_altair_type(y_spec)}"),
                            ],
                        )
                        .properties(height=320)
                    )
                    st.altair_chart(chart, width="stretch")
                else:
                    st.caption("The first two selected dimensions have no paired values in this preview.")

        st.dataframe(
            _display_rows(rows),
            hide_index=True,
            width="stretch",
            height=min(520, 88 + 35 * min(len(rows), 12)),
        )
        if int(primary_cohort.get("member_count") or 0) > len(primary_rows):
            st.caption("Primary table/chart preview is capped at 250 frozen members.")
        if comparison_cohort is not None and int(comparison_cohort.get("member_count") or 0) > len(comparison_rows):
            st.caption("Comparison table/chart preview is capped at 250 frozen members.")

    _render_dimension_provenance(dimension_ids)


def _save_analysis_panel(
    db,
    primary_cohort,
    *,
    comparison_cohort,
    dimension_ids,
    feature_definitions,
):
    with region("landscape-save-analysis", border=True):
        st.subheader("Save reproducible analysis")
        st.caption(
            "Saving freezes the current cohort snapshots plus dimension/feature definitions. "
            "It writes research-analysis configuration only."
        )
        name = st.text_input(
            "Analysis name",
            placeholder="Rank vs conductor · CEF high-rank cohort",
            key="landscape-analysis-name",
        )
        if st.button(
            "Save analysis",
            type="primary",
            disabled=not bool(name.strip()) or not bool(dimension_ids),
            key="landscape-save-analysis-button",
        ):
            try:
                analysis_id = save_landscape_analysis(
                    db,
                    name=name,
                    primary_cohort=primary_cohort,
                    comparison_cohort=comparison_cohort,
                    dimension_ids=dimension_ids,
                    feature_definitions=feature_definitions,
                )
            except ValueError as exc:
                st.error(str(exc))
            else:
                st.success(f"Saved Landscape analysis #{analysis_id}.")


def _explore(db):
    primary_kind, primary_source, primary_filters = _cohort_controls(
        db,
        prefix="primary",
        label="Primary cohort",
    )
    comparison_enabled = st.checkbox(
        "Add comparison cohort",
        value=False,
        key="landscape-comparison-enabled",
    )
    comparison_config = None
    if comparison_enabled:
        comparison_config = _cohort_controls(
            db,
            prefix="comparison",
            label="Comparison cohort",
        )

    dimension_ids = _dimension_controls(primary_kind)
    feature_definitions = _feature_controls("primary")

    if primary_source is None:
        st.info("Choose a valid primary population source to build a frozen cohort.")
        return
    if not dimension_ids:
        st.info("Choose at least one registered dimension.")
        return

    try:
        primary_cohort = build_landscape_cohort(
            db,
            source_kind=primary_kind,
            source=primary_source,
            filters=primary_filters,
        )
        comparison_cohort = None
        if comparison_config is not None:
            comparison_kind, comparison_source, comparison_filters = comparison_config
            if comparison_source is None:
                st.info("Choose a valid comparison population source.")
                return
            comparison_cohort = build_landscape_cohort(
                db,
                source_kind=comparison_kind,
                source=comparison_source,
                filters=comparison_filters,
            )
    except ValueError as exc:
        st.error(str(exc))
        return

    _render_analysis_preview(
        db,
        primary_cohort,
        comparison_cohort=comparison_cohort,
        dimension_ids=dimension_ids,
        feature_definitions=feature_definitions,
    )
    _save_analysis_panel(
        db,
        primary_cohort,
        comparison_cohort=comparison_cohort,
        dimension_ids=dimension_ids,
        feature_definitions=feature_definitions,
    )


def _saved_analyses(db):
    rows = list_landscape_analyses(db, limit=250)
    if not rows:
        with region("landscape-saved-empty", border=True):
            st.subheader("Saved analyses")
            st.info("No saved Landscape analyses yet.")
        return

    by_id = {int(row["id"]): row for row in rows}
    ids = list(by_id)
    selected = st.selectbox(
        "Saved analysis",
        ids,
        format_func=lambda analysis_id: (
            f'#{analysis_id} · {by_id[analysis_id]["name"]} · '
            f'{by_id[analysis_id]["created_at"]}'
        ),
        key="landscape-saved-analysis",
    )
    saved = get_landscape_analysis(db, int(selected))
    if saved is None:
        st.error("The selected saved analysis is no longer available.")
        return

    dimension_ids = [str(rec["id"]) for rec in saved["dimensions"]]
    with region("landscape-saved-summary", border=True):
        st.subheader(saved["name"])
        st.caption(
            f'Analysis #{saved["id"]} · core {saved["core_version"]} · '
            f'registry {saved["dimension_registry_hash"][:12]}… · '
            f'created {saved["created_at"]}'
        )
        st.caption(
            "Saved Landscape output is exploratory research context. "
            "Opening it does not promote or mutate scientific evidence."
        )

    _render_analysis_preview(
        db,
        saved["primary_cohort"],
        comparison_cohort=saved["comparison_cohort"],
        dimension_ids=dimension_ids,
        feature_definitions=saved["feature_definitions"],
    )


def _owner_links():
    with region("landscape-owner-links", border=True):
        st.subheader("Detail owners")
        st.caption(
            "Landscape compares populations. Proof work, individual curve records, "
            "and Pipeline/Candidate ownership remain on their specialist pages."
        )
        c1, c2, c3 = st.columns(3, gap="small")
        with c1:
            st.button(
                "Curves",
                key="landscape-open-curves",
                width="stretch",
                on_click=_go_to,
                args=("Curves",),
            )
        with c2:
            st.button(
                "Candidate Pools",
                key="landscape-open-pools",
                width="stretch",
                on_click=_go_to,
                args=("Candidate Pools",),
            )
        with c3:
            st.button(
                "Pipelines",
                key="landscape-open-pipelines",
                width="stretch",
                on_click=_go_to,
                args=("Pipelines",),
            )


def page(db, ctx):
    title(
        "Landscape",
        "Reproducible comparative research across frozen cohorts using registered dimensions and explicit provenance.",
        icon="landscape",
    )
    st.info(
        "Landscape is exploratory: it can reveal patterns worth investigating, "
        "but it never upgrades heuristic/numerical observations into mathematical proof."
    )

    current = str(st.session_state.get("landscape-view") or "Explore")
    choice = tabs(
        ["Explore", "Saved analyses"],
        value=current,
        key="landscape-tabs",
    )
    st.session_state["landscape-view"] = choice

    if choice == "Saved analyses":
        _saved_analyses(db)
    else:
        _explore(db)

    _owner_links()
