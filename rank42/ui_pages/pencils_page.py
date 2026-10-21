"""Pencil registry and guided pencil construction."""
from __future__ import annotations

import json
import hashlib
import html
import re

import streamlit as st
from pathlib import Path

from rank42.ui_store import enqueue_job

from rank42.plugins import discover_plugins
from rank42.pencil_registry import register_pencil
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


def _packet_labels(project_root, source_id):
    paths = {
        "3900": "plugins/families/x1152_mw17_exact/exact_sections_3900.json",
        "3926": "plugins/families/x1152_mw17_exact/exact_sections.json",
        "848": "plugins/families/x1152_mw17_848/exact_sections.json",
    }
    packet_path = Path(project_root) / paths[source_id]
    try:
        packet = json.loads(packet_path.read_text(encoding="utf-8"))
        rows = packet.get("sections", packet.get("exact_sections", []))
    except (OSError, ValueError):
        return []
    labels = ["S0", "SD", "SE", "S3"]
    if source_id in {"848", "3926"}:
        labels += ["P0", "PD", "PQ"]
    section_index = 0
    for row in rows:
        if not isinstance(row, dict) or row.get("y_coefficients") is None and not row.get("y_variants"):
            continue
        labels.append(str(row.get("label") or row.get("forced_label") or f"R{section_index + 1:03d}"))
        section_index += 1
    return list(dict.fromkeys(labels)) if section_index >= 2 else []


def _saved_model_sections(path):
    """Read the exact transferred sections required by the recursive constructor."""
    if not path.is_file() or path.stat().st_size > 32_000_000:
        return [], "Choose a saved model JSON file smaller than 32 MB."
    try:
        model = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [], "The selected file is not readable model JSON."
    if not isinstance(model, dict) or not isinstance(model.get("jacobian_model"), dict):
        return [], "This file has no Jacobian family model."
    jacobian = model["jacobian_model"]
    if not all(jacobian.get(key) for key in ("a4_low_to_high", "a6_low_to_high")):
        return [], "The Jacobian family needs exact a4 and a6 coefficient lists."
    if model.get("quadratic_basechange"):
        return [], "The recursive 2-neighbor constructor needs the original rational base, not a quadratic pullback."
    labels = []
    for row in model.get("neighbor_sections", []):
        if not isinstance(row, dict) or not all(row.get(key) for key in (
            "label", "x_numerator", "x_denominator", "y_numerator", "y_denominator"
        )):
            continue
        labels.append(str(row["label"]))
    if not labels:
        return [], "This model has no transferred exact sections for another pencil."
    return list(dict.fromkeys(labels)), None


def _recent_saved_models(db, project_root):
    root = Path(project_root).resolve()
    directory = root / "runs" / "x1152-neighbor-hunt"
    if not directory.is_dir():
        return []
    registry_paths = []
    has_registry = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='pencils'"
    ).fetchone()
    for row in (db.execute("SELECT definition_json FROM pencils ORDER BY updated_at DESC")
                if has_registry else ()):
        try:
            definition = json.loads(row["definition_json"] or "{}")
            source_paths = [definition.get("model_path")]
            source_paths += [item.get("model_path") for item in definition.get("pencil_provenance", [])
                             if isinstance(item, dict)]
            for source_path in source_paths:
                if source_path:
                    registry_paths.append((root / str(source_path)).resolve())
        except (TypeError, ValueError):
            continue
    recent = sorted(
        directory.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True
    )[:80]
    candidates = list(dict.fromkeys(registry_paths + recent))
    return [path for path in candidates if path.is_relative_to(root)
            and _saved_model_sections(path)[0]][:40]


def _pencil_output(project_root, source, selection):
    digest = hashlib.sha256(f"{source}|{selection}".encode("utf-8")).hexdigest()[:12]
    name = re.sub(r"[^A-Za-z0-9_-]+", "-", source).strip("-")[:48] or "source"
    return (Path(project_root) / "runs" / "x1152-neighbor-hunt"
            / f"pencils-page-{name}-{digest}.json")


