from rank42 import system_metrics as sm


def test_system_metrics_thresholds_use_status_semantics():
    assert sm._level_for_usage(10) == 'success'
    assert sm._level_for_usage(80) == 'warning'
    assert sm._level_for_usage(95) == 'danger'
    assert sm._level_for_temp(60) == 'success'
    assert sm._level_for_temp(82) == 'warning'
    assert sm._level_for_temp(92) == 'danger'
    assert sm._worst_level('success', 'danger') == 'danger'


def test_system_metrics_collection_is_normalized(monkeypatch):
    monkeypatch.setattr(sm, '_cpu_percentages', lambda: (67.2, 2.4))
    monkeypatch.setattr(sm, '_read_hwmon_temp', lambda: 78.0)
    monkeypatch.setattr(sm, '_read_meminfo', lambda: (71.0, 9.2))
    monkeypatch.setattr(sm, '_gpu_snapshot', lambda: {
        'percent': 94.0,
        'temp_c': 72.0,
        'used_gib': 7.3,
        'total_gib': 8.0,
    })
    rows=sm.collect_system_metrics()
    assert [row['key'] for row in rows] == ['cpu','gpu','ram','iowait']
    assert rows[0]['value'] == '67%'
    assert rows[0]['detail'] == '78°C'
    assert rows[1]['value'] == '94%'
    assert rows[1]['detail'] == '72°C · 7.3/8 GB'
    assert rows[2]['detail'] == '9.2 GB free'
    assert rows[3]['label'] == 'I/O Wait'
    assert rows[3]['value'] == '2.4%'
    assert rows[3]['show_bar'] is False
    assert rows[0]['show_bar'] is True
    assert rows[1]['show_bar'] is True
    assert rows[2]['show_bar'] is True
    assert rows[1]['level'] == 'warning'


def test_system_metrics_gracefully_handles_missing_gpu_and_temperature(monkeypatch):
    monkeypatch.setattr(sm, '_cpu_percentages', lambda: (None, None))
    monkeypatch.setattr(sm, '_read_hwmon_temp', lambda: None)
    monkeypatch.setattr(sm, '_read_meminfo', lambda: (None, None))
    monkeypatch.setattr(sm, '_gpu_snapshot', lambda: None)
    rows=sm.collect_system_metrics()
    gpu=next(row for row in rows if row['key']=='gpu')
    cpu=next(row for row in rows if row['key']=='cpu')
    assert gpu['detail'] == 'not detected'
    assert cpu['detail'] == 'temp unavailable'
