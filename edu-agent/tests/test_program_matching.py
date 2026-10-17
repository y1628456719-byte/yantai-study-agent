from backend.services import recommender, school_data


def test_missing_mlis_catalog_does_not_borrow_engineering_program():
    result = recommender.recommend({'profile': {'major': '图书情报', 'target_score': 220}})
    assert [r['school_id'] for r in result['recommendations']] == ['jlu']
    item = result['recommendations'][0]
    assert item['metrics']['score_line_total'] == {'value': 230, 'year': 2026, 'source_index': 0}
    assert item['metrics']['tier'] is None
    assert '历史复试线' in item['tier_note']
    assert '招生' in item['unknowns'][0]
    assert item['sources'][0]['url'] in [r['url'] for r in school_data.get('jlu')['records'] if r.get('category') == 'score_line']
    assert not any(word in str(item['fit_reasons']) for word in ('通信', '控制', '工学', '315', '300 分'))
    assert not item['metrics'].get('admission_scale')
    assert not item['metrics'].get('tuition')
    assert not item['metrics'].get('exam_subjects')
    assert len(result['excluded']) == 2
    assert all('不表示该校不招收' in r['reason'] for r in result['excluded'])


def test_unsupported_direction_has_no_fabricated_school_fit():
    result = recommender.recommend({'profile': {'major': '没有覆盖的测试方向', 'target_score': 220}})
    assert result['recommendations'] == []
    assert len(result['excluded']) == len(school_data.school_ids())


def test_supported_catalog_direction_preserves_sourced_candidates():
    result = recommender.recommend({'profile': {'major': '通信', 'target_score': 330}})
    assert len(result['recommendations']) == 3
    for item in result['recommendations']:
        assert item['sources']
        assert any('通信' in str(reason) for reason in item['fit_reasons'])
