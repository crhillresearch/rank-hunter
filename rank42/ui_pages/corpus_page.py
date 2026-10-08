from __future__ import annotations

import json

import streamlit as st

from rank42.plugins import (
    Plugin,
    discover_plugins,
    get_plugin,
    get_variant,
)
from rank42.research_library import (
    LIBRARY_CATEGORIES,
    LIBRARY_CATEGORY_LABELS,
    LIBRARY_MAPPING_ROLES,
    LIBRARY_MAPPING_ROLE_LABELS,
    add_research_artifact,
    create_research_library,
    delete_research_library,
    library_dir,
    list_research_libraries,
    load_research_library,
    mapped_preview_rows,
    research_library_artifacts,
    research_library_mapping_draft,
    save_research_library_mapping_draft,
)
from rank42.research_library_preview import preview_research_artifact
from rank42.research_library_projection import (
    build_research_projection,
    count_research_projection_records,
    research_projection_inventory,
    research_projection_status,
    search_research_projection,
)
from rank42.research_library_promote import (
    create_candidate_pool_from_research_projection,
)
from rank42.ui_components import rh_key, selectable_dataframe, tabs
from .common import launch, section_title, setting, title


def _open_pool_in_pipelines(plugin, variant, pool):
    """Carry an explicit corpus-derived population into the Pipeline editor."""
    st.session_state["byo_builder_state_version"] = 2
    st.session_state["byo_stage_mode"] = "family"
    st.session_state["byo-target-mode"] = "Family"
    st.session_state["byo_target_mode_revision"] = int(
        st.session_state.get("byo_target_mode_revision") or 0
    ) + 1
    st.session_state["byo-family"] = f"{plugin.id}:{variant.id}"
    st.session_state["byo-family-source"] = "Existing pool / corpus"
    st.session_state["byo-family-pool"] = int(pool["id"])
    st.session_state["byo-family-only-unsearched"] = False
    st.session_state["byo_stages"] = []
    st.session_state["byo_loaded_pipeline_id"] = None
    st.session_state.pop("byo_duplicate_source_pipeline_id", None)
    st.session_state.pop("byo_pipeline_name_input", None)
    st.session_state["byo-functional-features"] = []
    st.session_state["builder_main_view"] = "Editor"
    st.session_state["candidates_selected_pool_id"] = int(pool["id"])
    st.session_state["family_search_pool_id"] = int(pool["id"])
    st.session_state["rh_page"] = "Pipelines"


_LIBRARY_BROWSER_ROLE_FILTERS = {
    "Any semantic content": (),
    "Parameters": ("parameter",),
    "Family / curve labels": ("family_label", "curve_label"),
    "Curve models": ("a1", "a2", "a3", "a4", "a6"),
    "Point coordinates": ("x", "y"),
    "Rank claims": (
        "rank_claim",
        "rank_lower_claim",
        "rank_upper_claim",
        "exact_rank_claim",
    ),
    "Source / notes": ("source", "notes"),
}


def _queue_library_artifact_for_build(library_id, artifact_id):
    st.session_state["research-library-selected-id"] = str(library_id)
    st.session_state[f"research-library-preview-select-{library_id}"] = str(
        artifact_id
    )
    st.session_state["libraries-section-pending"] = "Build Library"


