"""Cross-check actual service completion independently of network drain.

Read-only result interpretation, shared by both native ASTRA service frontends.
The application here is the declared finite service observation, not a full LLM
request or GPU-visible completion. Failed runs never enter success rankings.
"""
from .scaleout_topology import require


def _time(value, label):
    require(type(value) is int and value >= 0, 'invalid '+label+' timestamp')
    return value


def _graphs(rows, label, stop):
    require(isinstance(rows, list) and bool(rows), 'missing '+label+' graphs')
    result = {}
    for row in rows:
        identity = row.get('graph_id')
        require(isinstance(identity, str) and identity and identity not in result,
                'missing or duplicate '+label+' graph')
        start = _time(row.get('start_ns'), label+' graph start')
        finish = _time(row.get('finished_ns'), label+' graph finish')
        require(start <= finish <= stop, label+' graph outside network interval')
        result[identity] = start, finish
    return result


def service_terminal(result, frontend=None, *, require_complete=False, config=None):
    """Return explicit nanosecond times; reject contradictory success evidence.

    A missing result/frontend yields unavailable timing, never a guessed
    application time. A failed native run has no successful application or
    drain time. Existing ``finish_ns`` remains the caller's legacy stop field.
    """
    timing = dict(timing_status='unavailable', timing_reason='missing_native_result',
                  application_finished_ns=None, network_drained_ns=None,
                  network_stopped_ns=None, post_application_drain_ns=None)
    if result is None:
        require(not require_complete, 'missing native service result')
        return timing
    stop = _time(result.get('finish_ns'), 'network stop')
    require(type(result.get('passed')) is bool and type(result.get('drained')) is bool,
            'missing native service success/drain state')
    timing['network_stopped_ns'] = stop
    if not result['passed']:
        require(not require_complete, 'native service failed')
        failure = result.get('failure') or {}
        timing.update(timing_status='failed', timing_reason=failure.get('reason', 'native_run_failed'))
        return timing
    require(result['drained'], 'successful service did not drain')
    timing['network_drained_ns'] = stop
    service = result.get('service', {})
    require(service.get('service_finished') is True and type(service.get('active_graphs')) is int and
            service['active_graphs'] == 0, 'successful service has no native terminal state')
    if frontend is None:
        require(not require_complete, 'missing frontend terminal evidence')
        timing['timing_reason'] = 'missing_frontend_evidence'
        return timing
    from .whole_call import configuration as whole_configuration
    whole=whole_configuration(config) if config is not None else None
    require(('whole_call_replication' in frontend)==(whole is not None),'whole call timing requires explicit matching input')
    if whole is not None:
        require(frontend['whole_call_replication'].get('configuration')==whole,'whole result configuration differs from input')
    history = _graphs(frontend.get('history'), 'frontend', stop)
    native = _graphs(service.get('waves'), 'native', stop)
    require(history == native, 'frontend/native graph completion differs')
    finish = max(end for _, end in history.values())
    transcript = frontend.get('transcript')
    require(isinstance(transcript, list) and bool(transcript), 'missing service control transcript')
    terminal = [i for i, row in enumerate(transcript) if row.get('reply', {}).get('service_finished') is True]
    require(terminal == [len(transcript)-1], 'missing, repeated or non-final terminal COMMIT')
    event, reply = transcript[-1]['event'], transcript[-1]['reply']
    require(event.get('type') == 'DECIDE' and reply.get('type') == 'COMMIT' and
            type(event.get('decision_id')) is int and event['decision_id'] >= 0 and
            reply.get('decision_id') == event['decision_id'] and type(reply.get('decision_id')) is int,
            'terminal control identity differs')
    require(_time(event.get('now_ns'), 'terminal event') == finish and
            _time(reply.get('now_ns'), 'terminal reply') == finish,
            'terminal COMMIT differs from actual graph completion')
    require(reply.get('submissions') == [], 'terminal COMMIT submits more work')
    require(not event.get('completed') or all(
        row.get('graph_id') in history and row.get('finished_ns') == history[row['graph_id']][1]
        for row in event['completed']), 'terminal completed graph differs')
    if 'whole_call_replication' in frontend:
        from .whole_call import logical_finish
        logical=logical_finish(frontend['whole_call_replication'],history)
        require(logical<=finish,'whole client result after attempts')
        timing.update(timing_scope=('delayed_whole_finite_segment/1' if whole['policy']=='delayed_complete_segment/1'
                                   else 'eager_whole_finite_segment/1'),attempts_finished_ns=finish)
        finish=logical
    timing.update(timing_status='complete', timing_reason=None, application_finished_ns=finish,
                  post_application_drain_ns=stop-finish)
    return timing
