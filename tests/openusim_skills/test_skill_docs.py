import unittest
from pathlib import Path
import re


class OpenUSimStageSkillDocsTest(unittest.TestCase):
    def repo_root(self):
        return Path(__file__).resolve().parents[2]

    def read_text(self, relative_path):
        return (self.repo_root() / relative_path).read_text(encoding="utf-8")

    def reference_files(self):
        return sorted((self.repo_root() / ".codex/skills/openusim-references").glob("*.md"))

    def test_stage_skill_bundle_exists(self):
        repo_root = self.repo_root()
        for relative_path in (
            ".codex/skills/openusim-welcome/SKILL.md",
            ".codex/skills/openusim-plan-experiment/SKILL.md",
            ".codex/skills/openusim-run-experiment/SKILL.md",
            ".codex/skills/openusim-analyze-results/SKILL.md",
            ".codex/skills/openusim-capture-insights/SKILL.md",
        ):
            self.assertTrue((repo_root / relative_path).is_file(), msg=relative_path)

    def test_stage_skill_docs_define_handover_surface(self):
        welcome_text = self.read_text(".codex/skills/openusim-welcome/SKILL.md")
        plan_text = self.read_text(".codex/skills/openusim-plan-experiment/SKILL.md")
        run_text = self.read_text(".codex/skills/openusim-run-experiment/SKILL.md")
        analyze_text = self.read_text(".codex/skills/openusim-analyze-results/SKILL.md")
        capture_text = self.read_text(".codex/skills/openusim-capture-insights/SKILL.md")
        topology_text = self.read_text(".codex/skills/openusim-references/topology-options.md")
        spec_rules_text = self.read_text(".codex/skills/openusim-references/spec-rules.md")

        for text in (welcome_text, plan_text, run_text, analyze_text, capture_text):
            self.assertIn("## Overview", text)
            self.assertIn("## When to Use", text)
            self.assertIn("## Handover", text)
            self.assertIn("## Integration", text)
            self.assertIn("Stay in this skill when:", text)

        self.assertIn("Hand off to `openusim-plan-experiment` when:", welcome_text)
        self.assertIn("Hand off to `openusim-run-experiment` when:", plan_text)
        self.assertIn("Before handoff, ensure `{case_dir}/experiment-spec.md` exists", plan_text)
        self.assertIn("Return to `openusim-welcome` when:", plan_text)
        self.assertIn("scratch/ns-3-ub-tools/net_sim_builder.py", run_text)
        self.assertIn("scratch/ns-3-ub-tools/traffic_maker/build_traffic.py", run_text)
        self.assertIn("Hand off to `openusim-analyze-results` when:", run_text)
        self.assertIn("Return to `openusim-plan-experiment` when:", run_text)
        self.assertIn("routing_intent", plan_text)
        self.assertIn("transport_channel_mode", plan_text)
        self.assertIn("default `on-demand`", plan_text)
        self.assertIn("custom-graph", plan_text)
        self.assertIn("graph.output_dir", run_text)
        self.assertIn("validate", run_text)
        self.assertIn("transport_channel_mode", run_text)
        self.assertIn("../openusim-references/", analyze_text)
        self.assertIn("<HARD-GATE>", analyze_text)
        self.assertIn("do not read full cards first", analyze_text)
        self.assertIn("<reference-hint>...</reference-hint>", analyze_text)
        self.assertIn("prefer `rg -U -o`", analyze_text)
        self.assertIn("fallback to `perl -0ne`", analyze_text)
        self.assertIn("fallback to `python3`", analyze_text)
        self.assertIn("Python regex is the fallback", analyze_text)
        self.assertIn("re.search", analyze_text)
        self.assertIn("perl", analyze_text)
        self.assertIn("rg -U -o", analyze_text)
        self.assertIn("do not `cat` every card in the directory just to choose", analyze_text)
        self.assertIn("do not hardcode a fixed card list as the only valid source", analyze_text)
        self.assertIn("within about 160 characters", analyze_text)
        self.assertIn("## Failure Interpretation Checklist", analyze_text)
        self.assertIn("Hand off to `openusim-plan-experiment` when:", analyze_text)
        self.assertIn("Hand off to `openusim-capture-insights` when:", analyze_text)
        self.assertIn("ask the user whether they want to preserve it as a knowledge card", analyze_text)
        self.assertIn("conclusion summary", analyze_text)
        self.assertIn("candidate existing card", analyze_text)
        self.assertIn("<HARD-GATE>", capture_text)
        self.assertIn("Do not create or modify a knowledge card unless the user has clearly agreed", capture_text)
        self.assertIn("write the judgment or insight, not the chat transcript", capture_text)
        self.assertIn("examples or evidence", capture_text)
        self.assertIn("future reader who does not know this conversation", capture_text)
        self.assertIn("future reader who does not know this case or chat", capture_text)
        self.assertIn("main-repo PR", capture_text)
        self.assertIn("Called by: `openusim-analyze-results`", capture_text)
        self.assertIn("### `custom-graph`", topology_text)
        self.assertIn("## Routing Intent", spec_rules_text)
        self.assertIn("## Transport Channel Mode", spec_rules_text)

    def test_reference_cards_expose_reference_hint_block(self):
        for path in self.reference_files():
            text = path.read_text(encoding="utf-8")
            lines = text.splitlines()
            self.assertTrue(lines, msg=path.name)
            self.assertTrue(lines[0].startswith("# "), msg=path.name)
            hint = re.search(r"<reference-hint>(.*?)</reference-hint>", text, re.S)
            self.assertIsNotNone(hint, msg=f"{path.name}: missing <reference-hint> block")

            hint_text = hint.group(1)
            use_when = re.search(r"<use-when>(.*?)</use-when>", hint_text, re.S)
            focus = re.search(r"<focus>(.*?)</focus>", hint_text, re.S)
            keywords = re.search(r"<keywords>(.*?)</keywords>", hint_text, re.S)

            self.assertIsNotNone(use_when, msg=f"{path.name}: missing <use-when>")
            self.assertIsNotNone(focus, msg=f"{path.name}: missing <focus>")
            self.assertIsNotNone(keywords, msg=f"{path.name}: missing <keywords>")

            use_when_text = use_when.group(1).strip()
            self.assertTrue(
                use_when_text.startswith("Use this reference when "),
                msg=f"{path.name}: <use-when> must start with 'Use this reference when '",
            )
            self.assertLessEqual(
                len(use_when_text),
                160,
                msg=f"{path.name}: <use-when> should stay within 160 characters",
            )

    def test_welcome_skill_spells_out_startup_gate(self):
        welcome_text = self.read_text(".codex/skills/openusim-welcome/SKILL.md")
        for marker in (
            "`./ns3` exists",
            "`scratch/ns-3-ub-tools/` exists",
            "`scratch/ns-3-ub-tools/requirements.txt` exists",
            "`scratch/ns-3-ub-tools/net_sim_builder.py` exists",
            "`scratch/ns-3-ub-tools/traffic_maker/build_traffic.py` exists",
            "`scratch/ns-3-ub-tools/trace_analysis/parse_trace.py` exists",
            "`build/` exists",
            "`cmake-cache/` exists",
            "`scratch/2nodes_single-tp` exists",
            "`git submodule update --init --recursive`",
            "`python3 -m pip install --user -r scratch/ns-3-ub-tools/requirements.txt`",
            "`python3.12 ./ns3 configure --enable-modules=unified-bus --disable-examples --disable-tests --disable-mpi --disable-mtp --disable-werror -d release -G Ninja`",
            "`BUILD_JOBS=${BUILD_JOBS:-$(python3.12 -c 'import os; print(os.cpu_count() or 1)')}`",
            '`python3.12 ./ns3 build -j "$BUILD_JOBS" ub-quick-example`',
            "`python3.12 ./ns3 run --no-build 'scratch/ub-quick-example --case-path=scratch/2nodes_single-tp'`",
        ):
            self.assertIn(marker, welcome_text)

    def test_spec_rules_define_minimal_experiment_spec_shape(self):
        spec_rules_text = self.read_text(
            ".codex/skills/openusim-references/spec-rules.md"
        )
        for marker in (
            "## Minimal template",
            "# Experiment Spec",
            "## Goal",
            "## Topology",
            "## Topology Realization",
            "## Workload",
            "## Routing Intent",
            "## Network Overrides",
            "## Transport Channel Mode",
            "## Observability",
            "## Startup Readiness",
            "## Execution Record",
            "## Validation Notes",
            "## Analysis Notes",
        ):
            self.assertIn(marker, spec_rules_text)
        self.assertIn("default: `on-demand`", spec_rules_text)

    def test_repo_agents_route_by_stage_not_monolith(self):
        agents_text = self.read_text("AGENTS.md")
        self.assertIn("openusim-welcome", agents_text)
        self.assertIn("openusim-plan-experiment", agents_text)
        self.assertIn("openusim-run-experiment", agents_text)
        self.assertIn("openusim-analyze-results", agents_text)
        self.assertNotIn("openusim-helper", agents_text)

    def test_repo_entry_docs_match_stage_skill_surface(self):
        readme_text = self.read_text("README.md")
        readme_en_text = self.read_text("README_en.md")
        quick_start_text = self.read_text("QUICK_START.md")
        quick_start_en_text = self.read_text("QUICK_START_en.md")

        for text in (
            readme_text,
            readme_en_text,
            quick_start_text,
            quick_start_en_text,
        ):
            self.assertIn(".codex/skills/", text)
            self.assertIn("openusim-welcome", text)
            self.assertIn("openusim-plan-experiment", text)
            self.assertIn("openusim-run-experiment", text)
            self.assertIn("openusim-analyze-results", text)
            self.assertIn("openusim-capture-insights", text)
            self.assertNotIn("openusim-helper", text)

    def test_skills_readme_and_repo_agents_document_capture_insights(self):
        skills_readme_text = self.read_text(".codex/skills/README.md")
        agents_text = self.read_text("AGENTS.md")

        self.assertIn("openusim-capture-insights", skills_readme_text)
        self.assertIn("Optional after analyze", skills_readme_text)
        self.assertIn("<reference-hint>", skills_readme_text)

        self.assertIn("openusim-capture-insights", agents_text)
        self.assertIn("optional post-analysis companion skill", agents_text)
        self.assertIn("not a fifth stage", agents_text)

    def test_pfc_dynamic_paper_reference_names_source_paper(self):
        lessons_text = self.read_text(
            ".codex/skills/openusim-references/congestion-control-and-pfc-lessons.md"
        )
        toolchain_text = self.read_text(
            ".codex/skills/openusim-references/spec-to-toolchain.md"
        )
        scratch_readme_text = self.read_text("scratch/README.md")
        paper_title = "Congestion Control for Large-Scale RDMA Deployments"

        for text in (lessons_text, toolchain_text, scratch_readme_text):
            self.assertIn("PFC_DYNAMIC_PAPER", text)
            self.assertIn(paper_title, text)

    def test_old_openusim_helper_surface_is_gone(self):
        repo_root = self.repo_root()
        self.assertFalse((repo_root / ".codex/skills/openusim-helper/SKILL.md").exists())
        self.assertFalse((repo_root / ".codex/skills/openusim-helper" / "scripts").exists())
