#!/usr/bin/env python3
"""Read Git metadata without fetching, switching, building, or writing files.

Exit 0: checked versions/states agree; 1: dependency/state errors;
2: invalid lock, arguments, or unavailable tools. This is not a build test.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
ENV = dict(os.environ, GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0")
SHA = re.compile(r"[0-9a-f]{40}")


class CheckError(Exception):
    pass


def git(directory, *args, allowed=(0,)):
    command = ["git", "-c", "core.fsmonitor=false", "-C", str(directory), *args]
    try:
        result = subprocess.run(command, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, env=ENV, timeout=30,
                                check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise CheckError("Git read failed in {}: {}".format(directory, error))
    if result.returncode not in allowed:
        detail = result.stderr.decode("utf-8", "replace").strip()
        raise CheckError("git {} in {}: {}".format(" ".join(args), directory, detail))
    return result.stdout.decode("utf-8", "surrogateescape")


def relative_path(value, allow_root=False):
    if not isinstance(value, str) or not value:
        raise CheckError("Expected a non-empty relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "\x00" in value:
        raise CheckError("Unsafe relative path: {!r}".format(value))
    if str(path) != value or (value == "." and not allow_root):
        raise CheckError("Non-canonical relative path: {!r}".format(value))
    return path


def load_lock():
    try:
        lock = json.loads((ROOT / "deps.lock.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise CheckError("Cannot read deps.lock.json: {}".format(error))
    if not isinstance(lock, dict) or lock.get("schema_version") != "moe-ub-deps/1":
        raise CheckError("Unsupported dependency lock schema")
    records = lock.get("dependencies")
    if not isinstance(records, list) or not records:
        raise CheckError("dependencies must be a non-empty list")
    dependencies = {}
    for record in records:
        if not isinstance(record, dict):
            raise CheckError("Each dependency must be an object")
        path = str(relative_path(record.get("path")))
        parent = relative_path(record.get("parent_path"), allow_root=True)
        child = relative_path(record.get("submodule_path"))
        if str(parent / child) != path or path in dependencies:
            raise CheckError("Duplicate or inconsistent dependency path: " + path)
        for field in ("baseline_commit", "checkout_commit"):
            value = record.get(field)
            if not isinstance(value, str) or not SHA.fullmatch(value):
                raise CheckError("Invalid {} for {}".format(field, path))
        for field in ("clone_url", "upstream_url"):
            if not isinstance(record.get(field), str) or not record[field].strip():
                raise CheckError("Missing {} for {}".format(field, path))
        dependencies[path] = record
    for path, record in dependencies.items():
        if record["parent_path"] != "." and record["parent_path"] not in dependencies:
            raise CheckError("Unlisted parent for explicit dependency: " + path)
    spec = lock.get("specification")
    if not isinstance(spec, dict):
        raise CheckError("Missing specification record")
    relative_path(spec.get("path"))
    if not isinstance(spec.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", spec["sha256"]):
        raise CheckError("Invalid specification SHA-256")
    return dependencies, spec


def committed_gitlinks(directory):
    links = {}
    for record in git(directory, "ls-tree", "-r", "-z", "HEAD").split("\x00"):
        if not record:
            continue
        metadata, path = record.split("\t", 1)
        mode, kind, sha = metadata.split()
        if mode == "160000":
            if kind != "commit":
                raise CheckError("Invalid gitlink object: " + path)
            relative_path(path)
            links[path] = sha
    return links


def indexed_gitlinks(directory):
    links = {}
    for record in git(directory, "ls-files", "--stage", "-z").split("\x00"):
        if not record:
            continue
        metadata, path = record.split("\t", 1)
        mode, sha, stage = metadata.split()
        if mode == "160000":
            if stage != "0":
                raise CheckError("Unmerged submodule entry: " + path)
            links[path] = sha
    return links


def module_urls(directory):
    config = directory / ".gitmodules"
    if not config.exists():
        return {}
    values = git(directory, "config", "--no-includes", "--null", "--file",
                 str(config), "--get-regexp", r"^submodule\..*\.path$", allowed=(0, 1))
    mapping = {}
    for record in values.split("\x00"):
        if not record:
            continue
        key, path = record.split("\n", 1)
        relative_path(path)
        url_key = key[:-len(".path")] + ".url"
        url = git(directory, "config", "--no-includes", "--file", str(config),
                  "--get", url_key).strip()
        if path in mapping or not url:
            raise CheckError("Duplicate path or missing submodule URL: " + path)
        mapping[path] = url
    return mapping


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    if shutil.which("git") is None:
        print("ERROR: Git not found", file=sys.stderr)
        return 2
    try:
        dependencies, spec = load_lock()
        if not (ROOT / ".git").exists():
            raise CheckError("Run this script from a Git checkout, not a source archive")
        if Path(git(ROOT, "rev-parse", "--show-toplevel").strip()).resolve() != ROOT:
            raise CheckError("Script directory is not the main repository root")
    except CheckError as error:
        print("ERROR: {}".format(error), file=sys.stderr)
        return 2

    errors = []
    seen = set()
    visited = set()

    def fail(message):
        errors.append(message)
        print("ERROR: " + message)

    try:
        actual = hashlib.sha256((ROOT / spec["path"]).read_bytes()).hexdigest()
        if actual != spec["sha256"]:
            fail("Specification file differs from the lock: " + spec["path"])
    except OSError as error:
        fail("Cannot read specification: {}".format(error))

    def visit(relative, expected=None):
        directory = (ROOT / relative).resolve()
        if directory != ROOT and ROOT not in directory.parents:
            fail("Submodule escapes the main checkout: " + relative)
            return
        if directory in visited:
            fail("Duplicate or cyclic checkout path: " + relative)
            return
        visited.add(directory)
        if not (directory / ".git").exists():
            fail("Submodule not initialized: " + relative)
            return
        try:
            top = Path(git(directory, "rev-parse", "--show-toplevel").strip()).resolve()
            if top != directory:
                raise CheckError("Not an independent submodule checkout: " + relative)
            head = git(directory, "rev-parse", "HEAD").strip()
            seen.add(relative)
            if expected is not None and head != expected:
                fail("HEAD differs from parent gitlink at {}: {} != {}".format(relative, head, expected))
            if relative in dependencies and head != dependencies[relative]["checkout_commit"]:
                fail("HEAD differs from explicit lock at " + relative)
            links = committed_gitlinks(directory)
            if indexed_gitlinks(directory) != links:
                fail("Index gitlinks differ from committed HEAD at " + relative)
            urls = module_urls(directory)
            if set(urls) != set(links):
                fail(".gitmodules paths differ from committed gitlinks at " + relative)
            dirty = git(directory, "status", "--porcelain=v1", "--untracked-files=normal",
                        "--ignore-submodules=all")
            if dirty:
                if relative == ".":
                    print("WARN: main repository has local file changes; review before committing")
                else:
                    fail("Dependency has local file changes: " + relative)
                for line in dirty.splitlines()[:20]:
                    print("  " + line)
                if len(dirty.splitlines()) > 20:
                    print("  ... additional changed paths omitted")
            print("READ: {} @ {}".format(relative, head))
            for child, sha in sorted(links.items()):
                child_relative = str(PurePosixPath(relative) / child)
                record = dependencies.get(child_relative)
                if record is not None:
                    if record["parent_path"] != relative or record["submodule_path"] != child:
                        fail("Lock parent relationship differs at " + child_relative)
                    if sha != record["checkout_commit"]:
                        fail("Parent gitlink differs from lock at " + child_relative)
                    if urls.get(child) != record["clone_url"]:
                        fail(".gitmodules URL differs from lock at " + child_relative)
                elif relative == ".":
                    fail("Direct submodule is missing from the lock: " + child_relative)
                else:
                    print("INFO: checking transitive dependency against its parent: " + child_relative)
                visit(child_relative, sha)
        except (CheckError, ValueError) as error:
            fail("{}: {}".format(relative, error))

    visit(".")
    missing = sorted(set(dependencies) - seen)
    if missing:
        fail("Explicit dependencies not checked, possibly due to an uninitialized parent: " + ", ".join(missing))
    if errors:
        print("FAIL: {} issue(s). No fetch, checkout, cleanup, or build was performed.".format(len(errors)))
        return 1
    print("PASS: checked {} dependency checkouts; versions and inspected states agree.".format(len(seen) - 1))
    print("This result does not validate remote availability, compilation, or simulation.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