def _render_library_browser(db, ctx):
    libraries = list_research_libraries(ctx.project_root)
    if not libraries:
        st.info("No researcher-created Libraries yet. Create one under Build Library.")
        return

    by_id = {str(library["id"]): library for library in libraries}
    library_ids = list(by_id)
    current_id = str(
        st.session_state.get("research-library-browser-id")
        or st.session_state.get("research-library-selected-id")
        or library_ids[0]
    )
    if current_id not in by_id:
        current_id = library_ids[0]

    selected_id = st.selectbox(
        "Researcher Library",
        library_ids,
        index=library_ids.index(current_id),
        format_func=lambda value: (
            f"{by_id[str(value)]['name']} · "
            f"{len(by_id[str(value)].get('artifacts') or []):,} artifact(s)"
        ),
        key="research-library-browser-id",
    )
    st.session_state["research-library-selected-id"] = str(selected_id)
    manifest = by_id[str(selected_id)]

    inventory = research_projection_inventory(ctx.project_root, selected_id)
    current = [
        row for row in inventory
        if row["projection_state"] == "current"
    ]
    stale = [
        row for row in inventory
        if row["projection_state"] == "stale"
    ]
    projected_records = sum(int(row["record_count"] or 0) for row in current)

    with st.container(border=True):
        section_title(
            manifest["name"],
            manifest.get("description") or "Researcher-created external Library",
        )
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Artifacts", len(inventory))
        m2.metric("Current projections", len(current))
        m3.metric("Projected records", projected_records)
        m4.metric("Stale projections", len(stale))
        st.caption(
            "Library-wide search includes current projections only. Stale mappings "
            "remain visible below but are excluded until explicitly rebuilt."
        )

    section_title(
        "Artifact inventory",
        "Original artifacts, mapping roles, and derived-projection state.",
    )
    if not inventory:
        st.info("This Library has no artifacts yet.")
        return

    state_labels = {
        "current": "current",
        "stale": "STALE",
        "mapped_not_built": "mapped · not built",
        "mapped_empty": "mapped · no fields",
        "unmapped": "unmapped",
    }
    inventory_display = [
        {
            "file": row["original_name"],
            "category": LIBRARY_CATEGORY_LABELS.get(
                row["category"], row["category"]
            ),
            "mapping": (
                ", ".join(
                    LIBRARY_MAPPING_ROLE_LABELS.get(role, role)
                    for role in row["mapping_roles"]
                )
                or "—"
            ),
            "projection": state_labels.get(
                row["projection_state"], row["projection_state"]
            ),
            "records": int(row["record_count"] or 0),
            "parser": row["parser"] or "—",
            "built": str(row["built_at"] or "")[:19] or "—",
        }
        for row in inventory
    ]
    selected_artifact = selectable_dataframe(
        inventory_display,
        inventory,
        semantic=f"research-library-browser-artifacts-{selected_id}",
        selected_id=st.session_state.get(
            f"research-library-browser-artifact-id-{selected_id}"
        ),
        id_key="artifact_id",
        selection_state_key=f"research-library-browser-artifact-id-{selected_id}",
        width="stretch",
        hide_index=True,
        localized_integer_columns=("records",),
    )
    if selected_artifact is not None:
        a1, a2, a3 = st.columns([1.4, 1.0, 1.0])
        a1.caption(
            f"SHA-256 · {str(selected_artifact['sha256'])}"
        )
        a2.caption(
            f"{int(selected_artifact['size_bytes']):,} bytes · "
            f"{selected_artifact['media_type'] or 'unknown media type'}"
        )
        with a3:
            if st.button(
                "Open artifact in Build Library",
                width="stretch",
                key=(
                    f"research-library-browser-open-artifact-"
                    f"{selected_id}-{selected_artifact['artifact_id']}"
                ),
            ):
                _queue_library_artifact_for_build(
                    selected_id,
                    selected_artifact["artifact_id"],
                )
                st.rerun()

    if not current:
        st.info(
            "No current external projections are available to search. Map and build "
            "an artifact under Build Library first."
        )
        return

    st.html("<div style='height:.65rem'></div>")
    section_title(
        "Search Library",
        "Search normalized external records across all current projections. "
        "Rank values shown here remain claims, not Rank Hunter proof state.",
    )

    current_by_id = {row["artifact_id"]: row for row in current}
    current_ids = list(current_by_id)
    category_values = sorted({row["category"] for row in current})
    artifact_options = [""] + current_ids
    category_options = [""] + category_values

    f1, f2, f3 = st.columns([1.5, 1.15, 1.15])
    with f1:
        query = st.text_input(
            "Search",
            value="",
            placeholder="parameter, family, point, rank claim, notes…",
            key=f"research-library-browser-query-{selected_id}",
        )
    with f2:
        artifact_filter = st.selectbox(
            "Artifact",
            artifact_options,
            format_func=lambda value: (
                "All current projections"
                if not value
                else current_by_id[str(value)]["original_name"]
            ),
            key=f"research-library-browser-artifact-filter-{selected_id}",
        )
    with f3:
        category_filter = st.selectbox(
            "Category",
            category_options,
            format_func=lambda value: (
                "Any category"
                if not value
                else LIBRARY_CATEGORY_LABELS.get(str(value), str(value))
            ),
            key=f"research-library-browser-category-{selected_id}",
        )

    semantic_label = st.selectbox(
        "Semantic content",
        list(_LIBRARY_BROWSER_ROLE_FILTERS),
        key=f"research-library-browser-role-filter-{selected_id}",
    )
    roles = _LIBRARY_BROWSER_ROLE_FILTERS[semantic_label]

    eligible_ids = list(current_ids)
    if artifact_filter:
        eligible_ids = [
            artifact_id for artifact_id in eligible_ids
            if artifact_id == str(artifact_filter)
        ]
    if category_filter:
        eligible_ids = [
            artifact_id for artifact_id in eligible_ids
            if current_by_id[artifact_id]["category"] == str(category_filter)
        ]

    match_count = count_research_projection_records(
        ctx.project_root,
        selected_id,
        artifact_ids=eligible_ids,
        query=query or None,
        roles=roles or None,
    )
    matches = search_research_projection(
        ctx.project_root,
        selected_id,
        artifact_ids=eligible_ids,
        query=query or None,
        roles=roles or None,
        limit=500,
    )
    st.caption(
        f"{match_count:,} current projected record(s) match"
        + (
            f" · showing first {len(matches):,}"
            if match_count > len(matches)
            else ""
        )
    )
    if not matches:
        st.info("No current projected records match these filters.")
        return

    browser_records = []
    for row in matches:
        record = dict(row)
        record["_browser_id"] = (
            f"{record['artifact_id']}:{int(record['record_index'])}"
        )
        browser_records.append(record)

    preferred = [
        "parameter",
        "family_label",
        "curve_label",
        "a1",
        "a2",
        "a3",
        "a4",
        "a6",
        "x",
        "y",
        "rank_claim",
        "rank_lower_claim",
        "rank_upper_claim",
        "exact_rank_claim",
        "source",
        "notes",
    ]
    visible_roles = [
        key
        for key in preferred
        if any(row.get(key) not in (None, "") for row in browser_records)
    ]
    result_display = []
    for row in browser_records:
        artifact = current_by_id[str(row["artifact_id"])]
        display = {
            "file": artifact["original_name"],
            "record": int(row["record_index"]),
            "category": LIBRARY_CATEGORY_LABELS.get(
                artifact["category"], artifact["category"]
            ),
        }
        display.update({key: row.get(key) for key in visible_roles})
        result_display.append(display)

    selected_record = selectable_dataframe(
        result_display,
        browser_records,
        semantic=f"research-library-browser-results-{selected_id}",
        selected_id=st.session_state.get(
            f"research-library-browser-record-id-{selected_id}"
        ),
        id_key="_browser_id",
        selection_state_key=f"research-library-browser-record-id-{selected_id}",
        width="stretch",
        hide_index=True,
        localized_integer_columns=("record",),
    )
    if selected_record is None:
        return

    source_artifact = current_by_id[str(selected_record["artifact_id"])]
    with st.container(border=True):
        section_title(
            "Selected projected record",
            (
                f"{source_artifact['original_name']} · "
                f"record {int(selected_record['record_index']):,}"
            ),
        )
        st.caption(
            f"Artifact SHA-256 · {source_artifact['sha256']} · "
            f"mapping roles: {', '.join(source_artifact['mapping_roles']) or '—'}"
        )
        d1, d2 = st.columns(2)
        with d1:
            with st.expander("Mapped record", expanded=True):
                try:
                    mapped = json.loads(selected_record.get("mapped_json") or "{}")
                except Exception:
                    mapped = {}
                st.json(mapped)
        with d2:
            with st.expander("Original source row", expanded=False):
                try:
                    raw = json.loads(selected_record.get("raw_json") or "{}")
                except Exception:
                    raw = {}
                st.json(raw)
        if st.button(
            "Open source artifact in Build Library",
            width="stretch",
            key=(
                f"research-library-browser-open-record-"
                f"{selected_id}-{selected_record['_browser_id']}"
            ),
        ):
            _queue_library_artifact_for_build(
                selected_id,
                selected_record["artifact_id"],
            )
            st.rerun()


