from __future__ import annotations

import json

import streamlit as st

from rank42.feature_hooks import render_feature_hook
from rank42.points import list_points
from rank42.ui_components import button, dataframe, number_input, region, selectbox, selectable_dataframe, selectable_dataframe_rows, tabs
from .common import curve_label, curve_options, launch, select_row, setting, title


ACTIONABLE_STATUSES = {"unknown", "numerical_novel", "inconclusive"}


def _json_dict(value):
    try:
        out = json.loads(value or "{}")
    except Exception:
        out = {}
    return out if isinstance(out, dict) else {}


def _find_number(value, keys, *, depth=0):
    if depth > 3 or not isinstance(value, dict):
        return None
    for key in keys:
        if key in value and value[key] not in (None, ""):
            try:
                return float(value[key])
            except (TypeError, ValueError, OverflowError):
                pass
    for child in value.values():
        if isinstance(child, dict):
            found = _find_number(child, keys, depth=depth + 1)
            if found is not None:
                return found
    return None


def _candidate_info(rec):
    meta = _json_dict(rec["metadata_json"])
    residual = _find_number(
        meta,
        (
            "relative_residual",
            "rel_residual",
            "novelty_relative_residual",
            "best_novelty_relative_residual",
            "mw_residual",
            "residual",
        ),
    )
    height = _find_number(meta, ("canonical_height", "height", "point_height", "nt_height"))
    last = meta.get("last_independence") if isinstance(meta.get("last_independence"), dict) else None
    status = str(rec["independence_status"] or "unknown")
    if status == "numerical_novel":
        priority = "screened candidate"
    elif status == "dependent":
        priority = "closed"
    elif last and last.get("status") in {"timeout", "error", "inconclusive"}:
        priority = "retry / review"
    else:
        priority = "untested"
    return {
        "point": int(rec["id"]),
        "priority": priority,
        "status": status,
        "source": rec["source"],
        "height": height,
        "MW residual": residual,
        "x": rec["x"],
        "search": rec["search_ref"] or "—",
        "last exact": (last or {}).get("status") if last else "—",
    }


def _candidate_sort_key(rec):
    info = _candidate_info(rec)
    status = info["status"]
    group = 0 if status == "numerical_novel" else 1 if status in {"unknown", "inconclusive"} else 2
    residual = info["MW residual"]
    return (group, -(float(residual) if residual is not None else -1.0), -int(rec["id"]))


def _basis_records(db, curve_id):
    return db.execute(
        """
        SELECT * FROM points
        WHERE curve_id=? AND exact_verified=1 AND rigorous_independent=1
        ORDER BY CASE role
            WHEN 'generic_section' THEN 0
            WHEN 'rigorous_witness' THEN 1
            WHEN 'basis' THEN 2
            ELSE 3 END,
            id ASC
        """,
        (int(curve_id),),
    ).fetchall()


def _certificate_history(db, curve_id):
    rows = db.execute(
        """
        SELECT * FROM rank_evidence
        WHERE curve_id=? AND engine='rank42.independence_check'
        ORDER BY id DESC
        LIMIT 500
        """,
        (int(curve_id),),
    ).fetchall()
    out = []
    details = {}
    for row in rows:
        options = _json_dict(row["options_json"])
        try:
            summary = json.loads(row["stdout_summary"] or "{}")
        except Exception:
            summary = {}
        if not isinstance(summary, dict):
            summary = {}
        cert = summary.get("certificate") if isinstance(summary.get("certificate"), dict) else {}
        cert_detail = cert.get("certificate") if isinstance(cert.get("certificate"), dict) else {}
        point_id = options.get("candidate_point_id")
        out.append(
            {
                "evidence": int(row["id"]),
                "point": point_id if point_id is not None else "—",
                "result": summary.get("outcome") or row["status"],
                "attempt": options.get("attempt_number"),
                "basis before": options.get("basis_size_before"),
                "rigorous lower": row["rigorous_lower"],
                "matrix rank": cert_detail.get("matrix_rank"),
                "torsion rank": cert_detail.get("torsion_rank"),
                "primes": len(cert_detail.get("primes") or []),
                "halvings": cert_detail.get("halvings"),
                "runtime s": row["elapsed_seconds"],
                "created": row["created_at"],
            }
        )
        details[int(row["id"])] = {
            "evidence_id": int(row["id"]),
            "engine": row["engine"],
            "engine_version": row["engine_version"],
            "status": row["status"],
            "rigorous": bool(row["rigorous"]),
            "rigorous_lower": row["rigorous_lower"],
            "model_a_invariants": json.loads(row["model_a_invariants_json"] or "[]"),
            "minimal_model_a_invariants": json.loads(row["minimal_model_a_invariants_json"] or "null"),
            "trial_points": json.loads(row["points_json"] or "[]"),
            "options": options,
            "result": summary,
            "created_at": row["created_at"],
        }
    return out, details


