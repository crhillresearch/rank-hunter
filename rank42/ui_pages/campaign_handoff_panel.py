from __future__ import annotations

import streamlit as st

from rank42.manage_store import campaign_handoff_markdown
from rank42.research_bundle import (
    build_campaign_bundle_archive,
    build_campaign_bundle_manifest,
)


def render_handoff(db, brief):
    campaign = brief["snapshot"]["campaign"]
    markdown = campaign_handoff_markdown(
        db,
        int(campaign["id"]),
        brief=brief,
    )
    st.markdown("#### Research handoff")
    st.caption(
        "Generated from the current durable campaign state. Creating or "
        "downloading this handoff does not persist a second scientific record."
    )
    st.download_button(
        "Download Markdown handoff",
        data=markdown,
        file_name=f"campaign-{int(campaign['id'])}-handoff.md",
        mime="text/markdown",
        width="stretch",
        key=f"campaign-handoff-download-{int(campaign['id'])}",
    )
    st.markdown(markdown)
    with st.expander("Raw Markdown", expanded=False):
        st.code(markdown, language="markdown")

    st.divider()
    st.markdown("#### Research Bundle")
    st.caption(
        "Prepare a reproducibility ZIP from authoritative durable state. "
        "Bundle preparation is read-only; it does not recertify points, recompute rank, "
        "or include Notebook entries marked Private scratch / Resolved."
    )
    bundle_key = f"campaign-research-bundle-{int(campaign['id'])}"
    if st.button(
        "Prepare / rebuild Research Bundle",
        width="stretch",
        icon=":material/archive:",
        key=f"campaign-bundle-prepare-{int(campaign['id'])}",
    ):
        try:
            manifest = build_campaign_bundle_manifest(
                db,
                int(campaign["id"]),
            )
            archive = build_campaign_bundle_archive(manifest)
        except ValueError as exc:
            st.error(str(exc))
        else:
            st.session_state[bundle_key] = {
                "data": archive,
                "generated_at": manifest["generated_at"],
                "manifest_version": manifest["manifest_version"],
            }

    prepared = st.session_state.get(bundle_key)
    if prepared:
        st.download_button(
            "Download Research Bundle ZIP",
            data=prepared["data"],
            file_name=f"campaign-{int(campaign['id'])}-research-bundle.zip",
            mime="application/zip",
            width="stretch",
            key=f"campaign-bundle-download-{int(campaign['id'])}",
        )
        st.caption(
            f"Prepared {prepared['generated_at']} · "
            f"manifest v{int(prepared['manifest_version'])}. "
            "Rebuild after Campaign/Notebook/Run/Landscape state changes."
        )