def _family_plugin_options(ctx):
    plugins = [
        plugin
        for plugin in discover_plugins(ctx.project_root)
        if isinstance(plugin, Plugin) and plugin.plugin_type == "family"
    ]
    return sorted(plugins, key=lambda plugin: (plugin.name.lower(), plugin.id))


def _render_projection_curve_import(
    db,
    ctx,
    library_id,
    artifact_id,
    *,
    query,
    match_count,
    roles,
):
    required = {"a1", "a2", "a3", "a4", "a6"}
    if not required.issubset(set(roles.values())):
        return

    st.html("<div style='height:.65rem'></div>")
    section_title(
        "Import mapped curve models",
        "Create or match retained Curve/model records through Rank Hunter's existing "
        "exact external-curve importer. Rank claims remain provenance only.",
    )

    over_limit = int(match_count) > 5000
    st.caption(
        f"{int(match_count):,} currently matching projected record(s) will be preflighted "
        "as exact rational Weierstrass models. Singular models or key/model conflicts "
        "abort the batch before any new Curve is created."
    )
    st.caption(
        "Mapped x/y coordinates are ignored by this handoff; use Validate mapped points "
        "for Point Ledger import. Mapped rank fields are copied only to import provenance."
    )
    if over_limit:
        st.warning(
            "Library Curve import is capped at 5,000 projected rows per job. "
            "Narrow the projection search first."
        )

    if st.button(
        f"Import {int(match_count):,} mapped curve model(s)",
        type="primary",
        width="stretch",
        disabled=over_limit or int(match_count) < 1,
        key=f"research-library-curve-import-{library_id}-{artifact_id}",
    ):
        py = setting(db, "science_python", ctx.detected_science_python())
        cmd = [
            py,
            "-m",
            "rank42.research_library_curve_import",
            "--db",
            ctx.db_path,
            "--project-root",
            str(ctx.project_root),
            "--library-id",
            str(library_id),
            "--artifact-id",
            str(artifact_id),
            "--max-rows",
            5000,
        ]
        if query:
            cmd += ["--query", str(query)]
        jid = launch(
            ctx,
            db,
            kind="library_curve_import",
            label=(
                f"Library curve models · {int(match_count)} row(s)"
            ),
            command=cmd,
            metadata={
                "library_id": str(library_id),
                "artifact_id": str(artifact_id),
                "query": str(query or ""),
                "selection_count": int(match_count),
                "validation": "existing_exact_external_curve_importer",
                "rank_claim_policy": "provenance_only",
                "point_policy": "ignored_use_library_point_validation",
            },
        )
        st.success(
            f"Started Library Curve import job #{jid}. "
            "Imported models will appear in Curves with rank unresolved unless "
            "Rank Hunter already has independent evidence."
        )


