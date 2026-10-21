from __future__ import annotations

import json

import streamlit as st

from rank42.feature_hooks import render_feature_hook
from rank42.ui_active_curve import get_active_curve_id, set_active_curve_id
from rank42.points import list_hard_points, list_points, set_point_hard_flag
from rank42.ui_components import rh_key, button, dataframe, number_input, region, selectbox, selectable_dataframe, selectable_dataframe_rows, tabs
from .common import active_curve_selector, curve_options, launch, setting, title


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


def _last_independence(rec):
    meta = _json_dict(rec["metadata_json"])
    last = meta.get("last_independence")
    return last if isinstance(last, dict) else None


def _hard_reason_from_last(last):
    if not isinstance(last, dict):
        return "manual hard-case bookmark"
    status = str(last.get("status") or "inconclusive")
    cert = last.get("certificate") if isinstance(last.get("certificate"), dict) else {}
    detail = cert.get("certificate") if isinstance(cert.get("certificate"), dict) else {}
    options = last.get("options") if isinstance(last.get("options"), dict) else {}
    try:
        halvings = int(detail.get("halvings"))
        limit = int(options.get("max_halvings"))
    except (TypeError, ValueError):
        halvings = limit = None
    if status == "inconclusive" and halvings is not None and limit is not None and halvings > limit:
        return f"halving budget exhausted ({halvings}/{limit})"
    if status == "timeout":
        return "exact certificate timeout"
    if status == "error":
        return "exact certificate error"
    return f"exact {status}"


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
    hard = bool(rec["hard_flag"] or 0) if "hard_flag" in rec.keys() else False
    if hard:
        priority = "hard case"
    elif status == "numerical_novel":
        priority = "screened candidate"
    elif status == "dependent":
        priority = "closed"
    elif last and last.get("status") in {"timeout", "error", "inconclusive"}:
        priority = "retry / review"
    else:
        priority = "untested"
    return {
        "🔖": "🔖" if hard else "",
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


def _basis_records(db, row):
    required = int(row["rigorous_lower"] or 0)
    try:
        active = json.loads(row["generators_json"] or "[]")
    except Exception:
        active = []
    records = []
    for xy in active:
        if not isinstance(xy, (list, tuple)) or len(xy) < 2:
            continue
        rec = db.execute(
            "SELECT * FROM points WHERE curve_id=? AND x=? AND y=? LIMIT 1",
            (int(row["id"]), str(xy[0]), str(xy[1])),
        ).fetchone()
        if rec is not None and int(rec["exact_verified"] or 0):
            records.append(rec)
        if len(records) >= required:
            return records

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
        LIMIT ?
        """,
        (int(row["id"]), max(required, 1)),
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
    hard = sum(bool(p["hard_flag"] or 0) for p in candidates if "hard_flag" in p.keys())
    st.caption(
        f"{unclassified} exact candidate point(s) remain actionable or unresolved · "
        f"🔖 {hard} bookmarked hard case(s). "
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
                unresolved = str(last.get("status") or "") in {"inconclusive", "timeout", "error"}
                hard = bool(rec["hard_flag"] or 0) if "hard_flag" in rec.keys() else False
                if unresolved:
                    reason = rec["hard_reason"] if hard and "hard_reason" in rec.keys() else _hard_reason_from_last(last)
                    if hard:
                        st.error(f"🔖 Hard case · {reason}")
                        if button(
                            "Remove hard bookmark",
                            semantic=f"independence-unflag-hard-{rec['id']}",
                            width="stretch",
                        ):
                            set_point_hard_flag(db, int(rec["id"]), False)
                            st.rerun()
                    else:
                        st.warning(f"Unresolved exact attempt · {_hard_reason_from_last(last)}")
                        if button(
                            "🔖 Bookmark as hard",
                            semantic=f"independence-flag-hard-{rec['id']}",
                            type="primary",
                            width="stretch",
                        ):
                            set_point_hard_flag(
                                db,
                                int(rec["id"]),
                                True,
                                reason=_hard_reason_from_last(last),
                            )
                            st.rerun()
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


def _hard_case_info(rec):
    info = _candidate_info(rec)
    meta = _json_dict(rec["metadata_json"])
    last = meta.get("last_independence") if isinstance(meta.get("last_independence"), dict) else {}
    cert = last.get("certificate") if isinstance(last.get("certificate"), dict) else {}
    detail = cert.get("certificate") if isinstance(cert.get("certificate"), dict) else {}
    options = last.get("options") if isinstance(last.get("options"), dict) else {}
    relation = meta.get("last_mw_relation") if isinstance(meta.get("last_mw_relation"), dict) else {}
    history = meta.get("independence_history") if isinstance(meta.get("independence_history"), list) else []
    return {
        "🔖": "🔖",
        "point": int(rec["id"]),
        "reason": rec["hard_reason"] or _hard_reason_from_last(last),
        "last exact": last.get("status") or "—",
        "halvings": detail.get("halvings"),
        "halving limit": options.get("max_halvings"),
        "matrix rank": detail.get("matrix_rank"),
        "attempts": len(history),
        "MW relation": relation.get("status") or "—",
        "relation denominator": relation.get("common_denominator"),
        "MW residual": info.get("MW residual"),
        "source": rec["source"],
    }


def _render_hard_cases(db, ctx, row):
    hard_points = list_hard_points(db, curve_id=row["id"], limit=2000)
    if not hard_points:
        st.success("No bookmarked hard cases on this curve.")
        return

    st.error(
        f"🔖 {len(hard_points)} hard case(s) on curve #{row['id']}. "
        "These are exact points you deliberately deferred after an unresolved certificate."
    )
    selected_ids = st.session_state.get("independence_hard_selected_ids")
    selected = selectable_dataframe_rows(
        [_hard_case_info(p) for p in hard_points],
        hard_points,
        semantic="independence-hard-table",
        selected_ids=selected_ids,
        selection_state_key="independence_hard_selected_ids",
        height=430,
        column_config={
            "MW residual": st.column_config.NumberColumn(format="%.4g"),
        },
    )

    st.markdown("#### Hard-Case Escalator")
    st.caption(
        "Autonomous exact-resolution ladder: existing rank ceiling → mwrank Selmer → Simon known-point descent → "
        "mwrank coverings → Sage proof-mode exact rank → basis preparation → full-trial saturation only as the last resort. "
        "It stops as soon as the point or curve is rigorously resolved."
    )
    c1, c2, c3, c4, c5 = st.columns(5)
    with c1:
        timeout = int(number_input(
            "Timeout / point (s)",
            semantic="hard-timeout",
            min_value=30,
            value=600,
            step=60,
        ))
    with c2:
        max_halvings = int(number_input(
            "Maximum halvings",
            semantic="hard-max-halvings",
            min_value=0,
            value=250,
            step=25,
        ))
    with c3:
        max_prime = int(number_input(
            "Prime search ceiling",
            semantic="hard-max-prime",
            min_value=5,
            value=2000000,
            step=250000,
            help="Upper prime bound for quadratic-character columns. Default exact workbench value is 1,000,000.",
        ))
    with c4:
        max_columns = int(number_input(
            "Character-column ceiling",
            semantic="hard-max-columns",
            min_value=64,
            value=1280,
            step=128,
            help="Maximum mod-2 character columns used by the final exact certificate.",
        ))
    with c5:
        saturation_timeout = int(number_input(
            "2-saturation timeout (s)",
            semantic="hard-saturation-timeout",
            min_value=60,
            value=600,
            step=60,
            help="Budget for preparing the rigorous basis. The escalator moves on instead of retrying this stage forever.",
        ))

    with st.expander("Escalator budgets", expanded=False):
        e1, e2, e3, e4, e5 = st.columns(5)
        with e1:
            trial_saturation_timeout = int(number_input(
                "Full-trial saturation budget (s)",
                semantic="hard-escalator-trial-saturation-timeout",
                min_value=60,
                value=300,
                step=60,
                help="Normal p=2 full-trial budget; Advanced Deep saturation also uses this full budget at every requested prime.",
            ))
        with e2:
            saturation_probe_timeout = int(number_input(
                "p>2 probe timeout (s)",
                semantic="hard-escalator-saturation-probe-timeout",
                min_value=30,
                value=90,
                step=30,
                help="Default short probe for p=3,5,7. One expensive failure suppresses the rest of the family.",
            ))
        with e3:
            descent_timeout = int(number_input(
                "Descent / stage (s)",
                semantic="hard-escalator-descent-timeout",
                min_value=60,
                value=300,
                step=60,
            ))
        with e4:
            rank_cert_timeout = int(number_input(
                "Proof-rank timeout (s)",
                semantic="hard-escalator-rank-cert-timeout",
                min_value=30,
                value=180,
                step=30,
            ))
        with e5:
            saturation_primes = st.text_input(
                "Targeted prime ladder",
                value="2,3,5,7",
                help="Single-prime exact saturation stages, in order. Sage uses min_prime=max_prime=p.",
                key="independence-hard-escalator-primes",
            )

    st.caption(
        "The escalator is history-aware and method-family-aware: equal-or-weaker retries on the same basis are skipped. "
        "One expensive full-trial saturation failure suppresses the remaining higher-prime probes. "
        "A stronger budget or changed basis unlocks work again. Deep saturation is available separately under Advanced Research Escalation. "
        "Exact claims still come only from rigorous engines."
    )
    resolve_label = (
        "Run Hard-Case Escalator"
        if len(selected) <= 1
        else f"Escalate {len(selected)} hard cases"
    )
    if button(
        resolve_label,
        semantic="hard-saturation-resolve",
        type="primary",
        width="stretch",
        disabled=not selected,
    ):
        py = setting(db, "science_python", ctx.detected_science_python())
        cmd = [
            py,
            "-m",
            "rank42.hard_case_escalator",
            "--db",
            ctx.db_path,
            "--curve-id",
            int(row["id"]),
            "--certificate-timeout",
            timeout,
            "--max-halvings",
            max_halvings,
            "--max-prime",
            max_prime,
            "--max-columns",
            max_columns,
            "--basis-saturation-timeout",
            saturation_timeout,
            "--trial-saturation-timeout",
            trial_saturation_timeout,
            "--saturation-probe-timeout",
            saturation_probe_timeout,
            "--descent-timeout",
            descent_timeout,
            "--rank-cert-timeout",
            rank_cert_timeout,
            "--saturation-primes",
            saturation_primes,
        ]
        for point in selected:
            cmd += ["--point-id", int(point["id"])]
        jid = launch(
            ctx,
            db,
            kind="independence",
            label=f"Hard-Case Escalator curve #{row['id']} · {len(selected)} point(s)",
            command=cmd,
            metadata={
                "curve_id": int(row["id"]),
                "point_ids": [int(p["id"]) for p in selected],
                "mode": "hard_case_escalator",
                "saturation_primes": saturation_primes,
                "basis_saturation_timeout": saturation_timeout,
                "trial_saturation_timeout": trial_saturation_timeout,
                "saturation_probe_timeout": saturation_probe_timeout,
                "deep_saturation": False,
                "descent_timeout": descent_timeout,
                "rank_cert_timeout": rank_cert_timeout,
                "max_halvings": max_halvings,
                "max_prime": max_prime,
                "max_columns": max_columns,
            },
        )
        st.success(f"Started Hard-Case Escalator job #{jid}.")

    with st.expander("Advanced Research Escalation", expanded=False):
        st.caption(
            "Point-specific exact attacks for dependence resolution. These are not family/plugin specific. "
            "Numerical work may suggest a relation, but Rank Hunter changes proof status only after exact verification."
        )

        st.markdown("**Exact MW relation attack**")
        r1, r2, r3 = st.columns(3)
        with r1:
            relation_timeout = int(number_input(
                "Relation timeout (s)",
                semantic="advanced-relation-timeout",
                min_value=30,
                value=180,
                step=30,
            ))
        with r2:
            relation_precision = int(number_input(
                "Height precision (bits)",
                semantic="advanced-relation-precision",
                min_value=128,
                value=256,
                step=64,
                help="Used only to guess rational MW coefficients. The final dependence check is exact.",
            ))
        with r3:
            relation_denominator = int(number_input(
                "Max relation denominator",
                semantic="advanced-relation-denominator",
                min_value=1,
                value=64,
                step=16,
                help="Maximum denominator used when reconstructing candidate coefficients in the active rigorous basis.",
            ))
        if button(
            "Run exact MW relation attack",
            semantic="advanced-mw-relation-run",
            width="stretch",
            disabled=not selected,
        ):
            py = setting(db, "science_python", ctx.detected_science_python())
            cmd = [
                py, "-m", "rank42.mw_relation_attack",
                "--db", ctx.db_path,
                "--curve-id", int(row["id"]),
                "--timeout", relation_timeout,
                "--precision-bits", relation_precision,
                "--max-denominator", relation_denominator,
            ]
            for point in selected:
                cmd += ["--point-id", int(point["id"])]
            jid = launch(
                ctx,
                db,
                kind="independence",
                label=f"Exact MW relation curve #{row['id']} · {len(selected)} point(s)",
                command=cmd,
                metadata={
                    "curve_id": int(row["id"]),
                    "point_ids": [int(p["id"]) for p in selected],
                    "mode": "advanced_mw_relation",
                    "timeout": relation_timeout,
                    "precision_bits": relation_precision,
                    "max_denominator": relation_denominator,
                },
            )
            st.success(f"Started exact MW relation job #{jid}.")

        st.divider()
        st.info(
            "Curve-level rigorous upper-bound and exact-rank attacks are planned and launched from Descent. "
            "Independence keeps only point-specific relation/certificate/saturation work."
        )

        st.divider()
        st.markdown("**Deep saturation override**")
        st.caption(
            "Bypasses the saturation-family cost barrier and deliberately gives every requested prime the full deep budget."
        )
        if button(
            "Run deep saturation ladder",
            semantic="advanced-deep-saturation-run",
            width="stretch",
            disabled=not selected,
        ):
            py = setting(db, "science_python", ctx.detected_science_python())
            cmd = [
                py,
                "-m",
                "rank42.hard_case_escalator",
                "--db",
                ctx.db_path,
                "--curve-id",
                int(row["id"]),
                "--certificate-timeout",
                timeout,
                "--max-halvings",
                max_halvings,
                "--max-prime",
                max_prime,
                "--max-columns",
                max_columns,
                "--basis-saturation-timeout",
                saturation_timeout,
                "--trial-saturation-timeout",
                trial_saturation_timeout,
                "--saturation-probe-timeout",
                saturation_probe_timeout,
                "--descent-timeout",
                descent_timeout,
                "--rank-cert-timeout",
                rank_cert_timeout,
                "--saturation-primes",
                saturation_primes,
                "--deep-saturation",
                "--saturation-only",
            ]
            for point in selected:
                cmd += ["--point-id", int(point["id"])]
            jid = launch(
                ctx,
                db,
                kind="independence",
                label=f"Deep saturation curve #{row['id']} · {len(selected)} point(s)",
                command=cmd,
                metadata={
                    "curve_id": int(row["id"]),
                    "point_ids": [int(p["id"]) for p in selected],
                    "mode": "hard_case_escalator",
                    "deep_saturation": True,
                    "saturation_primes": saturation_primes,
                    "trial_saturation_timeout": trial_saturation_timeout,
                },
            )
            st.success(f"Started deep saturation job #{jid}.")

    left, middle, right = st.columns([1.3, 1.0, 1.0])
    if left.button(
        "Raw certificate retry",
        width="stretch",
        key="independence-hard-raw-retry",
        disabled=not selected,
        help="Runs the old certificate directly without saturation preconditioning.",
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
            "--strategy",
            "standard",
            "--timeout",
            timeout,
            "--max-halvings",
            max_halvings,
            "--max-prime",
            max_prime,
            "--max-columns",
            max_columns,
        ]
        for point in selected:
            cmd += ["--point-id", int(point["id"])]
        jid = launch(
            ctx,
            db,
            kind="independence",
            label=f"Raw hard-case independence curve #{row['id']} · {len(selected)} point(s)",
            command=cmd,
            metadata={
                "curve_id": int(row["id"]),
                "point_ids": [int(p["id"]) for p in selected],
                "mode": "hard_case_raw_retry",
            },
        )
        st.success(f"Started raw hard-case job #{jid}.")

    if middle.button(
        "Remove bookmark",
        width="stretch",
        key="independence-hard-remove",
        disabled=not selected,
    ):
        for point in selected:
            set_point_hard_flag(db, int(point["id"]), False)
        st.rerun()

    if right.button(
        "Open MW Geometry",
        width="stretch",
        key="independence-hard-lattices",
        disabled=not selected,
    ):
        st.session_state.pop(rh_key("lattice-curve-select"), None)
        set_active_curve_id(
            st.session_state,
            int(row["id"]),
            "analysis_curve_id",
        )
        st.session_state["rh_page"] = "MW Geometry"
        st.rerun()

    if selected:
        inspect = selectbox(
            "Inspect hard case",
            [int(p["id"]) for p in selected],
            semantic="independence-hard-inspect",
            format_func=lambda pid: f"Point #{pid}",
        )
        rec = next(p for p in selected if int(p["id"]) == int(inspect))
        meta = _json_dict(rec["metadata_json"])
        last = meta.get("last_independence") if isinstance(meta.get("last_independence"), dict) else {}
        cert = last.get("certificate") if isinstance(last.get("certificate"), dict) else {}
        detail = cert.get("certificate") if isinstance(cert.get("certificate"), dict) else {}
        with region("independence-hard-detail", border=True):
            st.markdown(f"**🔖 Point #{rec['id']}** · {rec['hard_reason'] or _hard_reason_from_last(last)}")
            st.code(f"x = {rec['x']}\ny = {rec['y']}", language="text")
            h1, h2, h3, h4 = st.columns(4)
            h1.metric("Last exact result", last.get("status") or "—")
            h2.metric("Matrix rank", detail.get("matrix_rank", "—"))
            h3.metric("Primes used", len(detail.get("primes") or []))
            h4.metric("Halvings", detail.get("halvings", "—"))
            saturation = last.get("saturation") if isinstance(last.get("saturation"), dict) else {}
            basis_prep = (
                last.get("basis_preconditioning")
                if isinstance(last.get("basis_preconditioning"), dict)
                else {}
            )
            strategy = last.get("strategy") or "standard"
            if strategy == "saturation":
                cache_text = "cached" if basis_prep.get("cache_hit") else "fresh"
                fallback = (
                    f" · full-trial index={saturation.get('index', '—')}"
                    if saturation
                    else " · full-trial saturation skipped"
                )
                st.info(
                    f"Last hard strategy: basis-first 2-saturation · "
                    f"basis index={basis_prep.get('index', '—')} ({cache_text})"
                    f"{fallback}"
                )
            st.caption(
                "Hard is a researcher bookmark, not a mathematical verdict. "
                "Resolved independent/dependent retries automatically clear the bookmark."
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
        "Resolve exact candidate points against the rigorous Mordell–Weil basis; curve-level rank proof belongs in Descent.",
        "Analysis",
    )
    render_feature_hook(db, ctx, "analysis.independence.after_header")
    rows = curve_options(db)
    if not rows:
        st.info("No curves yet.")
        return

    wanted_curve = get_active_curve_id(
        st.session_state,
        "independence_curve_id",
    )
    row = active_curve_selector(
        db,
        rows,
        wanted=wanted_curve,
        state_prefix="independence",
        widget_prefix="independence-curve",
        compatibility_keys=("independence_curve_id",),
    )
    if row is None:
        return

    exact_points = list_points(db, curve_id=row["id"], exact_only=True, limit=10000)
    basis = _basis_records(db, row)
    candidates = [p for p in exact_points if not int(p["rigorous_independent"] or 0)]
    _summary_metrics(row, basis, exact_points)

    with region("independence-purpose", border=True):
        text_col, geometry_col, proof_col = st.columns([3.2, 1.0, 1.0], gap="medium")
        with text_col:
            st.markdown("**Research question:** does a newly found exact point add a new Mordell–Weil direction?")
            st.caption(
                "Different rational coordinates can still lie in the subgroup generated by the same basis. "
                "Height/lattice residuals help schedule candidates, but only the exact certificate can promote a point into the rigorous basis. "
                "Generic upper-bound and exact-rank planning is owned by Descent."
            )
        with geometry_col:
            if button(
                "Open MW Geometry",
                semantic="independence-open-lattices",
                width="stretch",
            ):
                st.session_state.pop(rh_key("lattice-curve-select"), None)
                set_active_curve_id(
                    st.session_state,
                    int(row["id"]),
                    "analysis_curve_id",
                )
                st.session_state["rh_page"] = "MW Geometry"
                st.rerun()
        with proof_col:
            if button(
                "Open Descent",
                semantic="independence-open-descent",
                width="stretch",
            ):
                set_active_curve_id(
                    st.session_state,
                    int(row["id"]),
                    "descent_curve_id",
                )
                st.session_state["rh_page"] = "Descent"
                st.rerun()

    active = tabs(["Candidates", "Rigorous basis", "Certificate history", "🔖"], key="independence-workbench-tab")
    st.divider()
    if active == "Rigorous basis":
        _render_basis(row, basis)
    elif active == "Certificate history":
        _render_history(db, row)
    elif active == "🔖":
        _render_hard_cases(db, ctx, row)
    else:
        _render_candidates(db, ctx, row, candidates)
