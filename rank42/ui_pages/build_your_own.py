from __future__ import annotations

import json
from fractions import Fraction
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

from rank42.auto_search_policy import auto_search_policy, default_target_rank
from rank42.candidates import list_pools
from rank42.db import now
from rank42.pipeline_catalog import (
    deep_strategy_budget,
    geometry_search_budget,
    normalize_pipeline,
    pipeline_preset_options,
    point_search_budget,
    pipeline_stage_specs,
    stage_spec,
    template_stages,
    validate_pipeline,
)
from rank42.launch_context import (
    launch_context_snapshot,
    normalize_launch_metadata,
    resolve_launch_context,
)
from rank42.manage_store import current_campaign, campaign_pipeline_runs
from rank42.pipeline_state import (
    create_pipeline_run,
    ensure_pipeline_schema,
    get_pipeline,
    get_pipeline_run,
    list_pipeline_runs,
    pipeline_candidates,
    pipeline_derivations,
    pipeline_definition_hash,
    pipeline_payload,
    pipeline_run_runtime_drift,
    save_pipeline,
    update_pipeline_run,
)
from rank42.plugins import (
    Plugin,
    discover_plugins,
    family_rank_claim,
    get_variant,
    is_enabled,
    variant_for_family_spec,
    variant_torsion_groups,
    variant_torsion_record,
)
from rank42.torsion import MAZUR_TORSION_CHOICES
from rank42.ui_components import tabs
from rank42.ui_handoffs import clear_research_handoff, get_research_handoff
from rank42.ui_store import (
    get_application_state,
    list_jobs,
    reconcile_jobs,
    set_application_state,
)

from .common import (
    curve_label,
    curve_options,
    launch_resolved,
    ratpoints_selector,
    section_title,
    setting,
    title,
)

from .pipeline_saved_library import (
    PIPELINE_LIBRARY_CARD_COMPONENT_DIR,
    _filtered_pipeline_library,
    _pipeline_library_event,
    _pipeline_library_item,
    _pipeline_library_page_slice,
    _pipeline_library_search_blob,
    _pipeline_stage_summary,
    _render_pipeline_library,
    _render_pipeline_library_card,
)


RUNNING_JOB_STATES = {"queued", "running", "stopping"}
RESUMABLE_RUN_STATES = {"paused", "interrupted", "killed", "failed"}
PIPELINE_DRAFT_SETTING_KEY = "pipeline_editor_draft_v1"
PIPELINE_DRAFT_VERSION = 1
TARGET_LABELS = {
    "family": "Family",
    "torsion": "Torsion Group",
    "general": "General Curves",
    "curve": "Target Curve",
}


PIPELINE_STAGE_COMPONENT_DIR = (
    Path(__file__).resolve().parents[1] / "ui_assets" / "pipeline_stage_list"
)
_PIPELINE_STAGE_LIST = components.declare_component(
    "rank42_pipeline_stage_list",
    path=str(PIPELINE_STAGE_COMPONENT_DIR),
)

PIPELINE_MODULE_ROW_COMPONENT_DIR = (
    Path(__file__).resolve().parents[1] / "ui_assets" / "pipeline_module_row"
)
_PIPELINE_MODULE_ROW = components.declare_component(
    "rank42_pipeline_module_row",
    path=str(PIPELINE_MODULE_ROW_COMPONENT_DIR),
)

PIPELINE_EDITOR_ACTIONS_COMPONENT_DIR = (
    Path(__file__).resolve().parents[1] / "ui_assets" / "pipeline_editor_actions"
)
_PIPELINE_EDITOR_ACTIONS = components.declare_component(
    "rank42_pipeline_editor_actions",
    path=str(PIPELINE_EDITOR_ACTIONS_COMPONENT_DIR),
)

def _json(value, default):
    try:
        return json.loads(value or "")
    except Exception:
        return default


def _family_lanes(db, ctx, torsion=None):
    rows = []
    for plugin in discover_plugins(ctx.project_root):
        if (
            not isinstance(plugin, Plugin)
            or plugin.plugin_type != "family"
            or "candidate_generation" not in plugin.capabilities
            or not is_enabled(db, plugin)
        ):
            continue
        for variant in plugin.variants:
            if torsion and torsion not in variant_torsion_groups(plugin, variant):
                continue
            claim = family_rank_claim(plugin, variant)
            lower = claim["effective_lower"]
            key = f"{plugin.id}:{variant.id}"
            rows.append((
                -(int(lower) if lower is not None else -1),
                plugin.name.lower(),
                variant.name.lower(),
                key,
                plugin,
                variant,
                claim,
            ))
    rows.sort(key=lambda rec: rec[:3])
    return rows


def _family_label(rec):
    _rk, _pn, _vn, _key, plugin, variant, claim = rec
    lower = claim["effective_lower"]
    rank = f"generic ≥{int(lower)}" if lower is not None else "generic rank —"
    suffix = f" / {variant.name}" if len(plugin.variants) > 1 else ""
    native = " · native geometry" if "target_search" in plugin.capabilities else ""
    return f"{plugin.name}{suffix} · {rank}{native}"



def _campaign_value(campaign, key, default=None):
    if campaign is None:
        return default
    try:
        value = campaign[key]
    except (KeyError, IndexError, TypeError):
        return default
    return default if value is None else value


def _campaign_family_lane_key(campaign, lanes):
    """Resolve the campaign's stored Family metadata to one current Builder lane."""
    if campaign is None:
        return None
    wanted_plugin = str(_campaign_value(campaign, "plugin_id", "") or "").strip()
    wanted_variant = str(_campaign_value(campaign, "variant_id", "") or "").strip()
    wanted_family = str(_campaign_value(campaign, "family", "") or "").strip()
    if not wanted_plugin:
        return None

    plugin_matches = []
    for rec in lanes:
        key, plugin, variant = rec[3], rec[4], rec[5]
        if str(plugin.id) != wanted_plugin:
            continue
        plugin_matches.append(key)
        if wanted_variant and str(variant.id) == wanted_variant:
            return key
        family = str(getattr(variant, "curve_family_name", "") or "").strip()
        if not wanted_variant and wanted_family and family == wanted_family:
            return key

    return plugin_matches[0] if len(plugin_matches) == 1 else None


def _campaign_context_token(campaign):
    if campaign is None:
        return None
    return ":".join(
        [
            str(_campaign_value(campaign, "id", "")),
            str(_campaign_value(campaign, "plugin_id", "") or ""),
            str(_campaign_value(campaign, "variant_id", "") or ""),
            str(_campaign_value(campaign, "family", "") or ""),
            str(_campaign_value(campaign, "target_rank", "") or ""),
        ]
    )



def _saved_pipeline_target(payload):
    launch_defaults = dict((payload or {}).get("launch_defaults") or {})
    target = launch_defaults.get("target")
    if isinstance(target, dict):
        return dict(target)
    config = dict((payload or {}).get("config") or {})
    target = config.get("target")
    return dict(target) if isinstance(target, dict) else {}


def _saved_family_lane_key(payload):
    if str((payload or {}).get("target_mode") or "") != "family":
        return None
    target = _saved_pipeline_target(payload)
    plugin_id = str(target.get("plugin_id") or "").strip()
    variant_id = str(target.get("variant_id") or "").strip()
    if not plugin_id or not variant_id:
        return None
    return f"{plugin_id}:{variant_id}"


def _saved_family_pool_settings(payload):
    target = _saved_pipeline_target(payload)
    pool_id = target.get("candidate_pool_id")
    return {
        "candidate_pool_id": None if pool_id in (None, "") else int(pool_id),
        "only_unsearched": bool(target.get("only_unsearched", False)),
    }


def _compatible_family_pools(db, plugin, variant):
    family_spec = str(getattr(variant, "family_spec", "") or "")
    return [
        row
        for row in list_pools(db, plugin_id=plugin.id, limit=500)
        if str(row["status"] or "") == "ready"
        and str(row["family_spec"] or "") == family_spec
    ]


def _pipeline_draft_session_keys():
    keys = [
        "byo_builder_state_version",
        "byo_stage_mode",
        "byo_stages",
        "byo_loaded_pipeline_id",
        "byo_duplicate_source_pipeline_id",
        "byo_pipeline_name_input",
        "byo-definition-config-shape",
        "byo-target-mode",
        "byo-curve",
        "byo-family",
        "byo-family-source",
        "byo-family-pool",
        "byo-family-only-unsearched",
        "byo-torsion",
        "byo-secondary-providers",
        "byo-general-mode",
        "byo-general-pool",
        "byo-u-min",
        "byo-u-max",
        "byo-v-min",
        "byo-v-max",
        "byo-a-min",
        "byo-a-max",
        "byo-b-min",
        "byo-b-max",
        "byo-cert-timeout",
        "byo-exact-candidates",
        "byo-functional-features",
        "rh-byo-ratpoints",
    ]
    for mode in ("family", "torsion", "general", "curve"):
        keys.extend([
            f"byo-goal-{mode}",
            f"byo-retention-{mode}",
            f"byo-module-favorites-{mode}",
            f"byo-module-recent-{mode}",
        ])
    return tuple(keys)


def _pipeline_draft_snapshot(*, mode, target, stages, definition_config):
    session = {}
    for key in _pipeline_draft_session_keys():
        if key in st.session_state:
            session[key] = st.session_state[key]
    session["byo_builder_state_version"] = 2
    session["byo_stage_mode"] = str(mode)
    session["byo_stages"] = normalize_pipeline(stages)
    return {
        "version": PIPELINE_DRAFT_VERSION,
        "saved_at": now(),
        "mode": str(mode),
        "target": dict(target or {}),
        "definition_config": dict(definition_config or {}),
        "session": json.loads(json.dumps(session, sort_keys=True)),
    }


def _pipeline_draft_compare_payload(value):
    if not isinstance(value, dict):
        return None
    out = dict(value)
    out.pop("saved_at", None)
    return out


def _discard_pipeline_draft(db):
    set_application_state(db, PIPELINE_DRAFT_SETTING_KEY, None)
    st.session_state.pop("byo_draft_recovered_notice", None)


def _persist_pipeline_draft(
    db,
    *,
    mode,
    target,
    stages,
    definition_config,
    lineage,
):
    """Persist only unsaved/modified editor work, never a saved definition."""
    source_id = lineage.get("source_pipeline_id")
    exact_saved = bool(lineage.get("pipeline_id") is not None and not lineage.get("dirty"))
    meaningful = bool(stages) or source_id is not None
    if exact_saved or not meaningful:
        return False

    snapshot = _pipeline_draft_snapshot(
        mode=mode,
        target=target,
        stages=stages,
        definition_config=definition_config,
    )
    existing = get_application_state(db, PIPELINE_DRAFT_SETTING_KEY, None)
    if (
        _pipeline_draft_compare_payload(existing)
        == _pipeline_draft_compare_payload(snapshot)
    ):
        return False
    set_application_state(db, PIPELINE_DRAFT_SETTING_KEY, snapshot)
    return True


def _restore_pipeline_draft(db):
    """Restore one durable editor Draft once per Streamlit session."""
    if st.session_state.get("byo_draft_recovery_checked"):
        return False
    st.session_state["byo_draft_recovery_checked"] = True
    if st.session_state.get("byo_loaded_pipeline_id") is not None:
        return False

    draft = get_application_state(db, PIPELINE_DRAFT_SETTING_KEY, None)
    if not isinstance(draft, dict):
        return False
    if int(draft.get("version") or 0) != PIPELINE_DRAFT_VERSION:
        return False
    session = draft.get("session")
    if not isinstance(session, dict):
        return False
    try:
        restored_stages = normalize_pipeline(session.get("byo_stages") or [])
    except Exception:
        return False

    allowed = set(_pipeline_draft_session_keys())
    for key, value in session.items():
        if key in allowed:
            st.session_state[key] = value
    st.session_state["byo_builder_state_version"] = 2
    st.session_state["byo_stage_mode"] = str(
        draft.get("mode")
        or st.session_state.get("byo_stage_mode")
        or "family"
    )
    st.session_state["byo_stages"] = restored_stages
    st.session_state["byo_stage_revision"] = int(
        st.session_state.get("byo_stage_revision") or 0
    ) + 1
    st.session_state["byo_target_mode_revision"] = int(
        st.session_state.get("byo_target_mode_revision") or 0
    ) + 1
    st.session_state["byo_draft_recovered_notice"] = {
        "saved_at": draft.get("saved_at"),
        "source_pipeline_id": session.get("byo_loaded_pipeline_id")
        or session.get("byo_duplicate_source_pipeline_id"),
    }
    return True


def _render_pipeline_draft_recovery(db):
    notice = st.session_state.get("byo_draft_recovered_notice")
    if not isinstance(notice, dict):
        return
    saved_at = str(notice.get("saved_at") or "").strip()
    source_id = notice.get("source_pipeline_id")
    details = "Recovered unsaved Pipeline draft"
    if saved_at:
        details += f" from {saved_at}"
    if source_id is not None:
        details += f" · based on Pipeline #{int(source_id)}"
    info_col, discard_col = st.columns(
        [8.5, 1.5],
        vertical_alignment="center",
    )
    info_col.info(details + ".")
    if discard_col.button(
        "Discard draft",
        icon=":material/delete:",
        width="stretch",
        key="byo-discard-recovered-draft",
    ):
        mode = str(st.session_state.get("byo_stage_mode") or "family")
        _discard_pipeline_draft(db)
        _clear_loaded_pipeline(mode)
        st.session_state["byo_draft_recovery_checked"] = True
        st.rerun()


def _family_pool_label(row):
    generation = _json(row["generation_json"], {})
    source = str(generation.get("source") or "").strip()
    corpus_name = str(generation.get("corpus_name") or "").strip()
    source_label = corpus_name or ("library" if source == "corpus" else source)
    suffix = f" · {source_label}" if source_label else ""
    return (
        f"#{int(row['id'])} · {row['name']} · "
        f"{int(row['candidate_count'] or 0):,} candidates{suffix}"
    )



def _saved_pipeline_run_settings(payload):
    config = dict(
        (payload or {}).get("recipe_config")
        or (payload or {}).get("config")
        or {}
    )
    return {
        "target_rank": (
            None
            if config.get("target_rank") in (None, "")
            else int(config["target_rank"])
        ),
        "retention_floor": max(0, int(config.get("retention_floor") or 0)),
        "certificate_timeout": max(
            1, int(config.get("certificate_timeout") or 120)
        ),
        "exact_candidates": max(
            1, int(config.get("exact_candidates") or 64)
        ),
        "ratpoints_backend": (
            None
            if not str(config.get("ratpoints_backend") or "").strip()
            else str(config["ratpoints_backend"]).upper()
        ),
    }


def _duplicate_loaded_pipeline():
    """Detach the loaded definition while preserving the entire editor state."""
    source_id = st.session_state.get("byo_loaded_pipeline_id")
    if source_id is None:
        return None
    source_id = int(source_id)
    st.session_state["byo_loaded_pipeline_id"] = None
    st.session_state["byo_duplicate_source_pipeline_id"] = source_id
    name = str(st.session_state.get("byo_pipeline_name_input") or "").strip()
    if name and not name.endswith(" — Copy"):
        st.session_state["byo_pipeline_name_input"] = f"{name} — Copy"
    return source_id


def _clear_loaded_pipeline(mode):
    st.session_state["byo_stage_mode"] = str(mode)
    st.session_state["byo_stages"] = []
    st.session_state["byo_loaded_pipeline_id"] = None
    st.session_state.pop("byo_duplicate_source_pipeline_id", None)
    st.session_state.pop("byo_pipeline_name_input", None)
    st.session_state.pop("byo-definition-config-shape", None)
    for key in (
        f"byo-goal-{mode}",
        f"byo-retention-{mode}",
        "byo-cert-timeout",
        "byo-exact-candidates",
    ):
        st.session_state.pop(key, None)
    for key in (
        "byo_pending_target_rank",
        "byo_pending_retention_floor",
        "byo_pending_certificate_timeout",
        "byo_pending_exact_candidates",
        "byo_pending_ratpoints_backend",
    ):
        st.session_state.pop(key, None)
    st.session_state["byo-functional-features"] = []
    if str(mode) == "family":
        st.session_state.pop("byo-family-source", None)
        st.session_state.pop("byo-family-pool", None)
        st.session_state.pop("byo-family-only-unsearched", None)
    if str(mode) == "curve":
        st.session_state.pop("byo-curve", None)
    _bump_stage_revision()


def _torsion_record_profile(lanes):
    records = []
    for _rk, _pn, _vn, _key, plugin, variant, _claim in lanes:
        record = variant_torsion_record(plugin, variant)
        if record is not None:
            records.append(record)
    if not records:
        return None
    return max(records, key=lambda rec: (int(rec["rank_lower"]), int(rec["goal_rank"])))


def _functional_features(db, ctx):
    out = []
    for plugin in discover_plugins(ctx.project_root):
        if (
            isinstance(plugin, Plugin)
            and plugin.plugin_type == "feature"
            and plugin.feature_command_hooks
            and is_enabled(db, plugin)
        ):
            hooks = sorted({hook.name for hook in plugin.feature_command_hooks})
            out.append({
                "id": plugin.id,
                "name": plugin.name,
                "version": plugin.version,
                "hooks": hooks,
            })
    return out


def _pipeline_jobs(db):
    reconcile_jobs(db)
    rows = [
        row for row in list_jobs(db, limit=100)
        if str(row["kind"]) == "pipeline_search"
    ]
    state_map = {
        "interrupted": "interrupted",
        "stopped": "paused",
        "killed": "killed",
        "failed": "failed",
    }
    for job in rows:
        meta = _json(job["metadata_json"], {})
        run_id = meta.get("pipeline_run_id")
        if run_id is None:
            continue
        run = get_pipeline_run(db, int(run_id))
        if run is None:
            continue
        mapped = state_map.get(str(job["status"] or ""))
        if mapped is not None and str(run["status"]) in {"queued", "running"}:
            fields = {"status": mapped}
            if mapped in {"killed", "failed"}:
                fields["finished_at"] = now()
            update_pipeline_run(db, int(run_id), **fields)
    return rows


def _active_pipeline_jobs(db):
    return [
        row for row in _pipeline_jobs(db)
        if str(row["status"]) in RUNNING_JOB_STATES
    ]


def _stage_list_rows(stages):
    rows = []
    for rec in stages:
        spec = stage_spec(rec["id"])
        rows.append({
            "token": str(id(rec)),
            "module": spec.label,
            "detail": f"{spec.category} · {spec.evidence.upper()} · {spec.cost}",
        })
    return rows


def _apply_stage_list_order(stages, ordered_tokens):
    current = {str(id(rec)): rec for rec in stages}
    ordered_tokens = [str(token) for token in (ordered_tokens or [])]
    if ordered_tokens == list(current):
        return False
    if len(ordered_tokens) != len(stages):
        return False
    if set(ordered_tokens) != set(current):
        return False
    stages[:] = [current[token] for token in ordered_tokens]
    return True


def _stage_index_for_token(stages, token):
    token = str(token or "")
    for index, rec in enumerate(stages):
        if str(id(rec)) == token:
            return index
    return None


def _render_stage_config_dialog(stages, token):
    index = _stage_index_for_token(stages, token)
    if index is None:
        return
    rec = stages[index]
    spec = stage_spec(rec["id"])

    @st.dialog(
        f"{spec.label} settings",
        width="large",
        on_dismiss="ignore",
    )
    def _dialog():
        st.caption(
            f"{spec.category} · {spec.evidence.upper()} · {spec.cost}"
        )
        _config_editor(rec, index)
        if st.button(
            "Done",
            type="primary",
            width="stretch",
            key=f"byo-stage-dialog-done-{token}",
        ):
            st.rerun()

    _dialog()