def _render_projection_point_validation(
    db,
    ctx,
    library_id,
    artifact_id,
    *,
    query,
    match_count,
    roles,
):
    if not {"x", "y"}.issubset(set(roles.values())):
        return

    st.html("<div style='height:.65rem'></div>")
    section_title(
        "Validate mapped points",
        "Exact Sage validation against one existing stored curve. Only coordinates "
        "that reconstruct on that curve over QQ enter the Point Ledger.",
    )

    curve_rows = db.execute(
        """
        SELECT id,family,parameter,a_invariants_json,updated_at
        FROM curves
        WHERE a_invariants_json IS NOT NULL
          AND TRIM(a_invariants_json) NOT IN ('', '[]', 'null')
        ORDER BY updated_at DESC,id DESC
        LIMIT 5000
        """
    ).fetchall()
    if not curve_rows:
        st.info("No retained curve with a stored model is available for exact point validation.")
        return

    curve_by_id = {int(row["id"]): row for row in curve_rows}
    curve_ids = list(curve_by_id)
    curve_id = st.selectbox(
        "Existing curve for exact validation",
        curve_ids,
        format_func=lambda value: (
            f"#{int(value)} · {curve_by_id[int(value)]['family']} · "
            f"t={curve_by_id[int(value)]['parameter']}"
        ),
        key=f"research-library-point-curve-{library_id}-{artifact_id}",
    )

    over_limit = int(match_count) > 5000
    st.caption(
        f"{int(match_count):,} currently matching projected record(s) will be checked "
        "exactly against the selected curve. Valid points enter as exact candidates "
        "with independence still unknown; invalid coordinates are recorded only as rejections."
    )
    if over_limit:
        st.warning(
            "Exact Library point validation is capped at 5,000 projected rows per job. "
            "Narrow the projection search first."
        )

    if st.button(
        f"Exact-validate {int(match_count):,} mapped point row(s)",
        type="primary",
        width="stretch",
        disabled=over_limit or int(match_count) < 1,
        key=f"research-library-point-validate-{library_id}-{artifact_id}",
    ):
        py = setting(db, "science_python", ctx.detected_science_python())
        cmd = [
            py,
            "-m",
            "rank42.research_library_point_import",
            "--db",
            ctx.db_path,
            "--project-root",
            str(ctx.project_root),
            "--library-id",
            str(library_id),
            "--artifact-id",
            str(artifact_id),
            "--curve-id",
            int(curve_id),
            "--max-rows",
            5000,
        ]
        if query:
            cmd += ["--query", str(query)]
        jid = launch(
            ctx,
            db,
            kind="library_point_import",
            label=(
                f"Library exact points · curve #{int(curve_id)} · "
                f"{int(match_count)} row(s)"
            ),
            command=cmd,
            metadata={
                "library_id": str(library_id),
                "artifact_id": str(artifact_id),
                "curve_id": int(curve_id),
                "query": str(query or ""),
                "selection_count": int(match_count),
                "validation": "exact_on_stored_curve_over_QQ",
            },
        )
        st.success(
            f"Started exact Library point validation job #{jid}. "
            "Verified coordinates will appear in Points; rejected rows never become point records."
        )


