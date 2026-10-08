from __future__ import annotations

import json
import shlex

import streamlit as st

from rank42.feature_hooks import render_feature_hook
from rank42.rank_evidence import reduce_rank_state
from rank42.saturation import primes_in_range
from rank42.ui_active_curve import get_active_curve_id
from rank42.ui_components import region
from rank42.ui_store import list_jobs

from .common import active_curve_selector, curve_options, launch, setting, title


def _json_dict(value):
    try:
        out = json.loads(value or "{}")
    except Exception:
        out = {}
    return out if isinstance(out, dict) else {}


def _json_list(value):
    try:
        out = json.loads(value or "[]")
    except Exception:
        out = []
    return out if isinstance(out, list) else []


def _basis_preview(db, row):
    """Return the stored witness coordinates that saturation will revalidate exactly."""
    required = int(row["rigorous_lower"] or 0)
    records = []
    seen = set()

    def add(x, y, *, source, point_id=None):
        key = (str(x), str(y))
        if key in seen:
            return
        seen.add(key)
        records.append(
            {
                "Generator": f"P{len(records) + 1}",
                "Point ID": point_id if point_id is not None else "—",
                "Source": str(source),
                "x": str(x),
                "y": str(y),
            }
        )

    try:
        active = json.loads(row["generators_json"] or "[]")
    except Exception:
        active = []
    for xy in active:
        if not isinstance(xy, (list, tuple)) or len(xy) < 2:
            continue
        add(xy[0], xy[1], source="Active basis")
        if required > 0 and len(records) >= required:
            break

    if required <= 0 or len(records) < required:
        witnesses = db.execute(
            """
            SELECT id,x,y,role,source
            FROM points
            WHERE curve_id=? AND exact_verified=1 AND rigorous_independent=1
            ORDER BY CASE role
                WHEN 'generic_section' THEN 0
                WHEN 'rigorous_witness' THEN 1
                WHEN 'basis' THEN 2
                ELSE 3 END,
                id ASC
            """,
            (int(row["id"]),),
        ).fetchall()
        for rec in witnesses:
            add(
                rec["x"],
                rec["y"],
                source=rec["role"] or rec["source"] or "Point Ledger",
                point_id=int(rec["id"]),
            )
            if required > 0 and len(records) >= required:
                break

    ledger_count = int(
        db.execute(
            """
            SELECT COUNT(*) AS n
            FROM points
            WHERE curve_id=? AND exact_verified=1 AND rigorous_independent=1
            """,
            (int(row["id"]),),
        ).fetchone()["n"]
        or 0
    )
    complete = required > 0 and len(records) >= required
    return {
        "required": required,
        "records": records[:required] if required > 0 else records,
        "count": len(records[:required] if required > 0 else records),
        "ledger_count": ledger_count,
        "complete": complete,
    }


def _saturation_evidence(db, curve_id, limit=30):
    return db.execute(
        """
        SELECT *
        FROM rank_evidence
        WHERE curve_id=?
          AND engine='sage_saturation'
          AND evidence_type='saturation'
        ORDER BY id DESC
        LIMIT ?
        """,
        (int(curve_id), int(limit)),
    ).fetchall()


def _history_display(value):
    if value is None or value == "":
        return "—"
    return str(value)


def _history_row(rec):
    options = _json_dict(rec["options_json"])
    before = options.get("basis_fingerprint_before")
    after = options.get("basis_fingerprint_after")
    if before and after:
        basis_change = "yes" if str(before) != str(after) else "no"
    else:
        basis_change = "—"

    min_prime = options.get("min_prime")
    max_prime = options.get("max_prime")
    if min_prime is None and max_prime is None:
        scope = "—"
    elif min_prime == max_prime:
        scope = f"p={max_prime}"
    else:
        scope = f"{min_prime or 2}–{max_prime or '—'}"

    return {
        "Evidence": f"#{int(rec['id'])}",
        "Status": str(rec["status"]),
        "Mode": str(options.get("mode") or "range"),
        "Prime scope": scope,
        "Saturated through": _history_display(options.get("saturated_through_prime")),
        "Index": _history_display(options.get("index")),
        "Subgroup regulator": _history_display(options.get("witness_subgroup_regulator")),
        "Basis changed": basis_change,
        "Witnesses": (
            f"{options.get('witness_count', '—')}/"
            f"{options.get('witness_required', '—')}"
        ),
        "Model": _history_display(options.get("model_used")),
        "Elapsed s": (
            round(float(rec["elapsed_seconds"]), 2)
            if rec["elapsed_seconds"] is not None
            else None
        ),
        "Created": str(rec["created_at"] or "")[:19],
    }


