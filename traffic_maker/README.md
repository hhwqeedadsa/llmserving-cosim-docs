# Collective Communication Traffic Maker

A tool suite for generating collective communication traffic patterns and schedules for simulation and analysis.

## Overview

The traffic maker generates `traffic.csv` files that describe RDMA operations for various collective communication algorithms (AllReduce, All-to-All). It supports multiple algorithm implementations and allows fine-grained control over communication parameters.

The `build_traffic.py` entry point can generate traffic patterns corresponding to common HCCL collective operators (for example, AllReduce variants and All-to-All variants), making it easy to simulate typical HCCL workloads.

## Quick Start

### Build Traffic (Main CLI)

The primary entry point is `build_traffic.py`, which generates a traffic schedule for a given collective algorithm.

#### Basic Usage

```bash
cd /path/to/traffic_maker
python build_traffic.py -n 8 -c 4 -b 1MB -a ar_ring
```

#### Arguments

- `-n, --host-count`: Total number of hosts (required)
- `-c, --comm-domain-size`: Communication domain size (hosts per domain) (required)
- `-b, --data-size`: Per-participant total data volume for one collective (B/KB/MB/GB) (required)
  - The algorithm will internally slice/partition this data per phase
- `-a, --algo`: Collective communication algorithm (required)
- `-d, --phase-delay`: Inter-phase delay between phases in nanoseconds (default: 0)
- `-o, --output-dir`: Output root directory (default: `./output`)
- `--scatter-k`: (Only for `a2a_scatter`) Number of pairwise phases to merge (default: 1)
- `--rank-mapping`: Strategy for assigning ranks to communication domains (default: `linear`)
  - `linear`: Consecutive ranks per domain: `[0,1,2,3][4,5,6,7]...`
  - `round-robin`: Round-robin distribution: `[0,2,4,6][1,3,5,7]...`

#### Examples

**Ring AllReduce with 8 hosts, 4 per domain, 1MB data:**
```bash
python build_traffic.py -n 8 -c 4 -b 1MB -a ar_ring
```

**NHR AllReduce with 16 hosts, 8 per domain, 512KB data:**
```bash
python build_traffic.py -n 16 -c 8 -b 512KB -a ar_nhr
```

**Recursive Halving/Doubling with 4 hosts, all in one domain, 256KB data:**
```bash
python build_traffic.py -n 4 -c 4 -b 256KB -a ar_rhd
```

**Pairwise All-to-All with 8 hosts, 8 per domain, 1MB data:**
```bash
python build_traffic.py -n 8 -c 8 -b 1MB -a a2a_pairwise
```

**All-to-All Scatter (merge 2 pairwise phases per scatter stage):**
```bash
python build_traffic.py -n 8 -c 8 -b 1MB -a a2a_scatter --scatter-k 2
```

**Ring AllReduce with round-robin rank mapping:**
```bash
python build_traffic.py -n 8 -c 4 -b 1MB -a ar_ring --rank-mapping round-robin
```

#### Output

The tool generates a timestamped folder in the output directory containing:
- `traffic.csv`: CSV file with columns:
  - `taskId`: Unique task ID
  - `sourceNodeId`: Source rank
  - `destNodeId`: Destination rank
  - `dataSize(Byte)`: Data size in bytes
  - `opType`: Operation type (URMA_WRITE)
  - `priority`: Priority level
  - `delay`: Phase delay
  - `phaseId`: Phase ID
  - `dependOnPhases`: Dependency on previous phases

Example output directory: `output/20250212T143022_8_4_1048576_ar_ring_d0/traffic.csv`

---

## Algorithm __main__ Features

Each algorithm module can be run standalone to visualize its schedule and communication pattern. Use `PYTHONPATH=.` and the `-m` flag to ensure proper module import.

### Ring AllReduce

**Command:**
```bash
cd /path/to/traffic_maker && PYTHONPATH=. python -m algorithms.all_reduce.ring -n 4 -b 1024
```

**Arguments:**
- `-n, --rank-size`: Number of ranks (default: 4)
- `-b, --bytes`: Total bytes per rank (default: 1024)

**Output:** Pretty-printed Ring AllReduce schedule with phase-by-phase ops and slice assignments.

**Example:**
```bash
cd /path/to/traffic_maker && PYTHONPATH=. python -m algorithms.all_reduce.ring -n 6 -b 2048
```

---

### NHR (Neighbor Halving Reduction) AllReduce

**Command:**
```bash
cd /path/to/traffic_maker && PYTHONPATH=. python -m algorithms.all_reduce.nhr -n 4 -b 1024
```

**Arguments:**
- `-n, --rank-size`: Number of ranks (default: 4)
- `-b, --bytes`: Total bytes per rank (default: 1024)

**Output:** Pretty-printed NHR schedule with:
- Slice mapping and tree reordering
- ReduceScatter and AllGather phases
- Per-rank send/receive operations and byte counts

