import copy
import importlib
import sqlite3
import sys
import types


def _row_pair():
    db=sqlite3.connect(':memory:')
    db.row_factory=sqlite3.Row
    db.execute('CREATE TABLE t(id INTEGER PRIMARY KEY, label TEXT)')
    db.executemany('INSERT INTO t(id,label) VALUES(?,?)',[(1,'one'),(2,'two')])
    return db, db.execute('SELECT * FROM t ORDER BY id').fetchall()


def _import_common_with_fake_streamlit(monkeypatch, fake):
    """Reload common while keeping the shared UI component layer on the same fake."""
    monkeypatch.setitem(sys.modules, 'streamlit', fake)
    components = importlib.import_module('rank42.ui_components')
    monkeypatch.setattr(components, 'st', fake)
    sys.modules.pop('rank42.ui_pages.common', None)
    return importlib.import_module('rank42.ui_pages.common')


class FakeStreamlit(types.ModuleType):
    def __init__(self):
        super().__init__('streamlit')
        self.last_options=None
        self.session_state={}

    def selectbox(self, label, options, index=0, **kwargs):
        self.last_options=list(options)
        # Mirrors the Streamlit state behavior that exposed sqlite3.Row as unsafe.
        selected=self.last_options[index]
        copy.deepcopy(selected)
        return selected

    def multiselect(self, label, options, default=None, **kwargs):
        self.last_options=list(options)
        selected=list(default or self.last_options[:1])
        copy.deepcopy(selected)
        return selected


def test_row_selectors_store_only_primitive_ids(monkeypatch):
    fake=FakeStreamlit()
    common=_import_common_with_fake_streamlit(monkeypatch,fake)
    db, rows=_row_pair()
    try:
        selected=common.select_row('row',rows,index=1,format_func=lambda r:r['label'])
        assert selected['id']==2
        assert fake.last_options==[1,2]
        assert all(isinstance(x,int) for x in fake.last_options)

        chosen=common.multiselect_rows('rows',rows,default=[rows[1]],format_func=lambda r:r['label'])
        assert [r['id'] for r in chosen]==[2]
        assert fake.last_options==[1,2]
    finally:
        db.close()


def test_launch_arms_immediate_live_refresh(monkeypatch):
    fake=FakeStreamlit()
    common=_import_common_with_fake_streamlit(monkeypatch,fake)
    plugins=importlib.import_module('rank42.plugins')
    manage_store=importlib.import_module('rank42.manage_store')
    monkeypatch.setattr(manage_store,'active_campaign',lambda db: None)
    monkeypatch.setattr(
        plugins,
        'apply_search_command_features',
        lambda root, db, command, **kwargs: (list(command), []),
    )
    monkeypatch.setattr(common,'enqueue_job',lambda *a,**k: 77)
    ctx=types.SimpleNamespace(db_path='rank42.db',project_root='.')
    jid=common.launch(ctx,None,kind='test',label='demo',command=['python','-V'])
    assert jid==77
    assert fake.session_state['_rh_job_just_started']==77
