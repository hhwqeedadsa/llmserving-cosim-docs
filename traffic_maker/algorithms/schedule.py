from __future__ import annotations

from dataclasses import dataclass
from typing import IO, List


@dataclass(frozen=True)
class RankOp:
    """Per-rank operation within one communication phase."""

    rank: int
    send_to: int
    recv_from: int

    # Block indices are algorithm-defined. For ring/NHR they are slice indices.
    tx_blocks: List[int]
    rx_blocks: List[int]

    # Optional: mapped block indices (e.g. NHR tree reorder)
    tx_mapped: List[int] | None = None
    rx_mapped: List[int] | None = None

    # Optional: byte offsets for each tx/rx block (e.g. AlltoAllV displacements).
    # Length should match tx_blocks/rx_blocks when provided.
    tx_offsets: List[int] | None = None
    rx_offsets: List[int] | None = None

    # Optional: remote byte offsets aligned with tx_blocks/rx_blocks.
    # Example (AlltoAllV):
    # - tx_remote_offsets[i] is the destination offset at send_to.
    # - rx_remote_offsets[i] is the source offset at recv_from.
    tx_remote_offsets: List[int] | None = None
    rx_remote_offsets: List[int] | None = None

    tx_bytes: int = 0
    rx_bytes: int = 0


@dataclass(frozen=True)
class Phase:
    """One phase (one step) of an algorithm schedule."""

    phase: int
    stage: str
    step: int

    delta_rank: int | None
    n_blocks: int
    delta_block_index: int | None

    rank_ops: List[RankOp]


@dataclass(frozen=True)
class Schedule:
    """A full algorithm schedule."""

    algo: str
    rank_size: int
    total_bytes: int
    n_steps: int

    # Optional: block sizes, indexed by block index (or mapped index).
    block_sizes: List[int] | None = None

    # Optional: block mapping metadata.
    tree: List[int] | None = None
    block_map: List[int] | None = None

    phases: List[Phase] = None  # type: ignore[assignment]


def pretty_print_schedule(
    schedule: Schedule,
    *,
    file: IO[str] | None = None,
    show_mapping: bool = True,
    show_bytes: bool = True,
) -> None:
    """Pretty-print a Schedule in a stable, greppable format."""
    print(
        f"[{schedule.algo}] rank_size={schedule.rank_size} total_bytes={schedule.total_bytes} n_steps={schedule.n_steps}",
        file=file,
    )

    if show_mapping and schedule.block_sizes is not None:
        print(f"[{schedule.algo}] block sizes (bytes):", file=file)
        for i, sz in enumerate(schedule.block_sizes):
            print(f"  block[{i}] = {sz}", file=file)

    if show_mapping and schedule.tree is not None:
        print(f"[{schedule.algo}] tree (index -> oldRank):", file=file)
        print("  ", schedule.tree, file=file)

    if show_mapping and schedule.block_map is not None:
        print(f"[{schedule.algo}] block_map (oldRank -> mapped_index):", file=file)
        print("  ", schedule.block_map, file=file)

    for phase in schedule.phases or []:
        dr = "-" if phase.delta_rank is None else str(phase.delta_rank)
        db = "-" if phase.delta_block_index is None else str(phase.delta_block_index)
        print(
            f"\n[phase {phase.phase}] {phase.stage} step={phase.step} delta_rank={dr} n_blocks={phase.n_blocks} dBlock={db}",
            file=file,
        )
        for op in phase.rank_ops:
            if show_bytes:
                print(
                    f"  r{op.rank}: sendTo={op.send_to} recvFrom={op.recv_from} tx_bytes={op.tx_bytes} rx_bytes={op.rx_bytes}",
                    file=file,
                )
            else:
                print(f"  r{op.rank}: sendTo={op.send_to} recvFrom={op.recv_from}", file=file)

            if op.tx_blocks:
                print(f"    tx={op.tx_blocks}", file=file)
            if op.tx_offsets is not None:
                print(f"    tx_off={op.tx_offsets}", file=file)
            if op.tx_remote_offsets is not None:
                print(f"    tx_roff={op.tx_remote_offsets}", file=file)
            if op.tx_mapped is not None:
                print(f"    tx_m={op.tx_mapped}", file=file)
            if op.rx_blocks:
                print(f"    rx={op.rx_blocks}", file=file)
            if op.rx_offsets is not None:
                print(f"    rx_off={op.rx_offsets}", file=file)
            if op.rx_remote_offsets is not None:
                print(f"    rx_roff={op.rx_remote_offsets}", file=file)
            if op.rx_mapped is not None:
                print(f"    rx_m={op.rx_mapped}", file=file)