def _render_stage_list(stages):
    revision = int(st.session_state.get("byo_stage_revision") or 0)
    payload = _PIPELINE_STAGE_LIST(
        rows=_stage_list_rows(stages),
        palette=_component_theme_palette(),
        key=f"byo-stage-list-{revision}",
        default=None,
    )

    if isinstance(payload, dict):
        nonce = payload.get("nonce")
        handled = st.session_state.get("byo_stage_list_handled_nonce")
        if nonce is not None and str(handled) != str(nonce):
            st.session_state["byo_stage_list_handled_nonce"] = str(nonce)
            action = str(payload.get("action") or "")
            token = str(payload.get("token") or "")

            if action == "reorder":
                if _apply_stage_list_order(stages, payload.get("order") or []):
                    st.session_state["byo_stages"] = stages
                    _bump_stage_revision()
                    st.rerun()
            elif action == "delete":
                index = _stage_index_for_token(stages, token)
                if index is not None:
                    stages.pop(index)
                    st.session_state["byo_stages"] = stages
                    _bump_stage_revision()
                    st.rerun()
            elif action == "config":
                if _stage_index_for_token(stages, token) is not None:
                    _render_stage_config_dialog(stages, token)



def _bump_stage_revision():
    st.session_state["byo_stage_revision"] = int(
        st.session_state.get("byo_stage_revision") or 0
    ) + 1


def _reset_pipeline(mode, template):
    st.session_state["byo_stage_mode"] = mode
    st.session_state["byo_stages"] = template_stages(template, mode)
    st.session_state["byo_loaded_pipeline_id"] = None
    _bump_stage_revision()


def _ensure_pipeline_state(mode):
    state_version = 2
    if (
        st.session_state.get("byo_builder_state_version") != state_version
        or "byo_stages" not in st.session_state
        or st.session_state.get("byo_stage_mode") != mode
    ):
        st.session_state["byo_builder_state_version"] = state_version
        st.session_state["byo_stage_mode"] = mode
        st.session_state["byo_stages"] = []
        st.session_state["byo_loaded_pipeline_id"] = None
        _bump_stage_revision()
    return st.session_state["byo_stages"]


def _parse_heights(value, fallback):
    try:
        items = [int(x.strip()) for x in str(value).split(",") if x.strip()]
    except ValueError:
        return list(fallback)
    return [x for x in items if x > 0] or list(fallback)



def _parse_signed_ints(value):
    text = str(value).strip()
    if not text:
        return []
    try:
        return [int(x.strip()) for x in text.split(",") if x.strip()]
    except ValueError:
        return None


def _parse_mobius_maps(value):
    maps = []
    for raw in str(value).split(";"):
        raw = raw.strip()
        if not raw:
            continue
        try:
            coeffs = [Fraction(x.strip()) for x in raw.split(",")]
        except (ValueError, ZeroDivisionError):
            return None
        if len(coeffs) != 4:
            return None
        maps.append([
            str(x.numerator) if x.denominator == 1
            else f"{x.numerator}/{x.denominator}"
            for x in coeffs
        ])
    return maps or None


def _parse_positive_ints(value):
    try:
        values = [int(x.strip()) for x in str(value).split(",") if x.strip()]
    except ValueError:
        return None
    if not values or any(x <= 0 for x in values):
        return None
    return values


def _parse_optional_positive_ints(value):
    text = str(value).strip()
    if not text:
        return []
    try:
        values = [int(x.strip()) for x in text.split(",") if x.strip()]
    except ValueError:
        return None
    if any(x <= 0 for x in values):
        return None
    return values


def _parse_denominator_bands(value):
    bands = []
    for token in str(value).split(","):
        token = token.strip()
        if not token:
            continue
        if "-" not in token:
            return None
        low_text, high_text = token.split("-", 1)
        try:
            low, high = int(low_text.strip()), int(high_text.strip())
        except ValueError:
            return None
        if low < 1 or high < low:
            return None
        bands.append([low, high])
    return bands or None



def _deep_hunt_timeout_envelope(cfg):
    return deep_strategy_budget(cfg)

