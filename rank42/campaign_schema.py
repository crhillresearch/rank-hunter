"""Shared schema fragment for the Campaign ownership parent table.

Campaign behavior remains owned by manage_store. This module exists so subsystems
that persist foreign keys to research_campaigns can initialize safely in isolation
without weakening the relational constraint.
"""

RESEARCH_CAMPAIGN_SCHEMA = """
CREATE TABLE IF NOT EXISTS research_campaigns(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    objective TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    target_rank INTEGER,
    plugin_id TEXT,
    variant_id TEXT,
    family TEXT,
    notes TEXT NOT NULL DEFAULT '',
    completed_at TEXT,
    completion_snapshot_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_research_campaigns_status
    ON research_campaigns(status, updated_at DESC);
"""
