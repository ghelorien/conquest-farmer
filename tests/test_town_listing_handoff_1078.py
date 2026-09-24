import pytest

from test_merchant_handoff import town_service


@pytest.mark.parametrize('existing_request', [None, 'merchant-refill:Spiritual:123'])
def test_phoenix_town_grants_receipt_qualified_refill_enough_work_time(
        town_service, monkeypatch, existing_request):
    from conquest.merchants import bridge
    run = town_service
    run.health['embedded_controls']['life']['map_id'] = 1011
    original = bridge.request

    def merchant(body):
        result = original(body)
        if body['action'] == 'status':
            result['characters']['Spiritual'] = {
                'connected': True, 'enabled': False, 'refill': {'enabled': True},
                'qualification': {'foreground_open_booth_listing_1078': True},
                'snapshot': {'inventory': [{'uid': 7}], 'booth': []}}
            if result['handoff_requested'] is None:
                result['handoff_requested'] = existing_request
        return result

    monkeypatch.setattr(bridge, 'request', merchant)
    assert run.run()
    grant = next(c for c in run.calls if c['action'] == 'handoff-grant')
    check = next(c for c in run.calls if c['action'] == 'refill-check')
    assert grant['scope'] == 'listing_1078' and grant['character'] == 'Spiritual'
    assert grant['expires_at'] == 1045  # Allows the guarded 20-second listing admission.
    assert grant['request_id'] == check['request_id']
    assert grant['request_id'].startswith('merchant-refill:Spiritual:')
    if existing_request:
        assert grant['request_id'] == existing_request
    assert run.windows.state()['scope'] == 'listing_1078'
    assert not run.visit.path.exists()
    assert any(c['action'] == 'handoff-release' for c in run.calls)


def test_market_town_keeps_original_visit_budget_for_native_refill(town_service, monkeypatch):
    from conquest.merchants import bridge
    run = town_service
    original = bridge.request

    def merchant(body):
        result = original(body)
        if body['action'] == 'status':
            result['characters']['Dutch'].update(
                refill={'enabled': True},
                qualification={'foreground_open_booth_listing_1078': True},
                snapshot={'inventory': [{'uid': 7}], 'booth': []})
            if result['handoff_requested'] is None:
                result['handoff_requested'] = 'merchant-refill:Dutch:123'
        return result

    monkeypatch.setattr(bridge, 'request', merchant)
    assert run.run()
    grant = next(c for c in run.calls if c['action'] == 'handoff-grant')
    assert grant['scope'] == 'market_visit' and grant['expires_at'] == 1060
    assert 'character' not in grant
    from conquest.discord_notify import read_json
    assert grant['visit_id'] == read_json(run.visit.path)['visit_id']