def _sync_completed_page_pencils(db, project_root):
    """Publish successful page-created models after the job has finished."""
    if not db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='pencils'"
    ).fetchone():
        return
    root = Path(project_root).resolve()
    rows = db.execute(
        """SELECT metadata_json FROM ui_jobs
           WHERE kind='x1152_neighbor_hunt' AND status='succeeded'
             AND metadata_json LIKE '%pencils-page-%'
           ORDER BY id DESC"""
    ).fetchall()
    added = 0
    for row in rows:
        try:
            metadata = json.loads(row["metadata_json"] or "{}")
            output = Path(metadata["output"]).resolve()
            if not output.is_relative_to(root) or not output.name.startswith("pencils-page-"):
                continue
            key = output.stem
            exists = db.execute(
                "SELECT 1 FROM pencils WHERE plugin_id=? AND pencil_key=?",
                ("x1152_neighbor_hunt", key),
            ).fetchone()
            if exists or not output.is_file():
                continue
            receipt = json.loads(output.read_text(encoding="utf-8"))
            verification = receipt.get("verification") or {}
            if not (verification.get("discriminant_nonzero") is True
                    and verification.get("quartic_covariant_identity") is True
                    and int(verification.get("h0") or 0) == 2):
                continue
            register_pencil(
                db, plugin_id="x1152_neighbor_hunt", pencil_key=key,
                label=f"2-neighbor · {metadata.get('section_selection') or key}",
                status="constructed",
                definition={
                    "model_path": str(output),
                    "source_id": metadata.get("source_id"),
                    "source_model": metadata.get("source_model"),
                    "section_selection": metadata.get("section_selection"),
                    "neighbor_divisor": receipt.get("neighbor_divisor"),
                    "verification": verification,
                },
            )
            added += 1
        except (OSError, ValueError, TypeError, KeyError):
            continue
    if added:
        db.commit()


