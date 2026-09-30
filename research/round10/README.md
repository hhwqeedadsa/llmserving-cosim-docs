# Round 10: long-window network contention

This package contains the reviewable 2026-09-30 artifacts for the 1.1-ms ns-3 long-window replay.

- `report.md`: Chinese report with mechanism, experiment, findings, and evidence boundaries.
- `long_window_packet_events.csv`: 1,302 exact simulated core packet intervals.
- `long_window_bandwidth_timeseries.csv`: per-flow 5-µs core bandwidth bins.
- `long_window_queue_timeseries.csv`: 1-µs core queue samples and raw peaks.
- `long_window_task_windows.csv`: first-packet, task-complete, and last-ACK times.
- `long_window_wave_summary.csv`: ten-wave payload and completion summary.
- `zoom_w1_w2_bandwidth_timeseries.csv`, `zoom_w1_w2_queue_timeseries.csv`: 0.25-µs zoom of the equal-byte same/reverse-direction peaks.
- `evidence_ledger.csv`: what each artifact can and cannot support.
- `experiments/`: traffic schedule and topology/routing inputs.
- `../../source/_static/round10/zoomed-w1-w2-contention-peaks.svg`: enlarged W1/W2 bandwidth, queue, and packet schedule comparison.

The release schedule is a controlled synthetic replay. Results marked `ns3-simulated/trace-derived` are not hardware counters or production traffic measurements.
