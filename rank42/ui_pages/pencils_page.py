"""Pencil registry and guided pencil construction."""
from __future__ import annotations

import json

import streamlit as st
from pathlib import Path

from rank42.ui_store import enqueue_job

from rank42.plugins import discover_plugins
from rank42.curve_research_state import curve_rank_summary_map
from .common import setting, title


def _plugin_labels(project_root):
    try:
        return {
            str(plugin.id): str(plugin.name)
            for plugin in discover_plugins(project_root)
        }
    except Exception:
        return {}


def _pencil_rows(db):
    if not db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='pencils'"
    ).fetchone():
        return None
    return db.execute(
        """SELECT p.id, p.plugin_id, p.pencil_key, p.label, p.status,
                  p.definition_json, p.created_at, p.updated_at,
                  COUNT(pc.curve_id) AS specialization_count
           FROM pencils p
           LEFT JOIN pencil_curves pc ON pc.pencil_id = p.id
           GROUP BY p.id
           ORDER BY p.label COLLATE NOCASE, p.plugin_id, p.pencil_key"""
    ).fetchall()


def _definition(row):
    try:
        value = json.loads(row["definition_json"] or "{}")
    except (TypeError, ValueError):
        return {"_invalid_definition_json": row["definition_json"]}
    return value if isinstance(value, dict) else {"value": value}


def _open_curve(curve_id):
    st.session_state["curves_selected_id"] = int(curve_id)
    st.session_state["curves_detail_id"] = int(curve_id)
    st.session_state["rh_page"] = "Curves"
    st.rerun()


def _render_create_pencil(db, ctx):
    """Expose the existing exact X1152 2-neighbor constructor from the core page."""
    st.subheader("Build an X1152 2-neighbor pencil")
    st.write(
        "An elliptic pencil is a family of curves indexed by a parameter. "
        "This constructor starts with a supported X1152 split-cubic fibration "
        "and two exact sections. Their divisor defines a genus-one pencil; "
        "Rank Hunter then computes its Jacobian model."
    )
    st.markdown(
        "1. Choose a source fibration.\n"
        "2. Choose two sections (the default `S0,S3` pair is preselected).\n"
        "3. Queue the exact Sage construction. The resulting model is saved under `runs/x1152-neighbor-hunt/`."
    )
    st.caption(
        "The source sections are curves on the surface. Their divisor gives a "
        "map to the parameter line; each fiber is a genus-one curve. Computing "
        "the Jacobian turns that into a Weierstrass fibration that Rank Hunter can search."
    )
    source_labels = {
        "ICARM #3926 · exact 17-section packet": "3926",
        "ICARM #848 · exact 17-section packet": "848",
        "ICARM #3900 · exact 10-section packet": "3900",
    }
    source_label = st.selectbox(
        "Source fibration", list(source_labels), key="pencils-create-x1152-source"
    )
    source_id = source_labels[source_label]
    section_pairs = {
        "S0 + S3 (recommended starting pair)": "S0,S3",
        "S0 + SD": "S0,SD",
        "S0 + SE": "S0,SE",
    }
    if source_id in {"848", "3926"}:
        section_pairs["S0 + PQ"] = "S0,PQ"
    pair_label = st.selectbox(
        "Two sections defining the pencil", list(section_pairs),
        key=f"pencils-create-x1152-pair-{source_id}",
    )
    st.caption(
        "A 2-neighbor is a new fibration on the same surface obtained from a "
        "different divisor. It is not a claim that the new fibration has higher rank."
    )
    safe_pair = section_pairs[pair_label].replace(",", "-").lower()
    output = (
        Path(ctx.project_root) / "runs" / "x1152-neighbor-hunt"
        / f"pencils-page-icarm{source_id}-2neighbor-{safe_pair}.json"
    )
    if st.button("Create pencil", type="primary", key=f"pencils-create-x1152-run-{source_id}"):
        script = Path(ctx.project_root) / "plugins" / "extensions" / "x1152_neighbor_hunt" / "construct_two_neighbor.sage"
        if not script.is_file():
            st.error("The X1152 2-neighbor constructor is not installed in this Rank Hunter project.")
            return
        output.parent.mkdir(parents=True, exist_ok=True)
        command = [
            str(setting(db, "science_python", ctx.detected_science_python())),
            str(script), str(output),
            source_id, str(ctx.db_path), "--pair-labels", section_pairs[pair_label],
        ]
        job_id = enqueue_job(
            ctx.db_path, kind="x1152_neighbor_hunt",
            label=f"Create ICARM #{source_id} 2-neighbor pencil ({section_pairs[pair_label]})",
            command=command, cwd=ctx.project_root,
            metadata={"operation": "construct_two_neighbor", "source_id": source_id,
                      "neighbor_degree": 2, "section_pair": section_pairs[pair_label],
                      "output": str(output)},
            priority=90, resource_class="sage_heavy", start_dispatcher=True,
        )
        st.success(f"Queued exact pencil construction as job #{job_id}. Open Jobs to follow it.")
        st.caption(f"Output model: `{output}`")


def page(db, ctx):
    title("Pencils", kicker="Analysis")
    inventory_tab, create_tab = st.tabs(["Pencil Registry", "Create Pencil"])
    with create_tab:
        _render_create_pencil(db, ctx)
    with inventory_tab:
        _render_registry(db, ctx)