def _render_neighbor_pencil(db, ctx):
    """Build from packet sections or transferred sections on a saved model."""
    st.subheader("Construct a 2-neighbor pencil")
    st.caption("Choose exact sections from a packet or saved model. Rank Hunter constructs and verifies the new fibration as a background job.")
    root = Path(ctx.project_root).resolve()
    source_kind = st.radio(
        "Start from", ["Exact section packet", "Saved neighbor model"],
        horizontal=True, key="pencils-create-source-kind",
    )
    source_id = None
    model_path = None
    if source_kind == "Exact section packet":
        source_id = st.selectbox(
            "Source fibration", ["3926", "848", "3900"],
            format_func=lambda value: f"ICARM #{value}",
            key="pencils-create-x1152-source",
        )
        labels = _packet_labels(root, source_id)
        if not labels:
            st.error("The exact section packet for this source is missing or unreadable.")
            return
    else:
        recent = _recent_saved_models(db, root)
        choices = ["Enter model path"] + [str(path.relative_to(root)) for path in recent]
        selected = st.selectbox("Saved source", choices, key="pencils-create-model-choice")
        raw_path = (st.text_input(
            "Model JSON path", placeholder="runs/x1152-neighbor-hunt/my-pencil.json",
            key="pencils-create-model-path",
        ).strip() if selected == "Enter model path" else selected)
        if not raw_path:
            st.info("Select a recent model or enter the path to one with transferred exact sections.")
            return
        model_path = (root / raw_path).resolve()
        if not model_path.is_relative_to(root):
            st.error("Choose a model inside this Rank Hunter project.")
            return
        labels, problem = _saved_model_sections(model_path)
        if problem:
            st.error(problem)
            return
        st.caption(f"{len(labels)} transferred exact sections · {model_path.relative_to(root)}")

    mode = st.radio(
        "Divisor section", ["One section", "Section combination"] if model_path else
        ["Pair of sections", "Section combination"],
        horizontal=True, key=f"pencils-create-mode-{source_kind}",
    )
    selection = ""
    if mode == "One section":
        selection = st.selectbox("Transferred section", labels, key="pencils-create-recursive-section")
    elif mode == "Pair of sections":
        left, right = st.columns(2)
        first = left.selectbox("First section", labels, key="pencils-create-pair-first")
        second = right.selectbox(
            "Second section", labels,
            index=labels.index("S3") if "S3" in labels else min(1, len(labels) - 1),
            key="pencils-create-pair-second",
        )
        negate_first = left.checkbox("Use negative first section", key="pencils-create-negative-first")
        negate_second = right.checkbox("Use negative second section", key="pencils-create-negative-second")
        if first == second:
            st.warning("Choose two distinct sections.")
            return
        selection = f"{'-' if negate_first else ''}{first},{'-' if negate_second else ''}{second}"
    else:
        chosen = st.multiselect(
            "Sections in the combination", labels,
            default=labels[:2] if len(labels) >= 2 else [],
            max_selections=6, key=f"pencils-create-combo-{source_kind}",
        )
        if len(chosen) < 2:
            st.warning("Choose at least two distinct sections.")
            return
        terms = []
        columns = st.columns(min(3, len(chosen)))
        for index, label in enumerate(chosen):
            coefficient = columns[index % len(columns)].number_input(
                f"{label} coefficient", min_value=-12, max_value=12, value=1,
                step=1, key=f"pencils-create-coeff-{source_kind}-{label}",
            )
            if coefficient:
                terms.append(f"{label}={int(coefficient)}")
        if len(terms) < 2:
            st.warning("At least two section coefficients must be nonzero.")
            return
        selection = ",".join(terms)

    with st.expander("What this construction proves", expanded=False):
        st.caption(
            "The constructor checks the divisor and Jacobian identities. Some section "
            "choices do not define a supported height-8 or height-12 2-neighbor; "
            "the job log explains any rejection. A new fibration alone does not prove a rank increase."
        )
    source_name = f"icarm{source_id}" if source_id else model_path.stem
    output = _pencil_output(root, str(model_path or source_name), selection)
    if st.button("Construct pencil", type="primary", key="pencils-create-run"):
        script = root / "plugins" / "extensions" / "x1152_neighbor_hunt" / "construct_two_neighbor.sage"
        if not script.is_file():
            st.error("The exact 2-neighbor constructor is not installed.")
            return
        output.parent.mkdir(parents=True, exist_ok=True)
        command = [
            str(setting(db, "science_python", ctx.detected_science_python())),
            str(script), str(output), source_id or f"neighbor-{model_path.stem}", str(ctx.db_path),
        ]
        if model_path:
            command.append(str(model_path))
            if mode == "One section":
                command.append(selection)
            else:
                command += ["--recursive-section-combination", selection]
        elif mode == "Pair of sections":
            command += ["--pair-labels", selection]
        else:
            command += ["--section-combination", selection]
        job_id = enqueue_job(
            ctx.db_path, kind="x1152_neighbor_hunt",
            label=f"Construct 2-neighbor pencil from {source_name} ({selection})",
            command=command, cwd=ctx.project_root,
            metadata={"operation": "construct_two_neighbor", "source_id": source_id,
                      "source_model": str(model_path) if model_path else None,
                      "neighbor_degree": 2, "section_selection": selection,
                      "output": str(output)},
            priority=90, resource_class="sage_heavy", start_dispatcher=True,
        )
        st.success(f"Queued exact pencil construction as job #{job_id}. Open Jobs to follow it.")
        st.caption(f"Output model: `{output}`")


