"""Offline history specification, reconstructed only from source observations."""
from .scaleout_topology import require


class HistoryReplay:
    def __init__(self, config):
        self.config=config;self.entries={};self.serial={}
        self.hits=self.misses=self.expirations=self.evictions=self.updates=0

    def expire(self, source, now):
        expired=[key for key,value in self.entries.items() if key[0]==source and now-value[1]>=self.config['max_age_ns']]
        for key in expired:del self.entries[key]
        self.expirations+=len(expired)

    def start(self, key, now, default):
        self.expire(key[0],now)
        if key not in self.entries:self.misses+=1;return default,False
        self.hits+=1;self.serial[key[0]]=self.serial.get(key[0],0)+1;rto,evidence,_=self.entries[key]
        self.entries[key]=(rto,evidence,self.serial[key[0]])
        return max(default,rto),True

    def observe(self, key, now, rto):
        self.expire(key[0],now)
        local=[k for k in self.entries if k[0]==key[0]]
        if key not in self.entries and len(local)==self.config['max_entries_per_source']:
            victim=min(local,key=lambda k:self.entries[k][2]);del self.entries[victim];self.evictions+=1
        previous=self.entries.get(key,(0,0,0))[0]
        self.serial[key[0]]=self.serial.get(key[0],0)+1;self.updates+=1
        self.entries[key]=(max(previous,rto),now,self.serial[key[0]])

    def check(self, actual):
        expected=[dict(source=key[0],destination_ipv4=key[1],rto_ns=value[0],evidence_ns=value[1],access_serial=value[2])
                  for key,value in sorted(self.entries.items())]
        require(actual['entries']==expected,'recovery history entries differ from source-local replay')
        for name in ('hits','misses','expirations','evictions','updates'):
            require(actual[name]==getattr(self,name),'recovery history counters differ from source-local replay: '+name)