def _format_budget_seconds(seconds):
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes, rem = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {rem:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"




RANKING_OPTIONS = {
    "rank_then_yield_then_score": "Rigorous rank → exact-point yield → current prime score",
    "rank_then_score": "Rigorous rank → current prime score",
    "score": "Current prime score only",
}


def _render_point_search_budget(stage_id, cfg):
    estimate = point_search_budget(stage_id, cfg)
    if not estimate:
        return
    calls = int(estimate["calls"])
    timeout = int(estimate["timeout_seconds"])
    seconds = int(estimate["worst_case_timeout_seconds"])
    st.caption(
        f"Configured ratpoints plan: {calls} call"
        f"{'' if calls == 1 else 's'} per curve × {timeout}s/search "
        f"= up to {seconds}s ratpoints timeout budget. "
        "Excludes model preparation, certification, and later retry/resume attempts."
    )


def _render_geometry_search_budget(stage_id, cfg):
    estimate = geometry_search_budget(stage_id, cfg)
    if not estimate:
        return
    searches = int(estimate["searches_max"])
    timeout = int(estimate["timeout_seconds"])
    setup = int(estimate["setup_timeout_seconds"])
    worst = int(estimate["worst_case_timeout_seconds"])
    no_growth_searches = int(estimate["searches_no_growth"])
    no_growth = int(estimate["no_growth_timeout_seconds"])
    rounds = int(estimate["rounds_max"])

    if estimate["kind"] == "coverings":
        st.caption(
            f"Configured covering plan: up to {searches} covering search"
            f"{'' if searches == 1 else 'es'} per curve × {timeout}s/search"
            f" + up to {setup}s covering derivation "
            f"= {_format_budget_seconds(worst)} worst-case configured timeout envelope. "
            "Exact certification, retry/resume attempts, cache effects, and process overhead are additional."
        )
        return

    st.caption(
        "Configured quartic timeout envelope (not an ETA): "
        f"a no-growth completed pass searches {no_growth_searches} model/height branch"
        f"{'' if no_growth_searches == 1 else 'es'} "
        f"(≤ {_format_budget_seconds(no_growth)} including reduction); "
        f"the configured {rounds}-round maximum can search up to {searches} branch"
        f"{'' if searches == 1 else 'es'} "
        f"(≤ {_format_budget_seconds(worst)} including reduction). "
        "Exact certification, retry/resume attempts, cache effects, early rank-goal stops, and process overhead are excluded."
    )


def _ranking_selector(cfg, *, key):
    current = str(cfg.get("ranking") or "rank_then_yield_then_score")
    if current not in RANKING_OPTIONS:
        current = "rank_then_yield_then_score"
    options = list(RANKING_OPTIONS)
    return st.selectbox(
        "Survivor ranking",
        options,
        index=options.index(current),
        format_func=lambda value: RANKING_OPTIONS[value],
        key=key,
        help=(
            "This is scheduling only. It does not create rank evidence. "
            "The score is the latest Multi-Scale Frobenius score when present, otherwise the candidate Nagao score. "
            "Rigorous lower bounds outrank heuristic tie-breakers unless score-only is selected."
        ),
    )


def _config_editor(rec, index):
    stage_id = rec["id"]
    cfg = dict(rec["config"])
    revision = int(st.session_state.get("byo_stage_revision") or 0)
    key = f"byo-stage-{revision}-{index}-{stage_id}"

    if stage_id in {"nagao_screen", "nagao_rescore"}:
        c1, c2 = st.columns(2)
        cfg["prime_bound"] = int(c1.number_input(
            "Prime bound", min_value=7, value=int(cfg.get("prime_bound") or 523),
            step=10, key=f"{key}-prime",
        ))
        cfg["keep"] = int(c2.number_input(
            "Survivors", min_value=1, value=int(cfg.get("keep") or 250),
            step=10, key=f"{key}-keep",
        ))
    elif stage_id == "quadratic_twist_sweep":
        explicit = _parse_signed_ints(
            st.text_input(
                "Explicit squarefree d values (optional)",
                ",".join(str(x) for x in cfg.get("d_values") or []),
                key=f"{key}-d-values",
                help="Leave blank to scan the d interval below.",
            )
        )
        if explicit is None:
            st.warning("Use comma-separated integers for d values.")
        else:
            cfg["d_values"] = explicit
        c1, c2, c3 = st.columns(3)
        cfg["d_min"] = int(c1.number_input(
            "d min", value=int(cfg.get("d_min") or -50), key=f"{key}-dmin",
        ))
        cfg["d_max"] = int(c2.number_input(
            "d max", value=int(cfg.get("d_max") or 50), key=f"{key}-dmax",
        ))
        cfg["max_children"] = int(c3.number_input(
            "Max children", min_value=1,
            value=int(cfg.get("max_children") or 24), key=f"{key}-children",
        ))
        cfg["include_parent"] = bool(st.toggle(
            "Keep parent in downstream population",
            value=bool(cfg.get("include_parent", False)),
            key=f"{key}-parent",
        ))
        st.caption(
            "Creates exact squarefree quadratic twists. Child curves do not inherit the parent's stored rank evidence."
        )
    elif stage_id == "targeted_twist_search":
        explicit = _parse_signed_ints(
            st.text_input(
                "Explicit squarefree d values (optional)",
                ",".join(str(x) for x in cfg.get("d_values") or []),
                key=f"{key}-d-values",
                help="Leave blank to scan the configured interval.",
            )
        )
        if explicit is None:
            st.warning("Use comma-separated integers for d values.")
        else:
            cfg["d_values"] = explicit
        c1, c2, c3 = st.columns(3)
        cfg["d_min"] = int(c1.number_input(
            "d min", value=int(cfg.get("d_min") or -200), key=f"{key}-dmin",
        ))
        cfg["d_max"] = int(c2.number_input(
            "d max", value=int(cfg.get("d_max") or 200), key=f"{key}-dmax",
        ))
        cfg["scan_limit"] = int(c3.number_input(
            "Scan limit", min_value=1,
            value=int(cfg.get("scan_limit") or 200), key=f"{key}-scan",
        ))
        c4, c5, c6 = st.columns(3)
        cfg["max_children"] = int(c4.number_input(
            "Keep twists", min_value=1,
            value=int(cfg.get("max_children") or 12), key=f"{key}-children",
        ))
        cfg["prime_bound"] = int(c5.number_input(
            "RH Nagao-style prime bound", min_value=3,
            value=int(cfg.get("prime_bound") or 199), key=f"{key}-prime",
        ))
        signs = ["any", "+1", "-1"]
        sign = str(cfg.get("root_sign") or "any")
        cfg["root_sign"] = c6.selectbox(
            "Root sign",
            signs,
            index=signs.index(sign if sign in signs else "any"),
            key=f"{key}-sign",
        )
        c7, c8 = st.columns(2)
        cfg["timeout"] = int(c7.number_input(
            "Stage hard timeout (s)", min_value=1,
            value=int(cfg.get("timeout") or 60), key=f"{key}-timeout",
        ))
        cfg["per_twist_timeout"] = int(c8.number_input(
            "Per-twist hard timeout (s)", min_value=1,
            value=int(cfg.get("per_twist_timeout") or 10),
            key=f"{key}-per-twist-timeout",
        ))
        cfg["allow_partial_scores"] = bool(st.toggle(
            "Allow partial-prime-coverage scores",
            value=bool(cfg.get("allow_partial_scores", True)),
            key=f"{key}-allow-partial-score",
            help=(
                "Keeps a twist rankable when some requested good-prime "
                "Frobenius terms fail; score provenance records the missing "
                "prime coverage explicitly."
            ),
        ))
        cfg["include_parent"] = bool(st.toggle(
            "Keep parent in downstream population",
            value=bool(cfg.get("include_parent", False)),
            key=f"{key}-parent",
        ))
        st.caption(
            "The twist and root number are exact. Ranking uses Rank Hunter's historical log-cardinality Nagao-style heuristic (cached-nagao-log-cardinality-v1), not a published Mestre–Nagao formula. Each trial is process-isolated; the stage stops at its wall budget and retains already-ranked children."
        )
    elif stage_id == "rank_jump_base_change":
        c1, c2 = st.columns(2)
        cfg["max_children"] = int(c1.number_input(
            "Max derived children",
            min_value=1,
            value=int(cfg.get("max_children") or 32),
            key=f"{key}-children",
        ))
        cfg["timeout"] = int(c2.number_input(
            "Plugin hard timeout (s)",
            min_value=1,
            value=int(cfg.get("timeout") or 30),
            key=f"{key}-timeout",
        ))
        cfg["include_parent"] = bool(st.toggle(
            "Keep parent in downstream population",
            value=bool(cfg.get("include_parent", False)),
            key=f"{key}-parent",
        ))
        st.caption(
            "Family-specific rank-jump maps stay in the family plugin. Core runs derive_pipeline_transform() in an isolated subprocess with this hard timeout and records every parent → child construction."
        )
    elif stage_id == "parameter_pullback":
        maps = _parse_mobius_maps(
            st.text_input(
                "Möbius maps a,b,c,d",
                ";".join(
                    ",".join(str(x) for x in row)
                    for row in cfg.get("maps") or []
                ),
                key=f"{key}-maps",
                help="Separate maps with semicolons. Example: 1,1,0,1;1,-1,0,1 maps t to t+1 and t-1.",
            )
        )
        if maps is None:
            st.warning("Each Möbius map must contain four rational coefficients a,b,c,d.")
        else:
            cfg["maps"] = maps
        cfg["include_parent"] = bool(st.toggle(
            "Keep parent in downstream population",
            value=bool(cfg.get("include_parent", False)),
            key=f"{key}-parent",
        ))
        st.caption(
            "Applies each exact nondegenerate Möbius map (a·t+b)/(c·t+d) to the current specialization. This generates new specializations; it is not a symbolic family pullback."
        )
    elif stage_id == "isogeny_walk":
        degrees = _parse_positive_ints(
            st.text_input(
                "Prime isogeny degrees",
                ",".join(str(x) for x in cfg.get("degrees") or [2, 3, 5, 7, 11, 13]),
                key=f"{key}-degrees",
            )
        )
        if degrees is None:
            st.warning("Use comma-separated positive prime degrees.")
        else:
            cfg["degrees"] = degrees
        c1, c2, c3 = st.columns(3)
        cfg["max_children"] = int(c1.number_input(
            "Max neighbors", min_value=1,
            value=int(cfg.get("max_children") or 12), key=f"{key}-children",
        ))
        cfg["timeout"] = int(c2.number_input(
            "Per-degree hard timeout (s)",
            min_value=1,
            value=int(cfg.get("timeout") or 60),
            key=f"{key}-timeout",
        ))
        cfg["include_parent"] = bool(c3.toggle(
            "Keep parent",
            value=bool(cfg.get("include_parent", False)),
            key=f"{key}-parent",
        ))
        cfg["transfer_basis"] = bool(st.toggle(
            "Transfer and re-certify rigorous witness basis",
            value=bool(cfg.get("transfer_basis", True)),
            key=f"{key}-transfer",
        ))
        c3, c4 = st.columns(2)
        cfg["certificate_timeout"] = int(c3.number_input(
            "Transfer certificate seconds", min_value=1,
            value=int(cfg.get("certificate_timeout") or 120),
            key=f"{key}-cert",
            disabled=not cfg["transfer_basis"],
        ))
        cfg["exact_candidates"] = int(c4.number_input(
            "Transfer exact candidates", min_value=1,
            value=int(cfg.get("exact_candidates") or 64),
            key=f"{key}-exact",
            disabled=not cfg["transfer_basis"],
        ))
        st.caption(
            "Enumerates each requested prime-degree isogeny neighborhood in a core-owned isolated Sage worker with the configured per-degree hard timeout. If requested, the parent's rigorous witness basis is mapped through the exact isogeny and independently certified on the child instead of copying a rank field."
        )
    elif stage_id == "covering_selmer_branch":
        engine_labels = {
            "simon_known": "Simon known-point 2-descent",
            "mwrank_selmer": "mwrank Selmer-only 2-descent",
            "mwrank_coverings": "mwrank full 2-covering search",
        }
        cfg["engines"] = st.multiselect(
            "Branches",
            list(engine_labels),
            default=[
                x for x in cfg.get("engines") or []
                if x in engine_labels
            ],
            format_func=lambda value: engine_labels[value],
            key=f"{key}-engines",
        )
        c1, c2, c3 = st.columns(3)
        cfg["timeout"] = int(c1.number_input(
            "Seconds / branch", min_value=1,
            value=int(cfg.get("timeout") or 60), key=f"{key}-timeout",
        ))
        cfg["certificate_timeout"] = int(c2.number_input(
            "Certificate seconds", min_value=1,
            value=int(cfg.get("certificate_timeout") or 120), key=f"{key}-cert",
        ))
        cfg["exact_candidates"] = int(c3.number_input(
            "Exact candidates", min_value=1,
            value=int(cfg.get("exact_candidates") or 64), key=f"{key}-exact",
        ))
        st.caption(
            "Each descent branch is independent. Rigorous uppers are stored as evidence; returned points still pass the ordinary exact independence path before rank can rise."
        )
    elif stage_id == "constructive_rank_jump_loop":
        heights = _parse_positive_ints(st.text_input(
            "Section-height ladder",
            ",".join(str(x) for x in cfg.get("heights") or [6, 8, 10, 12]),
            key=f"{key}-heights",
        ))
        if heights is None:
            st.warning("Use comma-separated positive heights.")
        else:
            cfg["heights"] = heights
        c1, c2, c3 = st.columns(3)
        modes = ["exact", "up_to"]
        mode = str(cfg.get("height_mode") or "exact")
        cfg["height_mode"] = c1.selectbox(
            "Height mode", modes,
            index=modes.index(mode if mode in modes else "exact"),
            key=f"{key}-height-mode",
        )
        cfg["coefficient_bound"] = int(c2.number_input(
            "Coefficient bound", min_value=1,
            value=int(cfg.get("coefficient_bound") or 10),
            key=f"{key}-coeff",
        ))
        cfg["extension_degree"] = int(c3.number_input(
            "Extension degree", min_value=2,
            value=int(cfg.get("extension_degree") or 2),
            key=f"{key}-degree",
        ))
        c4, c5, c6 = st.columns(3)
        cfg["division"] = int(c4.number_input(
            "Division", min_value=2,
            value=int(cfg.get("division") or 2),
            key=f"{key}-division",
        ))
        cfg["numerator_abs"] = int(c5.number_input(
            "|numerator| ≤", min_value=1,
            value=int(cfg.get("numerator_abs") or 1000),
            key=f"{key}-num",
        ))
        cfg["denominator_max"] = int(c6.number_input(
            "denominator ≤", min_value=1,
            value=int(cfg.get("denominator_max") or 1000),
            key=f"{key}-den",
        ))
        c7, c8, c9 = st.columns(3)
        cfg["max_tests"] = int(c7.number_input(
            "Max square tests", min_value=1,
            value=int(cfg.get("max_tests") or 500000),
            key=f"{key}-tests",
        ))
        cfg["max_children"] = int(c8.number_input(
            "Max children", min_value=1,
            value=int(cfg.get("max_children") or 96),
            key=f"{key}-children",
        ))
        cfg["stop_on_first_success"] = bool(c9.toggle(
            "Stop on first hit",
            value=bool(cfg.get("stop_on_first_success", False)),
            key=f"{key}-first",
        ))
        cfg["include_parent"] = bool(st.toggle(
            "Keep parent",
            value=bool(cfg.get("include_parent", False)),
            key=f"{key}-parent",
        ))
        st.caption(
            "Composite strategy: section shells → trace sections → forced division → exact square-condition specializations. Family hooks own the symbolic algebra; core owns exact witness checks and lineage."
        )
    elif stage_id == "section_height_shell":
        c1, c2, c3 = st.columns(3)
        cfg["height"] = int(c1.number_input(
            "Shell value", min_value=1,
            value=int(cfg.get("height") or 10), key=f"{key}-height",
        ))
        modes = ["exact", "up_to"]
        current = str(cfg.get("height_mode") or "exact")
        cfg["height_mode"] = c2.selectbox(
            "Shell mode", modes,
            index=modes.index(current if current in modes else "exact"),
            key=f"{key}-mode",
        )
        cfg["coefficient_bound"] = int(c3.number_input(
            "Coefficient bound", min_value=1,
            value=int(cfg.get("coefficient_bound") or 8), key=f"{key}-coeff",
        ))
        kinds = [
            "family_enumeration_shell",
            "coefficient_height",
            "polynomial_degree",
            "naive_parameter_height",
            "canonical_shioda_height",
        ]
        c4, c5 = st.columns(2)
        height_kind = str(
            cfg.get("height_kind") or "family_enumeration_shell"
        )
        cfg["height_kind"] = c4.selectbox(
            "Height / shell kind",
            kinds,
            index=kinds.index(
                height_kind
                if height_kind in kinds
                else "family_enumeration_shell"
            ),
            key=f"{key}-height-kind",
        )
        cfg["height_normalization"] = c5.text_input(
            "Height normalization",
            value=str(
                cfg.get("height_normalization") or "family_defined"
            ),
            key=f"{key}-height-normalization",
        )
        c6, c7 = st.columns(2)
        cfg["max_sections"] = int(c6.number_input(
            "Max section artifacts", min_value=1,
            value=int(cfg.get("max_sections") or 128), key=f"{key}-max",
        ))
        cfg["timeout"] = int(c7.number_input(
            "Hook hard timeout (s)", min_value=1,
            value=int(cfg.get("timeout") or 30), key=f"{key}-timeout",
        ))
        st.caption(
            "The selected height notion is part of the artifact contract. "
            "A family enumeration shell, coefficient height, polynomial "
            "degree, naive parameter height, and canonical/Shioda height are "
            "not interchangeable. Plugin artifacts are unverified by default; "
            "only a typed artifact that passes a registered verifier is marked "
            "as an exact construction. The family hook runs in a core-owned "
            "subprocess under the configured hard timeout."
        )
    elif stage_id == "trace_section_constructor":
        c1, c2, c3 = st.columns(3)
        cfg["extension_degree"] = int(c1.number_input(
            "Extension degree", min_value=2,
            value=int(cfg.get("extension_degree") or 2), key=f"{key}-degree",
        ))
        cfg["max_sections"] = int(c2.number_input(
            "Max trace sections", min_value=1,
            value=int(cfg.get("max_sections") or 128), key=f"{key}-max",
        ))
        cfg["timeout"] = int(c3.number_input(
            "Hook hard timeout (s)", min_value=1,
            value=int(cfg.get("timeout") or 30), key=f"{key}-timeout",
        ))
        st.caption(
            "Builds trace candidates such as P + σ(P) through the family adapter. "
            "Each typed artifact must name its extension/conjugates and exact source-section hash; "
            "only a registered verifier can mark the trace construction exact. "
            "The hook is process-isolated and bounded by the configured timeout."
        )
    elif stage_id == "forced_bisection_constructor":
        c1, c2, c3 = st.columns(3)
        divisions = [2, 3, 4]
        current_division = int(cfg.get("division") or 2)
        cfg["division"] = int(c1.selectbox(
            "Division n in nP = Q",
            divisions,
            index=divisions.index(
                current_division if current_division in divisions else 2
            ),
            key=f"{key}-division",
        ))
        slope_modes = [
            "forced_tangent",
            "division_polynomial",
            "family_exact",
        ]
        current_slope = str(
            cfg.get("slope_mode") or "forced_tangent"
        )
        if current_slope == "forced":
            current_slope = "forced_tangent"
        cfg["slope_mode"] = c2.selectbox(
            "Division relation mode",
            slope_modes,
            index=slope_modes.index(
                current_slope
                if current_slope in slope_modes
                else "forced_tangent"
            ),
            key=f"{key}-slope",
        )
        cfg["max_conditions"] = int(c3.number_input(
            "Max conditions", min_value=1,
            value=int(cfg.get("max_conditions") or 128), key=f"{key}-max",
        ))
        cfg["timeout"] = int(st.number_input(
            "Hook hard timeout (s)", min_value=1,
            value=int(cfg.get("timeout") or 30), key=f"{key}-timeout",
        ))
        st.caption(
            "Derives typed divisibility conditions bound to an exact source trace. "
            "The family plugin owns the formulas, but producer-declared exactness is ignored; "
            "a registered verifier must certify the nP = Q relation and condition. "
            "The hook is process-isolated and bounded by the configured timeout."
        )
    elif stage_id == "square_condition_specializer":
        c1, c2, c3 = st.columns(3)
        cfg["numerator_abs"] = int(c1.number_input(
            "|numerator| ≤", min_value=1,
            value=int(cfg.get("numerator_abs") or 500), key=f"{key}-num",
        ))
        cfg["denominator_max"] = int(c2.number_input(
            "denominator ≤", min_value=1,
            value=int(cfg.get("denominator_max") or 500), key=f"{key}-den",
        ))
        cfg["max_tests"] = int(c3.number_input(
            "Max square tests", min_value=1,
            value=int(cfg.get("max_tests") or 200000), key=f"{key}-tests",
        ))
        c4, c5, c6 = st.columns(3)
        cfg["max_children"] = int(c4.number_input(
            "Max children", min_value=1,
            value=int(cfg.get("max_children") or 64), key=f"{key}-children",
        ))
        cfg["timeout"] = int(c5.number_input(
            "Hook hard timeout (s)", min_value=1,
            value=int(cfg.get("timeout") or 30), key=f"{key}-timeout",
        ))
        cfg["include_parent"] = bool(c6.toggle(
            "Keep parent",
            value=bool(cfg.get("include_parent", False)),
            key=f"{key}-parent",
        ))
        st.caption(
            "The plugin proposes rational specialization hits behind a core-owned process-isolated timeout. Rank Hunter records requested/completed test and domain coverage; missing coverage cannot be called completed. A child is created only when the hit is bound to the exact parent-condition artifact and a registered verifier confirms condition(t)=square_value; core separately checks square_root²=square_value over QQ. Plugin partial/timeout/inconclusive/error status is preserved, and new specializations start with no inherited heuristic score."
        )
    elif stage_id == "surface_fibration_switch":
        c1, c2, c3 = st.columns(3)
        cfg["max_children"] = int(c1.number_input(
            "Max derived children", min_value=1,
            value=int(cfg.get("max_children") or 16), key=f"{key}-children",
        ))
        cfg["timeout"] = int(c2.number_input(
            "Plugin hard timeout (s)",
            min_value=1,
            value=int(cfg.get("timeout") or 30),
            key=f"{key}-timeout",
        ))
        cfg["include_parent"] = bool(c3.toggle(
            "Keep parent",
            value=bool(cfg.get("include_parent", False)),
            key=f"{key}-parent",
        ))
        st.caption(
            "The family plugin owns the surface/fibration mathematics. Core runs the transform hook in an isolated subprocess with this hard timeout and records every derived child with full lineage."
        )
    elif stage_id == "prime_table_cache":
        c1, c2 = st.columns(2)
        cfg["prime_bound"] = int(c1.number_input(
            "Good primes below",
            min_value=3,
            value=int(cfg.get("prime_bound") or 2000),
            step=100,
            key=f"{key}-bound",
        ))
        eager_bad = bool(c2.toggle(
            "Eagerly factor all bad primes",
            value=bool(cfg.get("eager_bad_primes", False)),
            key=f"{key}-eager-bad",
            help=(
                "Usually leave this off for large candidate populations. "
                "Exact bad-prime work can be done later by Local Root Numbers "
                "or Bad-Prime Fingerprint after the funnel narrows."
            ),
        ))
        cfg["eager_bad_primes"] = eager_bad
        cfg["include_all_bad"] = eager_bad
        cfg["bad_prime_timeout"] = int(st.number_input(
            "Bad-prime factorization seconds",
            min_value=1,
            value=int(cfg.get("bad_prime_timeout") or 20),
            key=f"{key}-bad-timeout",
            disabled=not eager_bad,
        ))
        st.caption(
            "Caches reusable exact good-prime Frobenius data. Full discriminant "
            "factorization is deferred by default because it can dominate large "
            "candidate pools."
        )
    elif stage_id == "multi_scale_frobenius":
        bounds = _parse_positive_ints(
            st.text_input(
                "Prime bounds",
                ",".join(str(x) for x in cfg.get("bounds") or [523, 1979, 5000]),
                key=f"{key}-bounds",
                help="Strictly increasing cumulative cutoffs, e.g. 523,1979,5000.",
            )
        )
        if bounds is None:
            st.warning("Use positive comma-separated prime bounds.")
        else:
            cfg["bounds"] = bounds
        st.caption(
            "Produces cumulative and band scores from cached #E(F_p). This is a survivor-ranking heuristic, never rank evidence."
        )
    elif stage_id == "mestre_nagao_ensemble":
        cfg["prime_bound"] = int(st.number_input(
            "Good primes below", min_value=3,
            value=int(cfg.get("prime_bound") or 5000), step=100,
            key=f"{key}-bound",
        ))
        st.caption(
            "Consensus of log-cardinality, classical -a_p log(p)/p, and normalized-trace signals. "
            "The consensus is scheduling-only and never becomes rank evidence."
        )
    elif stage_id == "frobenius_persistence":
        bounds = _parse_positive_ints(
            st.text_input(
                "Disjoint-band cutoffs",
                ",".join(str(x) for x in cfg.get("bounds") or [523, 1979, 5000, 10000]),
                key=f"{key}-bounds",
                help="Strictly increasing cumulative cutoffs; each resulting band is scored independently.",
            )
        )
        if bounds is None:
            st.warning("Use positive comma-separated prime bounds.")
        else:
            cfg["bounds"] = bounds
        st.caption(
            "The pipeline score is the weakest normalized band signal, so one unusually favorable small-prime interval cannot dominate."
        )
    elif stage_id == "explicit_formula_indicator":
        c1, c2 = st.columns(2)
        cfg["prime_bound"] = int(c1.number_input(
            "Prime / prime-power cutoff", min_value=3,
            value=int(cfg.get("prime_bound") or 5000), step=100,
            key=f"{key}-bound",
        ))
        cfg["max_prime_power"] = int(c2.number_input(
            "Maximum prime power", min_value=1, max_value=12,
            value=int(cfg.get("max_prime_power") or 4),
            key=f"{key}-power",
        ))
        st.caption(
            "Smoothed good-prime side of an explicit-formula calculation. Conductor, archimedean, and bad-prime corrections are omitted, so this is a heuristic indicator—not an analytic-rank bound."
        )
    elif stage_id == "mestre_bober_analytic_upper":
        c1, c2, c3 = st.columns(3)
        cfg["max_delta"] = float(c1.number_input(
            "Maximum Δ",
            min_value=0.1,
            max_value=3.0,
            value=float(cfg.get("max_delta") or 1.5),
            step=0.1,
            key=f"{key}-delta",
            help=(
                "Larger Δ can tighten the zero-sum analytic-rank upper, "
                "but Sage documents exponential runtime growth."
            ),
        ))
        cfg["timeout"] = int(c2.number_input(
            "Hard timeout (s)",
            min_value=1,
            value=int(cfg.get("timeout") or 60),
            key=f"{key}-timeout",
        ))
        cfg["ncpus"] = int(c3.number_input(
            "Maximum CPUs",
            min_value=1,
            value=int(cfg.get("ncpus") or 1),
            key=f"{key}-ncpus",
        ))
        cfg["adaptive"] = bool(st.toggle(
            "Adaptive Δ ladder",
            value=bool(cfg.get("adaptive", True)),
            key=f"{key}-adaptive",
            help=(
                "Allow Sage to try smaller Δ first and stop early when "
                "the parity-compatible minimum bound is reached."
            ),
        ))
        st.caption(
            "Mestre–Bober zero-sum analytic-rank upper from Sage. "
            "This result is conditional on GRH and is stored separately "
            "from rigorous Mordell–Weil rank bounds."
        )
    elif stage_id == "brumer_kramer_classgroup_bound":
        c1, c2 = st.columns(2)
        modes = ["grh", "unconditional"]
        current = str(cfg.get("proof_mode") or "grh")
        if current not in modes:
            current = "grh"
        cfg["proof_mode"] = c1.selectbox(
            "Class-group proof mode",
            modes,
            index=modes.index(current),
            format_func=lambda value: (
                "GRH-conditional (fast)"
                if value == "grh"
                else "Unconditional certification"
            ),
            key=f"{key}-proof-mode",
        )
        cfg["timeout"] = int(c2.number_input(
            "Hard timeout (s)",
            min_value=1,
            value=int(cfg.get("timeout") or 300),
            key=f"{key}-timeout",
        ))
        st.caption(
            "Brumer–Kramer class-group bound for curves with E(Q)[2] = 0. "
            "Fast mode uses Sage class_group(proof=False) and stores only a "
            "GRH-conditional Mordell–Weil upper. Unconditional mode may "
            "promote a completed bound to rigorous rank evidence."
        )
    elif stage_id == "cassels_tate_refinement":
        c1, c2 = st.columns(2)
        cfg["effort"] = int(c1.number_input(
            "PARI point-search effort",
            min_value=0,
            max_value=10,
            value=int(cfg.get("effort") or 0),
            key=f"{key}-effort",
            help=(
                "The pairing and refined upper remain unconditional. Values "
                "above zero also ask PARI to spend randomized effort finding "
                "points; runtime grows quickly."
            ),
        ))
        cfg["timeout"] = int(c2.number_input(
            "Hard timeout (s)",
            min_value=1,
            value=int(cfg.get("timeout") or 300),
            key=f"{key}-timeout",
        ))
        st.caption(
            "PARI computes the 2-Selmer ceiling and the 2-part Cassels pairing. "
            "Rank Hunter records the even pairing rank and the resulting "
            "unconditional refined Mordell–Weil upper separately. Returned "
            "points are diagnostic only in this stage."
        )
    elif stage_id == "isogeny_descent":
        c1, c2, c3 = st.columns(3)
        cfg["degree"] = int(c1.selectbox(
            "Isogeny degree",
            [2],
            index=0,
            key=f"{key}-degree",
            help="Core v1 supports rational 2-isogeny descent. Degree 3 requires optional Magma and is not approximated.",
        ))
        cfg["first_limit"] = int(c2.number_input(
            "First search limit",
            min_value=1,
            value=int(cfg.get("first_limit") or 20),
            key=f"{key}-first-limit",
        ))
        cfg["second_limit"] = int(c3.number_input(
            "Second search limit",
            min_value=1,
            value=int(cfg.get("second_limit") or 8),
            key=f"{key}-second-limit",
        ))
        c4, c5 = st.columns(2)
        cfg["second_descent"] = bool(c4.toggle(
            "Run second descent",
            value=bool(cfg.get("second_descent", True)),
            key=f"{key}-second-descent",
            help="Recommended for curves with rational 2-torsion; it can tighten the first isogeny-Selmer upper.",
        ))
        cfg["timeout"] = int(c5.number_input(
            "Hard timeout (s)",
            min_value=1,
            value=int(cfg.get("timeout") or 300),
            key=f"{key}-timeout",
        ))
        st.caption(
            "Runs mwrank's genuine descent through rational 2-isogenies and "
            "their duals. Every reported route is matched to an exact Sage "
            "isogeny map before its rigorous upper can enter rank evidence."
        )
    elif stage_id == "local_root_numbers":
        c1, c2 = st.columns(2)
        options = ["any", "+1", "-1"]
        current = str(cfg.get("global_sign") or "any")
        if current not in options:
            current = "any"
        cfg["global_sign"] = c1.selectbox(
            "Global root-number sign",
            options,
            index=options.index(current),
            key=f"{key}-sign",
        )
        cfg["prune_mismatch"] = bool(c2.toggle(
            "Prune sign mismatches",
            value=bool(cfg.get("prune_mismatch", False)),
            key=f"{key}-prune",
        ))
        cfg["bad_prime_timeout"] = int(st.number_input(
            "Bad-prime factorization seconds",
            min_value=1,
            value=int(cfg.get("bad_prime_timeout") or 20),
            key=f"{key}-bad-timeout",
        ))
        st.caption(
            "Stores the exact bad-prime local factors and global sign. A sign filter changes scheduling only; it does not promote rank."
        )
    elif stage_id == "bad_prime_fingerprint":
        parsed = _parse_optional_positive_ints(
            st.text_input(
                "Required bad primes",
                ",".join(str(x) for x in cfg.get("required_primes") or []),
                key=f"{key}-required",
                help="Optional comma-separated primes such as 29,37,41,73.",
            )
        )
        if parsed is None:
            st.warning("Required bad primes must be comma-separated prime numbers.")
        else:
            cfg["required_primes"] = parsed
        c1, c2 = st.columns(2)
        cfg["max_bad_primes"] = int(c1.number_input(
            "Maximum bad-prime count (0 = no cap)",
            min_value=0,
            value=int(cfg.get("max_bad_primes") or 0),
            key=f"{key}-maxbad",
        ))
        cfg["prune_mismatch"] = bool(c2.toggle(
            "Prune filter mismatches",
            value=bool(cfg.get("prune_mismatch", False)),
            key=f"{key}-prune",
        ))
        cfg["bad_prime_timeout"] = int(st.number_input(
            "Bad-prime factorization seconds",
            min_value=1,
            value=int(cfg.get("bad_prime_timeout") or 20),
            key=f"{key}-bad-timeout",
        ))
        st.caption(
            "The fingerprint records exact local reduction metadata. Any correlation with high rank remains a scheduling hypothesis unless separately proved."
        )
    elif stage_id == "torsion_mod_p_sieve":
        mode = str(st.session_state.get("byo_stage_mode") or "")
        groups = (["target"] + list(MAZUR_TORSION_CHOICES)) if mode == "torsion" else list(MAZUR_TORSION_CHOICES)
        current = str(cfg.get("torsion_group") or ("target" if mode == "torsion" else "C2"))
        if current not in groups:
            current = "target" if mode == "torsion" else "C2"
        cfg["torsion_group"] = st.selectbox(
            "Required rational torsion",
            groups,
            index=groups.index(current),
            format_func=lambda value: "Selected torsion target" if value == "target" else value,
            key=f"{key}-group",
        )
        c1, c2, c3 = st.columns(3)
        cfg["prime_bound"] = int(c1.number_input(
            "Good primes below",
            min_value=3,
            value=int(cfg.get("prime_bound") or 100),
            key=f"{key}-bound",
        ))
        cfg["max_primes"] = int(c2.number_input(
            "Maximum primes",
            min_value=1,
            value=int(cfg.get("max_primes") or 12),
            key=f"{key}-max",
        ))
        cfg["prune_obstructed"] = bool(c3.toggle(
            "Prune rigorous obstructions",
            value=bool(cfg.get("prune_obstructed", True)),
            key=f"{key}-prune",
        ))
        st.caption(
            "At good p not dividing the target torsion order, rational torsion injects into E(F_p). "
            "Failure of the divisibility test is a rigorous rejection; passing is only necessary, not proof of torsion."
        )
    elif stage_id == "local_solubility_sieve":
        parsed = _parse_optional_positive_ints(
            st.text_input(
                "Explicit local primes",
                ",".join(str(x) for x in cfg.get("explicit_primes") or [2, 3, 5, 7, 11]),
                key=f"{key}-primes",
            )
        )
        if parsed is None:
            st.warning("Local primes must be comma-separated prime numbers.")
        else:
            cfg["explicit_primes"] = parsed
        c1, c2 = st.columns(2)
        cfg["include_bad"] = bool(c1.toggle(
            "Also test bad primes",
            value=bool(cfg.get("include_bad", True)),
            key=f"{key}-bad",
        ))
        cfg["max_prime"] = int(c2.number_input(
            "Maximum local prime",
            min_value=2,
            value=int(cfg.get("max_prime") or 97),
            key=f"{key}-max",
        ))
        cfg["bad_prime_timeout"] = int(st.number_input(
            "Bad-prime factorization seconds",
            min_value=1,
            value=int(cfg.get("bad_prime_timeout") or 20),
            key=f"{key}-bad-timeout",
            disabled=not bool(cfg["include_bad"]),
        ))
        st.caption(
            "For degree-4 coverings, absence of any projective F_p point is a rigorous Q_p obstruction. "
            "A passing mod-p test is deliberately reported only as not obstructed. When this stage precedes Point-Centered Quartics, obstructed quartics skip ratpoints."
        )
    elif stage_id == "corpus_filter":
        cfg["policy"] = st.selectbox(
            "Library policy",
            ["annotate", "exclude-known", "known-only", "off"],
            index=["annotate", "exclude-known", "known-only", "off"].index(
                str(cfg.get("policy") or "annotate")
                if str(cfg.get("policy") or "annotate") in {"annotate", "exclude-known", "known-only", "off"}
                else "annotate"
            ),
            key=f"{key}-policy",
            help="exclude-known / known-only require a built plugin research library. Library membership is routing metadata, not rank evidence.",
        )
    elif stage_id == "family_baseline":
        c1, c2 = st.columns(2)
        cfg["certificate_timeout"] = int(c1.number_input(
            "Certificate seconds", min_value=1,
            value=int(cfg.get("certificate_timeout") or 120), key=f"{key}-cert",
        ))
        cfg["exact_candidates"] = int(c2.number_input(
            "Exact candidates", min_value=1,
            value=int(cfg.get("exact_candidates") or 64), key=f"{key}-cands",
        ))
    elif stage_id == "specialization_injectivity_certificate":
        c1, c2, c3 = st.columns(3)
        cfg["timeout"] = int(c1.number_input(
            "Criterion timeout (s)",
            min_value=1,
            value=int(cfg.get("timeout") or 60),
            key=f"{key}-timeout",
        ))
        cfg["max_divisors"] = int(c2.number_input(
            "Square-free divisor budget",
            min_value=1,
            max_value=65536,
            value=int(cfg.get("max_divisors") or 4096),
            key=f"{key}-divisors",
        ))
        cfg["certificate_timeout"] = int(c3.number_input(
            "Section independence timeout (s)",
            min_value=1,
            value=int(cfg.get("certificate_timeout") or 120),
            key=f"{key}-cert",
        ))
        st.caption(
            "Rigorous Gusić–Tadić injectivity criterion for exact FormulaFamily "
            "models already written as y²=x³+A(t)x²+B(t)x with exactly one "
            "nontrivial rational 2-torsion point. Generic-family evidence is "
            "stored separately from the selected fiber's rank evidence."
        )
    elif stage_id == "pari_upper_gate":
        c1, c2 = st.columns(2)
        cfg["timeout"] = int(c1.number_input(
            "Seconds", min_value=1, value=int(cfg.get("timeout") or 2),
            key=f"{key}-timeout",
        ))
        cfg["eliminate_below_goal"] = bool(c2.toggle(
            "Reject proven-below-goal fibers",
            value=bool(cfg.get("eliminate_below_goal", True)),
            key=f"{key}-elim",
        ))
    elif stage_id == "selmer_bound":
        c1, c2, c3 = st.columns(3)
        cfg["timeout"] = int(c1.number_input(
            "Seconds", min_value=1, value=int(cfg.get("timeout") or 60),
            key=f"{key}-timeout",
        ))
        cfg["first_limit"] = int(c2.number_input(
            "First limit", min_value=1, value=int(cfg.get("first_limit") or 20),
            key=f"{key}-first",
        ))
        cfg["second_limit"] = int(c3.number_input(
            "Second limit", min_value=1, value=int(cfg.get("second_limit") or 10),
            key=f"{key}-second",
        ))
    elif stage_id == "selmer_headroom":
        st.caption(
            "Uses the prior rigorous 2-Selmer upper minus the current rigorous lower as a scheduling score. A positive gap is not missing Mordell–Weil rank; Sha can contribute."
        )
    elif stage_id == "small_point_density":
        cfg["heights"] = _parse_heights(
            st.text_input(
                "Shallow integral heights",
                ",".join(str(x) for x in cfg.get("heights") or [100, 1000, 10000]),
                key=f"{key}-heights",
            ),
            cfg.get("heights") or [100, 1000, 10000],
        )
        c1, c2, c3 = st.columns(3)
        cfg["timeout"] = int(c1.number_input(
            "Seconds/search", min_value=1,
            value=int(cfg.get("timeout") or 3), key=f"{key}-timeout",
        ))
        cfg["certificate_timeout"] = int(c2.number_input(
            "Certificate seconds", min_value=1,
            value=int(cfg.get("certificate_timeout") or 120), key=f"{key}-cert",
        ))
        cfg["exact_candidates"] = int(c3.number_input(
            "Exact candidates", min_value=1,
            value=int(cfg.get("exact_candidates") or 32), key=f"{key}-exact",
        ))
        st.caption(
            "Rank Hunter experimental search-yield heuristic, not a mathematical density statistic. "
            "Each height searches one fixed denominator-1 search model; fresh exact-point yield is scored only from comparable completed rounds. "
            "Discovered points still use the ordinary exact verification/certification path."
        )
    elif stage_id == "denominator_band":
        c1, c2, c3, c4 = st.columns(4)
        cfg["denominator_low"] = int(c1.number_input(
            "Denominator min",
            min_value=1,
            value=int(cfg.get("denominator_low") or 2),
            key=f"{key}-den-low",
        ))
        cfg["denominator_high"] = int(c2.number_input(
            "Denominator max",
            min_value=1,
            value=int(cfg.get("denominator_high") or 100),
            key=f"{key}-den-high",
        ))
        cfg["charts"] = int(c3.number_input(
            "Charts/fiber",
            min_value=1,
            value=int(cfg.get("charts") or 5),
            key=f"{key}-charts",
        ))
        cfg["timeout"] = int(c4.number_input(
            "Seconds/search",
            min_value=1,
            value=int(cfg.get("timeout") or 8),
            key=f"{key}-timeout",
        ))
        cfg["heights"] = _parse_heights(
            st.text_input(
                "Height stages",
                ",".join(str(x) for x in cfg.get("heights") or [10000, 100000]),
                key=f"{key}-heights",
            ),
            cfg.get("heights") or [10000, 100000],
        )
        c5, c6 = st.columns(2)
        cfg["certificate_timeout"] = int(c5.number_input(
            "Certificate seconds",
            min_value=1,
            value=int(cfg.get("certificate_timeout") or 120),
            key=f"{key}-cert",
        ))
        cfg["exact_candidates"] = int(c6.number_input(
            "Exact candidates",
            min_value=1,
            value=int(cfg.get("exact_candidates") or 64),
            key=f"{key}-exact",
        ))
        st.caption(
            "The band applies to ratpoints denominators in each adaptive affine search chart. "
            "These are chart-search regions, not invariant or disjoint native Mordell–Weil denominator shells. "
            "A completed no-hit region says only that no finite point was found in that configured region. "
            "Any discovered point is mapped back to the stored curve and exact-certified."
        )
    elif stage_id == "point_yield_persistence":
        cfg["minimum_bands"] = int(st.number_input(
            "Minimum completed denominator bands", min_value=1,
            value=int(cfg.get("minimum_bands") or 2),
            key=f"{key}-minimum-bands",
        ))
        st.caption(
            "Rank Hunter experimental scheduling heuristic: scores continued fresh exact-point discovery across completed comparable, progressively deeper chart-denominator searches. "
            "The bands are search geometry, not invariant native Mordell–Weil shells or a theorem about high-denominator points."
        )
    elif stage_id in {"integral_seed", "affine_search"}:
        heights = ",".join(str(x) for x in cfg.get("heights") or [])
        cfg["heights"] = _parse_heights(
            st.text_input("Height stages", heights, key=f"{key}-heights"),
            cfg.get("heights") or [1000, 10000],
        )
        c1, c2 = st.columns(2)
        if stage_id == "affine_search":
            cfg["charts"] = int(c1.number_input(
                "Charts", min_value=1, value=int(cfg.get("charts") or 24),
                key=f"{key}-charts",
            ))
            timeout_col = c2
        else:
            modes = ["stored", "minimal"]
            current = str(cfg.get("model_mode") or "stored")
            cfg["model_mode"] = c1.selectbox(
                "Search model",
                modes,
                index=modes.index(current if current in modes else "stored"),
                format_func=lambda value: (
                    "Stored/family model (fast)"
                    if value == "stored"
                    else "Global minimal model (optional)"
                ),
                key=f"{key}-model-mode",
            )
            timeout_col = c2
        cfg["timeout"] = int(timeout_col.number_input(
            "Seconds/search", min_value=1, value=int(cfg.get("timeout") or 4),
            key=f"{key}-timeout",
        ))
        if stage_id == "integral_seed":
            cfg["certify_after_search"] = bool(st.toggle(
                "Certify immediately",
                value=bool(cfg.get("certify_after_search", False)),
                key=f"{key}-certify-now",
                help=(
                    "Usually leave off when Exact Independence appears later in the pipeline. "
                    "Search hits remain exact verified points and are batch-certified there."
                ),
            ))
            if cfg["model_mode"] == "minimal":
                cfg["model_prep_timeout"] = int(st.number_input(
                    "Minimal-model prep seconds",
                    min_value=1,
                    value=int(cfg.get("model_prep_timeout") or 8),
                    key=f"{key}-model-prep",
                ))
            st.caption(
                "Stored-model mode sends the exact family/stored model directly to ratpoints. "
                "Rank Hunter clears rational coefficient denominators exactly; global minimalization is optional, not a prerequisite."
            )
    elif stage_id == "simon_covering":
        c1, c2, c3, c4 = st.columns(4)
        cfg["timeout"] = int(c1.number_input(
            "Seconds", min_value=1, value=int(cfg.get("timeout") or 90),
            key=f"{key}-timeout",
        ))
        cfg["lim1"] = int(c2.number_input(
            "lim1", min_value=1, value=int(cfg.get("lim1") or 5),
            key=f"{key}-lim1",
        ))
        cfg["lim3"] = int(c3.number_input(
            "lim3", min_value=1, value=int(cfg.get("lim3") or 80),
            key=f"{key}-lim3",
        ))
        cfg["limbigprime"] = int(c4.number_input(
            "LIMBIGPRIME", min_value=0,
            value=int(
                cfg.get("limbigprime")
                if cfg.get("limbigprime") is not None
                else 0
            ),
            key=f"{key}-limbigprime",
            help=(
                "0 requests deterministic local tests. Nonzero values enable "
                "Simon's probabilistic large-prime branch; Rank Hunter will "
                "not promote that reported upper as rigorous evidence."
            ),
        ))
        st.caption(
            "Legacy/alternative Sage Simon path. Keep LIMBIGPRIME = 0 for "
            "deterministic rigorous upper-bound evidence; nonzero mode remains "
            "usable for exact point discovery only."
        )
    elif stage_id == "mwrank_covering":
        c1, c2, c3, c4 = st.columns(4)
        cfg["timeout"] = int(c1.number_input(
            "Seconds", min_value=1, value=int(cfg.get("timeout") or 90),
            key=f"{key}-timeout",
        ))
        cfg["first_limit"] = int(c2.number_input(
            "First limit", min_value=1, value=int(cfg.get("first_limit") or 20),
            key=f"{key}-first",
        ))
        cfg["second_limit"] = int(c3.number_input(
            "Second limit", min_value=1, value=int(cfg.get("second_limit") or 10),
            key=f"{key}-second",
        ))
        cfg["exact_candidates"] = int(c4.number_input(
            "Exact candidates", min_value=1,
            value=int(cfg.get("exact_candidates") or 64),
            key=f"{key}-cands",
        ))
        r1, r2 = st.columns(2)
        retry_choices = ["manual", "automatic", "escalated"]
        retry_value = str(cfg.get("retry_policy") or "manual")
        if retry_value not in retry_choices:
            retry_value = "manual"
        cfg["retry_policy"] = r1.selectbox(
            "Retry policy",
            retry_choices,
            index=retry_choices.index(retry_value),
            key=f"{key}-retry-policy",
            help=(
                "manual preserves a timeout/error until you choose to rerun; "
                "automatic repeats the same budget; escalated retries once "
                "with the larger retry timeout."
            ),
        )
        cfg["retry_timeout"] = int(r2.number_input(
            "Retry timeout", min_value=1,
            value=int(cfg.get("retry_timeout") or max(300, cfg["timeout"])),
            key=f"{key}-retry-timeout",
            help="Used by escalated retry; must exceed Seconds when escalated.",
        ))
        st.caption(
            "Each bounded mwrank covering attempt keeps a stable Pipeline "
            "attempt identity. Timeout/error is inconclusive, not failure, and "
            "will not repeat automatically under the default manual policy."
        )
    elif stage_id == "upper_bound_rescue_ladder":
        c1, c2, c3 = st.columns(3)
        cfg["pari_enabled"] = bool(c1.toggle(
            "Quick PARI",
            value=bool(cfg.get("pari_enabled", True)),
            key=f"{key}-pari-enabled",
        ))
        cfg["pari_timeout"] = int(c2.number_input(
            "PARI seconds", min_value=1,
            value=int(cfg.get("pari_timeout") or 3),
            key=f"{key}-pari-time",
            disabled=not cfg["pari_enabled"],
        ))
        cfg["isogeny_pari_enabled"] = bool(c3.toggle(
            "PARI on isogenous models",
            value=bool(cfg.get("isogeny_pari_enabled", True)),
            key=f"{key}-iso-enabled",
        ))
        degrees = _parse_positive_ints(st.text_input(
            "Isogeny degrees",
            ",".join(str(x) for x in cfg.get("isogeny_degrees") or [2, 3, 5, 7, 11, 13]),
            key=f"{key}-iso-degrees",
        ))
        if degrees is None:
            st.warning("Use comma-separated positive isogeny degrees.")
        else:
            cfg["isogeny_degrees"] = degrees
        c4, c5, c6, c7 = st.columns(4)
        cfg["isogeny_max_models"] = int(c4.number_input(
            "Max isogenous models", min_value=1,
            value=int(cfg.get("isogeny_max_models") or 6),
            key=f"{key}-iso-models",
            disabled=not cfg["isogeny_pari_enabled"],
        ))
        cfg["isogeny_discovery_timeout"] = int(c5.number_input(
            "Seconds / isogeny degree", min_value=1,
            value=int(cfg.get("isogeny_discovery_timeout") or 15),
            key=f"{key}-iso-discovery-time",
            disabled=not cfg["isogeny_pari_enabled"],
        ))
        cfg["isogeny_pari_timeout"] = int(c6.number_input(
            "Seconds / isogenous PARI", min_value=1,
            value=int(cfg.get("isogeny_pari_timeout") or 3),
            key=f"{key}-iso-time",
            disabled=not cfg["isogeny_pari_enabled"],
        ))
        cfg["selmer_enabled"] = bool(c7.toggle(
            "Short 2-Selmer fallback",
            value=bool(cfg.get("selmer_enabled", True)),
            key=f"{key}-selmer-enabled",
        ))
        c7, c8, c9 = st.columns(3)
        cfg["selmer_timeout"] = int(c7.number_input(
            "2-Selmer seconds", min_value=1,
            value=int(cfg.get("selmer_timeout") or 10),
            key=f"{key}-selmer-time",
            disabled=not cfg["selmer_enabled"],
        ))
        cfg["known_basis_coverings"] = bool(c8.toggle(
            "Known-basis coverings",
            value=bool(cfg.get("known_basis_coverings", True)),
            key=f"{key}-known-cover",
        ))
        cfg["higher_descent_hooks"] = bool(c9.toggle(
            "Higher-descent hooks",
            value=bool(cfg.get("higher_descent_hooks", True)),
            key=f"{key}-higher-enabled",
        ))
        c10, c11, c12 = st.columns(3)
        cfg["simon_timeout"] = int(c10.number_input(
            "Simon seconds", min_value=1,
            value=int(cfg.get("simon_timeout") or 12),
            key=f"{key}-simon-time",
            disabled=not cfg["known_basis_coverings"],
        ))
        cfg["covering_timeout"] = int(c11.number_input(
            "mwrank covering seconds", min_value=1,
            value=int(cfg.get("covering_timeout") or 12),
            key=f"{key}-cover-time",
            disabled=not cfg["known_basis_coverings"],
        ))
        cfg["higher_timeout"] = int(c12.number_input(
            "Higher-descent seconds", min_value=1,
            value=int(cfg.get("higher_timeout") or 20),
            key=f"{key}-higher-time",
            disabled=not cfg["higher_descent_hooks"],
        ))
        levels = _parse_positive_ints(st.text_input(
            "Higher descent levels",
            ",".join(str(x) for x in cfg.get("higher_levels") or [4, 8, 12]),
            key=f"{key}-higher-levels",
        ))
        if levels is None:
            st.warning("Use comma-separated descent levels greater than 2.")
        else:
            cfg["higher_levels"] = levels
        st.caption(
            "Bounded rigorous upper-bound fallbacks. A timeout, engine failure, or unsupported branch is inconclusive and the curve continues. "
            "Isogeny degrees must be prime; discovery runs in a core-owned subprocess with a separate per-degree hard timeout. "
            "Exact Q-isogenous models may be retried because Mordell-Weil rank is invariant under rational isogeny."
        )
    elif stage_id in {"large_height_generator_hunt", "record_breaker_lane"}:
        if stage_id == "record_breaker_lane":
            threshold_labels = {
                "goal_minus_one": "Goal − 1",
                "fixed": "Fixed rigorous rank",
            }
            threshold_mode = str(cfg.get("minimum_rank_mode") or "fixed")
            if threshold_mode not in threshold_labels:
                threshold_mode = "fixed"
            cfg["minimum_rank_mode"] = st.selectbox(
                "Entry threshold",
                list(threshold_labels),
                index=list(threshold_labels).index(threshold_mode),
                format_func=lambda value: threshold_labels[value],
                key=f"{key}-minimum-rank-mode",
                help=(
                    "Goal − 1 automatically sends only curves one rigorous rank below "
                    "the pipeline target into the deep record lane."
                ),
            )
            cfg["minimum_rank"] = int(st.number_input(
                "Minimum rigorous rank to enter lane",
                min_value=1,
                value=int(cfg.get("minimum_rank") or 25),
                key=f"{key}-minimum-rank",
                disabled=cfg["minimum_rank_mode"] != "fixed",
                help=(
                    "Used only in Fixed rigorous rank mode. The threshold is checked "
                    "only against the persisted rigorous lower bound; heuristic scores "
                    "never qualify a curve for the Record Breaker Lane."
                ),
            ))
        levels = _parse_positive_ints(st.text_input(
            "Descent ladder",
            ",".join(str(x) for x in cfg.get("descent_levels") or [2, 4, 8, 12]),
            key=f"{key}-levels",
        ))
        if levels is None:
            st.warning("Use comma-separated descent levels.")
        else:
            cfg["descent_levels"] = levels
        bounds = _parse_positive_ints(st.text_input(
            "Saturation bounds",
            ",".join(str(x) for x in cfg.get("saturation_bounds") or [7, 31]),
            key=f"{key}-sat-bounds",
        ))
        if bounds is None:
            st.warning("Use comma-separated saturation bounds.")
        else:
            cfg["saturation_bounds"] = bounds
        bands_text = ",".join(
            f"{pair[0]}-{pair[1]}"
            for pair in cfg.get("denominator_bands")
            or [[2, 100], [101, 1000], [1001, 10000], [10001, 100000]]
        )
        bands = _parse_denominator_bands(st.text_input(
            "Denominator ladder",
            bands_text,
            key=f"{key}-bands",
        ))
        if bands is None:
            st.warning("Use non-overlapping bands like 2-100,101-1000.")
        else:
            cfg["denominator_bands"] = bands
        c1, c2, c3 = st.columns(3)
        cfg["descent_timeout"] = int(c1.number_input(
            "Descent seconds", min_value=1,
            value=int(cfg.get("descent_timeout") or 120),
            key=f"{key}-descent-time",
        ))
        cfg["max_coverings"] = int(c2.number_input(
            "Max coverings", min_value=1,
            value=int(cfg.get("max_coverings") or 24),
            key=f"{key}-coverings",
        ))
        cfg["covering_timeout"] = int(c3.number_input(
            "Covering seconds", min_value=1,
            value=int(cfg.get("covering_timeout") or 60),
            key=f"{key}-cover-time",
        ))
        c4, c5, c6 = st.columns(3)
        cfg["padic_enabled"] = bool(c4.toggle(
            "Use p-adic branch",
            value=bool(cfg.get("padic_enabled", True)),
            key=f"{key}-padic-enabled",
        ))
        cfg["padic_prime"] = int(c5.number_input(
            "p-adic prime", min_value=2,
            value=int(cfg.get("padic_prime") or 2),
            key=f"{key}-padic-prime",
            disabled=not cfg["padic_enabled"],
        ))
        cfg["padic_precision"] = int(c6.number_input(
            "p-adic precision", min_value=1,
            value=int(cfg.get("padic_precision") or 60),
            key=f"{key}-padic-precision",
            disabled=not cfg["padic_enabled"],
        ))
        c7, c8, c9 = st.columns(3)
        cfg["mw_growth_rounds"] = int(c7.number_input(
            "MW feedback rounds", min_value=1,
            value=int(cfg.get("mw_growth_rounds") or 6),
            key=f"{key}-growth-rounds",
        ))
        cfg["certificate_timeout"] = int(c8.number_input(
            "Certificate seconds", min_value=1,
            value=int(cfg.get("certificate_timeout") or 180),
            key=f"{key}-cert",
        ))
        cfg["exact_candidates"] = int(c9.number_input(
            "Exact candidates", min_value=1,
            value=int(cfg.get("exact_candidates") or 128),
            key=f"{key}-exact",
        ))
        cfg["stop_on_growth"] = bool(st.toggle(
            "Stop after first certified new generator",
            value=bool(cfg.get("stop_on_growth", True)),
            key=f"{key}-stop-growth",
        ))
        cwall1, cwall2, cwall3 = st.columns(3)
        cfg["wall_timeout"] = int(cwall1.number_input(
            "Strategy wall seconds", min_value=1,
            value=int(cfg.get("wall_timeout") or (7200 if stage_id == "record_breaker_lane" else 1800)),
            key=f"{key}-strategy-wall",
        ))
        retry_labels = ["automatic", "manual", "escalated"]
        retry_value = str(cfg.get("retry_policy") or "automatic")
        if retry_value not in retry_labels:
            retry_value = "automatic"
        cfg["retry_policy"] = cwall2.selectbox(
            "Strategy retry policy", retry_labels,
            index=retry_labels.index(retry_value),
            key=f"{key}-strategy-retry",
        )
        cfg["retry_timeout"] = int(cwall3.number_input(
            "Escalated wall seconds", min_value=1,
            value=int(cfg.get("retry_timeout") or max(3600, int(cfg["wall_timeout"]) * 2)),
            key=f"{key}-strategy-retry-wall",
        ))

        c13, c14, c15 = st.columns(3)
        cfg["mw_growth_anchors"] = int(c13.number_input(
            "MW anchor models", min_value=1,
            value=int(cfg.get("mw_growth_anchors") or 32),
            key=f"{key}-growth-anchors",
        ))
        cfg["mw_growth_pool"] = int(c14.number_input(
            "MW anchor pool", min_value=1,
            value=int(cfg.get("mw_growth_pool") or 320),
            key=f"{key}-growth-pool",
        ))
        cfg["mw_growth_deep_keep"] = int(c15.number_input(
            "MW deep keep", min_value=1,
            value=int(cfg.get("mw_growth_deep_keep") or 12),
            key=f"{key}-growth-deep-keep",
        ))
        cfg["mw_growth_heights"] = _parse_heights(
            st.text_input(
                "MW height stages",
                ",".join(str(x) for x in cfg.get("mw_growth_heights") or [10000, 100000, 1000000]),
                key=f"{key}-growth-heights",
            ),
            cfg.get("mw_growth_heights") or [10000, 100000, 1000000],
        )
        c16, c17 = st.columns(2)
        cfg["mw_growth_timeout"] = int(c16.number_input(
            "MW seconds/search", min_value=1,
            value=int(cfg.get("mw_growth_timeout") or 8),
            key=f"{key}-growth-timeout",
        ))
        cfg["mw_growth_reduce_timeout"] = int(c17.number_input(
            "MW reduction seconds", min_value=0,
            value=int(cfg.get("mw_growth_reduce_timeout") or 20),
            key=f"{key}-growth-reduce-timeout",
        ))

        c18, c19, c20 = st.columns(3)
        cfg["denominator_charts"] = int(c18.number_input(
            "Denominator charts/fiber", min_value=1,
            value=int(cfg.get("denominator_charts") or 8),
            key=f"{key}-denom-charts",
        ))
        cfg["denominator_timeout"] = int(c19.number_input(
            "Denominator seconds/search", min_value=1,
            value=int(cfg.get("denominator_timeout") or 12),
            key=f"{key}-denom-timeout",
        ))
        cfg["denominator_heights"] = _parse_heights(
            c20.text_input(
                "Denominator heights",
                ",".join(str(x) for x in cfg.get("denominator_heights") or [100000, 1000000]),
                key=f"{key}-denom-heights",
            ),
            cfg.get("denominator_heights") or [100000, 1000000],
        )

        c10, c11, c12 = st.columns(3)
        cfg["late_saturation_enabled"] = bool(c10.toggle(
            "Late saturation rescue",
            value=bool(cfg.get("late_saturation_enabled", True)),
            key=f"{key}-late-sat",
        ))
        cfg["saturation_timeout"] = int(c11.number_input(
            "Saturation seconds/round",
            min_value=1,
            value=int(cfg.get("saturation_timeout") or 20),
            key=f"{key}-sat-time",
            disabled=not cfg["late_saturation_enabled"],
        ))
        cfg["late_lattice_enabled"] = bool(c12.toggle(
            "Late lattice rescue",
            value=bool(cfg.get("late_lattice_enabled", True)),
            key=f"{key}-late-lattice",
        ))
        envelope = _deep_hunt_timeout_envelope(cfg)
        st.caption(
            "Configured timeout envelope (not an ETA): "
            f"no-growth MW pass ≤ {_format_budget_seconds(envelope['mw_seconds_no_growth'])} "
            f"across {envelope['mw_searches_no_growth']} search(es); "
            f"configured denominator portfolio = {envelope['denominator_configured_slots']} "
            f"chart/height slot(s) × {int(cfg.get('denominator_timeout') or 0)}s "
            f"= {_format_budget_seconds(envelope['denominator_configured_seconds'])} raw timeout envelope; "
            f"{envelope['denominator_searches']} slot(s) are runnable after denominator/height pruning "
            f"(≤ {_format_budget_seconds(envelope['denominator_seconds'])}). "
            f"Total strategy wall cap = {_format_budget_seconds(envelope['wall_timeout_seconds'])} "
            "per attempt before checkpointed resume. "
            f"Certified growth can trigger up to {envelope['mw_rounds_max']} MW round(s). "
            "Each denominator chart/height attempt is durably checkpointed. "
            "Descent, coverings, certification, retry attempts, and process overhead are additional."
        )
        if stage_id == "record_breaker_lane":
            st.caption(
                "Record Breaker Lane uses the same deep generator-recovery engine as Large-Height Generator Hunt, "
                "but only after the persisted rigorous lower bound reaches the threshold above. "
                "Curves below the threshold are skipped; heuristic scores never promote them into the lane. "
                "The deep lane inherits the durable checkpoint/resume plan and total wall-clock budget."
            )
        else:
            st.caption(
                "Composite strategy: structured descent/covering hooks → MW feedback geometry → denominator escalation → late saturation/LLL rescue. Search-producing work runs before subgroup cleanup; unsupported research engines remain inconclusive. Each strategy occurrence checkpoints substeps, returned rounds, and denominator bands durably; automatic retry resumes from the first unfinished checkpoint under the total wall budget."
            )
    elif stage_id == "higher_descent_ladder":
        levels = _parse_positive_ints(st.text_input(
            "Descent levels",
            ",".join(str(x) for x in cfg.get("levels") or [2, 4, 8, 12]),
            key=f"{key}-levels",
        ))
        if levels is None:
            st.warning("Use comma-separated descent levels.")
        else:
            cfg["levels"] = levels
        c1, c2, c3 = st.columns(3)
        cfg["timeout"] = int(c1.number_input(
            "Seconds / level", min_value=1,
            value=int(cfg.get("timeout") or 120), key=f"{key}-timeout",
        ))
        cfg["certificate_timeout"] = int(c2.number_input(
            "Certificate seconds", min_value=1,
            value=int(cfg.get("certificate_timeout") or 120), key=f"{key}-cert",
        ))
        cfg["exact_candidates"] = int(c3.number_input(
            "Exact candidates", min_value=1,
            value=int(cfg.get("exact_candidates") or 96), key=f"{key}-exact",
        ))
        cfg["stop_on_growth"] = bool(st.toggle(
            "Stop ladder after first certified growth",
            value=bool(cfg.get("stop_on_growth", False)),
            key=f"{key}-stop-growth",
        ))
        st.caption(
            "Level 2 uses Rank Hunter's rigorous built-in 2-descent. Higher levels run only when a compatible plugin exposes a true higher-descent engine; unsupported levels are reported, not simulated."
        )
    elif stage_id == "covering_minimize_reduce":
        c1, c2 = st.columns(2)
        cfg["max_coverings"] = int(c1.number_input(
            "Maximum coverings",
            min_value=1,
            value=int(cfg.get("max_coverings") or 16),
            key=f"{key}-max-coverings",
        ))
        cfg["timeout"] = int(c2.number_input(
            "Hard timeout per covering (s)",
            min_value=1,
            value=int(cfg.get("timeout") or 20),
            key=f"{key}-timeout",
        ))
        cfg["require_improvement"] = bool(st.toggle(
            "Keep only smaller models",
            value=bool(cfg.get("require_improvement", True)),
            key=f"{key}-require-improvement",
            help=(
                "Leave the original covering active when PARI's equivalent "
                "model does not improve the quartic coefficient complexity."
            ),
        ))
        st.caption(
            "Clears denominators, computes a PARI minimal-discriminant model, "
            "applies Cremona–Stoll reduction, and composes the exact inverse transformation into "
            "the covering map. This prepares point searches; it does not "
            "claim local solubility, Selmer membership, or rank growth."
        )
    elif stage_id == "covering_local_height_planner":
        c1, c2 = st.columns(2)
        cfg["max_coverings"] = int(c1.number_input(
            "Maximum coverings",
            min_value=1,
            value=int(cfg.get("max_coverings") or 16),
            key=f"{key}-max-coverings",
        ))
        cfg["timeout"] = int(c2.number_input(
            "Local-analysis timeout per covering (s)",
            min_value=1,
            value=int(cfg.get("timeout") or 30),
            key=f"{key}-timeout",
        ))
        c3, c4 = st.columns(2)
        cfg["base_height"] = int(c3.number_input(
            "Base search height",
            min_value=1,
            value=int(cfg.get("base_height") or 10000),
            key=f"{key}-base-height",
        ))
        cfg["max_height"] = int(c4.number_input(
            "Maximum planned height",
            min_value=1,
            value=int(cfg.get("max_height") or 10000000),
            key=f"{key}-max-height",
        ))
        c5, c6 = st.columns(2)
        cfg["base_timeout"] = int(c5.number_input(
            "Base search timeout (s)",
            min_value=1,
            value=int(cfg.get("base_timeout") or 30),
            key=f"{key}-base-timeout",
        ))
        cfg["max_timeout"] = int(c6.number_input(
            "Maximum planned timeout (s)",
            min_value=1,
            value=int(cfg.get("max_timeout") or 300),
            key=f"{key}-max-timeout",
        ))
        cfg["reject_locally_insoluble"] = bool(st.toggle(
            "Suppress rigorously insoluble coverings",
            value=bool(cfg.get("reject_locally_insoluble", True)),
            key=f"{key}-reject-local",
        ))
        st.caption(
            "Full local conclusions check every completion of Q for supported "
            "square quartics. Local rejection is rigorous; difficulty tiers, "
            "search heights, and timeout recommendations are planning heuristics."
        )
    elif stage_id == "selmer_element_fanout":
        c1, c2, c3 = st.columns(3)
        cfg["max_coverings"] = int(c1.number_input(
            "Max coverings", min_value=1,
            value=int(cfg.get("max_coverings") or 16), key=f"{key}-coverings",
        ))
        cfg["height"] = int(c2.number_input(
            "Quartic height", min_value=1,
            value=int(cfg.get("height") or 100000), key=f"{key}-height",
        ))
        cfg["timeout"] = int(c3.number_input(
            "Seconds / covering", min_value=1,
            value=int(cfg.get("timeout") or 30), key=f"{key}-timeout",
        ))
        c4, c5 = st.columns(2)
        cfg["certificate_timeout"] = int(c4.number_input(
            "Certificate seconds", min_value=1,
            value=int(cfg.get("certificate_timeout") or 120), key=f"{key}-cert",
        ))
        cfg["exact_candidates"] = int(c5.number_input(
            "Exact candidates", min_value=1,
            value=int(cfg.get("exact_candidates") or 96), key=f"{key}-exact",
        ))
        cfg["one_point"] = bool(st.toggle(
            "Stop each covering after first rational point",
            value=bool(cfg.get("one_point", False)),
            key=f"{key}-one-point",
        ))
        cfg["use_covering_plans"] = bool(st.toggle(
            "Use stored covering search plans",
            value=bool(cfg.get("use_covering_plans", True)),
            key=f"{key}-covering-plans",
        ))
        st.caption(
            "Searches every exact stored or plugin-supplied covering as its own branch, maps hits exactly back to E(Q), then runs ordinary independence certification."
        )
    elif stage_id == "padic_covering_search":
        c1, c2, c3 = st.columns(3)
        cfg["prime"] = int(c1.number_input(
            "p-adic prime", min_value=2,
            value=int(cfg.get("prime") or 2), key=f"{key}-prime",
        ))
        cfg["precision"] = int(c2.number_input(
            "p-adic precision", min_value=1,
            value=int(cfg.get("precision") or 40), key=f"{key}-precision",
        ))
        cfg["timeout"] = int(c3.number_input(
            "Seconds", min_value=1,
            value=int(cfg.get("timeout") or 120), key=f"{key}-timeout",
        ))
        c4, c5 = st.columns(2)
        cfg["certificate_timeout"] = int(c4.number_input(
            "Certificate seconds", min_value=1,
            value=int(cfg.get("certificate_timeout") or 120), key=f"{key}-cert",
        ))
        cfg["exact_candidates"] = int(c5.number_input(
            "Exact candidates", min_value=1,
            value=int(cfg.get("exact_candidates") or 96), key=f"{key}-exact",
        ))
        st.caption(
            "This stage requires a genuine p-adic covering search hook. Ordinary ratpoints is never relabeled as p-adic search."
        )
    elif stage_id == "plugin_geometry":
        c1, c2 = st.columns(2)
        cfg["timeout"] = int(c1.number_input(
            "Seconds/fiber", min_value=1, value=int(cfg.get("timeout") or 180),
            key=f"{key}-timeout",
        ))
        cfg["exact_candidates"] = int(c2.number_input(
            "Exact candidates", min_value=1,
            value=int(cfg.get("exact_candidates") or 64), key=f"{key}-cands",
        ))
    elif stage_id == "pointed_quartic":
        cfg["heights"] = _parse_heights(
            st.text_input(
                "Quartic heights",
                ",".join(str(x) for x in cfg.get("heights") or [10000, 100000]),
                key=f"{key}-heights",
            ),
            [10000, 100000],
        )
        c1, c2, c3 = st.columns(3)
        cfg["anchors"] = int(c1.number_input(
            "Anchors", min_value=1, value=int(cfg.get("anchors") or 16),
            key=f"{key}-anchors",
        ))
        cfg["pool_size"] = int(c2.number_input(
            "Quartic pool", min_value=1, value=int(cfg.get("pool_size") or 160),
            key=f"{key}-pool",
        ))
        cfg["timeout"] = int(c3.number_input(
            "Seconds/search", min_value=1, value=int(cfg.get("timeout") or 6),
            key=f"{key}-timeout",
        ))
    elif stage_id == "mw_growth_loop":
        cfg["heights"] = _parse_heights(
            st.text_input(
                "Quartic heights",
                ",".join(
                    str(x)
                    for x in cfg.get("heights")
                    or [10000, 100000, 1000000]
                ),
                key=f"{key}-heights",
            ),
            [10000, 100000, 1000000],
        )
        c1, c2, c3 = st.columns(3)
        cfg["anchors"] = int(c1.number_input(
            "Anchors / round",
            min_value=1,
            value=int(cfg.get("anchors") or 32),
            key=f"{key}-anchors",
        ))
        cfg["pool_size"] = int(c2.number_input(
            "Anchor pool",
            min_value=1,
            value=int(cfg.get("pool_size") or 320),
            key=f"{key}-pool",
        ))
        cfg["max_rounds"] = int(c3.number_input(
            "Max rounds",
            min_value=1,
            value=int(cfg.get("max_rounds") or 8),
            key=f"{key}-rounds",
        ))
        c4, c5, c6 = st.columns(3)
        cfg["deep_keep"] = int(c4.number_input(
            "Deep keep",
            min_value=1,
            value=int(cfg.get("deep_keep") or 12),
            key=f"{key}-deep",
        ))
        cfg["timeout"] = int(c5.number_input(
            "Seconds / search",
            min_value=1,
            value=int(cfg.get("timeout") or 8),
            key=f"{key}-timeout",
        ))
        cfg["reduce_timeout"] = int(c6.number_input(
            "Reduction seconds",
            min_value=0,
            value=int(cfg.get("reduce_timeout") or 20),
            key=f"{key}-reduce",
        ))
        c7, c8 = st.columns(2)
        cfg["certificate_timeout"] = int(c7.number_input(
            "Certificate seconds",
            min_value=1,
            value=int(cfg.get("certificate_timeout") or 120),
            key=f"{key}-cert",
        ))
        cfg["exact_candidates"] = int(c8.number_input(
            "Exact candidates",
            min_value=1,
            value=int(cfg.get("exact_candidates") or 96),
            key=f"{key}-exact",
        ))
        st.caption(
            "Each successful round adds exact points to the ledger, re-certifies rank growth, "
            "then rebuilds the next point-centered quartic portfolio from the enlarged point set. "
            "The loop stops at the first dry round, the configured round cap, or the rigorous rank goal."
        )
    elif stage_id == "specialization_seeds":
        cfg["certificate_timeout"] = int(st.number_input(
            "Certificate seconds",
            min_value=1,
            value=int(cfg.get("certificate_timeout") or 180),
            key=f"{key}-cert",
            help=(
                "Historical coordinates are imported only as candidate evidence. "
                "This is the independent Rank Hunter exact-certificate budget."
            ),
        ))
        st.caption(
            "Fast on ordinary fibers: the stage is a no-op unless the family plugin has a preserved exact specialization bundle matching this curve. Historical rank labels alone never promote rank."
        )
    elif stage_id == "independence":
        c1, c2 = st.columns(2)
        cfg["certificate_timeout"] = int(c1.number_input(
            "Certificate seconds", min_value=1,
            value=int(cfg.get("certificate_timeout") or 120), key=f"{key}-cert",
        ))
        cfg["max_candidates"] = int(c2.number_input(
            "Ledger candidates", min_value=1,
            value=int(cfg.get("max_candidates") or 64), key=f"{key}-max",
        ))
    elif stage_id == "saturation":
        c1, c2 = st.columns(2)
        cfg["max_prime"] = int(c1.number_input(
            "Saturate through prime", min_value=2,
            value=int(cfg.get("max_prime") or 100), key=f"{key}-prime",
        ))
        cfg["timeout"] = int(c2.number_input(
            "Seconds", min_value=1, value=int(cfg.get("timeout") or 300),
            key=f"{key}-timeout",
        ))
    elif stage_id == "full_saturation_index_recovery":
        bounds = _parse_positive_ints(st.text_input(
            "Prime-bound ladder",
            ",".join(str(x) for x in cfg.get("prime_bounds") or [7, 31, 127, 509]),
            key=f"{key}-bounds",
        ))
        if bounds is None:
            st.warning("Use comma-separated positive prime bounds.")
        else:
            cfg["prime_bounds"] = bounds
        c1, c2, c3 = st.columns(3)
        cfg["timeout"] = int(c1.number_input(
            "Seconds / round", min_value=1,
            value=int(cfg.get("timeout") or 180), key=f"{key}-timeout",
        ))
        cfg["certificate_timeout"] = int(c2.number_input(
            "Certificate seconds", min_value=1,
            value=int(cfg.get("certificate_timeout") or 120), key=f"{key}-cert",
        ))
        cfg["exact_candidates"] = int(c3.number_input(
            "Exact candidates", min_value=1,
            value=int(cfg.get("exact_candidates") or 96), key=f"{key}-exact",
        ))
        cfg["stop_on_unit_index"] = bool(st.toggle(
            "Stop after index 1 round",
            value=bool(cfg.get("stop_on_unit_index", False)),
            key=f"{key}-unit",
        ))
        st.caption(
            "Each round is rigorous through its stated prime bound. Rank Hunter does not claim global saturation unless an independent global index bound is available."
        )
    elif stage_id == "height_lattice_reduction":
        c1, c2, c3, c4 = st.columns(4)
        cfg["precision_bits"] = int(c1.number_input(
            "Height precision (bits)", min_value=64,
            value=int(cfg.get("precision_bits") or 256), step=64,
            key=f"{key}-precision",
        ))
        cfg["timeout"] = int(c2.number_input(
            "Lattice seconds", min_value=1,
            value=int(cfg.get("timeout") or 300),
            key=f"{key}-timeout",
        ))
        cfg["certificate_timeout"] = int(c3.number_input(
            "Certificate seconds", min_value=1,
            value=int(cfg.get("certificate_timeout") or 120), key=f"{key}-cert",
        ))
        cfg["exact_candidates"] = int(c4.number_input(
            "Exact candidates", min_value=1,
            value=int(cfg.get("exact_candidates") or 96), key=f"{key}-exact",
        ))
        r1, r2 = st.columns(2)
        retry_choices = ["manual", "automatic", "escalated"]
        retry_value = str(cfg.get("retry_policy") or "manual")
        if retry_value not in retry_choices:
            retry_value = "manual"
        cfg["retry_policy"] = r1.selectbox(
            "Retry policy",
            retry_choices,
            index=retry_choices.index(retry_value),
            key=f"{key}-retry-policy",
        )
        cfg["retry_timeout"] = int(r2.number_input(
            "Retry timeout", min_value=1,
            value=int(cfg.get("retry_timeout") or max(900, cfg["timeout"])),
            key=f"{key}-retry-timeout",
        ))
        st.caption(
            "Height pairing + LLL run in a hard-timeout worker. Retry policy "
            "applies to that lattice worker; unresolved exact recertification "
            "stays explicit and requires an explicit rerun/certificate budget. "
            "Numerical conditioning is never itself rank proof."
        )
    elif stage_id == "select_survivors":
        c1, c2 = st.columns([1, 2])
        cfg["keep"] = int(c1.number_input(
            "Keep fibers",
            min_value=1,
            value=int(cfg.get("keep") or 10),
            key=f"{key}-keep",
        ))
        with c2:
            cfg["ranking"] = _ranking_selector(cfg, key=f"{key}-ranking")
        st.caption(
            "Fibers not selected here become funnel-pruned: their evidence is preserved, "
            "but later pipeline stages do not spend more compute on them."
        )
    elif stage_id == "adaptive_ladder":
        bands_text = ",".join(
            f"{int(pair[0])}-{int(pair[1])}"
            for pair in (cfg.get("bands") or [[2, 50], [51, 500], [501, 5000]])
        )
        parsed_bands = _parse_denominator_bands(
            st.text_input(
                "Denominator bands",
                bands_text,
                key=f"{key}-bands",
                help="Comma-separated inclusive bands, e.g. 2-50,51-500,501-5000.",
            )
        )
        if parsed_bands is None:
            st.warning("Use comma-separated positive low-high bands such as 2-50,51-500.")
        else:
            cfg["bands"] = parsed_bands

        keeps_text = ",".join(
            str(int(x)) for x in (cfg.get("keeps") or [10, 5, 2])
        )
        parsed_keeps = _parse_positive_ints(
            st.text_input(
                "Survivors per band",
                keeps_text,
                key=f"{key}-keeps",
                help="One keep count per denominator band; counts should narrow or stay equal.",
            )
        )
        if parsed_keeps is None:
            st.warning("Use positive comma-separated survivor counts such as 10,5,2.")
        else:
            cfg["keeps"] = parsed_keeps

        cfg["ranking"] = _ranking_selector(cfg, key=f"{key}-ranking")
        c1, c2 = st.columns(2)
        cfg["charts"] = int(c1.number_input(
            "Charts/fiber",
            min_value=1,
            value=int(cfg.get("charts") or 5),
            key=f"{key}-charts",
        ))
        cfg["timeout"] = int(c2.number_input(
            "Seconds/search",
            min_value=1,
            value=int(cfg.get("timeout") or 8),
            key=f"{key}-timeout",
        ))
        cfg["heights"] = _parse_heights(
            st.text_input(
                "Height stages",
                ",".join(str(x) for x in cfg.get("heights") or [10000, 100000]),
                key=f"{key}-heights",
            ),
            cfg.get("heights") or [10000, 100000],
        )
        c3, c4 = st.columns(2)
        cfg["certificate_timeout"] = int(c3.number_input(
            "Certificate seconds",
            min_value=1,
            value=int(cfg.get("certificate_timeout") or 120),
            key=f"{key}-cert",
        ))
        cfg["exact_candidates"] = int(c4.number_input(
            "Exact candidates",
            min_value=1,
            value=int(cfg.get("exact_candidates") or 64),
            key=f"{key}-exact",
        ))
        st.caption(
            "Each round re-ranks the survivors using fresh rigorous rank / point-yield data, "
            "narrows the population, searches the next denominator band, and exact-certifies growth."
        )
    elif stage_id == "final_upper":
        cfg["timeout"] = int(st.number_input(
            "PARI seconds", min_value=1, value=int(cfg.get("timeout") or 30),
            key=f"{key}-timeout",
        ))
    else:
        st.caption("No stage-specific settings.")

    if stage_id in {
        "small_point_density",
        "integral_seed",
        "denominator_band",
        "affine_search",
        "adaptive_ladder",
    }:
        _render_point_search_budget(stage_id, cfg)
    if stage_id in {
        "pointed_quartic",
        "mw_growth_loop",
        "selmer_element_fanout",
    }:
        _render_geometry_search_budget(stage_id, cfg)

    rec["config"] = cfg


def _pipeline_editor_style():
    st.html(
        """
        <style>
        div[class*="st-key-byo-search-space"] {
            margin-top: 0.8rem;
            margin-bottom: 1.15rem;
            padding: 0.5rem 0.7rem 0.6rem;
            border-radius: 0.7rem;
            background: var(--rh-card,#FFFFFF);
            border: 1px solid var(--rh-border,rgba(127,127,127,0.18));
            box-shadow: inset 0 1px 0 color-mix(in srgb,var(--rh-text,#31333f) 4%,transparent);
        }
        div[class*="st-key-byo-pipeline-settings"] {
            margin-top: 1rem;
            padding: 0.7rem 0.8rem 0.8rem;
        }
        div[class*="st-key-byo-functional-features"] [data-baseweb="select"] svg {
            width: 1rem !important;
            height: 1rem !important;
            color: var(--rh-text-secondary,#5c6370) !important;
            fill: currentColor !important;
        }
        div[class*="st-key-byo-functional-features"] [data-baseweb="select"] svg path {
            fill: currentColor !important;
        }
        </style>
        """
    )


def _component_theme_palette():
    palette = st.session_state.get("_rh_theme_palette") or {}
    return dict(palette) if isinstance(palette, dict) else {}


def _module_action_style():
    st.html(
        """
        <style>
        div[class*="st-key-byo-modules-inset"] {
            padding: 0.8rem 0.85rem 0.9rem;
            border: 1px solid var(--rh-border,rgba(127,127,127,0.24));
            border-radius: 0.75rem;
            background: var(--rh-card,#FFFFFF);
            box-shadow: inset 0 1px 0 color-mix(in srgb,var(--rh-text,#31333f) 5%,transparent);
        }
        div[class*="st-key-byo-modules-preset"] {
            margin-bottom: 0.55rem;
        }
        div[class*="st-key-byo-module-scroll-"] {
            scrollbar-gutter: stable;
            background: var(--rh-card,#FFFFFF);
            border-color: var(--rh-border,rgba(127,127,127,0.24)) !important;
        }
        div[class*="st-key-byo-module-scroll-"] details {
            margin-bottom: 0.2rem;
        }
        div[class*="st-key-byo-module-scroll-"] details [data-testid="stCustomComponentV1"],
        div[class*="st-key-byo-module-scroll-"] details [data-testid="stCustomComponentV1"] iframe {
            height: 54px !important;
            min-height: 54px !important;
            max-height: 54px !important;
        }
        </style>
        """
    )


def _module_search_blob(spec):
    return " ".join(
        [
            str(spec.label),
            str(spec.category),
            str(spec.evidence),
            str(spec.cost),
            str(spec.description),
            str(spec.conditional or ""),
            " ".join(sorted(spec.requires)),
            " ".join(sorted(spec.provides)),
        ]
    ).lower()


def _module_append_issues(mode, stages, spec):
    before = validate_pipeline(mode, stages)
    trial = [
        *stages,
        {"id": spec.id, "config": dict(spec.defaults)},
    ]
    after = validate_pipeline(mode, trial)
    baseline = set(before)
    return [error for error in after if error not in baseline]


def _module_palette_specs(
    mode,
    stages,
    *,
    query="",
    view="Suggested",
    category="All categories",
    cost="Any cost",
    favorites=None,
    recent=None,
):
    specs = list(pipeline_stage_specs(mode))
    query = str(query or "").strip().lower()
    favorites = {str(value) for value in (favorites or [])}
    recent = [str(value) for value in (recent or [])]
    recent_set = set(recent)
    counts = {}
    for rec in stages:
        sid = str(rec["id"])
        counts[sid] = counts.get(sid, 0) + 1

    out = []
    for spec in specs:
        count = counts.get(spec.id, 0)
        if query and query not in _module_search_blob(spec):
            continue
        if category != "All categories" and spec.category != category:
            continue
        if cost != "Any cost" and spec.cost != str(cost).lower():
            continue

        if view == "Added":
            if count <= 0:
                continue
        elif view == "Favorites":
            if spec.id not in favorites:
                continue
        elif view == "Recent":
            if spec.id not in recent_set:
                continue
        elif view == "Suggested":
            if count > 0 and not spec.repeatable:
                continue
            if _module_append_issues(mode, stages, spec):
                continue

        out.append(spec)

    if view == "Recent":
        recent_order = {stage_id: index for index, stage_id in enumerate(recent)}
        out.sort(key=lambda spec: recent_order.get(spec.id, len(recent_order)))
    return out


def _module_palette_groups(specs):
    grouped = []
    by_category = {}
    for spec in specs:
        category = str(spec.category)
        if category not in by_category:
            bucket = []
            by_category[category] = bucket
            grouped.append((category, bucket))
        by_category[category].append(spec)
    return grouped


def _module_favorites(mode):
    key = f"byo-module-favorites-{mode}"
    values = {
        str(value)
        for value in (st.session_state.get(key) or [])
        if str(value).strip()
    }
    st.session_state[key] = sorted(values)
    return values


def _toggle_module_favorite(mode, stage_id):
    key = f"byo-module-favorites-{mode}"
    values = {
        str(value)
        for value in (st.session_state.get(key) or [])
        if str(value).strip()
    }
    stage_id = str(stage_id)
    if stage_id in values:
        values.remove(stage_id)
    else:
        values.add(stage_id)
    st.session_state[key] = sorted(values)


def _recent_modules(mode):
    key = f"byo-module-recent-{mode}"
    values = []
    seen = set()
    for value in st.session_state.get(key) or []:
        stage_id = str(value)
        if not stage_id or stage_id in seen:
            continue
        seen.add(stage_id)
        values.append(stage_id)
    st.session_state[key] = values[:12]
    return values[:12]


def _remember_module_use(mode, stage_id):
    key = f"byo-module-recent-{mode}"
    stage_id = str(stage_id)
    values = [
        str(value)
        for value in (st.session_state.get(key) or [])
        if str(value) != stage_id
    ]
    st.session_state[key] = [stage_id, *values][:12]


def _render_module_details(mode, stages, spec, count):
    st.write(spec.description)
    meta = [
        f"**Evidence:** {spec.evidence}",
        f"**Cost:** {spec.cost}",
        f"**Requires:** {', '.join(sorted(spec.requires)) or 'nothing'}",
        f"**Provides:** {', '.join(sorted(spec.provides)) or 'nothing'}",
    ]
    st.markdown("  \n".join(meta))
    if spec.conditional:
        st.info(spec.conditional)
    issues = _module_append_issues(mode, stages, spec)
    if issues:
        st.warning(
            "If appended at the end right now:\n\n"
            + "\n".join(f"• {error}" for error in issues[:4])
        )
    elif count > 0 and spec.repeatable:
        st.success("Ready to append another copy.")
    elif count == 0:
        st.success("Ready to append at the end of the current pipeline.")
    if spec.defaults:
        with st.expander("Default settings", expanded=False):
            st.json(spec.defaults)


def _render_module_details_dialog(mode, stages, spec, count):
    @st.dialog(
        f"{spec.label} details",
        width="large",
        on_dismiss="ignore",
    )
    def _dialog():
        _render_module_details(mode, stages, spec, count)
        if st.button(
            "Close",
            type="primary",
            width="stretch",
            key=f"byo-module-details-close-{mode}-{spec.id}",
        ):
            st.rerun()

    _dialog()


def _render_module_row(mode, stages, spec, count, favorites):
    is_favorite = spec.id in favorites
    can_add = count == 0 or spec.repeatable
    payload = _PIPELINE_MODULE_ROW(
        module=spec.label,
        palette=_component_theme_palette(),
        detail=(
            f"{spec.category} · {spec.evidence.upper()} · "
            f"{spec.cost}"
            + (f" · in pipeline ×{count}" if count > 0 else "")
        ),
        favorite=bool(is_favorite),
        can_add=bool(can_add),
        repeat=bool(count > 0),
        key=f"byo-module-row-{mode}-{spec.id}",
        default=None,
    )

    if not isinstance(payload, dict):
        return

    nonce = payload.get("nonce")
    handled_key = f"byo_module_row_handled_nonce_{mode}_{spec.id}"
    if nonce is None or str(st.session_state.get(handled_key)) == str(nonce):
        return
    st.session_state[handled_key] = str(nonce)

    action = str(payload.get("action") or "")
    if action == "favorite":
        _toggle_module_favorite(mode, spec.id)
        st.rerun()
    elif action == "details":
        _render_module_details_dialog(mode, stages, spec, count)
    elif action == "add" and can_add:
        stages.append({
            "id": spec.id,
            "config": dict(spec.defaults),
        })
        st.session_state["byo_stages"] = stages
        _remember_module_use(mode, spec.id)
        _bump_stage_revision()
        st.rerun()

def _render_stage_palette(mode, stages):
    _module_action_style()
    section_title(
        "Modules",
        "Search and add stages.",
    )

    with st.container(border=True, key="byo-modules-preset"):
        preset_col, load_col = st.columns([2.25, 1.1], vertical_alignment="bottom")
        preset_options = pipeline_preset_options(mode)
        preset_labels = [rec["label"] for rec in preset_options]
        template = preset_col.selectbox(
            "Preset",
            preset_labels,
            key="byo-template-select",
            label_visibility="collapsed",
        )
        selected_preset = next(
            rec for rec in preset_options if rec["label"] == template
        )
        if load_col.button(
            "Load Preset",
            width="stretch",
            key="byo-load-template",
        ):
            _reset_pipeline(mode, selected_preset["id"])
            st.rerun()
        st.caption(selected_preset["description"])

    specs = list(pipeline_stage_specs(mode))
    categories = []
    for spec in specs:
        if spec.category not in categories:
            categories.append(spec.category)

    query = st.text_input(
        "Find modules",
        value="",
        placeholder="Search name, purpose, requirement, capability…",
        key=f"byo-module-search-{mode}",
        label_visibility="collapsed",
    )

    favorites = _module_favorites(mode)
    recent = _recent_modules(mode)
    view_options = ["Suggested", "All", "Favorites", "Recent", "Added"]
    view = st.segmented_control(
        "Module view",
        view_options,
        default="Suggested",
        selection_mode="single",
        key=f"byo-module-view-v3-{mode}",
        label_visibility="collapsed",
        help=(
            "Suggested shows modules that can be appended now. Favorites and Recent "
            "keep frequently used modules one click away."
        ),
    )
    if view is None:
        view = "Suggested"

    filter_a, filter_b = st.columns(2)
    category = filter_a.selectbox(
        "Category",
        ["All categories", *categories],
        key=f"byo-module-category-{mode}",
    )
    cost = filter_b.selectbox(
        "Cost",
        ["Any cost", "Cheap", "Medium", "Expensive"],
        key=f"byo-module-cost-{mode}",
    )

    visible = _module_palette_specs(
        mode,
        stages,
        query=query,
        view=str(view),
        category=category,
        cost=cost,
        favorites=favorites,
        recent=recent,
    )
    st.caption(
        f"{len(visible)} module{'s' if len(visible) != 1 else ''} shown · "
        f"{len(stages)} stage{'s' if len(stages) != 1 else ''} in pipeline · "
        f"{len(favorites)} favorite{'s' if len(favorites) != 1 else ''}"
    )

    if not visible:
        message = {
            "Favorites": "No favorite modules match these filters. Star modules from All or Suggested.",
            "Recent": "No recently added modules match these filters yet.",
        }.get(str(view), "No modules match these filters.")
        st.info(message)
        return

    stage_counts = {}
    for rec in stages:
        sid = str(rec["id"])
        stage_counts[sid] = stage_counts.get(sid, 0) + 1

    groups = _module_palette_groups(visible)
    with st.container(
        height=560,
        border=True,
        key=f"byo-module-scroll-{mode}",
    ):
        for group_index, (group_name, group_specs) in enumerate(groups):
            expanded = (
                len(groups) == 1
                or str(view) in {"Favorites", "Recent", "Added"}
                or group_index < 2
            )
            with st.expander(
                f"{group_name} · {len(group_specs)}",
                expanded=expanded,
            ):
                for spec in group_specs:
                    _render_module_row(
                        mode,
                        stages,
                        spec,
                        stage_counts.get(spec.id, 0),
                        favorites,
                    )


def _pipeline_builder_lineage(
    db,
    *,
    pipeline_id=None,
    derived_from_pipeline_id=None,
    pipeline_name,
    target_mode,
    stages,
    definition_config,
):
    """Classify the editor snapshot against its saved Pipeline ancestry."""
    source_id = (
        int(pipeline_id)
        if pipeline_id is not None
        else (
            int(derived_from_pipeline_id)
            if derived_from_pipeline_id is not None
            else None
        )
    )
    if source_id is None:
        return {
            "pipeline_id": None,
            "dirty": False,
            "source_pipeline_id": None,
            "source_pipeline_revision": None,
            "source_pipeline_hash": None,
            "provenance": {},
        }

    source = get_pipeline(db, source_id)
    if source is None:
        return {
            "pipeline_id": None,
            "dirty": True,
            "source_pipeline_id": source_id,
            "source_pipeline_revision": None,
            "source_pipeline_hash": None,
            "provenance": {
                "derived_from_pipeline_id": source_id,
                "pipeline_source_relation": "modified",
            },
        }

    payload = pipeline_payload(source)
    current_hash = pipeline_definition_hash(
        target_mode=target_mode,
        stages=stages,
        config=definition_config,
    )
    exact = (
        pipeline_id is not None
        and str(pipeline_name) == str(payload["name"])
        and current_hash == str(payload["content_hash"])
    )
    if exact:
        provenance = {
            "source_pipeline_id": int(payload["id"]),
            "source_pipeline_revision": int(payload["revision"]),
            "source_pipeline_hash": str(payload["content_hash"]),
            "pipeline_source_relation": "exact",
        }
        return {
            "pipeline_id": int(payload["id"]),
            "dirty": False,
            "source_pipeline_id": int(payload["id"]),
            "source_pipeline_revision": int(payload["revision"]),
            "source_pipeline_hash": str(payload["content_hash"]),
            "provenance": provenance,
        }

    provenance = {
        "derived_from_pipeline_id": int(payload["id"]),
        "derived_from_pipeline_revision": int(payload["revision"]),
        "derived_from_pipeline_hash": str(payload["content_hash"]),
        "pipeline_source_relation": "modified",
    }
    return {
        "pipeline_id": None,
        "dirty": True,
        "source_pipeline_id": int(payload["id"]),
        "source_pipeline_revision": int(payload["revision"]),
        "source_pipeline_hash": str(payload["content_hash"]),
        "provenance": provenance,
    }


def _launch_pipeline_builder_search(
    db,
    ctx,
    *,
    pipeline_id,
    pipeline_name,
    mode,
    target,
    stages,
    run_config,
    definition_config=None,
    derived_from_pipeline_id=None,
    selected_plugin=None,
    selected_variant=None,
):
    """Create one Pipeline Run and Job from a single frozen Launch Context."""
    target_payload = dict(target or {})
    lineage = (
        _pipeline_builder_lineage(
            db,
            pipeline_id=pipeline_id,
            derived_from_pipeline_id=derived_from_pipeline_id,
            pipeline_name=pipeline_name,
            target_mode=mode,
            stages=stages,
            definition_config=dict(definition_config or {}),
        )
        if definition_config is not None or derived_from_pipeline_id is not None
        else {
            "pipeline_id": pipeline_id,
            "dirty": False,
            "source_pipeline_id": pipeline_id,
            "source_pipeline_revision": None,
            "source_pipeline_hash": None,
            "provenance": {},
        }
    )
    pipeline_id = lineage["pipeline_id"]
    run_config = dict(run_config or {})
    run_config.update(lineage["provenance"])
    plugin_id = (
        getattr(selected_plugin, "id", None)
        if selected_plugin is not None
        else target_payload.get("plugin_id")
    )
    plugin_variant = (
        getattr(selected_variant, "id", None)
        if selected_variant is not None
        else (
            target_payload.get("variant_id")
            or target_payload.get("plugin_variant")
        )
    )
    source_pool_id = (
        target_payload.get("candidate_pool_id")
        or target_payload.get("pool_id")
    )
    run_context = resolve_launch_context(
        db,
        metadata=run_config,
        launch_surface="pipeline_builder",
        plugin_id=plugin_id,
        plugin_variant=plugin_variant,
        target_mode=mode,
        pipeline_id=pipeline_id,
        pipeline_name=pipeline_name,
        feature_plugin_ids=list(
            (run_config or {}).get("feature_plugin_ids") or []
        ),
        source_pool_id=source_pool_id,
    )
    frozen_run_config = dict(run_context.metadata)

    run_id = create_pipeline_run(
        db,
        pipeline_id=pipeline_id,
        pipeline_name=pipeline_name,
        target_mode=mode,
        target=target_payload,
        stages=stages,
        run_config=frozen_run_config,
        project_root=ctx.project_root,
    )
    py = setting(db, "science_python", ctx.detected_science_python())
    command = [
        str(py), "-m", "rank42.pipeline_runner",
        "--project-root", str(ctx.project_root),
        "--db", str(ctx.db_path),
        "--run-id", str(run_id),
    ]

    metadata = {
        "ratpoints_backend": str(
            frozen_run_config.get("ratpoints_backend") or ""
        ),
    }
    metadata.update(launch_context_snapshot(frozen_run_config))
    metadata.update(lineage["provenance"])
    metadata = normalize_launch_metadata(
        metadata,
        launch_surface="pipeline_builder",
        plugin_id=plugin_id,
        plugin_variant=plugin_variant,
        target_mode=mode,
        pipeline_run_id=run_id,
        pipeline_id=pipeline_id,
        pipeline_name=pipeline_name,
        feature_plugin_ids=frozen_run_config.get("feature_plugin_ids") or [],
        source_pool_id=source_pool_id,
    )
    jid = launch_resolved(
        ctx,
        db,
        kind="pipeline_search",
        label=f"Pipeline · {pipeline_name}",
        command=command,
        metadata=metadata,
        campaign_failure=run_context.failure,
    )
    return int(run_id), int(jid)


def _render_settings(
    db,
    ctx,
    *,
    mode,
    mode_label,
    stages,
    errors,
    suggested_goal,
    target,
    selected_plugin,
    selected_variant,
    launch_blocked=False,
):
    section_title(
        "Settings",
        "Save and run configuration for the current pipeline.",
    )
    default_name = (
        st.session_state.get("byo_pipeline_name_input")
        or f"My {mode_label} Pipeline"
    )
    if "byo_pipeline_name_input" not in st.session_state:
        st.session_state["byo_pipeline_name_input"] = default_name

    name_col, goal_col, retain_col = st.columns(
        [3.4, 1.0, 1.0],
        vertical_alignment="bottom",
    )
    pipeline_name = name_col.text_input(
        "Pipeline name",
        key="byo_pipeline_name_input",
    )

    goal_key = f"byo-goal-{mode}"
    pending_goal = st.session_state.pop("byo_pending_target_rank", None)
    if goal_key in st.session_state:
        target_rank = int(goal_col.number_input(
            "Rank goal",
            min_value=1,
            step=1,
            key=goal_key,
            help="Rigorous lower-bound goal used by goal-aware stages.",
        ))
    else:
        target_rank = int(goal_col.number_input(
            "Rank goal",
            min_value=1,
            value=max(1, int(pending_goal or suggested_goal)),
            step=1,
            key=goal_key,
            help="Rigorous lower-bound goal used by goal-aware stages.",
        ))

    retention_key = f"byo-retention-{mode}"
    pending_retention = st.session_state.pop(
        "byo_pending_retention_floor", None
    )
    retention_help = (
        "Fresh curves created by this pipeline run are kept in Curves only "
        "when their rigorous lower bound reaches this value. 0 keeps the "
        "normal retention behavior. Preexisting curves are never removed."
    )
    if retention_key in st.session_state:
        retention_floor = int(retain_col.number_input(
            "Keep rank ≥",
            min_value=0,
            step=1,
            key=retention_key,
            help=retention_help,
        ))
    else:
        retention_floor = int(retain_col.number_input(
            "Keep rank ≥",
            min_value=0,
            value=max(0, int(pending_retention or 0)),
            step=1,
            key=retention_key,
            help=retention_help,
        ))

    cert_key = "byo-cert-timeout"
    exact_key = "byo-exact-candidates"
    pending_cert = st.session_state.pop(
        "byo_pending_certificate_timeout", None
    )
    pending_exact = st.session_state.pop(
        "byo_pending_exact_candidates", None
    )

    budget_col, exact_col, engine_col, features_col = st.columns(
        [1.0, 1.0, 1.45, 2.95],
        vertical_alignment="bottom",
    )
    if cert_key in st.session_state:
        certificate_timeout = int(budget_col.number_input(
            "Certificate seconds",
            min_value=1,
            step=10,
            key=cert_key,
        ))
    else:
        certificate_timeout = int(budget_col.number_input(
            "Certificate seconds",
            min_value=1,
            value=max(1, int(pending_cert or 120)),
            step=10,
            key=cert_key,
        ))

    if exact_key in st.session_state:
        exact_candidates = int(exact_col.number_input(
            "Exact candidates",
            min_value=1,
            step=1,
            key=exact_key,
        ))
    else:
        exact_candidates = int(exact_col.number_input(
            "Exact candidates",
            min_value=1,
            value=max(1, int(pending_exact or 64)),
            step=1,
            key=exact_key,
        ))

    pending_backend = st.session_state.pop(
        "byo_pending_ratpoints_backend", None
    )
    with engine_col:
        backend, ratpoints = ratpoints_selector(
            db,
            key="byo-ratpoints",
            label="Point engine",
            default_backend=pending_backend,
        )

    features = _functional_features(db, ctx)
    feature_by_id = {rec["id"]: rec for rec in features}
    feature_key = "byo-functional-features"
    if feature_key not in st.session_state:
        st.session_state[feature_key] = []
    else:
        st.session_state[feature_key] = [
            pid for pid in st.session_state[feature_key]
            if pid in feature_by_id
        ]
    with features_col:
        feature_ids = st.multiselect(
            "Features",
            list(feature_by_id),
            format_func=lambda pid: (
                f"{feature_by_id[pid]['name']} {feature_by_id[pid]['version']}"
            ),
            key=feature_key,
            help=(
                "Cross-cutting compatible command transforms saved with "
                "the recipe/run."
            ),
        )

    recipe_config = {
        "feature_plugin_ids": list(feature_ids),
        "target_rank": int(target_rank),
        "retention_floor": int(retention_floor),
        "certificate_timeout": int(certificate_timeout),
        "exact_candidates": int(exact_candidates),
        "ratpoints_backend": str(backend),
    }
    config_shape = st.session_state.setdefault(
        "byo-definition-config-shape",
        "launch_defaults",
    )
    definition_config = dict(recipe_config)
    if config_shape == "legacy_target":
        definition_config["target"] = dict(target)
    else:
        definition_config["launch_defaults"] = {
            "target": dict(target),
        }
    lineage = _pipeline_builder_lineage(
        db,
        pipeline_id=st.session_state.get("byo_loaded_pipeline_id"),
        derived_from_pipeline_id=st.session_state.get(
            "byo_duplicate_source_pipeline_id"
        ),
        pipeline_name=pipeline_name,
        target_mode=mode,
        stages=normalize_pipeline(stages),
        definition_config=definition_config,
    )
    _persist_pipeline_draft(
        db,
        mode=mode,
        target=target,
        stages=stages,
        definition_config=definition_config,
        lineage=lineage,
    )
    if lineage["dirty"] and lineage["source_pipeline_id"] is not None:
        revision = lineage.get("source_pipeline_revision")
        revision_text = f" revision {revision}" if revision is not None else ""
        st.caption(
            f"Unsaved changes: Search will run the current snapshot as modified "
            f"from Pipeline #{lineage['source_pipeline_id']}{revision_text}."
        )

    save_col, run_col = st.columns([1, 1])
    if save_col.button(
        "Save Pipeline",
        width="stretch",
        disabled=bool(errors),
        key="byo-save",
    ):
        pid = save_pipeline(
            db,
            pipeline_id=st.session_state.get("byo_loaded_pipeline_id"),
            name=pipeline_name,
            target_mode=mode,
            stages=normalize_pipeline(stages),
            config=definition_config,
        )
        st.session_state["byo_loaded_pipeline_id"] = pid
        st.session_state.pop("byo_duplicate_source_pipeline_id", None)
        _discard_pipeline_draft(db)
        st.toast(f"Saved pipeline #{pid}.")
        st.rerun()

    if run_col.button(
        "Search",
        type="primary",
        width="stretch",
        disabled=bool(errors) or bool(launch_blocked) or not bool(ratpoints),
        key="byo-run",
    ):
        normalized = normalize_pipeline(stages)
        run_config = {
            "target_rank": target_rank,
            "retention_floor": int(retention_floor),
            "certificate_timeout": certificate_timeout,
            "exact_candidates": exact_candidates,
            "ratpoints_backend": backend,
            "ratpoints": ratpoints,
            "feature_plugin_ids": list(feature_ids),
        }
        run_id, jid = _launch_pipeline_builder_search(
            db,
            ctx,
            pipeline_id=st.session_state.get("byo_loaded_pipeline_id"),
            pipeline_name=pipeline_name,
            mode=mode,
            target=target,
            stages=normalized,
            run_config=run_config,
            definition_config=definition_config,
            derived_from_pipeline_id=st.session_state.get(
                "byo_duplicate_source_pipeline_id"
            ),
            selected_plugin=selected_plugin,
            selected_variant=selected_variant,
        )
        st.success(f"Started pipeline run #{run_id} as job #{jid}.")
        st.rerun()

def _render_pipeline_editor_actions(
    mode,
    *,
    show_duplicate,
    show_new,
    identity,
):
    payload = _PIPELINE_EDITOR_ACTIONS(
        show_duplicate=bool(show_duplicate),
        palette=_component_theme_palette(),
        show_new=bool(show_new),
        key=f"byo-pipeline-editor-actions-{identity}",
        default=None,
    )
    if not isinstance(payload, dict):
        return

    nonce = payload.get("nonce")
    handled_key = f"byo_pipeline_editor_action_nonce_{identity}"
    if nonce is None or str(st.session_state.get(handled_key)) == str(nonce):
        return
    st.session_state[handled_key] = str(nonce)

    action = str(payload.get("action") or "")
    if action == "duplicate" and show_duplicate:
        source_id = _duplicate_loaded_pipeline()
        if source_id is not None:
            st.toast(
                f"Duplicated pipeline #{int(source_id)}. "
                "Save Pipeline will create a new definition.",
                icon="📋",
            )
        st.rerun()
    elif action == "new" and show_new:
        _clear_loaded_pipeline(mode)
        st.session_state["byo_discard_pipeline_draft_pending"] = True
        st.rerun()


def _render_pipeline_column(*, mode, stages):
    section_title(
        "Your pipeline",
        "Execution runs top to bottom. Reorder the active recipe here; "
        "module settings stay attached to each stage.",
    )

    loaded_pipeline_id = st.session_state.get("byo_loaded_pipeline_id")
    duplicate_source_id = st.session_state.get(
        "byo_duplicate_source_pipeline_id"
    )
    if loaded_pipeline_id is not None:
        edit_info, actions_col = st.columns(
            [5.1, 1.0],
            vertical_alignment="center",
        )
        edit_info.info(
            f"Editing saved pipeline #{int(loaded_pipeline_id)}"
        )
        with actions_col:
            _render_pipeline_editor_actions(
                mode,
                show_duplicate=True,
                show_new=True,
                identity=f"loaded-{int(loaded_pipeline_id)}",
            )
    elif duplicate_source_id is not None:
        edit_info, actions_col = st.columns(
            [5.1, 1.0],
            vertical_alignment="center",
        )
        edit_info.info(
            f"Unsaved duplicate of pipeline #{int(duplicate_source_id)}. "
            "Save Pipeline creates a new saved definition; the source stays unchanged."
        )
        with actions_col:
            _render_pipeline_editor_actions(
                mode,
                show_duplicate=False,
                show_new=True,
                identity=f"duplicate-{int(duplicate_source_id)}",
            )

    errors = validate_pipeline(mode, stages)
    if not stages:
        st.info("Your pipeline is blank. Add modules from the right or load a preset.")
    elif errors:
        st.error("Pipeline needs attention:")
        for error in errors:
            st.caption(f"• {error}")
    else:
        st.success("Pipeline contract is valid.")

    if stages:
        _render_stage_list(stages)
        st.caption(
            "Drag by the grip handle. Use the gear to configure or trash to remove."
        )

    st.session_state["byo_stages"] = stages
    return errors

def _render_builder(
    db,
    ctx,
    *,
    mode,
    mode_label,
    suggested_goal,
    target,
    selected_plugin,
    selected_variant,
    launch_blocked=False,
):
    stages = _ensure_pipeline_state(mode)
    pipeline_col, modules_col = st.columns(2, gap="large")
    with pipeline_col:
        errors = _render_pipeline_column(
            mode=mode,
            stages=stages,
        )
    with modules_col:
        with st.container(border=True, key="byo-modules-inset"):
            _render_stage_palette(mode, stages)

    with st.container(border=True, key="byo-pipeline-settings"):
        _render_settings(
            db,
            ctx,
            mode=mode,
            mode_label=mode_label,
            stages=stages,
            errors=errors,
            suggested_goal=suggested_goal,
            target=target,
            selected_plugin=selected_plugin,
            selected_variant=selected_variant,
            launch_blocked=launch_blocked,
        )
    return stages, errors

def _open_pipeline_run_in_jobs(run_id):
    st.session_state["manage_pipeline_run_id"] = int(run_id)
    st.session_state["rh_page"] = "Jobs"


def _render_running(db):
    active = _active_pipeline_jobs(db)
    if not active:
        return
    with st.container(border=True):
        section_title(
            "Running pipelines",
            "Scientific progress is summarized here. Process controls and logs live in Jobs.",
        )
        for job in active:
            meta = _json(job["metadata_json"], {})
            run_id = meta.get("pipeline_run_id")
            run = get_pipeline_run(db, int(run_id)) if run_id is not None else None
            st.write(f"**{job['label']}**")
            if run is not None:
                st.caption(
                    f"Run #{run_id} · {run['candidates_done']}/{run['candidates_total']} candidates · "
                    f"best rigorous rank ≥{int(run['best_lower'] or 0)} · "
                    f"stage {run['current_stage_id'] or '—'}"
                )
                if st.button(
                    "Open process in Jobs",
                    width="stretch",
                    icon=":material/work_history:",
                    key=f"byo-open-job-{job['id']}",
                ):
                    _open_pipeline_run_in_jobs(int(run_id))
                    st.rerun()
            else:
                st.caption(
                    f"Job #{int(job['id'])} has no durable Pipeline Run link. "
                    "Open Jobs for process diagnostics."
                )
                if st.button(
                    "Open job in Jobs",
                    width="stretch",
                    icon=":material/work_history:",
                    key=f"byo-open-unlinked-job-{job['id']}",
                ):
                    st.session_state["manage_job_id"] = int(job["id"])
                    st.session_state["jobs_section_pending"] = "Active"
                    st.session_state["rh_page"] = "Jobs"
                    st.rerun()


def _render_pipeline_runs_page(db, ctx, campaign):
    _render_running(db)

    if campaign is not None:
        show_all_runs = st.toggle(
            "Show all pipeline runs",
            value=False,
            key="byo-show-all-runs",
            help=(
                "Off shows only runs durably attached to the active campaign. "
                "Turn on to inspect global Pipeline history."
            ),
        )
        runs = (
            list_pipeline_runs(db, limit=25)
            if show_all_runs
            else campaign_pipeline_runs(db, int(campaign["id"]))[:25]
        )
    else:
        st.info("No active campaign · showing all Pipeline runs.")
        runs = list_pipeline_runs(db, limit=25)

    focus_run_id = st.session_state.pop("byo_focus_run_id", None)
    if focus_run_id is not None:
        try:
            focus_run_id = int(focus_run_id)
        except (TypeError, ValueError):
            focus_run_id = None
    if focus_run_id is not None:
        if all(int(row["id"]) != focus_run_id for row in runs):
            focused_run = get_pipeline_run(db, focus_run_id)
            if focused_run is not None:
                runs = [focused_run, *runs]
        if any(int(row["id"]) == focus_run_id for row in runs):
            st.session_state["byo-inspect-run"] = focus_run_id

    if not runs:
        if campaign is not None:
            st.caption(
                "No pipeline runs are attached to this campaign yet. "
                "Turn on “Show all pipeline runs” to inspect global history."
            )
        else:
            st.info("No pipeline runs yet.")
        return

    valid_run_ids = {int(row["id"]) for row in runs}
    selected_state = st.session_state.get("byo-inspect-run")
    if selected_state is not None:
        try:
            selected_state = int(selected_state)
        except (TypeError, ValueError):
            selected_state = None
        if selected_state not in valid_run_ids:
            st.session_state.pop("byo-inspect-run", None)

    with st.container(border=True, key="byo-pipeline-runs"):
        section_title(
            "Pipeline runs",
            "Scientific history, candidates, transform lineage, provenance, and resumability.",
        )
        st.dataframe(
            [
                {
                    "run": int(row["id"]),
                    "pipeline": row["pipeline_name"],
                    "source": TARGET_LABELS.get(
                        str(row["target_mode"]), row["target_mode"]
                    ),
                    "status": row["status"],
                    "done": (
                        f"{int(row['candidates_done'])}/"
                        f"{int(row['candidates_total'])}"
                    ),
                    "best ≥": int(row["best_lower"] or 0),
                    "best curve": row["best_curve_id"],
                    "stage": row["current_stage_id"],
                }
                for row in runs
            ],
            hide_index=True,
            width="stretch",
        )
        by_id = {int(row["id"]): row for row in runs}
        inspect_id = st.selectbox(
            "Inspect run",
            list(by_id),
            format_func=lambda rid: (
                f"#{rid} · {by_id[rid]['pipeline_name']} · "
                f"{by_id[rid]['status']}"
            ),
            key="byo-inspect-run",
        )
        selected_run = by_id[int(inspect_id)]
        runtime_drift = pipeline_run_runtime_drift(
            selected_run,
            project_root=ctx.project_root,
        )
        if runtime_drift["blocking"]:
            st.error(
                "Runtime incompatibility: "
                + "; ".join(runtime_drift["blocking"])
            )
        elif runtime_drift["warnings"]:
            st.warning(
                "Replay/runtime drift: "
                + "; ".join(runtime_drift["warnings"])
            )
        candidates = pipeline_candidates(db, int(inspect_id), limit=250)
        if candidates:
            st.dataframe(
                [
                    {
                        "provider": row["provider_key"] or "—",
                        "parameter": row["parameter"],
                        "status": row["status"],
                        "stage": row["current_stage_id"],
                        "score": row["score"],
                        "rank ≥": int(row["rigorous_lower"] or 0),
                        "upper": row["rigorous_upper"],
                        "exact": row["exact_rank"],
                        "curve": row["curve_id"],
                    }
                    for row in candidates
                ],
                hide_index=True,
                width="stretch",
            )

        derivations = pipeline_derivations(db, int(inspect_id), limit=1000)
        if derivations:
            with st.expander(
                f"Transform lineage · {len(derivations)} edge(s)",
                expanded=False,
            ):
                st.dataframe(
                    [
                        {
                            "stage": (
                                f"{int(row['stage_index'])} · "
                                f"{row['stage_id']}"
                            ),
                            "transform": row["transform_kind"],
                            "parent": row["parent_parameter"],
                            "parent curve": row["parent_curve_id"],
                            "child": row["child_parameter"],
                            "child curve": row["child_curve_id"],
                        }
                        for row in derivations
                    ],
                    hide_index=True,
                    width="stretch",
                )

        process_col, state_col = st.columns(
            [1.35, 4.65],
            vertical_alignment="center",
        )
        if process_col.button(
            "Open in Jobs",
            width="stretch",
            icon=":material/work_history:",
            key="byo-open-selected-run-job",
        ):
            _open_pipeline_run_in_jobs(int(inspect_id))
            st.rerun()
        if str(selected_run["status"]) in RESUMABLE_RUN_STATES:
            state_col.info(
                "This Pipeline Run is resumable. Continue it from Jobs, "
                "where process attempts, logs, Resume/Retry, and stop controls are owned."
            )
        else:
            state_col.caption(
                "Process attempts, logs, scheduling, and execution controls are managed in Jobs."
            )

def _switch_builder_view(view):
    st.session_state["builder_main_view"] = str(view)
    st.session_state["builder_view_revision"] = int(
        st.session_state.get("builder_view_revision") or 0
    ) + 1


def _load_pipeline_for_edit(row):
    payload = pipeline_payload(row)
    mode = str(payload["target_mode"])
    mode_labels = {
        "family": "Family",
        "torsion": "Torsion Group",
        "general": "General Curves",
        "curve": "Target Curve",
    }
    st.session_state["byo_builder_state_version"] = 2
    raw_config = dict(payload.get("config") or {})
    st.session_state["byo-definition-config-shape"] = (
        "launch_defaults"
        if (
            isinstance(raw_config.get("launch_defaults"), dict)
            or "target" not in raw_config
        )
        else "legacy_target"
    )
    st.session_state["byo_stage_mode"] = mode
    target_label = mode_labels.get(mode, "Family")
    st.session_state["byo-target-mode"] = target_label
    st.session_state["byo_target_mode_revision"] = int(
        st.session_state.get("byo_target_mode_revision") or 0
    ) + 1

    saved_family = _saved_family_lane_key(payload)
    if saved_family:
        st.session_state["byo-family"] = saved_family

    saved_target = _saved_pipeline_target(payload)
    if mode == "family":
        pool_settings = _saved_family_pool_settings(payload)
        if pool_settings["candidate_pool_id"] is not None:
            st.session_state["byo-family-source"] = "Existing pool / corpus"
            st.session_state["byo-family-pool"] = int(
                pool_settings["candidate_pool_id"]
            )
            st.session_state["byo-family-only-unsearched"] = bool(
                pool_settings["only_unsearched"]
            )
        else:
            st.session_state["byo-family-source"] = "Generated candidates"
            st.session_state.pop("byo-family-pool", None)
            st.session_state.pop("byo-family-only-unsearched", None)
    if mode == "torsion" and saved_target.get("torsion_group"):
        st.session_state["byo-torsion"] = str(saved_target["torsion_group"])
        st.session_state["byo-secondary-providers"] = bool(
            saved_target.get("include_secondary_providers", False)
        )
    elif mode == "general" and saved_target.get("pool_mode"):
        st.session_state["byo-general-mode"] = str(saved_target["pool_mode"])
    elif mode == "curve":
        curve_id = saved_target.get("curve_id")
        st.session_state["byo-curve"] = (
            None if curve_id in (None, "") else int(curve_id)
        )

    st.session_state["byo_stages"] = normalize_pipeline(payload["stages"])
    st.session_state["byo_pipeline_name_input"] = payload["name"]
    st.session_state["byo-functional-features"] = list(
        payload.get("config", {}).get("feature_plugin_ids") or []
    )
    saved_settings = _saved_pipeline_run_settings(payload)
    for key in (
        f"byo-goal-{mode}",
        f"byo-retention-{mode}",
        "byo-cert-timeout",
        "byo-exact-candidates",
        "rh-byo-ratpoints",
    ):
        st.session_state.pop(key, None)
    st.session_state["byo_pending_target_rank"] = saved_settings["target_rank"]
    st.session_state["byo_pending_retention_floor"] = saved_settings[
        "retention_floor"
    ]
    st.session_state["byo_pending_certificate_timeout"] = saved_settings[
        "certificate_timeout"
    ]
    st.session_state["byo_pending_exact_candidates"] = saved_settings[
        "exact_candidates"
    ]
    st.session_state["byo_pending_ratpoints_backend"] = saved_settings[
        "ratpoints_backend"
    ]
    st.session_state.pop("byo_duplicate_source_pipeline_id", None)
    st.session_state["byo_loaded_pipeline_id"] = int(payload["id"])
    _bump_stage_revision()
    _switch_builder_view("Editor")


def page(db, ctx):
    ensure_pipeline_schema(db)
    if st.session_state.pop("byo_discard_pipeline_draft_pending", False):
        _discard_pipeline_draft(db)
        st.session_state["byo_draft_recovery_checked"] = True
    _restore_pipeline_draft(db)
    title(
        "Pipelines",
        "Create, save, run, and inspect reproducible search strategies built from Rank Hunter's candidate, arithmetic, geometry, point-search, and proof modules.",
        "Manage",
        icon="tune",
    )

    research_handoff = get_research_handoff(st.session_state)
    if research_handoff is not None:
        with st.container(border=True):
            header_cols = st.columns([0.78, 0.22], vertical_alignment="center")
            with header_cols[0]:
                st.markdown("**Research selection context**")
            with header_cols[1]:
                if st.button(
                    "Clear research context",
                    use_container_width=True,
                    key="clear-research-selection-context",
                ):
                    clear_research_handoff(st.session_state)
                    st.rerun()
            family = str(research_handoff.get("family") or "—")
            kind = str(research_handoff.get("kind") or "selection")
            count = len(research_handoff.get("curve_ids") or ())
            selection = research_handoff.get("selection") or {}
            item_count = selection.get("item_count")
            item_text = (
                f" · selection items: {int(item_count):,}"
                if isinstance(item_count, int) and item_count >= 0
                else ""
            )
            st.caption(
                f"Source: {research_handoff['source']} · kind: {kind} · family: {family} · "
                f"stored curves: {count:,}{item_text} · "
                f"hash: `{research_handoff['selection_hash'][:12]}`"
            )
            parameters = list(research_handoff.get("parameters") or ())
            if parameters:
                with st.expander(
                    f"Retained curve parameters ({len(parameters):,})",
                    expanded=False,
                ):
                    st.dataframe(
                        parameters,
                        hide_index=True,
                        width="stretch",
                        height=min(
                            420,
                            max(120, 42 + 35 * min(len(parameters), 10)),
                        ),
                    )
            selection_items = selection.get("items")
            if isinstance(selection_items, list) and selection_items:
                item_rows = []
                for item in selection_items:
                    if not isinstance(item, dict):
                        continue
                    item_rows.append(
                        {
                            key: (
                                json.dumps(value, sort_keys=True, separators=(",", ":"))
                                if isinstance(value, (dict, list, tuple))
                                else value
                            )
                            for key, value in item.items()
                        }
                    )
                if item_rows:
                    with st.expander(
                        f"Selected research items ({len(item_rows):,})",
                        expanded=True,
                    ):
                        st.dataframe(
                            item_rows,
                            hide_index=True,
                            width="stretch",
                            height=min(
                                420,
                                max(120, 42 + 35 * min(len(item_rows), 10)),
                            ),
                        )
                    st.caption(
                        "Retained curve parameters above list only stored database matches. "
                        "Unstored twists/isogeny nodes remain explicit here as research items."
                    )
            if selection:
                with st.expander("Selection metadata", expanded=False):
                    st.json(selection)
            st.caption(
                "Context only: the originating Workspace has not changed Builder modules, "
                "candidate sources, or rank goals. Rank Hunter does not yet have a generic "
                "arbitrary-curve/parameter population contract; choose an ordinary Pipeline "
                "target/source below."
            )

    campaign = current_campaign(db)

    campaign_context = _campaign_context_token(campaign)
    campaign_context_pending = (
        campaign_context is not None
        and st.session_state.get("byo_campaign_family_context") != campaign_context
    )
    if campaign_context_pending and campaign["plugin_id"]:
        if st.session_state.get("byo-target-mode") != "Family":
            st.session_state["byo-target-mode"] = "Family"
            st.session_state["byo_target_mode_revision"] = int(
                st.session_state.get("byo_target_mode_revision") or 0
            ) + 1

    view_revision = int(st.session_state.get("builder_view_revision") or 0)
    current_view = str(st.session_state.get("builder_main_view") or "Editor")
    # Preserve old session-state values from the pre-Pipelines UI.
    current_view = {"Builder": "Editor", "Pipelines": "Saved"}.get(current_view, current_view)
    view = tabs(
        ["Editor", "Saved", "Runs"],
        value=(
            current_view
            if current_view in {"Editor", "Saved", "Runs"}
            else "Editor"
        ),
        key=f"builder-main-tabs-{view_revision}",
    )
    st.session_state["builder_main_view"] = view

    if view == "Saved":
        _render_pipeline_library(db)
        return
    if view == "Runs":
        _render_pipeline_runs_page(db, ctx, campaign)
        return

    _pipeline_editor_style()
    _render_pipeline_draft_recovery(db)

    with st.container(border=True, key="byo-search-space"):
        target_title_col, target_mode_col = st.columns(
            [1.15, 2.85],
            vertical_alignment="center",
        )
        with target_title_col:
            st.subheader("Search target")
        target_mode_revision = int(
            st.session_state.get("byo_target_mode_revision") or 0
        )
        with target_mode_col:
            mode_label = tabs(
                ["Family", "Torsion Group", "General Curves", "Target Curve"],
                value=str(st.session_state.get("byo-target-mode") or "Family"),
                key=f"byo-target-mode-tabs-{target_mode_revision}",
                label="",
            )
        st.session_state["byo-target-mode"] = mode_label
        mode = {
            "Family": "family",
            "Torsion Group": "torsion",
            "General Curves": "general",
            "Target Curve": "curve",
        }[mode_label]

        target = {}
        suggested_goal = 8
        selected_plugin = None
        selected_variant = None
        launch_blocked = False

        if mode == "family":
            lanes = _family_lanes(db, ctx)
            if not lanes:
                st.warning("No enabled candidate-generating family plugin is available.")
                return
            by_key = {rec[3]: rec for rec in lanes}
            needs_campaign_family = bool(
                campaign_context is not None
                and (
                    campaign_context_pending
                    or "byo-family" not in st.session_state
                )
            )
            if needs_campaign_family:
                preferred_lane = _campaign_family_lane_key(campaign, lanes)
                if preferred_lane in by_key:
                    st.session_state["byo-family"] = preferred_lane
                st.session_state["byo_campaign_family_context"] = campaign_context
            elif (
                campaign_context is not None
                and "byo_campaign_family_context" not in st.session_state
            ):
                st.session_state["byo_campaign_family_context"] = campaign_context

            selected_state = st.session_state.get("byo-family")
            if selected_state not in by_key:
                st.session_state.pop("byo-family", None)

            lane_col, source_col, pool_col, unsearched_col = st.columns(
                [2.45, 1.3, 2.65, 0.95],
                vertical_alignment="bottom",
            )
            lane_key = lane_col.selectbox(
                "Family / variant",
                list(by_key),
                format_func=lambda key: _family_label(by_key[key]),
                key="byo-family",
            )
            lane = by_key[lane_key]
            selected_plugin, selected_variant, claim = lane[4], lane[5], lane[6]
            target = {
                "plugin_id": selected_plugin.id,
                "variant_id": selected_variant.id,
            }

            compatible_pools = _compatible_family_pools(
                db, selected_plugin, selected_variant
            )
            source_options = ["Generated candidates", "Existing pool / corpus"]
            source = source_col.selectbox(
                "Candidate source",
                source_options,
                key="byo-family-source",
                format_func=lambda value: (
                    "Existing pool / library"
                    if value == "Existing pool / corpus"
                    else value
                ),
                help=(
                    "Generated candidates runs the family candidate generator. "
                    "Existing pool / library uses an already materialized parameter "
                    "population and skips fresh candidate generation."
                ),
            )
            if source == "Existing pool / corpus":
                if not compatible_pools:
                    st.warning(
                        "No ready candidate pool matches this exact plugin family variant. "
                        "Build or create a library/candidate pool first, or switch back "
                        "to Generated candidates."
                    )
                    return
                pool_by_id = {
                    int(row["id"]): row for row in compatible_pools
                }
                selected_pool_state = st.session_state.get("byo-family-pool")
                try:
                    selected_pool_state = int(selected_pool_state)
                except (TypeError, ValueError):
                    selected_pool_state = None
                if selected_pool_state not in pool_by_id:
                    st.session_state.pop("byo-family-pool", None)

                pool_id = int(pool_col.selectbox(
                    "Candidate pool",
                    list(pool_by_id),
                    format_func=lambda pid: _family_pool_label(
                        pool_by_id[int(pid)]
                    ),
                    key="byo-family-pool",
                ))
                only_unsearched = bool(unsearched_col.toggle(
                    "Unsearched only",
                    value=False,
                    key="byo-family-only-unsearched",
                    help="Only consume candidates not yet searched in this pool.",
                ))
                target["candidate_pool_id"] = pool_id
                target["only_unsearched"] = only_unsearched
            else:
                pool_col.text_input(
                    "Candidate pool",
                    value="Generated at run time",
                    disabled=True,
                    key="byo-family-generated-pool-label",
                )
                st.session_state.pop("byo-family-pool", None)
                st.session_state.pop("byo-family-only-unsearched", None)

            policy = auto_search_policy(selected_plugin, selected_variant)
            lower = claim["effective_lower"]
            suggested_goal = default_target_rank(
                policy,
                (int(lower) + 1) if lower is not None else 8,
            )
        elif mode == "torsion":
            torsion = st.selectbox(
                "Exact rational torsion group",
                MAZUR_TORSION_CHOICES,
                key="byo-torsion",
            )
            lanes = _family_lanes(db, ctx, torsion)
            if not lanes:
                st.warning(f"No enabled direct provider declares {torsion}.")
                return
            record = _torsion_record_profile(lanes)
            suggested_goal = int(record["goal_rank"]) if record else 8
            include_secondary = st.toggle(
                "Try unrelated family providers after direct providers",
                value=False,
                key="byo-secondary-providers",
            )
            target = {
                "torsion_group": torsion,
                "include_secondary_providers": bool(include_secondary),
            }
            st.caption(
                f"{len(lanes)} direct provider lane(s) currently installed; each provider is exact-preflighted."
            )
        elif mode == "general":
            c1, c2 = st.columns(2)
            pool_mode = c1.selectbox(
                "General pool",
                ["seeded", "open"],
                key="byo-general-mode",
            )
            pool_size = int(c2.number_input(
                "Broad pool size", min_value=10, value=5000, step=500,
                key="byo-general-pool",
            ))
            target = {
                "pool_mode": pool_mode,
                "pool_size": pool_size,
                "random_seed": 42,
            }
            if pool_mode == "seeded":
                a, b, c, d = st.columns(4)
                target.update(
                    u_min=int(a.number_input("u min", value=1, key="byo-u-min")),
                    u_max=int(b.number_input("u max", value=250, key="byo-u-max")),
                    v_min=int(c.number_input("v min", value=1, key="byo-v-min")),
                    v_max=int(d.number_input("v max", value=250, key="byo-v-max")),
                )
            else:
                a, b, c, d = st.columns(4)
                target.update(
                    a_min=int(a.number_input("A min", value=-5000, key="byo-a-min")),
                    a_max=int(b.number_input("A max", value=5000, key="byo-a-max")),
                    b_min=int(c.number_input("B min", value=-5000, key="byo-b-min")),
                    b_max=int(d.number_input("B max", value=5000, key="byo-b-max")),
                )
            suggested_goal = 8
        else:
            rows = curve_options(db)
            row_by_id = {int(row["id"]): row for row in rows}
            selected_state = st.session_state.get("byo-curve")
            try:
                selected_state = (
                    None if selected_state in (None, "") else int(selected_state)
                )
            except (TypeError, ValueError):
                selected_state = None
            if selected_state not in row_by_id:
                selected_state = None
                st.session_state["byo-curve"] = None

            options = [None, *row_by_id]
            curve_id = st.selectbox(
                "Default target curve",
                options,
                index=options.index(selected_state),
                key="byo-curve",
                format_func=lambda value: (
                    "None — choose a curve at launch"
                    if value is None
                    else curve_label(row_by_id[int(value)])
                ),
                help=(
                    "This is a launch default, not part of the reusable mathematical "
                    "recipe. Leave it unset to save a generic curve-mode Pipeline."
                ),
            )
            if curve_id is None:
                launch_blocked = True
                st.caption(
                    "This curve-mode recipe can be saved now. Choose a default target "
                    "curve before launching it from Pipelines."
                )
            else:
                selected_curve = row_by_id[int(curve_id)]
                target = {"curve_id": int(curve_id)}
                suggested_goal = max(
                    1,
                    int(selected_curve.get("rigorous_lower") or 0) + 1,
                )
                plugin_id = str(selected_curve.get("plugin_id") or "").strip()
                if plugin_id:
                    selected_plugin = next(
                        (
                            plugin
                            for plugin in discover_plugins(ctx.project_root)
                            if isinstance(plugin, Plugin)
                            and plugin.plugin_type == "family"
                            and plugin.id == plugin_id
                        ),
                        None,
                    )
                    if selected_plugin is not None:
                        selected_variant = (
                            variant_for_family_spec(
                                selected_plugin,
                                selected_curve.get("family_spec"),
                            )
                            or get_variant(selected_plugin)
                        )
                        target["plugin_id"] = selected_plugin.id
                        if selected_variant is not None:
                            target["variant_id"] = selected_variant.id

    if campaign is not None and campaign["target_rank"] is not None:
        suggested_goal = int(campaign["target_rank"])

    stages, errors = _render_builder(
        db,
        ctx,
        mode=mode,
        mode_label=mode_label,
        suggested_goal=suggested_goal,
        target=target,
        selected_plugin=selected_plugin,
        selected_variant=selected_variant,
        launch_blocked=launch_blocked,
    )

