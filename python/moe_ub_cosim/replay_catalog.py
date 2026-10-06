"""Narrow, auditable migration of inactive native settings in old WRITE bundles."""
from copy import deepcopy

from .plan import require

REQUEST_IDS='ns3::UbLdstApi::RequestIdCount'
REQUEST_SPEC=dict(default='65536',help='Maximum concurrent native requests per initiator; bounded by the 16-bit wire ID.',value_type='ns3::UintegerValue')


def rebind(settings,catalog):
    previous=settings['catalog']
    if previous==catalog:return None
    same=lambda a,b:{k:v for k,v in a.items() if k!='ub_revision'}=={k:v for k,v in b.items() if k!='ub_revision'}
    exact=same(previous,catalog);changes=[]
    if not exact:
        require(all(p.get('programming_model')=='URMA' and p.get('operation')=='WRITE' and p.get('transport')=='RTP'
                    for p in settings['profiles'].values()),'Replay catalog migration only applies to inactive native settings in WRITE/RTP')
        adjusted=deepcopy(previous)
        require(REQUEST_IDS not in adjusted['attributes'] and catalog['attributes'].get(REQUEST_IDS)==REQUEST_SPEC and
                REQUEST_IDS not in settings['attributes'] and REQUEST_IDS not in settings['origins']['attributes'],
                'Replay backend parameter catalog is incompatible')
        adjusted['attributes'][REQUEST_IDS]=deepcopy(REQUEST_SPEC);changes.append(REQUEST_IDS)
        for name,kind in [('ns3::UbLdstThread::StoreRequestSize','STORE request'),('ns3::UbLdstThread::LoadResponseSize','LOAD response')]:
            old=adjusted['attributes'][name];new=catalog['attributes'][name]
            expected=dict(old,help=f'{kind} payload size in bytes; rounded up to 64B * 2^n at Init().')
            require(old['help']==f'{kind} payload size in bytes; rounded to nearest 64B * 2^n at Init().' and expected==new,
                    'Replay changed an unapproved native parameter definition')
            adjusted['attributes'][name]=deepcopy(new);changes.append(name+'.help')
        require(same(adjusted,catalog),'Replay backend parameter catalog has other incompatible changes')
        settings['attributes'][REQUEST_IDS]=REQUEST_SPEC['default']
        settings['origins']['attributes'][REQUEST_IDS]=[dict(source='legacy_write_replay_inactive_native_default',value=REQUEST_SPEC['default'])]
    settings['catalog']=deepcopy(catalog)
    return dict(old_revision=previous['ub_revision'],new_revision=catalog['ub_revision'],same_parameter_catalog=exact,
        compatibility_rule='same_catalog' if exact else 'v03_write_inactive_native_parameters/1',changes=changes,
        behavior_equivalence='must_be_checked_by_execution')