def _summary_metrics(row, basis, exact_points):
    rigorous_lower = int(row["rigorous_lower"] or 0)
    candidates = [p for p in exact_points if not int(p["rigorous_independent"] or 0)]
    numerical = sum(str(p["independence_status"]) == "numerical_novel" for p in candidates)
    dependent = sum(str(p["independence_status"]) == "dependent" for p in candidates)
    unclassified = sum(str(p["independence_status"]) in ACTIONABLE_STATUSES for p in candidates)
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Rigorous MW rank", f"≥ {rigorous_lower}")
    c2.metric("Stored witness points", len(basis))
    c3.metric("Exact candidates", len(candidates))
    c4.metric("Numerically novel", numerical)
    c5.metric("Dependent / closed", dependent)
    st.caption(
        f"{unclassified} exact candidate point(s) remain actionable or unresolved. "
        "Numerical novelty is scheduling evidence only; exact certificates decide rank growth."
    )


def _render_candidates(db, ctx, row, candidates):
    if not candidates:
        st.success("No unclassified exact candidate points remain on this curve.")
        return

    mode = selectbox(
        "Candidate view",
        ["Actionable", "Numerically novel", "Untested / unresolved", "Dependent", "All exact candidates"],
        semantic="independence-candidate-view",
    )
    if mode == "Actionable":
        visible = [p for p in candidates if str(p["independence_status"] or "unknown") != "dependent"]
    elif mode == "Numerically novel":
        visible = [p for p in candidates if str(p["independence_status"] or "unknown") == "numerical_novel"]
    elif mode == "Untested / unresolved":
        visible = [p for p in candidates if str(p["independence_status"] or "unknown") in {"unknown", "inconclusive"}]
    elif mode == "Dependent":
        visible = [p for p in candidates if str(p["independence_status"] or "unknown") == "dependent"]
    else:
        visible = list(candidates)

    visible.sort(key=_candidate_sort_key)
    if not visible:
        st.info("No candidate points match this view.")
        return

    wanted_point = st.session_state.get("independence_point_id")
    selected_ids = st.session_state.get("independence_selected_point_ids")
    if selected_ids is None and wanted_point:
        selected_ids = [int(wanted_point)]
    selected = selectable_dataframe_rows(
        [_candidate_info(p) for p in visible],
        visible,
        semantic="independence-candidate-table",
        selected_ids=selected_ids,
        selection_state_key="independence_selected_point_ids",
        column_config={
            "height": st.column_config.NumberColumn(format="%.6g"),
            "MW residual": st.column_config.NumberColumn(format="%.4g"),
        },
    )

    st.markdown("#### Exact certification")
    st.caption(
        "Select one or more rows above. One selected point runs a direct exact test; several selected rows extract a maximal certified independent subset. "
        "The points are tested sequentially in table order; dependent points do not poison the rest of the batch."
    )
    if selected:
        st.session_state["independence_point_id"] = int(selected[0]["id"])

    a, b = st.columns(2)
    with a:
        timeout = int(
            number_input(
                "Exact certificate timeout / point",
                semantic="independence-timeout",
                min_value=1,
                value=120,
                step=30,
            )
        )
    with b:
        max_halvings = int(
            number_input(
                "Maximum exact halvings",
                semantic="independence-max-halvings",
                min_value=0,
                value=40,
                step=5,
                help="Budget for exact halving replacements inside the Cremona/Brumer certificate.",
            )
        )

    label = "Test exact independence" if len(selected) <= 1 else "Extract maximal independent subset"
    if button(
        label,
        semantic="independence-run-certificate",
        type="primary",
        width="stretch",
        disabled=not selected,
    ):
        py = setting(db, "science_python", ctx.detected_science_python())
        cmd = [
            py,
            "-m",
            "rank42.independence_check",
            "--db",
            ctx.db_path,
            "--curve-id",
            int(row["id"]),
            "--timeout",
            timeout,
            "--max-halvings",
            max_halvings,
        ]
        for point in selected:
            cmd += ["--point-id", int(point["id"])]
        jid = launch(
            ctx,
            db,
            kind="independence",
            label=f"Independence curve #{row['id']} · {len(selected)} point(s)",
            command=cmd,
            metadata={
                "curve_id": int(row["id"]),
                "point_ids": [int(p["id"]) for p in selected],
                "mode": "maximal_subset",
            },
        )
        st.success(f"Started independence job #{jid}.")

    if selected:
        inspect = selectbox(
            "Inspect selected point",
            [int(p["id"]) for p in selected],
            semantic="independence-inspect-selected",
            format_func=lambda pid: f"Point #{pid}",
        )
        rec = next(p for p in selected if int(p["id"]) == int(inspect))
        meta = _json_dict(rec["metadata_json"])
        last = meta.get("last_independence") if isinstance(meta.get("last_independence"), dict) else None
        with region("independence-point-detail", border=True):
            st.markdown(f"**Point #{rec['id']}** · `{rec['source']}` · `{rec['independence_status']}`")
            st.code(f"x = {rec['x']}\ny = {rec['y']}", language="text")
            st.caption(f"Search provenance: {rec['search_ref'] or '—'} · Plugin: {rec['plugin_id'] or '—'}")
            if last:
                cert = last.get("certificate") if isinstance(last.get("certificate"), dict) else {}
                cert_detail = cert.get("certificate") if isinstance(cert.get("certificate"), dict) else {}
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("Last exact result", last.get("status") or "—")
                c2.metric("Matrix rank", cert_detail.get("matrix_rank", "—"))
                c3.metric("Primes used", len(cert_detail.get("primes") or []))
                c4.metric("Halvings", cert_detail.get("halvings", "—"))
                st.caption(
                    "Dependency subset indices emitted by the exact worker are diagnostic only; Rank Hunter does not present them as a recovered Mordell–Weil relation."
                )
                st.download_button(
                    "Export point certificate JSON",
                    data=json.dumps(last, indent=2, sort_keys=True),
                    file_name=f"rank-hunter-curve-{row['id']}-point-{rec['id']}-independence.json",
                    mime="application/json",
                    width="stretch",
                )
            else:
                st.caption("No exact independence certificate has been recorded for this point yet.")