def _interpret_latest(rec):
    options = _json_dict(rec["options_json"])
    status = str(rec["status"] or "")
    index = options.get("index")
    try:
        index_value = abs(int(str(index)))
    except (TypeError, ValueError):
        index_value = None

    if status == "completed":
        if index_value == 1:
            st.success(
                "Bounded saturation completed with index 1: no missing divisibility "
                "was detected for this supplied rigorous subgroup in the tested prime scope."
            )
        elif index_value is not None and index_value > 1:
            st.warning(
                f"Non-primitive subgroup detected: Sage enlarged the supplied subgroup "
                f"by index {index_value}. This changes the subgroup basis/index, not its rank."
            )
        else:
            st.success("Bounded saturation completed.")
    elif status == "timeout":
        st.warning(
            "Saturation timed out. Any completed earlier ladder steps remain useful, "
            "but the requested saturation scope is incomplete."
        )
    elif status == "inconclusive":
        st.info("Saturation was inconclusive; no negative mathematical conclusion follows.")
    else:
        st.error(
            "Saturation did not complete successfully. The failure is computational, "
            "not evidence that the subgroup is saturated."
        )

    st.caption(
        "Saturation does not search for additional independent generators and never establishes exact rank by itself. "
        "Exact rank still requires rigorous lower = rigorous upper."
    )


def _render_latest_result(rec):
    with region("saturation-latest-result", border=True):
        st.subheader("Latest saturation result")
        _interpret_latest(rec)

        options = _json_dict(rec["options_json"])
        a, b, c, d = st.columns(4, gap="medium")
        a.metric("Status", str(rec["status"]).title())
        b.metric("Subgroup index", options.get("index") or "—")
        c.metric(
            "Subgroup regulator",
            options.get("witness_subgroup_regulator") or "—",
            help=(
                "Regulator of the bounded saturated witness subgroup. It is not "
                "automatically the full Mordell–Weil regulator."
            ),
        )
        d.metric(
            "Saturated through",
            (
                f"p ≤ {options.get('saturated_through_prime')}"
                if options.get("saturated_through_prime") is not None
                else "—"
            ),
        )

        before = options.get("basis_fingerprint_before")
        after = options.get("basis_fingerprint_after")
        if before and after:
            changed = str(before) != str(after)
            st.caption(
                f"Basis fingerprint: {'changed' if changed else 'unchanged'} · "
                f"{str(before)[:12]} → {str(after)[:12]}"
            )

        if options.get("model_warning"):
            st.warning(f"Model transport warning: {options['model_warning']}")

        prime_results = options.get("prime_results")
        if isinstance(prime_results, list) and prime_results:
            st.markdown("**Prime ladder**")
            rows = []
            for step in prime_results:
                if not isinstance(step, dict):
                    continue
                rows.append(
                    {
                        "Prime": step.get("prime"),
                        "Status": step.get("status"),
                        "Index factor": step.get("index_factor") or "—",
                        "Regulator": step.get("regulator") or "—",
                        "Basis changed": (
                            "yes" if step.get("basis_changed") else
                            ("no" if step.get("status") == "completed" else "—")
                        ),
                        "Elapsed s": (
                            round(float(step["elapsed_seconds"]), 2)
                            if step.get("elapsed_seconds") is not None
                            else None
                        ),
                        "Error": step.get("error") or "",
                    }
                )
            if rows:
                st.dataframe(rows, width="stretch", hide_index=True)

        saturated = _json_list(rec["points_json"])
        if saturated:
            st.subheader("Exact saturated basis returned by Sage")
            st.dataframe(
                [
                    {
                        "Generator": f"S{i + 1}",
                        "x": point[0] if isinstance(point, list) and point else "—",
                        "y": (
                            point[1]
                            if isinstance(point, list) and len(point) > 1
                            else "—"
                        ),
                    }
                    for i, point in enumerate(saturated)
                ],
                width="stretch",
                hide_index=True,
            )
            st.caption(
                "The returned basis is retained in saturation evidence. This page does not automatically replace the active witness basis."
            )


