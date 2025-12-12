import math
from typing import List, Tuple

from .all_reduce.ring import build_ar_ring_schedule
from .all_reduce.nhr import build_nhr_schedule
from .all_reduce.recursive_hd import build_ar_rhd_schedule
from .schedule import Schedule

# (sender_rank, receiver_rank, byte_count)
LogicCommPair = Tuple[int, int, int]
# A schedule is a list of phases, each phase is a list of `LogicCommPair`s.
LogicCommPairsByPhase = List[List[LogicCommPair]]


AVAILABLE_ALGOS = ["ar_ring", "ar_nhr", "ar_rhd", "a2a_pairwise", "a2a_scatter"]


def print_logic_comm_pairs_by_phase(logic_comm_pairs: LogicCommPairsByPhase, *, title: str | None = None) -> None:
    """Print per-phase (sender, receiver, bytes) pairs and traffic volume."""
    if title:
        print(title)

    total_bytes_all = 0
    for phase_id, phase_pairs in enumerate(logic_comm_pairs):
        phase_bytes = sum(int(p[2]) for p in phase_pairs)
        total_bytes_all += phase_bytes
        print(f"\n[phase {phase_id}] pairs={len(phase_pairs)} phase_bytes={phase_bytes}")
        for sender, receiver, trans_byte in phase_pairs:
            print(f"  ({sender} -> {receiver}, {int(trans_byte)} bytes)")

    print(f"\n[total] phases={len(logic_comm_pairs)} total_bytes={total_bytes_all}")


def schedule_to_logic_comm_pairs(schedule: Schedule) -> LogicCommPairsByPhase:
    """Convert a Schedule object to LogicCommPairsByPhase format."""
    result: LogicCommPairsByPhase = []
    for phase in schedule.phases:
        phase_pairs: List[LogicCommPair] = []
        for rank_op in phase.rank_ops:
            # Each rank sends to send_to with tx_bytes
            pair: LogicCommPair = (rank_op.rank, rank_op.send_to, rank_op.tx_bytes)
            phase_pairs.append(pair)
        result.append(phase_pairs)
    return result


def generate_ar_ring_logic_comm_pairs(n_comm_size: int, n_byte: int) -> LogicCommPairsByPhase:
    """Ring AllReduce schedule (logical ranks) - delegates to ring.py."""
    schedule = build_ar_ring_schedule(int(n_comm_size), int(n_byte))
    return schedule_to_logic_comm_pairs(schedule)


def generate_ar_RHD_logic_comm_pairs(n_comm_size: int, n_byte: int) -> LogicCommPairsByPhase:
    """Recursive halving/doubling (RHD) AllReduce - delegates to recursive_hd.py."""
    schedule = build_ar_rhd_schedule(int(n_comm_size), int(n_byte))
    return schedule_to_logic_comm_pairs(schedule)


def generate_ar_NHR_logic_comm_pairs(n_comm_size: int, n_byte: int) -> LogicCommPairsByPhase:
    """NHR AllReduce (ReduceScatter + AllGather) - delegates to nhr.py."""
    schedule = build_nhr_schedule(int(n_comm_size), int(n_byte))
    return schedule_to_logic_comm_pairs(schedule)


def generate_a2a_pairwise_logic_comm_pairs(n_comm_size: int, n_byte: int) -> LogicCommPairsByPhase:
    """Pairwise All2All schedule (logical ranks)."""
    n_total_phase = int(n_comm_size) - 1
    logic_rank_table = [i for i in range(int(n_comm_size))]
    pw_logic_comm_pairs: LogicCommPairsByPhase = []
    for phase_id in range(n_total_phase):
        phase_comm_pair: List[LogicCommPair] = []
        phase_byte = n_byte / n_comm_size
        send_gap = phase_id + 1
        for r in logic_rank_table:
            send_pair: LogicCommPair = (r, (r + send_gap) % len(logic_rank_table), int(phase_byte))
            phase_comm_pair.append(send_pair)
        pw_logic_comm_pairs.append(phase_comm_pair)
    return pw_logic_comm_pairs


def generate_a2a_scatter_logic_comm_pairs(n_comm_size: int, n_byte: int, k: int) -> LogicCommPairsByPhase:
    """All2All scatter schedule: merge every k pairwise phases into one phase."""
    # Start with the base pairwise All-to-All phases, then merge consecutive blocks of k stages
    pw_logic_comm_pairs = generate_a2a_pairwise_logic_comm_pairs(n_comm_size, n_byte)
    n_phases = len(pw_logic_comm_pairs)
    out: LogicCommPairsByPhase = []
    for i in range(0, n_phases, int(k)):
        merged: List[LogicCommPair] = []
        for stage in pw_logic_comm_pairs[i : i + int(k)]:
            merged.extend(stage)
        out.append(merged)
    return out


def generate_logic_comm_pairs(*, algo: str, comm_size: int, comm_bytes: int, scatter_k: int = 1) -> LogicCommPairsByPhase:
    """Unified entry: generate per-phase logical comm pairs for an algo."""
    if algo not in AVAILABLE_ALGOS:
        raise ValueError(f"Unsupported algo: {algo}. Supported: {AVAILABLE_ALGOS}")

    if algo == "ar_ring":
        return generate_ar_ring_logic_comm_pairs(comm_size, comm_bytes)
    if algo == "a2a_pairwise":
        return generate_a2a_pairwise_logic_comm_pairs(comm_size, comm_bytes)
    if algo == "a2a_scatter":
        if not (1 <= int(scatter_k) < int(comm_size)):
            raise ValueError("a2a_scatter requires scatter_k in range [1, comm_size)")
        return generate_a2a_scatter_logic_comm_pairs(comm_size, comm_bytes, int(scatter_k))
    if algo == "ar_nhr":
        return generate_ar_NHR_logic_comm_pairs(comm_size, comm_bytes)
    if algo == "ar_rhd":
        return generate_ar_RHD_logic_comm_pairs(comm_size, comm_bytes)

    raise ValueError(f"Unsupported algo: {algo}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Generate sample logic comm pairs from an algorithm")
    parser.add_argument("-a", "--algo", choices=AVAILABLE_ALGOS, default="ar_ring",
                        help="Algorithm to build sample comm pairs for")
    parser.add_argument("-n", "--comm-size", type=int, default=4,
                        help="Logical communicator size to simulate")
    parser.add_argument("-b", "--bytes", type=int, default=1024,
                        help="Total bytes per rank to distribute")
    parser.add_argument("--scatter-k", type=int, default=1,
                        help="(a2a_scatter only) number of pairwise phases to merge")
    args = parser.parse_args()

    pairs = generate_logic_comm_pairs(
        algo=args.algo,
        comm_size=int(args.comm_size),
        comm_bytes=int(args.bytes),
        scatter_k=int(args.scatter_k),
    )
    print_logic_comm_pairs_by_phase(pairs, title=f"Sample {args.algo} comm pairs")
