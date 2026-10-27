"""Validate generated commands before accepting an optimized native build."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import shlex
from .scaleout_topology import require

REQUIRED_SOURCES = frozenset(('fabric.cc', 'persistent_streams.cc', 'remote_transactions.cc',
    'transaction_session.cc', 'simulator.cc', 'default-simulator-impl.cc',
    'qbb-net-device.cc', 'rdma-hw.cc', 'switch-node.cc'))


def inspect_commands(path):
    path = Path(path)
    require(path.is_file() and path.stat().st_size <= 16*2**20, 'native compile database missing or exceeds 16MiB')
    raw = path.read_bytes(); rows = json.loads(raw)
    require(isinstance(rows, list) and 0 < len(rows) <= 65536, 'invalid native compile database')
    seen, sequences, important = set(), Counter(), []
    for row in rows:
        require(isinstance(row, dict) and isinstance(row.get('file'), str) and row['file'], 'invalid compile source')
        if 'arguments' in row: argv = row['arguments']
        else:
            require(isinstance(row.get('command'), str), 'missing compile command')
            argv = shlex.split(row['command'])
        require(isinstance(argv, list) and argv and all(isinstance(a, str) and a for a in argv), 'invalid compiler arguments')
        require('-c' in argv and not any(a.startswith('@') or a in ('&&', ';', '|', '-Xclang', '-Xcompiler') for a in argv),
                'uninterpreted compiler command or response file')
        position = argv.index('-c')
        require(position+1 < len(argv), 'missing source after -c')
        base = Path(row.get('directory', '.'))
        require((base/argv[position+1]).resolve() == (base/row['file']).resolve(), 'compile source differs from command')
        flags = [a for a in argv if a.startswith('-O')]
        require(flags and all(a in ('-O', '-O0', '-O1', '-O2', '-O3', '-Og', '-Os', '-Oz', '-Ofast') for a in flags),
                'missing or unknown optimization option')
        require(flags[-1] == '-O3', 'native Release requires effective -O3: '+row['file'])
        require('-Wall' in argv and '-Wpedantic' in argv, 'native Release must retain warnings: '+row['file'])
        require(not {'-w', '-Wno-all', '-Wno-pedantic', '-Ofast', '-ffast-math', '-funsafe-math-optimizations'}.intersection(argv),
                'unsafe math or disabled warnings in native Release')
        require(not any('MOE_VERIFY_PROGRESS_UPDATES' in a for a in argv), 'development progress oracle in Release build')
        require('unified-bus' not in row['file'], 'UB source in independent native Release build')
        name = Path(row['file']).name; seen.add(name); sequences[tuple(flags)] += 1
        if name in REQUIRED_SOURCES:
            important.append(dict(source=name, optimization=flags[-1], warnings=['-Wall', '-Wpedantic']))
    require(REQUIRED_SOURCES <= seen, 'native compile database lacks critical network/session sources')
    return dict(schema_version='moe-native-compilation/1', passed=True,
        compile_commands_sha256=hashlib.sha256(raw).hexdigest(), translation_units=len(rows),
        effective_optimization='-O3', retained_warnings=['-Wall', '-Wpedantic'],
        optimization_sequences=[dict(flags=list(k), units=v) for k, v in sorted(sequences.items())],
        critical_units=important, progress_full_scan_development_oracle=False,
        scope='generated command verification; not runtime performance or hardware calibration')