def _render_curve_seed_pencil(db, ctx):
    st.subheader("Start from one of your curves")
    st.caption(
        "Choose a stored curve and up to two exact points. Rank Hunter builds a "
        "searchable family with your curve at T=0."
    )
    suggested = (st.session_state.get("curves_selected_id")
                 or st.session_state.get("curves_detail_id"))
    if not suggested:
        latest = db.execute(
            "SELECT id FROM curves WHERE a_invariants_json IS NOT NULL ORDER BY id DESC LIMIT 1"
        ).fetchone()
        suggested = int(latest["id"]) if latest else 1
    curve_id = int(st.number_input(
        "Curve ID", min_value=1, value=max(1, int(suggested)), step=1,
        key="pencils-create-curve-id",
    ))
    curve = db.execute(
        "SELECT id,family,parameter,a_invariants_json FROM curves WHERE id=?",
        (curve_id,),
    ).fetchone()
    if curve is None or not curve["a_invariants_json"]:
        st.info("Choose a stored curve with an exact Weierstrass model.")
        return
    st.caption(f"Curve #{curve_id} · {curve['family']} · t={curve['parameter']}")
    points = db.execute(
        """SELECT id,x,y,rigorous_independent FROM points
           WHERE curve_id=? AND exact_verified=1
           ORDER BY rigorous_independent DESC,id ASC LIMIT 300""",
        (curve_id,),
    ).fetchall()
    if not points:
        st.info("No exact finite points are stored for this curve. You can still build a pencil with only the zero section; find points first to carry more information into the family.")
    by_id = {int(row["id"]): row for row in points}
    labels = {
        int(row["id"]): (
            f"#{row['id']} · ({str(row['x'])[:22]}, {str(row['y'])[:22]})"
            + (" · independent witness" if row["rigorous_independent"] else "")
        )
        for row in points
    }
    defaults = []
    seen_x = set()
    for row in points:
        if str(row["x"]) in seen_x:
            continue
        defaults.append(int(row["id"]))
        seen_x.add(str(row["x"]))
        if len(defaults) == 2:
            break
    chosen = (st.multiselect(
        "Points carried through the pencil", list(by_id),
        default=defaults, max_selections=2,
        format_func=lambda point_id: labels[point_id],
        key=f"pencils-create-curve-points-{curve_id}",
    ) if points else [])
    if len(chosen) == 2 and str(by_id[chosen[0]]["x"]) == str(by_id[chosen[1]]["x"]):
        st.warning("Choose points with different x-coordinates.")
        return
    with st.expander("How the pencil is constructed", expanded=False):
        st.caption(
            "The selected points remain exact sections as the Weierstrass coefficients vary. "
            "With no point selected, only the zero section is carried. This is a new "
            "surface through your curve, and it makes no automatic generic-rank claim."
        )
    if st.button("Build searchable pencil", type="primary", icon=":material/add:",
                 key="pencils-create-from-curve"):
        command = [
            str(setting(db, "science_python", ctx.detected_science_python())),
            "-m", "rank42.curve_pencil_job",
            "--db", str(ctx.db_path),
            "--project-root", str(ctx.project_root),
            "--curve-id", str(curve_id),
        ]
        for point_id in chosen:
            command += ["--point-id", str(point_id)]
        job_id = enqueue_job(
            ctx.db_path, kind="curve_pencil_construction",
            label=f"Build exact pencil through curve #{curve_id}",
            command=command, cwd=ctx.project_root,
            metadata={"operation": "construct_curve_seed_pencil",
                      "source_curve_id": curve_id, "point_ids": chosen},
            priority=90, resource_class="sage_heavy", start_dispatcher=True,
        )
        st.success(f"Queued exact pencil construction as job #{job_id}. Open Jobs to follow it.")


def _render_create_pencil(db, ctx):
    st.caption("Start from a saved curve for a searchable family. Use the 2-neighbor path to construct a new fibration from exact sections.")
    curve_tab, neighbor_tab = st.tabs(["From a saved curve", "Advanced · 2-neighbor"])
    with curve_tab:
        _render_curve_seed_pencil(db, ctx)
    with neighbor_tab:
        _render_neighbor_pencil(db, ctx)


def page(db, ctx):
    title("Pencils", "Explore saved pencils or construct one from a curve or exact sections.", "Analysis")
    _sync_completed_page_pencils(db, ctx.project_root)
    inventory_tab, create_tab = st.tabs(["Registry", "Create Pencil"])
    with inventory_tab:
        _render_registry(db, ctx)
    with create_tab:
        _render_create_pencil(db, ctx)


