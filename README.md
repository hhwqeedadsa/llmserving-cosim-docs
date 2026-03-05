# ns-3-ub-tools

This submodule provides the **Python toolchain** for `scratch/` cases:
- Generate topology and transport configuration CSVs.
- Generate workload definitions (`traffic.csv`).
- Visualize topologies.
- Post-process `runlog/` into summary CSVs.

## Components

### Topology
- `net_sim_builder.py` — generates the following files under `./output/<timestamp>/` by default:
	- `node.csv`
	- `topology.csv`
	- `routing_table.csv` (written by `gen_route_table()` when `write_file=True`, which is the default)
	- `transport_channel.csv` (built from the generated routes and configured priorities)

	Generation order (as used in the example scripts): call `build_graph_config()` → `gen_route_table()` → `config_transport_channel()` → `write_config()`.
- `user_topo_example.py`, `user_topo_2layer_clos.py`, `user_topo_4x4_2DFM.py` — example topology definitions that drive `net_sim_builder.py`; they generate the same CSV set above.
- `topo_plot.py` — reads `node.csv` + `topology.csv` from the input directory and writes:
	- `network_topology.png` (always)
	- `network_topology.pdf` (optional via `--pdf`)

### Traffic generation
The `traffic_maker/build_traffic.py` tool  [（README）](./traffic_maker/README.md) can generate traffic patterns corresponding to common HCCL collective operators (for example, AllReduce and All-to-All), which makes it straightforward to simulate typical HCCL workloads.

- `traffic_maker/build_traffic.py` — writes a timestamped folder under `./output/` (or `--output-dir`), containing:
  - `traffic.csv`
- `traffic_maker/all2allv_maker.py` — writes to `output/<timestamp>/` by default:
  - `traffic.csv`
  - `traffic_matrix.png`
  - `traffic_matrix.csv`

### Trace analysis
- `trace_analysis/parse_trace.py` — runs the two scripts below using the current Python interpreter.
- `trace_analysis/task_statistics.py` — reads `runlog/TaskTrace_node_*.tr` and `runlog/PacketTrace_node_*.tr` plus the case `traffic.csv`, and writes:
  - `output/task_statistics.csv` (or `test/task_statistics.csv` when the optional flag is `true`)
- `trace_analysis/cal_throughput.py` — reads `runlog/PortTrace_node_*_port_*.tr` and writes:
  - `output/throughput.csv` (or `test/throughput.csv` when the optional flag is `true`)

## Installation

Prerequisite: Python 3.10+.

Install third-party dependencies with `uv` and the requirements file:

```bash
uv venv
source .venv/bin/activate
uv pip install -r requirements.txt
```

Dependencies (for reference): pandas, numpy, matplotlib, seaborn, networkx.

## Usage

### Trace analysis
Run the parser on a case directory. It expects `runlog/` and a case-level `traffic.csv`, and writes results into `output/` by default.

Note: the current scripts treat `case_dir` as a string prefix; pass a path ending with `/`.

```bash
python3 trace_analysis/parse_trace.py <case_dir> [true|false]
```

This invokes `task_statistics.py` and `cal_throughput.py` using the same Python interpreter.

### Topology visualization
```bash
python3 topo_plot.py -i <case_dir>
```

### Topology examples
```bash
python3 user_topo_example.py
```

---

## Related Documentation

| Document | Description |
|----------|-------------|
| [../../README.md](../../README.md) | Project overview and features |
| [../../QUICK_START.md](../../QUICK_START.md) | Quick start guide with examples |
| [../README.md](../README.md) | Scenario configuration file specifications |

---