def _render_projection_candidate_promotion(
    db,
    ctx,
    library_id,
    artifact_id,
    *,
    query,
    match_count,
):
    manifest = load_research_library(ctx.project_root, library_id)
    associated_plugin = str(manifest.get("plugin_id") or "").strip()
    family_plugins = _family_plugin_options(ctx)
    if associated_plugin:
        family_plugins = [
            plugin for plugin in family_plugins
            if plugin.id == associated_plugin
        ]
        if not family_plugins:
            st.warning(
                f"Associated family plugin {associated_plugin!r} is not currently installed."
            )
            return

    if not family_plugins:
        st.info("No family plugin is available for Candidate Pool handoff.")
        return

    st.html("<div style='height:.65rem'></div>")
    section_title(
        "Send to Candidate Pool",
        "This is the first explicit core-state handoff. It creates unsearched "
        "Candidate Pool rows only; external rank values remain provenance claims.",
    )
    plugin_by_id = {plugin.id: plugin for plugin in family_plugins}
    plugin_ids = list(plugin_by_id)
    p1, p2 = st.columns(2)
    plugin_id = p1.selectbox(
        "Family plugin",
        plugin_ids,
        format_func=lambda value: (
            f"{plugin_by_id[str(value)].name} ({value})"
        ),
        key=f"research-library-promote-plugin-{library_id}-{artifact_id}",
        disabled=bool(associated_plugin),
    )
    plugin = plugin_by_id[str(plugin_id)]
    variants = list(plugin.variants)
    if not variants:
        st.warning("Selected family plugin exposes no variants.")
        return
    variant_by_id = {variant.id: variant for variant in variants}
    variant_id = p2.selectbox(
        "Family variant",
        list(variant_by_id),
        format_func=lambda value: (
            f"{variant_by_id[str(value)].name} ({value})"
        ),
        key=f"research-library-promote-variant-{library_id}-{artifact_id}",
    )

    st.caption(
        f"{int(match_count):,} currently matching projected record(s) will become "
        "unsearched candidates. Rank claims are copied only into candidate provenance metadata."
    )
    if st.button(
        f"Create Candidate Pool from {int(match_count):,} record(s)",
        type="primary",
        width="stretch",
        key=f"research-library-promote-button-{library_id}-{artifact_id}",
    ):
        try:
            pool = create_candidate_pool_from_research_projection(
                db,
                ctx.project_root,
                library_id=library_id,
                artifact_id=artifact_id,
                plugin_id=plugin.id,
                variant_id=variant_id,
                query=query or None,
            )
        except (KeyError, OSError, ValueError) as exc:
            st.error(f"Candidate Pool handoff refused: {exc}")
        else:
            st.session_state["research-library-created-pool-id"] = int(pool["id"])
            st.success(
                f"Created Candidate Pool #{int(pool['id'])} · "
                f"{int(pool['candidate_count'] or 0):,} unsearched candidate(s)."
            )

    created_pool_id = st.session_state.get("research-library-created-pool-id")
    if created_pool_id:
        pool = db.execute(
            "SELECT * FROM candidate_pools WHERE id=?",
            (int(created_pool_id),),
        ).fetchone()
        if pool is not None:
            try:
                generation = json.loads(pool["generation_json"] or "{}")
            except Exception:
                generation = {}
            belongs_here = (
                str(generation.get("library_id") or "") == str(library_id)
                and str(generation.get("artifact_id") or "") == str(artifact_id)
            )
            if belongs_here:
                if st.button(
                    "Open created pool in Pipelines",
                    width="stretch",
                    key=f"research-library-open-pool-{library_id}-{artifact_id}",
                ):
                    chosen_plugin = get_plugin(ctx.project_root, str(pool["plugin_id"]))
                    chosen_variant = get_variant(
                        chosen_plugin,
                        str(generation.get("variant_id") or ""),
                    )
                    _open_pool_in_pipelines(chosen_plugin, chosen_variant, pool)
                    st.rerun()


def _render_library_delete(ctx, manifest):
    library_id = str(manifest["id"])
    library_name = str(manifest["name"])
    st.html("<div style='height:1rem'></div>")
    with st.expander("Danger zone · Delete Library", expanded=False):
        st.warning(
            "This permanently deletes the researcher Library manifest, immutable "
            "originals, mapping drafts, and external projection. It does not delete "
            "Candidate Pools or any other Rank Hunter core state created earlier."
        )
        confirmation = st.text_input(
            f"Type the Library name to confirm: {library_name}",
            value="",
            key=f"research-library-delete-confirm-{library_id}",
        )
        if st.button(
            "Delete Library permanently",
            type="primary",
            width="stretch",
            disabled=confirmation != library_name,
            key=f"research-library-delete-{library_id}",
        ):
            try:
                delete_research_library(
                    ctx.project_root,
                    library_id,
                    expected_name=confirmation,
                )
            except (OSError, ValueError) as exc:
                st.error(f"Library delete refused: {exc}")
            else:
                st.session_state.pop("research-library-selected-id", None)
                st.session_state.pop("research-library-created-pool-id", None)
                st.success(f"Deleted Library · {library_name}")
                st.rerun()