def _curve_saturation_jobs(db, curve_id, limit=100):
    rows = []
    for job in list_jobs(db, limit=limit):
        if str(job["kind"]) != "saturation":
            continue
        meta = _json_dict(job["metadata_json"])
        try:
            job_curve_id = int(meta.get("curve_id"))
        except (TypeError, ValueError):
            continue
        if job_curve_id != int(curve_id):
            continue
        rows.append((job, meta))
    return rows


def _render_job_handoff(db, curve_id):
    jobs = _curve_saturation_jobs(db, curve_id)
    if not jobs:
        return

    job, meta = jobs[0]
    with region("saturation-job-handoff", border=True):
        st.subheader("Latest saturation Job")
        a, b, c = st.columns(3, gap="medium")
        a.metric("Job", f"#{int(job['id'])}")
        b.metric("Status", str(job["status"]).title())
        c.metric("Mode", str(meta.get("mode") or "range").title())

        command = _json_list(job["command_json"])
        if command:
            st.code(shlex.join(str(value) for value in command), language="bash")

        if st.button(
            "Open Job / raw log",
            width="stretch",
            key=f"saturation-open-job-{int(job['id'])}",
            icon=":material/terminal:",
        ):
            st.session_state["manage_job_id"] = int(job["id"])
            st.session_state["jobs_section_pending"] = "Results"
            st.session_state["rh_page"] = "Jobs"
            st.rerun()


