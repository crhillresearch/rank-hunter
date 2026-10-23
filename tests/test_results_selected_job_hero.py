from rank42.ui_pages.results_page import _selected_job_metrics


def _job(job_id):
    return {
        'id': job_id,
        'status': 'succeeded',
        'kind': 'search',
        'label': f'Job {job_id}',
        'started_at': '2026-10-23T10:00:00+00:00',
        'created_at': '2026-10-23T10:00:00+00:00',
        'finished_at': '2026-10-23T10:01:30+00:00',
        'pid': None,
    }


def test_results_hero_metrics_follow_selected_job():
    first = dict(_selected_job_metrics(None, _job(1), {
        'candidates_completed': 10,
        'candidates_planned': 20,
        'best_rigorous_lower': 2,
        'rigorous_hits': 1,
    }))
    second = dict(_selected_job_metrics(None, _job(2), {
        'candidates_completed': 40,
        'candidates_planned': 40,
        'best_rigorous_lower': 4,
        'rigorous_hits': 3,
    }))
    assert first['Progress'] == '10/20'
    assert second['Progress'] == '40/40'
    assert first['Best certified'] == '≥ 2'
    assert second['Best certified'] == '≥ 4'
    assert first['Rank growth'] == '1'
    assert second['Rank growth'] == '3'
    assert first['Elapsed'] == second['Elapsed'] == '00:01:30'
