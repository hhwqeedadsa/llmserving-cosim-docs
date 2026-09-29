# Round 09 evidence package

This directory contains the reviewable summaries for the 2026-09-29 model, hardware, trace, and network-contention study.

- `report.md`: complete Chinese report with experiment boundaries and figure captions.
- `llmservingsim_input_sweep.csv`: six request-size runs plus collective counts and logical bytes.
- `network_contention_summary.csv`: 11 controlled ns-3-UB cases.
- `network_contention_tasks.csv`: per-task flow results.
- `model_inventory.csv`, `hardware_profile_inventory.csv`: model and hardware/link provenance.
- `trace_lineage.csv`, `ccl_implementation_ledger.csv`: request-to-packet lineage and CCL code path.
- `huawei_384_mapping.csv`: two candidate logical mappings; all uncalibrated bandwidths remain `TBD`.
- `evidence_ledger.csv`: what each artifact supports and does not support.

The full packet, hop, queue, and port traces remain in the local experiment package because they are substantially larger. Results labeled `ns3-simulated` are simulation outputs, not hardware measurements. The GPU profile is RTX PRO 6000 Blackwell Server Edition, not A100.
