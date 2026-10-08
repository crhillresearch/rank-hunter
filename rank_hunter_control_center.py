"""Top-level Streamlit entry point for the Rank Hunter Control Center.

The internal scientific package remains named ``rank42`` for compatibility.
Keeping the Streamlit script at the project root makes package resolution
deterministic under WSL/Streamlit.
"""

from rank42.ui import main

main()