def _search_pencil(row):
    st.session_state["byo-target-mode"] = "Family"
    st.session_state["byo_target_mode_revision"] = int(
        st.session_state.get("byo_target_mode_revision") or 0
    ) + 1
    st.session_state["byo-family"] = f"{row['plugin_id']}:default"
    st.session_state["byo-family-source"] = "Generated candidates"
    st.session_state["builder_main_view"] = "Editor"
    st.session_state["builder_view_revision"] = int(
        st.session_state.get("builder_view_revision") or 0
    ) + 1
    st.session_state["rh_page"] = "Pipelines"
    st.rerun()


def _rank_summary(curve_ids, rank_states):
    ranked = [
        (curve_id, rank_states[curve_id])
        for curve_id in curve_ids
        if curve_id in rank_states and not rank_states[curve_id]["rank_inconsistent"]
    ]
    if not ranked:
        return "—", None
    curve_id, best = max(ranked, key=lambda item: (int(item[1]["rigorous_lower"]), -item[0]))
    lower = int(best["rigorous_lower"])
    if lower <= 0:
        return "—", None
    exact = best["exact_rank"]
    return (f"={int(exact)}" if exact is not None and int(exact) == lower else f"≥{lower}"), curve_id


def _pencil_kind(row, definition):
    if definition.get("source_curve_id") is not None:
        return "Curve-seeded family"
    if definition.get("neighbor_divisor") or definition.get("pencil_provenance"):
        return "2-neighbor pencil"
    return "Registered pencil"


def _pencil_source(definition):
    if definition.get("source_curve_id") is not None:
        return f"Curve #{definition['source_curve_id']}"
    if definition.get("source_id"):
        return f"ICARM #{definition['source_id']}"
    if definition.get("source_model"):
        return "Saved neighbor model"
    return "Research registry"