**Example:**
```bash
cd /path/to/traffic_maker && PYTHONPATH=. python -m algorithms.all_reduce.nhr -n 8 -b 4096
```

---

### Recursive Halving/Doubling (RHD) AllReduce

**Command:**
```bash
cd /path/to/traffic_maker && PYTHONPATH=. python algorithms/all_reduce/recursive_hd.py -n 8 -b 4096 2>/dev/null
```

**Arguments:**
- `-n, --rank-size`: Number of ranks (default: 8)
- `-b, --bytes`: Total bytes per rank (default: 4096)

**Output:** Pretty-printed RHD schedule with:
- Block (slice) sizes and active rank mapping
- FoldIn phase (if applicable)
- ReduceScatter and AllGather phases
- FoldOut phase (if applicable)

**Example:**
```bash
cd /path/to/traffic_maker && PYTHONPATH=. python -m algorithms.all_reduce.recursive_hd -n 16 -b 8192
```

---

### Pairwise All-to-All

**Command:**
```bash
cd /path/to/traffic_maker && PYTHONPATH=. python -m algorithms.all_to_all.pairwise -n 4 -b 1024
```

**Arguments:**
- `-n, --rank-size`: Number of ranks (default: 4)
- `-b, --bytes`: Total bytes per rank (default: 1024)

**Output:** Pretty-printed pairwise All-to-All schedule showing:
- Each phase with all-to-all communication pairs
- Per-rank send/receive operations

**Example:**
```bash
cd /path/to/traffic_maker && PYTHONPATH=. python -m algorithms.all_to_all.pairwise -n 6 -b 2048
```

---

### All-to-All Variable (AlltoAllv)

**Command:**
```bash
cd /path/to/traffic_maker && PYTHONPATH=. python -m algorithms.all_to_all.pairwisev -n 4 -u 1
```

**Arguments:**
- `-n, --rank-size`: Number of ranks (default: 4)
- `-u, --unit-bytes`: Unit byte size for each send/recv element (default: 1)

**Output:** Pretty-printed AlltoAllv schedule with:
- Variable-size send/receive patterns
- Offset and remote offset calculations
- Local copy phase (if applicable)
- Pairwise communication phases

**Example (uniform all-to-all with 4 ranks, 256 bytes per element):**
```bash
cd /path/to/traffic_maker && PYTHONPATH=. python -m algorithms.all_to_all.pairwisev -n 4 -u 256
```

---

### Unified Algorithm Entry Point

**Command:**
```bash
cd /path/to/traffic_maker && PYTHONPATH=. python -m algorithms.algos -a ar_ring -n 4 -b 1024
```

**Arguments:**
- `-a, --algo`: Algorithm name (default: `ar_ring`)
  - Choices: `ar_ring`, `ar_nhr`, `ar_rhd`, `a2a_pairwise`, `a2a_scatter`
- `-n, --comm-size`: Communicator size (default: 4)
- `-b, --bytes`: Total bytes per rank (default: 1024)
- `--scatter-k`: (For `a2a_scatter`) Number of pairwise phases to merge (default: 1)

**Output:** Pretty-printed logic communication pairs by phase showing:
- Phase ID, number of pairs, and total phase bytes
- Per-pair sender, receiver, and byte count
- Overall phase and byte statistics

**Examples:**

Show default Ring AllReduce schedule:
```bash
cd /path/to/traffic_maker && PYTHONPATH=. python -m algorithms.algos
```

Show NHR AllReduce with 8 ranks and 4MB total per rank:
```bash
cd /path/to/traffic_maker && PYTHONPATH=. python -m algorithms.algos -a ar_nhr -n 8 -b 4194304
```

Show All-to-All Scatter with k=3 (merge 3 pairwise phases per scatter stage):
```bash
cd /path/to/traffic_maker && PYTHONPATH=. python -m algorithms.algos -a a2a_scatter -n 8 -b 2097152 --scatter-k 3
```


---

## Rank Mapping Strategies

The `--rank-mapping` option controls how ranks are assigned to communication domains. This is useful for simulating different network topologies and their effects on collective communication performance.

### Linear Mapping (Default)

**Description:** Consecutive ranks per domain.

**Pattern (with 8 hosts, 4 per domain):**
```
Domain 0: [0, 1, 2, 3]
Domain 1: [4, 5, 6, 7]
```

**Use case:** Simulates contiguous rank allocation, typical in many HPC schedulers.

### Round-Robin Mapping

**Description:** Ranks distributed round-robin across domains.

**Pattern (with 8 hosts, 4 per domain):**
```
Domain 0: [0, 2, 4, 6]
Domain 1: [1, 3, 5, 7]
```

**Use case:** Simulates interleaved rank allocation to balance network load or simulate specific scheduling policies.

**Example:**
```bash
python build_traffic.py -n 8 -c 4 -b 1MB -a ar_ring --rank-mapping round-robin
```