def _render_basis(row, basis):
    rigorous_lower = int(row["rigorous_lower"] or 0)
    if not basis:
        st.info("No rigorous witness points are present in the point ledger for this curve.")
        return
    counts = {}
    for rec in basis:
        key = str(rec["role"] or "unknown")
        counts[key] = counts.get(key, 0) + 1
    st.caption(" · ".join(f"{name}: {count}" for name, count in sorted(counts.items())))
    dataframe(
        [
            {
                "point": int(rec["id"]),
                "role": rec["role"],
                "source": rec["source"],
                "x": rec["x"],
                "y": rec["y"],
                "search": rec["search_ref"] or "—",
                "plugin": rec["plugin_id"] or "—",
            }
            for rec in basis
        ],
        semantic="independence-basis-table",
        width="stretch",
        hide_index=True,
    )
    if len(basis) < rigorous_lower:
        st.warning(
            f"The point ledger contains {len(basis)} rigorous witness row(s), but the curve's rigorous lower bound is ≥{rigorous_lower}. "
            "The point ledger is incomplete for this lower bound. The exact checker will try the stored generators_json "
            "compatibility fallback and will refuse rank-growth certification if the full witness basis still cannot be replayed."
        )
    elif len(basis) > rigorous_lower:
        st.info(
            f"The point ledger contains {len(basis)} rigorous independent witness row(s), exceeding the displayed lower bound ≥{rigorous_lower}. "
            "Review rank evidence/reducer state before publication."
        )