def _render_registry(db, ctx):
    rows = _pencil_rows(db)
    if rows is None:
        st.info("The pencil registry is not available in this database yet.")
        return

    plugins = _plugin_labels(ctx.project_root)
    st.html(
        '<div class="rh-pencils-intro"><div><span>RESEARCH REGISTRY</span>'
        '<h2>Your pencils</h2><p>Choose a pencil to inspect its strongest linked curve, '
        'searchable family, and construction record.</p></div>'
        f'<div class="rh-pencils-total"><strong>{len(rows):,}</strong><span>saved pencils</span></div></div>'
    )
    if not rows:
        st.info("No pencils are registered yet. Open Create Pencil to start from a saved curve.")
        return

    search_col, status_col = st.columns([3, 1], vertical_alignment="bottom")
    search = search_col.text_input(
        "Find a pencil", placeholder="Search name, source curve, or key",
        key="pencils-search", icon=":material/search:",
    ).strip().casefold()
    statuses = ["All statuses", "Explored", "Constructed", "Registered"]
    selected_status = status_col.selectbox(
        "Status", statuses, key="pencils-status-filter",
    )
    filtered = []
    for row in rows:
        definition = _definition(row)
        owner = plugins.get(str(row["plugin_id"]), str(row["plugin_id"]))
        if selected_status != "All statuses" and str(row["status"]).casefold() != selected_status.casefold():
            continue
        searchable = " ".join((
            str(row["label"]), str(row["plugin_id"]), owner,
            str(row["pencil_key"]), str(definition.get("source_curve_id") or ""),
            str(definition.get("source_id") or ""),
        )).casefold()
        if search and search not in searchable:
            continue
        filtered.append((row, definition, owner))
    if not filtered:
        st.info("No pencils match. Try another name or status.")
        return

    linked_rows = db.execute(
        "SELECT pencil_id, curve_id FROM pencil_curves ORDER BY pencil_id, curve_id"
    ).fetchall()
    linked = {}
    for item in linked_rows:
        linked.setdefault(int(item["pencil_id"]), []).append(int(item["curve_id"]))
    rank_states = curve_rank_summary_map(
        db, [curve_id for row, _, _ in filtered for curve_id in linked.get(int(row["id"]), [])]
    )
    by_id = {int(row["id"]): (row, definition, owner) for row, definition, owner in filtered}
    selected_id = st.selectbox(
        "Selected pencil", list(by_id),
        format_func=lambda pencil_id: (
            f"{by_id[pencil_id][0]['label']}  ·  "
            f"{by_id[pencil_id][0]['status'].title()}"
        ),
        key="pencils-selected-id",
    )
    row, definition, owner = by_id[selected_id]
    curve_ids = linked.get(selected_id, [])
    best_rank, best_curve_id = _rank_summary(curve_ids, rank_states)
    kind = _pencil_kind(row, definition)
    source = _pencil_source(definition)
    esc = html.escape
    st.html(
        '<section class="rh-pencils-focus" aria-label="Selected pencil">'
        '<div class="rh-pencils-focus-heading">'
        f'<div><span class="rh-pencils-eyebrow">{esc(kind.upper())} · #{selected_id}</span>'
        f'<h2>{esc(str(row["label"]))}</h2>'
        f'<p>{esc(source)} · {esc(str(row["status"]).title())}</p></div>'
        f'<div class="rh-pencils-rank"><span>BEST LINKED RANK</span><strong>{esc(best_rank)}</strong></div>'
        '</div><div class="rh-pencils-facts">'
        f'<div><span>LINKED CURVES</span><strong>{len(curve_ids):,}</strong></div>'
        f'<div><span>BEST CURVE</span><strong>{"#" + str(best_curve_id) if best_curve_id else "—"}</strong></div>'
        f'<div><span>SEARCH</span><strong>{"Ready" if definition.get("family_spec") else "Research model"}</strong></div>'
        '</div></section>'
    )
    action_cols = st.columns([1.5, 1, 3], vertical_alignment="center")
    if definition.get("family_spec") and str(row["plugin_id"]).startswith("curve_pencil_"):
        if action_cols[0].button(
            "Search in Pipelines", icon=":material/arrow_forward:",
            type="primary", key=f"pencil-search-{selected_id}", width="stretch",
        ):
            _search_pencil(row)
    else:
        action_cols[0].caption("This research model has no installed family search adapter.")
    source_curve_id = definition.get("source_curve_id")
    if source_curve_id is not None and action_cols[1].button(
        "Source curve", icon=":material/open_in_new:",
        key=f"pencil-source-{selected_id}", width="stretch",
    ):
        _open_curve(int(source_curve_id))
    elif best_curve_id is not None and source_curve_id is None and action_cols[1].button(
        "Best curve", icon=":material/open_in_new:",
        key=f"pencil-best-{selected_id}", width="stretch",
    ):
        _open_curve(best_curve_id)

    st.markdown("### Linked curves")
    if curve_ids:
        curves = db.execute(
            """SELECT c.id, c.family, c.parameter FROM pencil_curves pc
               JOIN curves c ON c.id=pc.curve_id WHERE pc.pencil_id=? ORDER BY c.id""",
            (selected_id,),
        ).fetchall()
        curve_by_id = {int(curve["id"]): curve for curve in curves}
        curve_col, open_col = st.columns([4, 1], vertical_alignment="bottom")
        selected_curve_id = curve_col.selectbox(
            "Curve", list(curve_by_id),
            format_func=lambda curve_id: (
                f"#{curve_id} · {curve_by_id[curve_id]['family']} · "
                f"t={curve_by_id[curve_id]['parameter']} · "
                f"{_rank_summary([curve_id], rank_states)[0]}"
            ),
            key=f"pencils-linked-curve-{selected_id}",
        )
        if open_col.button(
            "Open curve", icon=":material/arrow_forward:",
            key=f"pencil-open-curve-{selected_id}", width="stretch",
        ):
            _open_curve(selected_curve_id)
        st.caption("Ranks use stored rigorous evidence. A dash means no positive lower bound is recorded.")
    else:
        st.info("No curves are linked to this pencil yet.")

    with st.expander("Construction and provenance", expanded=False, icon=":material/description:"):
        st.caption(f"Source: {owner} · Registry key: {row['pencil_key']}")
        if definition:
            st.json(definition, expanded=False)
