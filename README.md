# ns-3 UB Tools

A collection of network simulation tools for ns-3, focusing on cluster network topology generation and traffic analysis.

## Components

### Network Topology Tools
- `net_sim_builder.py` - Network simulation builder
- `topo_plot.py` - Topology visualization tool
- `user_topo_*.py` - Various topology examples (2-layer Clos, 4x4 2DFM, etc.)

### Traffic Generation
- `traffic_maker/all2allv_maker.py` - All-to-all traffic pattern generator
- `traffic_maker/xccl_rdma_maker.py` - XCCL RDMA traffic generator

### Trace Analysis
- `trace_analysis/parse_trace.py` - Main trace parsing script
- `trace_analysis/task_statistics.py` - Task statistics analyzer
- `trace_analysis/cal_throughput.py` - Throughput calculator

## Usage

### Running Trace Analysis
```bash
python3 trace_analysis/parse_trace.py <trace_file.csv> [true/false]
```

### Generating Topologies
```bash
python3 user_topo_example.py
```

## Requirements
- Python 3.x
- pandas
- Required packages will be auto-installed when running parse_trace.py

## License
Open source project for network simulation research.
