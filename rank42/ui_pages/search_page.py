from __future__ import annotations
import streamlit as st
from rank42.feature_hooks import render_feature_hook
from rank42.ui_components import tabs
from .common import title
from . import family_search, general_hunt


def page(db, ctx):
    title('Search')
    render_feature_hook(db,ctx,'search.after_header')
    current = str(st.session_state.get('search_tab') or 'Family')
    choice = tabs(['Family', 'General'], value=current if current in {'Family','General'} else 'Family', key='search-main-tabs')
    st.session_state['search_tab'] = choice
    if choice == 'General':
        general_hunt.render(db, ctx, embedded=True)
    else:
        family_search.render(db, ctx, embedded=True)