def _render_history(db, row):
    table, details = _certificate_history(db, row["id"])
    if not table:
        st.info("No Independence Workbench certificate evidence has been recorded for this curve yet.")
        return
    selected = selectable_dataframe(
        table,
        table,
        semantic="independence-history-table",
        selected_id=st.session_state.get("independence_evidence_id"),
        id_key="evidence",
        selection_state_key="independence_evidence_id",
        column_config={"runtime s": st.column_config.NumberColumn(format="%.3f")},
    )
    if selected is None:
        return
    evidence_id = int(selected["evidence"])
    detail = details[evidence_id]
    result = detail.get("result") or {}
    cert = result.get("certificate") if isinstance(result.get("certificate"), dict) else {}
    cert_detail = cert.get("certificate") if isinstance(cert.get("certificate"), dict) else {}
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Outcome", result.get("outcome") or detail.get("status") or "—")
    c2.metric("Trial points", len(detail.get("trial_points") or []))
    c3.metric("Matrix rank", cert_detail.get("matrix_rank", "—"))
    c4.metric("Torsion rank", cert_detail.get("torsion_rank", "—"))
    c5.metric("Halvings", cert_detail.get("halvings", "—"))
    with st.expander("Certificate payload"):
        st.json(detail)
    st.download_button(
        "Export certificate evidence JSON",
        data=json.dumps(detail, indent=2, sort_keys=True),
        file_name=f"rank-hunter-curve-{row['id']}-evidence-{evidence_id}.json",
        mime="application/json",
        width="stretch",
    )


def page(db, ctx):
    title(
        "Independence",
        "Understand the discovered Mordell–Weil subgroup, prioritize exact points, and certify which directions genuinely increase rank.",
        "Analysis",
    )
    render_feature_hook(db, ctx, "analysis.independence.after_header")
    rows = curve_options(db)
    if not rows:
        st.info("No curves yet.")
        return

    wanted_curve = st.session_state.get("independence_curve_id")
    cidx = next((i for i, rec in enumerate(rows) if wanted_curve and int(rec["id"]) == int(wanted_curve)), 0)
    row = select_row("Curve", rows, index=cidx, format_func=curve_label, key="independence-curve")
    st.session_state["independence_curve_id"] = int(row["id"])

    exact_points = list_points(db, curve_id=row["id"], exact_only=True, limit=10000)
    basis = _basis_records(db, row["id"])
    candidates = [p for p in exact_points if not int(p["rigorous_independent"] or 0)]
    _summary_metrics(row, basis, exact_points)

    with region("independence-purpose", border=True):
        text_col, action_col = st.columns([3.2, 1.0], gap="medium")
        with text_col:
            st.markdown("**Research question:** does a newly found exact point add a new Mordell–Weil direction?")
            st.caption(
                "Different rational coordinates can still lie in the subgroup generated by the same basis. "
                "Height/lattice residuals help schedule candidates, but only the exact certificate can promote a point into the rigorous basis."
            )
        with action_col:
            if button(
                "Open Lattices & Heights",
                semantic="independence-open-lattices",
                width="stretch",
            ):
                st.session_state["analysis_curve_id"] = int(row["id"])
                st.session_state["rh_page"] = "Lattices & Heights"
                st.rerun()

    active = tabs(["Candidates", "Rigorous basis", "Certificate history"], key="independence-workbench-tab")
    st.divider()
    if active == "Rigorous basis":
        _render_basis(row, basis)
    elif active == "Certificate history":
        _render_history(db, row)
    else:
        _render_candidates(db, ctx, row, candidates)