def _render_registry(db, ctx):
    rows = _pencil_rows(db)
    if rows is None:
        st.info("The pencil registry is not available in this database yet.")
        return

    plugins = _plugin_labels(ctx.project_root)
    search = st.text_input(
        "Find a pencil",
        placeholder="Search label, owner, key, or definition…",
        key="pencils-search",
    ).strip().casefold()
    statuses = ["All statuses", "Registered", "Constructed", "Explored"]
    selected_status = st.selectbox(
        "Status", statuses, key="pencils-status-filter", label_visibility="collapsed"
    )
    wanted_status = selected_status.casefold()

    filtered = []
    for row in rows:
        definition = _definition(row)
        owner = plugins.get(str(row["plugin_id"]), str(row["plugin_id"]))
        if wanted_status != "all statuses" and str(row["status"]).casefold() != wanted_status:
            continue
        searchable = " ".join(
            (str(row["label"]), str(row["plugin_id"]), owner,
             str(row["pencil_key"]), json.dumps(definition, sort_keys=True))
        ).casefold()
        if search and search not in searchable:
            continue
        filtered.append((row, definition, owner))

    explored = sum(str(row["status"]) == "explored" for row in rows)
    constructed = sum(str(row["status"]) == "constructed" for row in rows)
    c1, c2, c3 = st.columns(3)
    c1.metric("Registered pencils", f"{len(rows):,}")
    c2.metric("Explored", f"{explored:,}")
    c3.metric("Constructed", f"{constructed:,}")

    st.caption(
        "A pencil is a registered elliptic fibration. Its linked curves are "
        "specializations; repeated charts and searches remain grouped under "
        "their parent pencil."
    )
    if not filtered:
        st.info("No pencils match these filters." if rows else "No pencils have been registered yet.")
        return

    # Show a scannable summary before the per-pencil details. Rank values come
    # from the same merged evidence view used by the Curves inventory.
    linked = {
        int(row["id"]): db.execute(
            "SELECT curve_id FROM pencil_curves WHERE pencil_id=? ORDER BY curve_id",
            (int(row["id"]),),
        ).fetchall()
        for row, _definition_value, _owner in filtered
    }
    all_curve_ids = [int(curve["curve_id"]) for curves in linked.values() for curve in curves]
    rank_states = curve_rank_summary_map(db, all_curve_ids)
    overview = []
    for row, _definition_value, _owner in filtered:
        curve_ids = [int(curve["curve_id"]) for curve in linked[int(row["id"])]]
        ranked = [
            (curve_id, rank_states[curve_id])
            for curve_id in curve_ids
            if curve_id in rank_states and not rank_states[curve_id]["rank_inconsistent"]
        ]
        best_lower = max((int(state["rigorous_lower"]) for _, state in ranked), default=0)
        best_exact = next(
            (int(state["exact_rank"]) for _, state in ranked
             if state["exact_rank"] is not None and int(state["exact_rank"]) == best_lower),
            None,
        )
        best_curve_id = next(
            (curve_id for curve_id, state in ranked
             if int(state["rigorous_lower"]) == best_lower),
            None,
        ) if best_lower else None
        rank_label = (
            f"={best_exact}" if best_exact is not None else f"≥{best_lower}"
        ) if best_lower else "—"
        overview.append({
            "Pencil": str(row["label"]),
            "Status": str(row["status"]).title(),
            "Best rank": rank_label,
            "Best curve": f"#{best_curve_id}" if best_curve_id is not None else "—",
            "Specializations": len(curve_ids),
            "_rank_sort": best_lower,
        })
    overview.sort(key=lambda item: (
        -item["_rank_sort"],
        item["Pencil"].casefold(),
    ))
    st.subheader("Pencil overview")
    st.dataframe(
        [{key: value for key, value in item.items() if key != "_rank_sort"}
         for item in overview],
        hide_index=True,
        width="stretch",
    )
    st.caption("Specializations counts distinct linked curves; best rank uses stored rigorous evidence.")

    for row, definition, owner in filtered:
        label = str(row["label"])
        status = str(row["status"]).title()
        count = int(row["specialization_count"] or 0)
        with st.expander(f"{label} · {status} · {count:,} specializations"):
            st.caption(f"Owner: {owner} · Key: `{row['pencil_key']}`")
            if definition:
                st.json(definition, expanded=False)
            if count:
                curves = db.execute(
                    """SELECT c.id, c.family, c.parameter, c.exact_rank,
                              c.generic_lower, c.descent_lower
                       FROM pencil_curves pc JOIN curves c ON c.id=pc.curve_id
                       WHERE pc.pencil_id=? ORDER BY c.id""",
                    (int(row["id"]),),
                ).fetchall()
                st.markdown("**Linked specializations**")
                for curve in curves:
                    rank = (
                        f"rank {int(curve['exact_rank'])}"
                        if curve["exact_rank"] is not None
                        else f"lower bound ≥{max(int(curve['generic_lower'] or 0), int(curve['descent_lower'] or 0))}"
                    )
                    description = (
                        f"Curve #{int(curve['id'])} · {curve['family']} · "
                        f"{curve['parameter']} · {rank}"
                    )
                    st.button(
                        description,
                        key=f"pencil-open-curve-{int(row['id'])}-{int(curve['id'])}",
                        on_click=_open_curve,
                        args=(int(curve["id"]),),
                        width="stretch",
                    )
            else:
                st.caption("No specializations are linked yet.")
