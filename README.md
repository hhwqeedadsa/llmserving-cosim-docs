# ns-3 UB Tools

Utilities for ns-3-based network simulation, including topology generation/visualization, traffic generation, and trace analysis.

## Components

### Topology
- `net_sim_builder.py` — Network simulation builder.
- `topo_plot.py` — Topology visualization.
- `user_topo_*.py` — Example topologies (e.g., 2-layer Clos, 4x4 2DFM).

### Traffic Generation
- `traffic_maker/all2allv_maker.py` — All-to-all(-v) traffic generator.
- `traffic_maker/xccl_rdma_maker.py` — XCCL RDMA traffic generator.

### Trace Analysis
- `trace_analysis/parse_trace.py` — Orchestrates trace parsing.
- `trace_analysis/task_statistics.py` — Task-level statistics.
- `trace_analysis/cal_throughput.py` — Link throughput calculation.

## Installation

Prerequisite: Python 3.10+ recommended.

Install third-party dependencies via requirements file:

```bash
python3 -m pip install --user -r requirements.txt
```

Dependencies (for reference): pandas, numpy, matplotlib, seaborn, networkx.

## Usage

### Trace Analysis
Run the parser on a case directory (the directory containing `runlog/`, `output/`, and `traffic.csv`). The optional flag controls test vs output subdirectory.

```bash
python3 trace_analysis/parse_trace.py <case_dir> [true|false]
```

This invokes `task_statistics.py` and `cal_throughput.py` using the same Python interpreter.

### Topology Visualization
```bash
python3 topo_plot.py -i <case_dir>
```

### Topology Examples
```bash
python3 user_topo_example.py
```

## License
Open-source utilities for network simulation research.
