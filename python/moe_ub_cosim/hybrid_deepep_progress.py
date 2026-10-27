"""Caller-declared Dispatch visibility and forwarder admission, never timing.

Each step is a stable logical snapshot; no GPU polling duration or final slot
allocation is inferred. Cross-forwarder action order is caller input only.
"""
from copy import deepcopy
from pathlib import Path

from .deepep_plan import encoded, fields, integer, read, require
from .hybrid_deepep_plan import MAX_INPUT, SCOPE, load_plan


class HybridDispatchProgress:
    def __init__(self, plan):
        self.plan = plan = deepcopy(plan)
        self.channels = {c['id']: c for c in plan['channels']}
        self.transfers = {t['id']: t for t in plan['dispatch']}
        self.visible = set()
        self.published = {name: -1 for name in self.channels}
        self.forwarders = {}
        for c in plan['channels']:
            key = (c['call'], c['target'], c['channel'])
            self.forwarders.setdefault(key, {})[c['source_domain']] = c['id']
        d = plan['domains']
        for key, peers in list(self.forwarders.items()):
            self.forwarders[key] = dict(peers=peers, tail=[0]*d, finished=[False]*d,
                consumed=[0]*d, last=key[2] % d, selected=None, admissions=[])
        self.log = []

    def apply(self, action):
        require(len(self.log) < 16384, 'Hybrid action budget exceeded')
        require(isinstance(action, dict), 'Hybrid action must be an object')
        kind = action.get('kind')
        if kind == 'data_visible':
            fields(action, 'kind transfer', 'Hybrid data action')
            name = action['transfer']
            require(isinstance(name, str) and name in self.transfers and name not in self.visible, 'unknown/repeated Hybrid data')
            self.visible.add(name)
            result = dict(action, status='caller_declared_visible')
        elif kind == 'tail_visible':
            fields(action, 'kind channel publication', 'Hybrid tail action')
            name, index = action['channel'], action['publication']
            require(isinstance(name, str) and name in self.channels, 'unknown Hybrid channel')
            c = self.channels[name]
            integer(index, 0, len(c['publications'])-1, 'Hybrid publication')
            require(index == self.published[name]+1, 'repeated/skipped/out-of-order Hybrid publication')
            publication = c['publications'][index]
            require(all(t in self.visible for t in c['transfers'][:publication['tail']]), 'Hybrid tail before visible data prefix')
            self.published[name] = index
            result = dict(action, status='caller_declared_visible', **publication)
        elif kind == 'forward_step':
            fields(action, 'kind call ingress channel', 'Hybrid forward step')
            for k, maximum in (('call', max(c['call'] for c in self.channels.values())),
                               ('ingress', self.plan['ranks']-1), ('channel', self.plan['channels_per_rank']-1)):
                integer(action[k], 0, maximum, 'Hybrid '+k)
            key = (action['call'], action['ingress'], action['channel'])
            state = self.forwarders[key]
            selected = state['selected']
            if selected is None:
                candidates = [i for i in range(self.plan['domains']) if
                              state['consumed'][i] < state['tail'][i] or not state['finished'][i]]
                require(bool(candidates), 'Hybrid forwarder admission already complete')
                selected = min(candidates, key=lambda i: (i-state['last']-1) % self.plan['domains'])
                state['selected'] = selected
            # The source waits on its selected peer, refreshing all lane tails
            # only when that peer has neither cached data nor cached finish.
            if state['tail'][selected] == state['consumed'][selected] and not state['finished'][selected]:
                for peer, name in state['peers'].items():
                    index = self.published[name]
                    if index >= 0:
                        publication = self.channels[name]['publications'][index]
                        state['tail'][peer] = publication['tail']
                        state['finished'][peer] = publication['finished']
            start = state['consumed'][selected]
            name = state['peers'][selected]
            result = dict(action, source_domain=selected, source_channel=name)
            if state['tail'][selected] == start and not state['finished'][selected]:
                result.update(status='waiting_for_selected_peer', admitted=[])
            else:
                end = min(state['tail'][selected], start+6)
                admitted = self.channels[name]['transfers'][start:end]
                state['consumed'][selected] = end
                state['last'], state['selected'] = selected, None
                state['admissions'].extend(admitted)
                result.update(status='chunk_admitted' if admitted else 'finished_empty_peer', admitted=admitted,
                              domain_channel_slot_begin=start, domain_channel_slot_end=end)
        else:
            raise ValueError('DeepEP plan: unknown Hybrid action kind')
        self.log.append(deepcopy(result))
        return result

    def snapshot(self):
        forwarders = []
        for (call, ingress, channel), state in sorted(self.forwarders.items()):
            complete = all(f and n == tail for f, n, tail in
                           zip(state['finished'], state['consumed'], state['tail']))
            forwarders.append(dict(call=call, ingress=ingress, channel=channel,
                selected_source_domain=state['selected'], cached_tail=list(state['tail']),
                cached_finish=list(state['finished']), consumed=list(state['consumed']),
                admissions=list(state['admissions']), admission_complete=complete))
        return dict(schema_version='moe-hybrid-progress-replay/1', scope=dict(SCOPE),
            actions_are_caller_declarations=True, log=deepcopy(self.log), forwarders=forwarders,
            all_forwarder_admissions_complete=all(s['admission_complete'] for s in forwarders),
            visible_transfers=sorted(self.visible),
            unpublished_channels=sorted(n for n, index in self.published.items()
                                        if index+1 < len(self.channels[n]['publications'])))


def replay(plan_dir, actions_path, output):
    output = Path(output)
    require(not output.exists(), 'Hybrid replay output already exists')
    plan, config = load_plan(plan_dir)
    _, transcript = read(Path(actions_path), MAX_INPUT)
    fields(transcript, 'schema_version actions', 'Hybrid transcript')
    require(transcript['schema_version'] == 'moe-hybrid-progress-actions/1', 'unsupported Hybrid transcript')
    require(isinstance(transcript['actions'], list) and len(transcript['actions']) <= 16384, 'Hybrid action budget exceeded')
    runtime = HybridDispatchProgress(plan)
    for action in transcript['actions']:
        runtime.apply(action)
    result = runtime.snapshot()
    raw = encoded(result)
    require(len(raw) <= config['limits']['max_output_bytes'], 'Hybrid replay byte budget exceeded')
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(raw)
    return result