def page(db, ctx):
    title(
        "Saturation",
        "Test whether the known rigorous Mordell–Weil subgroup is primitive through selected primes.",
        "Analysis",
        icon="hub",
    )
    render_feature_hook(db, ctx, "analysis.saturation.after_header")

    rows = curve_options(db)
    if not rows:
        st.info("No curves with a rigorous lower bound are available.")
        return

    wanted = get_active_curve_id(
        st.session_state,
        "saturation_curve_id",
        "descent_curve_id",
        "analysis_curve_id",
    )
    row = active_curve_selector(
        db,
        rows,
        wanted=wanted,
        state_prefix="saturation",
        widget_prefix="saturation-curve",
        compatibility_keys=("saturation_curve_id",),
    )
    if row is None:
        return

    curve_id = int(row["id"])
    state = reduce_rank_state(db, curve_id)
    basis = _basis_preview(db, row)

    with region("saturation-question", border=True):
        st.markdown(
            "**Research question: is the current rigorous witness subgroup primitive "
            "through the primes we care about?**"
        )
        st.caption(
            "Sage saturation can recover hidden divisibility and a better basis of the "
            "same rank. It does not look for a new independent Mordell–Weil direction."
        )

        a, b, c, d = st.columns(4, gap="medium")
        a.metric("Rigorous lower", f"≥ {int(state['rigorous_lower'] or 0)}")
        b.metric(
            "Rigorous upper",
            state["rigorous_upper"] if state["rigorous_upper"] is not None else "—",
        )
        c.metric(
            "Exact rank",
            state["exact_rank"] if state["exact_rank"] is not None else "—",
        )
        d.metric(
            "Witness basis",
            f"{basis['count']} / {basis['required']}",
        )

    with region("saturation-basis", border=True):
        st.subheader("Rigorous witness basis")
        if basis["complete"]:
            st.success(
                f"Ready · {basis['count']} stored witness coordinates cover the "
                f"rigorous lower bound {basis['required']}."
            )
        else:
            st.warning(
                f"Incomplete basis preview · {basis['count']} / {basis['required']}. "
                "Saturation will remain inconclusive until a complete rigorous witness "
                "basis can be reconstructed."
            )
        st.caption(
            f"Point Ledger rigorous witnesses: {basis['ledger_count']}. "
            "The saturation Job reconstructs every supplied coordinate exactly on the "
            "stored elliptic curve before doing arithmetic."
        )
        if basis["records"]:
            st.dataframe(basis["records"], width="stretch", hide_index=True)

    with region("saturation-run", border=True):
        st.subheader("Run saturation")
        mode_label = st.selectbox(
            "Mode",
            ["Bounded prime range", "Prime-by-prime ladder"],
            key="saturation-mode",
            help=(
                "Range asks Sage to saturate over one bounded interval. Ladder runs "
                "each prime separately and carries the exact saturated basis forward, "
                "retaining an index/regulator outcome for every prime."
            ),
        )
        mode = "ladder" if mode_label == "Prime-by-prime ladder" else "range"

        a, b, c = st.columns(3, gap="medium")
        min_prime = int(
            a.number_input(
                "First prime bound",
                min_value=2,
                value=2,
                step=1,
                key=f"saturation-min-prime-{mode}",
            )
        )
        max_prime = int(
            b.number_input(
                "Final prime bound",
                min_value=2,
                max_value=997 if mode == "ladder" else 100000,
                value=19 if mode == "ladder" else 100,
                step=1,
                key=f"saturation-max-prime-{mode}",
                help=(
                    "Ladder mode is capped at 997 because it launches one bounded "
                    "Sage saturation subprocess per prime. Range mode remains available "
                    "for larger one-shot bounds."
                ),
            )
        )
        timeout = int(
            c.number_input(
                "Timeout per prime" if mode == "ladder" else "Saturation timeout",
                min_value=10,
                value=int(setting(db, "deep_cert_timeout", 900)),
                step=60,
                key=f"saturation-timeout-{mode}",
            )
        )

        primes = primes_in_range(min_prime, max_prime) if min_prime <= max_prime else []
        if min_prime > max_prime:
            st.error("First prime bound cannot exceed final prime bound.")
        elif not primes:
            st.info("The selected interval contains no primes.")
        elif mode == "ladder":
            st.caption(
                f"Ladder will test {len(primes)} prime(s): "
                + ", ".join(str(value) for value in primes[:16])
                + (" …" if len(primes) > 16 else "")
            )
        else:
            st.caption(
                f"Bounded Sage saturation will cover primes in [{min_prime}, {max_prime}]."
            )

        can_run = bool(basis["complete"] and primes and min_prime <= max_prime)
        if st.button(
            "Run saturation",
            type="primary",
            width="stretch",
            disabled=not can_run,
            key="saturation-run-button",
            icon=":material/hub:",
        ):
            py = setting(db, "science_python", ctx.detected_science_python())
            command = [
                py,
                "-m",
                "rank42.cli",
                "saturate",
                "--db",
                ctx.db_path,
                "--curve-id",
                curve_id,
                "--mode",
                mode,
                "--min-prime",
                min_prime,
                "--max-prime",
                max_prime,
                "--timeout",
                timeout,
            ]
            jid = launch(
                ctx,
                db,
                kind="saturation",
                label=f"Saturation curve #{curve_id}",
                command=command,
                metadata={
                    "curve_id": curve_id,
                    "mode": mode,
                    "min_prime": min_prime,
                    "max_prime": max_prime,
                    "prime_count": len(primes),
                    "witness_count": basis["count"],
                    "witness_required": basis["required"],
                },
            )
            st.success(f"Started saturation Job #{jid}.")

    evidence = _saturation_evidence(db, curve_id)
    if evidence:
        _render_latest_result(evidence[0])

        with region("saturation-history", border=True):
            st.subheader("Saturation history")
            st.dataframe(
                [_history_row(rec) for rec in evidence],
                width="stretch",
                hide_index=True,
            )
    else:
        with region("saturation-history", border=True):
            st.subheader("Saturation history")
            st.caption("No saturation evidence has been recorded for this curve yet.")

    _render_job_handoff(db, curve_id)
