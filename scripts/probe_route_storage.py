"""Import all fixed target routes and independently check their source indices."""
import argparse
from array import array
import hashlib
import json
from pathlib import Path
import time

from moe_ub_cosim.host_preflight import target_inputs
from moe_ub_cosim.route_evidence import read_json, select_routes
from moe_ub_cosim.route_records import RouteDomain, RouteUsage


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    out = args.output_dir.resolve(); out.mkdir(parents=True, exist_ok=False)
    config = json.loads(args.input.read_text())
    values, _ = target_inputs(config)
    # Match the existing host import probe: validate the source's declared
    # structure. The capacity fixture has a separate reference-audit label and
    # is not silently renamed into an execution model by an import benchmark.
    manifest = values['manifest']
    model = dict(name=manifest['model']['name'], hidden_size=manifest['model']['hidden_dim'],
                 layers=manifest['layers'])
    selected = [layer['layer_id'] for layer in model['layers'] if layer['kind'] == 'moe']
    started = time.monotonic()
    normalized, records, evidence = select_routes(config['manifest'], values['requests'], [model], selected, compact=True)
    imported = time.monotonic() - started
    assert len(normalized) == 1024 and len(records) == 1024 * 64 * 58
    assert records.storage_counts() == dict(source_records=1024, logical_records=1024*64*58,
                                            expert_storage_bytes=1024*64*58*8*4)
    domain = RouteDomain(normalized, selected); consumed = RouteUsage(domain)
    expected = hashlib.sha256(); actual = hashlib.sha256(); count = 0
    for number, (request, normal, source) in enumerate(zip(values['requests'], normalized, values['manifest']['files'])):
        assert request['file_id'] == source['file_id']
        _, raw = read_json(source['file'])
        prompt = len(raw[0][str(selected[0])])
        for step in range(request['decode_steps']):
            entry = request['first_decode_entry'] + step
            for layer in selected:
                key = str(request['request_id']), prompt + entry - 1, layer
                record = records[key]
                assert record == dict(request_id=key[0], phase='decode', token_index=key[1], layer_id=layer,
                    expert_ids=raw[entry][str(layer)][0], source_file=source['file'], file_id=source['file_id'],
                    source_entry=entry, source_layer_id=layer, source_token_row=0, decode_index=entry-1,
                    source_index_convention='entry_0_prefill_entry_1_first_decode')
                assert record['expert_ids'] == normal['decode_routes'][step][str(layer)]
                expected.update(array('I', raw[entry][str(layer)][0]).tobytes())
                actual.update(array('I', record['expert_ids']).tobytes())
                assert key not in consumed
                consumed.update((key,)); count += 1
        del raw
        if (number + 1) % 128 == 0:
            print('Verified source files:', number + 1, flush=True)
    assert consumed == domain and len(consumed) == len(records) == count
    assert actual.digest() == expected.digest()
    report = dict(passed=True, input=config, files=1024, records=count, import_seconds=imported,
        total_seconds=time.monotonic()-started, storage=records.storage_counts(),
        consumption_bit_bytes=len(consumed.bits), selected_expert_uint32_sha256=actual.hexdigest(),
        expert_hash_byte_order='native_uint32_linux_little_endian', source_files_duplicated=0,
        original_routes_compared_individually=True, source_evidence=evidence['source'],
        route_model_name=model['name'], capacity_reference_label=values['capacity']['model_spec']['name'],
        combined_service_configuration_validated=False,
        target_execution=False, hardware_calibration=False)
    (out / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print('PASS: all target route records match original files and are consumed once')


if __name__ == '__main__':
    main()
