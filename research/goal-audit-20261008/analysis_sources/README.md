# Analysis source snapshots

These are byte-identical copies of the modified round-09/round-10 analysis files, retained with the public evidence audit. They are not a standalone simulator package.

To reproduce using the original workspace, restore each file to the corresponding round's `analysis/` directory, removing the `round09_` or `round10_` prefix. The scripts resolve inputs relative to that original research directory, and the round-10 analyzer reads the archived ns-3 case from round 05. Do not overwrite later user edits when restoring.

From the round-10 research directory:

```bash
python3 analysis/analyze_long_window.py
python3 -m unittest discover -s analysis -p 'test_long_window.py' -v
python3 analysis/generate_figures.py
```

No simulator rerun is required. Raw traces must be retained locally. The test suite checks reconstruction and exported-window invariants, not hardware accuracy. Source checksums are listed in `../sources.csv`.
