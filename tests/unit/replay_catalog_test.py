from copy import deepcopy
import unittest
from moe_ub_cosim.replay_catalog import REQUEST_IDS,REQUEST_SPEC,QUEUE_POLICY,QUEUE_SPEC,rebind


class ReplayCatalogTest(unittest.TestCase):
    def values(self):
        old=dict(ub_revision='previous',attributes={})
        for name,kind in [('ns3::UbLdstThread::StoreRequestSize','STORE request'),('ns3::UbLdstThread::LoadResponseSize','LOAD response')]:
            old['attributes'][name]=dict(default='512',value_type='ns3::UintegerValue',help=f'{kind} payload size in bytes; rounded to nearest 64B * 2^n at Init().')
        new=deepcopy(old);new['ub_revision']='current';new['attributes'][REQUEST_IDS]=deepcopy(REQUEST_SPEC)
        for key in old['attributes']:new['attributes'][key]['help']=old['attributes'][key]['help'].replace('to nearest','up to')
        settings=dict(catalog=old,profiles={k:dict(programming_model='URMA',operation='WRITE',transport='RTP') for k in ('dispatch','combine')},
                      attributes={k:'1024' for k in old['attributes']},origins=dict(attributes={k:[dict(source='explicit',value='1024')] for k in old['attributes']}))
        return settings,new

    def test_only_inactive_addition_and_help_changes(self):
        settings,catalog=self.values();previous=deepcopy(settings);r=rebind(settings,catalog)
        self.assertFalse(r['same_parameter_catalog']);self.assertEqual(settings['catalog'],catalog)
        self.assertEqual(settings['attributes'][REQUEST_IDS],'65536')
        for k,v in previous['attributes'].items():self.assertEqual(settings['attributes'][k],v)
        self.assertIsNone(rebind(settings,catalog))

    def test_refuse_active_native_or_any_other_parameter_change(self):
        for mutate in [lambda s,c:s['profiles']['dispatch'].update(programming_model='native_memory',operation='STORE'),
                       lambda s,c:c.update(constants={'new':'incompatible'}),
                       lambda s,c:c['attributes'][REQUEST_IDS].update(default='32'),
                       lambda s,c:c['attributes']['ns3::UbLdstThread::StoreRequestSize'].update(default='1024')]:
            settings,catalog=self.values();mutate(settings,catalog);before=deepcopy(settings)
            with self.assertRaises(ValueError):rebind(settings,catalog)
            self.assertEqual(settings,before)

    def test_fifo_default_addition_preserves_active_native_settings(self):
        settings,catalog=self.values();rebind(settings,catalog)
        settings['profiles']['dispatch'].update(programming_model='native_memory',operation='STORE')
        target=deepcopy(catalog);target['ub_revision']='queue-version';target['attributes'][QUEUE_POLICY]=deepcopy(QUEUE_SPEC)
        original=deepcopy(settings);result=rebind(settings,target)
        self.assertEqual(settings['attributes'][QUEUE_POLICY],'fifo')
        self.assertEqual(result['changes'],[QUEUE_POLICY])
        for k,v in original['attributes'].items():self.assertEqual(settings['attributes'][k],v)
        for key,value in [('default','producer_round_robin'),('help','different'),('value_type','ns3::EnumValue')]:
            bad=deepcopy(target);bad['attributes'][QUEUE_POLICY][key]=value;s=deepcopy(original)
            with self.assertRaises(ValueError):rebind(s,bad)
            self.assertEqual(s,original)


if __name__=='__main__':unittest.main()