def _render_mapping_draft(db, ctx, library_id, artifact_id, preview):
    columns = [str(column) for column in list(preview.get("columns") or [])]
    rows = list(preview.get("rows") or [])
    if not columns or not rows:
        return

    parser = str(preview.get("parser") or "")
    saved = research_library_mapping_draft(
        ctx.project_root,
        library_id,
        artifact_id,
    )
    saved_roles = {}
    stale = False
    if saved:
        stale = (
            list(saved.get("columns") or []) != columns
            or str(saved.get("parser") or "") != parser
        )
        if not stale:
            saved_roles = dict(saved.get("roles") or {})

    st.html("<div style='height:.65rem'></div>")
    section_title(
        "Map fields (draft only)",
        "Describe what source columns appear to mean. Saving this draft does not "
        "create Curves, Points, Candidates, or proof evidence.",
    )
    if stale:
        st.warning(
            "The saved mapping draft does not match this parser/column shape. "
            "It will not be reused until you save a new draft."
        )

    role_options = ["", *LIBRARY_MAPPING_ROLES]
    with st.form(f"research-library-mapping-form-{library_id}-{artifact_id}"):
        assignments = {}
        for index, source_column in enumerate(columns):
            current = str(saved_roles.get(source_column) or "")
            if current not in role_options:
                current = ""
            selected = st.selectbox(
                f"Source column · {source_column}",
                role_options,
                index=role_options.index(current),
                format_func=lambda value: (
                    "Ignore / unmapped"
                    if not value
                    else LIBRARY_MAPPING_ROLE_LABELS[str(value)]
                ),
                key=(
                    f"research-library-map-{library_id}-{artifact_id}-"
                    f"{index}"
                ),
            )
            if selected:
                assignments[source_column] = str(selected)

        save_mapping = st.form_submit_button(
            "Save mapping draft",
            type="primary",
            width="stretch",
        )

    if save_mapping:
        try:
            saved = save_research_library_mapping_draft(
                ctx.project_root,
                library_id,
                artifact_id,
                parser=parser,
                columns=columns,
                roles=assignments,
            )
        except (KeyError, ValueError) as exc:
            st.error(f"Could not save mapping draft: {exc}")
        else:
            st.success(
                f"Saved {len(saved.get('roles') or {}):,} descriptive field mapping(s)."
            )
            st.rerun()

    current = research_library_mapping_draft(
        ctx.project_root,
        library_id,
        artifact_id,
    )
    if not current:
        st.caption("No mapping draft saved for this artifact.")
        return
    if (
        list(current.get("columns") or []) != columns
        or str(current.get("parser") or "") != parser
    ):
        st.caption("Saved mapping exists, but it belongs to a different preview shape.")
        return

    roles = dict(current.get("roles") or {})
    if not roles:
        st.caption("Mapping draft currently leaves every source column unmapped.")
        return

    st.caption(
        "Saved draft · "
        + " · ".join(
            f"{source} → {LIBRARY_MAPPING_ROLE_LABELS.get(role, role)}"
            for source, role in roles.items()
        )
    )
    claim_roles = {
        "rank_claim",
        "rank_lower_claim",
        "rank_upper_claim",
        "exact_rank_claim",
    }
    if claim_roles.intersection(roles.values()):
        st.info(
            "Mapped rank fields remain external claims only. A later explicit "
            "validation workflow would be required before any rigorous rank evidence exists."
        )

    projected = mapped_preview_rows(rows, current, limit=25)
    if projected:
        st.caption("Mapped preview · display only")
        st.dataframe(
            projected,
            width="stretch",
            hide_index=True,
            height=min(560, max(180, 38 * len(projected) + 40)),
        )

    st.html("<div style='height:.65rem'></div>")
    section_title(
        "External projection",
        "Materialize this mapping into a searchable Library-side SQLite projection. "
        "This remains external derived data and never writes rank42.db.",
    )
    projection = research_projection_status(
        ctx.project_root,
        library_id,
        artifact_id,
    )
    if projection.get("exists") and projection.get("stale"):
        st.warning(
            "The saved external projection is stale because the mapping draft "
            "changed. Rebuild before using it."
        )
    elif projection.get("exists"):
        st.caption(
            f"Current projection · {int(projection.get('record_count') or 0):,} record(s) · "
            f"built {str(projection.get('built_at') or '')[:19]}"
        )
    else:
        st.caption("No external projection has been built for this artifact yet.")

    if st.button(
        "Build / Rebuild external projection",
        type="primary",
        width="stretch",
        key=f"research-library-project-{library_id}-{artifact_id}",
    ):
        try:
            result = build_research_projection(
                ctx.project_root,
                library_id,
                artifact_id,
            )
        except (KeyError, OSError, ValueError) as exc:
            st.error(f"Projection build refused: {exc}")
        else:
            st.success(
                f"Built external projection with {int(result['record_count']):,} record(s)."
            )
            st.rerun()

    projection = research_projection_status(
        ctx.project_root,
        library_id,
        artifact_id,
    )
    if not projection.get("exists") or projection.get("stale"):
        return

    query = st.text_input(
        "Search external projection",
        value="",
        placeholder="parameter, family, curve label, point, rank claim, notes…",
        key=f"research-library-projection-query-{library_id}-{artifact_id}",
    )
    match_count = count_research_projection_records(
        ctx.project_root,
        library_id,
        artifact_id=artifact_id,
        query=query or None,
    )
    matches = search_research_projection(
        ctx.project_root,
        library_id,
        artifact_id=artifact_id,
        query=query or None,
        limit=250,
    )
    if not matches:
        st.info("No projected records match the current search.")
        return

    st.caption(
        f"{match_count:,} projected record(s) match"
        + (
            f" · showing first {len(matches):,}"
            if match_count > len(matches)
            else ""
        )
    )
    preferred = [
        "record_index",
        "parameter",
        "family_label",
        "curve_label",
        "a1",
        "a2",
        "a3",
        "a4",
        "a6",
        "x",
        "y",
        "rank_claim",
        "rank_lower_claim",
        "rank_upper_claim",
        "exact_rank_claim",
        "source",
        "notes",
    ]
    visible = [
        key
        for key in preferred
        if key == "record_index"
        or any(row.get(key) not in (None, "") for row in matches)
    ]
    st.dataframe(
        [
            {key: row.get(key) for key in visible}
            for row in matches
        ],
        width="stretch",
        hide_index=True,
        height=min(650, max(180, 38 * len(matches) + 40)),
    )
    st.caption(
        "Projection rows remain external research material. Rank fields are claims; "
        "searching them does not create rigorous evidence."
    )

    _render_projection_curve_import(
        db,
        ctx,
        library_id,
        artifact_id,
        query=query or None,
        match_count=match_count,
        roles=roles,
    )

    _render_projection_point_validation(
        db,
        ctx,
        library_id,
        artifact_id,
        query=query or None,
        match_count=match_count,
        roles=roles,
    )

    _render_projection_candidate_promotion(
        db,
        ctx,
        library_id,
        artifact_id,
        query=query or None,
        match_count=match_count,
    )


