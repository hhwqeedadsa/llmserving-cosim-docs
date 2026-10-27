"""Public API example; untimed state/feedback checks, no network execution."""
import argparse
import json
from pathlib import Path

from moe_ub_cosim.rc_reference import RcRequester, RcResponder, UnsupportedTransition


def run(lose_feedback):
    requester = RcRequester(mtu=256)
    responder = RcResponder((41).to_bytes(8, 'little') + bytes(768), mtu=256)
    requester.post('write', 8, data=b'X'*600)
    requester.post('fetch_add', 0, addend=5)
    packets = requester.send() + requester.send()
    rejected = None
    for packet in packets:
        response = responder.receive(packet)
        if response and not lose_feedback:
            requester.receive(response)
    if lose_feedback:
        reply = responder.receive(requester.retransmit()[0])
        try:
            requester.receive(reply)
        except UnsupportedTransition as error:
            rejected = str(error)
        held_before_atomic_reply = requester.snapshot()['source_held']
        # A caller-declared retry of the original atomic returns the cached old
        # value. There is no automatic timer or new ACK of the atomic response.
        requester.receive(responder.receive(packets[-1]))
    else:
        held_before_atomic_reply = []
    return dict(requester=requester.snapshot(),target_value=int.from_bytes(responder.memory[:8],'little'),
                atomic_applications=sum(e['kind']=='fetch_add' for e in responder.effects),
                ambiguous_plain_ack_rejected=rejected,source_held_before_atomic_reply=held_before_atomic_reply)


def report():
    return dict(schema_version='moe-rc-reference-example/1',healthy=run(False),lost_feedback=run(True),
                network_executed=False,hardware_calibrated=False,exact_RXE_equivalence=False)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    with args.output.open('x') as target:
        json.dump(report(),target,sort_keys=True,indent=2)
        target.write('\n')
