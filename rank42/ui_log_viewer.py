from __future__ import annotations

import html as html_lib


def raw_log_document(log: str) -> str:
    """Return a safe full-log viewer that opens scrolled to the newest output."""
    escaped = html_lib.escape(str(log), quote=False)
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
    gap: 12px; padding: 0 2px 8px 2px;
  }}
  .hint {{ opacity: .68; font-size: 12px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
  button {{
    flex: 0 0 auto; border: 1px solid rgba(128,128,128,.42); border-radius: 8px;
    padding: 6px 11px; background: ButtonFace; color: ButtonText;
    font: inherit; font-size: 12px; cursor: pointer;
  }}
  button:hover {{ filter: brightness(1.06); }}
  #viewport {{
    height: 520px; overflow-y: auto; overflow-x: hidden;
    border: 1px solid rgba(128,128,128,.34);
    border-radius: 8px; background: rgba(128,128,128,.07);
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
    <div class="hint">Full raw log · auto-following newest output · word wrap on</div>
    <div>
      <button id="copy-log" type="button">Copy full log</button>
      <span id="copy-status" aria-live="polite"></span>
    </div>
  </div>
  <div id="viewport"><pre id="raw-log">{escaped}</pre></div>
<script>
(() => {{
  const viewport = document.getElementById("viewport");
  const raw = document.getElementById("raw-log");
  const button = document.getElementById("copy-log");
  const status = document.getElementById("copy-status");

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

  requestAnimationFrame(pinBottom);
  window.addEventListener("load", pinBottom, {{ once: true }});
  if ("ResizeObserver" in window) {{
    new ResizeObserver(pinBottom).observe(raw);
  }}
}})();
</script>
</body>
</html>"""


def render_raw_log(log: str, *, height: int = 570) -> None:
    """Render the complete raw log with bottom pinning, wrapping, and one-click copy."""
    import streamlit as st

    st.iframe(raw_log_document(log), width="stretch", height=int(height))
