"""Input-only seven-stage references supplied the maximum declared compute."""
import argparse
from copy import deepcopy
from pathlib import Path
from experiments.shared_uplink_choice.predict import estimate
from experiments.readiness_network_choice.prepare import PROFILES, CONDITIONS, load, save


def execute(observed, output):
    output.mkdir()
    plan = load(observed/'plan/plan.json')
    for profile in PROFILES:
        cfg = load(observed/(profile+'.json'))
        vector, = cfg['expert_stage']['durations_ns']
        # This existing arithmetic helper reads expert_ns only for the global
        # phase cost. Actual execution continues to receive the full v2 input.
        view = deepcopy(cfg)
        view['costs']['expert_ns'] = max(vector)
        predictions = {name: estimate(view, plan, load(observed/(name+'-topology/topology.json')))
                       for name in CONDITIONS}
        choices = {}
        for name, prediction in predictions.items():
            prediction['methods']['volume']['estimated_application_ns'] += max(vector)
        for method in ('volume', 'endpoint_phase', 'nic_phase', 'link_phase'):
            values = {name: prediction['methods'][method]['estimated_application_ns']
                      for name, prediction in predictions.items()}
            ties = [name for name in CONDITIONS if values[name] == min(values.values())]
            choices[method] = dict(selected=ties[0], tied_best=ties)
        save(output/(profile+'.json'), dict(profile=profile, expert_durations_ns=vector,
            compute_accounting='maximum supplied vector; no per-rank release schedule',
            predictions=predictions, choices=choices, future_results_read=False,
            fitted_constants=False, hardware_calibrated=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--observed', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    execute(args.observed.resolve(), args.output.resolve())
