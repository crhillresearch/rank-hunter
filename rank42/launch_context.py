"""Shared read-only launch-context resolution for UI job/Pipeline launches."""
from __future__ import annotations

from dataclasses import dataclass
import logging
import sqlite3

from rank42.ui_store import get_application_state


SHARED_LAUNCH_METADATA_KEYS = (
    "campaign_id",
    "campaign_name",
    "campaign_created_at",
    "campaign_context_error",
    "launch_surface",
    "plugin_id",
    "plugin_variant",
    "target_mode",
    "feature_plugin_ids",
    "source_pool_id",
)


def launch_context_snapshot(metadata):
    """Return only the normalized cross-surface Launch Context fields."""
    raw = dict(metadata or {})
    out = {}
    for key in SHARED_LAUNCH_METADATA_KEYS:
        if key not in raw:
            continue
        value = raw[key]
        out[key] = list(value) if key == "feature_plugin_ids" else value
    return out


@dataclass(frozen=True)
class LaunchContextResolution:
    metadata: dict
    campaign: object | None
    failure: dict | None


def _campaign_value(row, key, default=None):
    if row is None:
        return default
    try:
        return row[key]
    except Exception:
        if isinstance(row, dict):
            return row.get(key, default)
    return default


def lookup_active_campaign(db, *, logger=None):
    """Read the current Campaign only when lifecycle status permits auto-attachment."""
    log = logger or logging.getLogger(__name__)
    try:
        campaign_id = get_application_state(
            db,
            "current_campaign_id",
            None,
        )
        if campaign_id is None:
            return None, None
        campaign = db.execute(
            """SELECT * FROM research_campaigns
               WHERE id=? AND status='active'""",
            (int(campaign_id),),
        ).fetchone()
        return campaign, None
    except sqlite3.OperationalError as exc:
        text = str(exc)
        lowered = text.lower()
        transient = any(
            token in lowered
            for token in (
                "database is locked",
                "database is busy",
                "database table is locked",
                "database schema is locked",
            )
        )
        if transient:
            return None, {
                "level": "warning",
                "message": f"Active campaign temporarily unavailable: {text}",
            }
        log.exception("Active campaign lookup failed")
        return None, {
            "level": "error",
            "message": f"Active campaign lookup failed: OperationalError: {text}",
        }
    except Exception as exc:
        log.exception("Active campaign lookup failed")
        return None, {
            "level": "error",
            "message": (
                "Active campaign lookup failed: "
                f"{type(exc).__name__}: {exc}"
            ),
        }


def normalize_launch_metadata(
    metadata=None,
    *,
    launch_surface=None,
    plugin_id=None,
    plugin_variant=None,
    target_mode=None,
    pipeline_run_id=None,
    pipeline_id=None,
    pipeline_name=None,
    feature_plugin_ids=None,
    source_pool_id=None,
):
    """Return a normalized launch-provenance copy without database access."""
    out = dict(metadata or {})

    def set_default(key, value, *, transform=None):
        if value is None or key in out:
            return
        out[key] = transform(value) if transform else value

    set_default("launch_surface", launch_surface, transform=str)
    set_default("plugin_id", plugin_id, transform=str)
    set_default("plugin_variant", plugin_variant, transform=str)
    set_default("target_mode", target_mode, transform=str)
    set_default("pipeline_run_id", pipeline_run_id, transform=int)
    set_default("pipeline_id", pipeline_id)
    set_default("pipeline_name", pipeline_name, transform=str)

    if "plugin_variant" not in out and out.get("variant_id") is not None:
        out["plugin_variant"] = str(out["variant_id"])

    if feature_plugin_ids is not None and "feature_plugin_ids" not in out:
        out["feature_plugin_ids"] = list(feature_plugin_ids)
    elif "feature_plugin_ids" in out:
        out["feature_plugin_ids"] = list(out.get("feature_plugin_ids") or [])

    inferred_pool = source_pool_id
    if inferred_pool is None:
        for key in ("source_pool_id", "candidate_pool_id", "pool_id"):
            if out.get(key) is not None:
                inferred_pool = out[key]
                break
    if inferred_pool is not None and "source_pool_id" not in out:
        try:
            out["source_pool_id"] = int(inferred_pool)
        except Exception:
            out["source_pool_id"] = inferred_pool

    return out


def resolve_launch_context(
    db,
    *,
    metadata=None,
    launch_surface=None,
    plugin_id=None,
    plugin_variant=None,
    target_mode=None,
    pipeline_run_id=None,
    pipeline_id=None,
    pipeline_name=None,
    feature_plugin_ids=None,
    source_pool_id=None,
    logger=None,
):
    """Resolve one launch metadata snapshot without writing to the database."""
    out = normalize_launch_metadata(
        metadata,
        launch_surface=launch_surface,
        plugin_id=plugin_id,
        plugin_variant=plugin_variant,
        target_mode=target_mode,
        pipeline_run_id=pipeline_run_id,
        pipeline_id=pipeline_id,
        pipeline_name=pipeline_name,
        feature_plugin_ids=feature_plugin_ids,
        source_pool_id=source_pool_id,
    )

    if "campaign_id" in out:
        return LaunchContextResolution(
            metadata=out,
            campaign=None,
            failure=None,
        )

    campaign, failure = lookup_active_campaign(db, logger=logger)
    if failure is not None:
        out["campaign_context_error"] = {
            "level": str(failure.get("level") or "error"),
            "message": str(failure.get("message") or ""),
        }
    if campaign is not None:
        out["campaign_id"] = int(_campaign_value(campaign, "id"))
        out["campaign_name"] = str(_campaign_value(campaign, "name"))
        created_at = _campaign_value(campaign, "created_at")
        if created_at is not None:
            out["campaign_created_at"] = str(created_at)

    return LaunchContextResolution(
        metadata=out,
        campaign=campaign,
        failure=failure,
    )
