"""Small, bounded experiment files. No hidden-state readers belong here."""
import hashlib
import json
from pathlib import Path


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read(path):
    path = Path(path)
    require(not path.is_symlink(), f"Symlink input is not allowed: {path}")
    require(path.stat().st_size <= 32 * 2**20, f"JSON input exceeds 32 MiB: {path}")
    return json.loads(path.read_text())


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n")


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def rows(path, *, max_rows=2_000_000):
    path = Path(path)
    require(not path.is_symlink(), f"Symlink input is not allowed: {path}")
    require(path.stat().st_size <= 512 * 2**20, f"Table exceeds 512 MiB: {path}")
    with path.open() as stream:
        for count, line in enumerate(stream, 1):
            require(count <= max_rows and len(line) <= 65536, "Table row budget exceeded")
            yield json.loads(line)


def write_rows(path, values):
    with Path(path).open("w") as stream:
        for count, value in enumerate(values, 1):
            require(count <= 2_000_000, "Table row budget exceeded")
            line = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
            require(len(line) <= 65536, "Table record budget exceeded")
            stream.write(line)
            require(stream.tell() <= 512 * 2**20, "Table byte budget exceeded")
