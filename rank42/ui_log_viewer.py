from __future__ import annotations

import html as html_lib
import re


def _elapsed_label(seconds):
    if seconds is None:
        return "—"
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _activity_label(log):
    for line in reversed(str(log or "").splitlines()):
        match = re.search(r"(\[auto:[^\]]+\].*?chart=\d+/\d+)", line)
        if match:
            return match.group(1).strip()[-140:]
    return ""


def raw_log_document(
    log: str,
    *,
    channel_name: str = "",
    best_rank: str = "—",
    elapsed_seconds=None,
    stage_text: str = "",
) -> str:
    """Return a safe full-log viewer that opens scrolled to the newest output."""
    escaped = html_lib.escape(str(log), quote=False)
    escaped_rank = html_lib.escape(str(best_rank or "—"), quote=True)
    elapsed = html_lib.escape(_elapsed_label(elapsed_seconds), quote=True)
    stage = html_lib.escape(str(stage_text or "Stage unavailable"), quote=True)
    activity = html_lib.escape(_activity_label(log) or "", quote=True)
    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<style>
  :root {{ color-scheme: light dark; }}
  * {{ box-sizing: border-box; }}
  html, body {{ margin: 0; padding: 0; width: 100%; height: 100%; background: transparent; }}
  body {{ font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
  .toolbar {{
    height: 42px; display: flex; align-items: center; justify-content: space-between;
    gap: 12px; padding: 0 10px; color: #f5f7fa; background: #000;
    border-radius: 8px 8px 0 0;
  }}
  .log-title {{ margin-right: auto; font-size: 13px; font-weight: 500; }}
  .toolbar-actions {{ display:flex; align-items:center; gap:6px; }}
  .rank-badge {{ height:30px; display:inline-flex; align-items:center; gap:8px;
    padding:0 10px; border:1px solid rgba(128,128,128,.42); border-radius:8px;
    background:ButtonFace; color:ButtonText; white-space:nowrap; }}
  .rank-label {{ font-size:11px; opacity:.68; }}
  .rank-value {{ font-size:12px; font-weight:750; }}
  .statusbar {{ min-height:36px; display:flex; align-items:center; gap:18px;
    padding:0 12px; color:#d8dde5; background:#000; border-radius:0 0 8px 8px;
    font-size:11px; line-height:1.25; white-space:nowrap; overflow:hidden; }}
  .status-item {{ display:inline-flex; align-items:center; gap:6px; min-width:0; }}
  .status-label {{ color:#9299a5; text-transform:uppercase; letter-spacing:.06em; font-size:9px; }}
  .status-value {{ overflow:hidden; text-overflow:ellipsis; }}
  .status-activity {{ margin-left:auto; color:#aab2bf; font-family:ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }}
  @media (max-width:620px) {{
    .statusbar {{ gap:9px; padding:0 8px; }}
    .status-label {{ letter-spacing:0; }}
    .status-activity {{ max-width:46%; }}
  }}
  html:fullscreen {{ background: Canvas; padding: 12px; }}
  html:fullscreen body {{ height: 100%; }}
  html:fullscreen #viewport {{ height: calc(100vh - 102px); }}
  html.rh-raw-log-fullscreen, html.rh-raw-log-fullscreen body {{
    width:100%!important; height:100vh!important; overflow:hidden!important; background:#090b0f!important;
  }}
  html.rh-raw-log-fullscreen body {{ padding:0!important; }}
  html.rh-raw-log-fullscreen #viewport {{ height:calc(100vh - 78px)!important; max-height:none!important; }}
  button {{
    flex: 0 0 auto; border: 1px solid rgba(128,128,128,.42); border-radius: 8px;
    padding: 6px 11px; background: ButtonFace; color: ButtonText;
    font: inherit; font-size: 12px; cursor: pointer;
  }}
  button:hover {{ filter: brightness(1.06); }}
  .icon-button {{ width: 34px; height: 30px; display: inline-flex; align-items: center;
    justify-content: center; margin-left: 6px; padding: 0; }}
  .icon-button svg {{ width: 18px; height: 18px; fill: currentColor; }}
  .sr-only {{ position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px;
    overflow: hidden; clip: rect(0,0,0,0); white-space: nowrap; border: 0; }}
  #viewport {{
    height: 520px; overflow-y: auto; overflow-x: hidden;
    border: 1px solid rgba(128,128,128,.34);
    border-radius: 0 0 8px 8px; background: rgba(128,128,128,.07);
  }}
  pre {{
    margin: 0; padding: 12px 14px 18px 14px; width: 100%; min-width: 0;
    white-space: pre-wrap; overflow-wrap: anywhere; word-break: break-word; tab-size: 4;
    font: 12px/1.42 ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;
    color: CanvasText;
  }}
  #copy-status {{ margin-left: 8px; opacity: .75; font-size: 12px; }}
</style>
</head>
<body>
  <div class="toolbar">
    <span class="log-title">Rank Hunter · Raw Log</span>
    <div class="toolbar-actions">
      <div class="rank-badge" title="Best rigorously established rank found by this job">
        <span class="rank-label">Best rank found</span>
        <span class="rank-value">{escaped_rank}</span>
      </div>
      <button id="fullscreen-log" class="icon-button" type="button" title="Open full screen raw log" aria-label="Open full screen raw log">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 4h6V2H2v8h2zm6 14H4v-6H2v8h8zm10-6v6h-6v2h8v-8zM14 2v2h6v6h2V2z"/></svg>
        <span class="sr-only">Full screen</span>
      </button>
      <button id="copy-log" class="icon-button" type="button" title="Copy full log" aria-label="Copy full log">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M16 1H4c-1.1 0-2 .9-2 2v14h2V3h12zm3 4H8c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h11c1.1 0 2-.9 2-2V7c0-1.1-.9-2-2-2m0 16H8V7h11z"/></svg>
        <span class="sr-only">Copy full log</span>
      </button>
      <span id="copy-status" aria-live="polite"></span>
    </div>
  </div>
  <div id="viewport"><pre id="raw-log">{escaped}</pre></div>
  <div class="statusbar" role="status" aria-label="Job progress">
    <span class="status-item"><span class="status-label">Elapsed</span><span class="status-value">{elapsed}</span></span>
    <span class="status-item"><span class="status-label">Stage</span><span class="status-value">{stage}</span></span>
    <span class="status-item status-activity"><span class="status-value">{activity}</span></span>
  </div>
<script>
(() => {{
  const viewport = document.getElementById("viewport");
  const raw = document.getElementById("raw-log");
  const button = document.getElementById("copy-log");
  const fullscreenButton = document.getElementById("fullscreen-log");
  const status = document.getElementById("copy-status");
  const channelName = {channel_name!r};
  let logChannel = null;

  const parentDoc = window.parent.document;
  const parentWin = window.parent;
  const screenClass = "rh-raw-log-fullscreen";
  const findHost = () => {{
    const frame = window.frameElement;
    return frame && (frame.closest('[data-testid="stElementContainer"]') || frame.parentElement);
  }};
  const markHost = () => {{
    const host = findHost();
    if (host) host.setAttribute("data-rh-raw-log-fullscreen", "true");
    parentDoc.documentElement.classList.add(screenClass);
    document.documentElement.classList.add("rh-raw-log-fullscreen");
  }};
  const screenStyleId = "rh-raw-log-fullscreen-style";
  if (!parentDoc.getElementById(screenStyleId)) {{
    const style = parentDoc.createElement("style");
    style.id = screenStyleId;
    style.textContent = `
      [data-rh-raw-log-fullscreen="true"]:fullscreen {{
        position:fixed!important; inset:0!important; z-index:2147483647!important;
        width:100vw!important; height:100vh!important; max-width:none!important;
        max-height:none!important; margin:0!important; padding:0!important;
        overflow:hidden!important; background:#090b0f!important;
      }}
      [data-rh-raw-log-fullscreen="true"]:fullscreen iframe {{
        display:block!important; width:100vw!important; height:100vh!important;
        min-height:100vh!important; max-height:none!important; border:0!important;
      }}
    `;
    parentDoc.head.appendChild(style);
  }}

  if (parentDoc.documentElement.classList.contains(screenClass)) markHost();
  if (!parentWin.__rhRawLogFullscreenChangeBound) {{
    parentWin.__rhRawLogFullscreenChangeBound = true;
    parentDoc.addEventListener("fullscreenchange", () => {{
      if (parentDoc.fullscreenElement?.hasAttribute("data-rh-raw-log-fullscreen")) return;
      parentDoc.documentElement.classList.remove(screenClass);
      parentDoc.querySelectorAll('[data-rh-raw-log-fullscreen="true"]').forEach((node) => {{
        node.removeAttribute("data-rh-raw-log-fullscreen");
      }});
      parentDoc.querySelectorAll("iframe").forEach((frame) => {{
        try {{ frame.contentDocument?.documentElement?.classList.remove("rh-raw-log-fullscreen"); }} catch (_) {{}}
      }});
    }});
  }}

  if (channelName && "BroadcastChannel" in window) {{
    try {{
      logChannel = new BroadcastChannel(channelName);
      logChannel.postMessage({{ log: raw.textContent || "" }});
    }} catch (_) {{ logChannel = null; }}
  }}

  const pinBottom = () => {{
    viewport.scrollTop = viewport.scrollHeight;
    viewport.scrollLeft = 0;
  }};

  const fallbackCopy = (text) => {{
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand("copy");
    ta.remove();
    if (!ok) throw new Error("copy command failed");
  }};

  button.addEventListener("click", async () => {{
    const text = raw.textContent || "";
    try {{
      if (navigator.clipboard && navigator.clipboard.writeText) {{
        await navigator.clipboard.writeText(text);
      }} else {{
        fallbackCopy(text);
      }}
      status.textContent = "Copied";
      window.setTimeout(() => {{ status.textContent = ""; }}, 1600);
    }} catch (_) {{
      try {{
        fallbackCopy(text);
        status.textContent = "Copied";
        window.setTimeout(() => {{ status.textContent = ""; }}, 1600);
      }} catch (_) {{
        status.textContent = "Copy failed";
      }}
    }}
  }});

  fullscreenButton.addEventListener("click", async () => {{
    try {{
      const host = findHost();
      if (parentDoc.fullscreenElement?.hasAttribute("data-rh-raw-log-fullscreen")) {{
        await parentDoc.exitFullscreen();
        return;
      }}
      markHost();
      if (!host) throw new Error("raw log container unavailable");
      await host.requestFullscreen();
    }} catch (_) {{
      parentDoc.documentElement.classList.remove(screenClass);
      parentDoc.querySelectorAll('[data-rh-raw-log-fullscreen="true"]').forEach((node) => {{
        node.removeAttribute("data-rh-raw-log-fullscreen");
      }});
      fullscreenButton.title = "Fullscreen is unavailable in this embedded view";
      window.setTimeout(() => {{ fullscreenButton.title = "Open full screen raw log"; }}, 2200);
    }}
  }});

  requestAnimationFrame(pinBottom);
  window.addEventListener("load", pinBottom, {{ once: true }});
  if ("ResizeObserver" in window) {{
    new ResizeObserver(pinBottom).observe(raw);
  }}
}})();
</script>
</body>
</html>"""


def render_raw_log(
    log: str,
    *,
    height: int = 570,
    job_id: int | None = None,
    best_rank: str = "—",
    elapsed_seconds=None,
    stage_text: str = "",
) -> None:
    """Render the complete raw log with bottom pinning, wrapping, and one-click copy."""
    import streamlit as st

    channel_name = "" if job_id is None else f"rankhunter-job-log-{int(job_id)}"
    st.iframe(
        raw_log_document(
            log, channel_name=channel_name, best_rank=best_rank,
            elapsed_seconds=elapsed_seconds, stage_text=stage_text,
        ),
        width="stretch",
        height=int(height) + 36,
    )