def _render_build_library(db, ctx):
    st.caption(
        "Researcher Libraries preserve original files outside rank42.db. Nothing is "
        "promoted automatically. Explicit handoffs are limited to unsearched Candidate "
        "Pools, exact point validation, and exact external Curve/model import. Library "
        "rank claims never become proof evidence directly."
    )

    with st.container(border=True):
        section_title(
            "Create Library",
            "Start with metadata only. You can add mixed artifacts after creation.",
        )
        family_plugins = _family_plugin_options(ctx)
        plugin_by_id = {plugin.id: plugin for plugin in family_plugins}
        plugin_options = [""] + [plugin.id for plugin in family_plugins]
        with st.form("research-library-create-form"):
            name = st.text_input(
                "Library name",
                placeholder="e.g. Old Elkies experiments",
            )
            description = st.text_area(
                "Description",
                placeholder="What is this material, where did it come from, and why is it useful?",
                height=90,
            )
            plugin_id = st.selectbox(
                "Family plugin association",
                plugin_options,
                format_func=lambda value: (
                    "None"
                    if not value
                    else f"{plugin_by_id[value].name} ({value})"
                ),
                help=(
                    "Optional organizational link only. Association does not turn "
                    "Library claims into Rank Hunter evidence."
                ),
            )
            submitted = st.form_submit_button(
                "Create Library",
                type="primary",
                width="stretch",
            )
        if submitted:
            try:
                manifest = create_research_library(
                    ctx.project_root,
                    name=name,
                    description=description,
                    plugin_id=plugin_id or None,
                )
            except ValueError as exc:
                st.error(str(exc))
            else:
                st.session_state["research-library-selected-id"] = manifest["id"]
                st.success(f"Created Library · {manifest['name']}")
                st.rerun()

    libraries = list_research_libraries(ctx.project_root)
    if not libraries:
        st.info("No researcher-created Libraries yet.")
        return

    by_id = {str(library["id"]): library for library in libraries}
    ids = list(by_id)
    selected_id = str(
        st.session_state.get("research-library-selected-id") or ids[0]
    )
    if selected_id not in by_id:
        selected_id = ids[0]
    selected_id = st.selectbox(
        "Researcher Library",
        ids,
        index=ids.index(selected_id),
        format_func=lambda value: (
            f"{by_id[str(value)]['name']} · "
            f"{len(by_id[str(value)].get('artifacts') or []):,} artifact(s)"
        ),
        key="research-library-selected-id",
    )
    manifest = by_id[str(selected_id)]

    with st.container(border=True):
        section_title(manifest["name"], manifest.get("description") or None)
        c1, c2, c3 = st.columns(3)
        c1.metric("Artifacts", len(manifest.get("artifacts") or []))
        c2.metric("Plugin", manifest.get("plugin_id") or "None")
        c3.metric("Schema", int(manifest.get("schema_version") or 0))
        st.caption(
            f"Vault: {library_dir(ctx.project_root, selected_id)} · "
            "original bytes are content-addressed and immutable."
        )

        a1, a2 = st.columns([1.25, 1.0])
        with a1:
            uploads = st.file_uploader(
                "Add files",
                accept_multiple_files=True,
                key=f"research-library-files-{selected_id}",
                help=(
                    "This first Libraries chunk stores originals only. Parser previews "
                    "and normalized projections come later."
                ),
            )
        with a2:
            category = st.selectbox(
                "Artifact category",
                list(LIBRARY_CATEGORIES),
                format_func=lambda value: LIBRARY_CATEGORY_LABELS[str(value)],
                key=f"research-library-category-{selected_id}",
            )

        if st.button(
            "Add files to Library",
            type="primary",
            width="stretch",
            disabled=not uploads,
            key=f"research-library-add-{selected_id}",
        ):
            added = duplicates = 0
            try:
                for upload in uploads or []:
                    _artifact, created = add_research_artifact(
                        ctx.project_root,
                        selected_id,
                        original_name=upload.name,
                        data=upload.getvalue(),
                        category=category,
                        media_type=getattr(upload, "type", None),
                    )
                    if created:
                        added += 1
                    else:
                        duplicates += 1
            except (OSError, ValueError) as exc:
                st.error(f"Could not add artifact: {exc}")
            else:
                message = f"Added {added:,} immutable artifact(s)."
                if duplicates:
                    message += f" {duplicates:,} duplicate(s) already existed."
                st.success(message)
                st.rerun()

    artifacts = research_library_artifacts(ctx.project_root, selected_id)
    section_title("Artifacts", "Original-file vault; no parser or scientific promotion yet.")
    if not artifacts:
        st.info("This Library has no artifacts yet.")
        _render_library_delete(ctx, manifest)
        return

    st.dataframe(
        [
            {
                "file": artifact["original_name"],
                "category": LIBRARY_CATEGORY_LABELS.get(
                    artifact["category"], artifact["category"]
                ),
                "size": int(artifact["size_bytes"]),
                "sha256": str(artifact["sha256"])[:16] + "…",
                "media type": artifact["media_type"],
                "added": artifact["added_at"],
            }
            for artifact in artifacts
        ],
        width="stretch",
        hide_index=True,
        column_config={
            "size": st.column_config.NumberColumn("bytes", format="%d"),
        },
    )
    st.caption(
        "Stored artifacts remain external research material. No rank claim, point, "
        "curve, or certificate is promoted by this page."
    )

    st.html("<div style='height:.65rem'></div>")
    section_title(
        "Preview artifact",
        "Bounded read-only inspection. Previewing never creates a normalized record "
        "or changes scientific state.",
    )
    artifact_by_id = {
        str(artifact["id"]): artifact
        for artifact in artifacts
    }
    artifact_ids = list(artifact_by_id)
    preview_id = st.selectbox(
        "Artifact to preview",
        artifact_ids,
        format_func=lambda value: (
            f"{artifact_by_id[str(value)]['original_name']} · "
            f"{LIBRARY_CATEGORY_LABELS.get(artifact_by_id[str(value)]['category'], artifact_by_id[str(value)]['category'])}"
        ),
        key=f"research-library-preview-select-{selected_id}",
    )
    preview_key = f"research-library-preview-result-{selected_id}"
    if st.button(
        "Preview selected artifact",
        width="stretch",
        key=f"research-library-preview-button-{selected_id}",
    ):
        st.session_state[preview_key] = preview_research_artifact(
            ctx.project_root,
            selected_id,
            preview_id,
        )

    preview = st.session_state.get(preview_key)
    if preview and str(preview.get("artifact_id")) == str(preview_id):
        status = str(preview.get("status") or "error")
        parser = str(preview.get("parser") or "none")
        meta = dict(preview.get("meta") or {})
        if status == "too_large":
            st.warning(
                f"Preview skipped: {int(meta.get('size_bytes') or 0):,} bytes exceeds "
                f"the {int(meta.get('max_preview_bytes') or 0):,}-byte preview limit."
            )
        elif status == "unsupported":
            st.info(
                "No read-only preview parser is available for this artifact type yet. "
                "The original file remains safely stored."
            )
        elif status == "error":
            st.warning(f"Preview failed safely: {meta.get('error') or 'unknown parser error'}")
        else:
            details = [f"parser: {parser}"]
            if meta.get("size_bytes") is not None:
                details.append(f"{int(meta['size_bytes']):,} bytes")
            if meta.get("encoding"):
                details.append(str(meta["encoding"]))
            if meta.get("preview_sheet"):
                details.append(f"sheet: {meta['preview_sheet']}")
            if meta.get("delimiter"):
                details.append(f"delimiter: {meta['delimiter']}")
            st.caption(" · ".join(details))

            rows = list(preview.get("rows") or [])
            if rows:
                st.dataframe(
                    rows,
                    width="stretch",
                    hide_index=True,
                    height=min(560, max(180, 38 * len(rows) + 40)),
                )
            elif preview.get("text") is not None:
                st.code(str(preview.get("text") or ""), language=None)
            else:
                st.info("The parser found no previewable rows or text.")

            parse_errors = list(meta.get("parse_errors") or [])
            if parse_errors:
                st.warning(
                    f"{len(parse_errors)} JSONL parse error(s) in the preview window."
                )
                with st.expander("Preview parse errors"):
                    for error in parse_errors:
                        st.code(str(error), language="text")

            _render_mapping_draft(
                db,
                ctx,
                selected_id,
                preview_id,
                preview,
            )

    _render_library_delete(ctx, manifest)


def page(db, ctx):
    title(
        "Libraries",
        None,
        "Data",
        icon="dataset",
    )
    pending = st.session_state.pop("libraries-section-pending", None)
    if pending in {"Browse Library", "Build Library"}:
        st.session_state[f"_{rh_key('libraries-section')}_value"] = pending

    section = tabs(
        ["Browse Library", "Build Library"],
        value="Browse Library",
        key="libraries-section",
        variant="line",
        width="content",
    )
    st.html("<div style='height:.85rem'></div>")
    if section == "Build Library":
        _render_build_library(db, ctx)
    else:
        _render_library_browser(db, ctx)

