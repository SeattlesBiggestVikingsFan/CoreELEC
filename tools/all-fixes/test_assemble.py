import json
import os
from pathlib import Path
import tempfile
import unittest

import assemble as a


class AssemblyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        a.git(self.repo, "init", "-q")
        a.git(self.repo, "config", "user.name", "Test")
        a.git(self.repo, "config", "user.email", "test@example.invalid")
        self.base = self.change("data", "base\n", "base")
        self.first = self.change("first", "one\n", "one")
        self.second = self.change("second", "two\n", "two")
        a.git(self.repo, "branch", "feature/one", self.first)
        a.git(self.repo, "branch", "feature/two", self.second)
        self.manifest = {
            "schema": 1,
            "target": {"PROJECT": "Amlogic-ce", "DEVICE": "Amlogic-ng", "ARCH": "arm"},
            "required_validation": ["source-tests"],
            "inputs": [],
            "components": {"example": {
                "upstream": {"commit": self.base, "repository": "https://github.com/example/source.git"},
                "expected_tree": a.text(self.repo, "rev-parse", self.second + "^{tree}"),
                "features": [
                    {"id": "two", "ref": "refs/heads/feature/two", "ordered_commits": [self.second],
                     "depends_on": ["one"], "default": True, "status": "included"},
                    {"id": "one", "ref": "refs/heads/feature/one", "ordered_commits": [self.first],
                     "default": True, "status": "included"}]}}}
        self.manifest_path = self.root / "manifest.json"
        self.bindings = self.root / "bindings.json"
        a.write(self.bindings, {"example": str(self.repo)})
        self.counter = 0

    def tearDown(self):
        self.temp.cleanup()

    def change(self, name, value, message):
        (self.repo / name).write_text(value)
        a.git(self.repo, "add", name)
        a.git(self.repo, "commit", "-qm", message)
        return a.text(self.repo, "rev-parse", "HEAD")

    def build(self, **kwargs):
        self.manifest_path.write_text(json.dumps(self.manifest))
        self.counter += 1
        work = self.root / f"candidate{self.counter}"
        return work, a.assemble(self.manifest_path, self.bindings, work, **kwargs)

    def evidence(self, work, receipt):
        proof = self.root / "proof.txt"
        proof.write_text("real fixture test result\n")
        value = {"manifest_sha256": receipt["manifest_sha256"],
                 "components": {name: {"head": item["head"], "tree": item["tree"]}
                                for name, item in receipt["components"].items()},
                 "checks": [{"id": "source-tests", "passed": True, "path": "proof.txt",
                             "sha256": a.digest(proof.read_bytes())}]}
        path = self.root / "evidence.json"
        path.write_text(json.dumps(value))
        return path

    def test_order_repeat_and_dirty_worktree_safety(self):
        (self.repo / "data").write_text("uncommitted user work\n")
        (self.repo / "untracked").write_text("keep me\n")
        before = a.git(self.repo, "status", "--porcelain").stdout
        original_head = a.text(self.repo, "rev-parse", "HEAD")
        work, first = self.build()
        _, second = self.build()
        self.assertEqual(first["components"], second["components"])
        self.assertEqual(first["components"]["example"]["selected"], ["one", "two"])
        self.assertEqual(a.git(self.repo, "status", "--porcelain").stdout, before)
        self.assertEqual(a.text(self.repo, "rev-parse", "HEAD"), original_head)
        self.assertEqual((self.repo / "data").read_text(), "uncommitted user work\n")
        self.assertEqual((work / "example" / "data").read_text(), "base\n")

    def test_cycles_missing_dependencies_and_unselected_dependencies(self):
        component = self.manifest["components"]["example"]
        component["features"][1]["depends_on"] = ["two"]
        with self.assertRaisesRegex(a.Failure, "cycle"):
            self.build()
        component["features"][1]["depends_on"] = ["missing"]
        with self.assertRaisesRegex(a.Failure, "Missing dependency"):
            self.build()
        component["features"][1]["depends_on"] = []
        component["features"][1]["default"] = False
        with self.assertRaisesRegex(a.Failure, "unselected"):
            self.build()

    def test_missing_commit_or_moved_feature_ref_fails(self):
        self.manifest["components"]["example"]["features"][1]["ordered_commits"] = ["f" * 40]
        with self.assertRaises(a.Failure):
            self.build()
        self.manifest["components"]["example"]["features"][1]["ordered_commits"] = [self.first]
        a.git(self.repo, "update-ref", "refs/heads/feature/one", self.base)
        with self.assertRaisesRegex(a.Failure, "ref moved"):
            self.build()

    def test_conflict_retains_isolated_diagnostics(self):
        a.git(self.repo, "switch", "--detach", self.base)
        conflicting = self.change("data", "upstream\n", "upstream")
        a.git(self.repo, "switch", "--detach", self.base)
        fix = self.change("data", "feature\n", "feature")
        component = self.manifest["components"]["example"]
        component["upstream"]["commit"] = conflicting
        component["features"] = [{"id": "fix", "ordered_commits": [fix], "default": True}]
        with self.assertRaisesRegex(a.Failure, "cherry-pick"):
            self.build()
        failure = a.load(self.root / "candidate1" / "assembly.json")
        self.assertEqual(failure["status"], "blocked")
        self.assertTrue(a.text(self.root / "candidate1" / "example", "ls-files", "-u"))
        self.assertEqual(a.text(self.repo, "rev-parse", "HEAD"), fix)

    def test_upstream_fix_requires_checksum_verified_review(self):
        component = self.manifest["components"]["example"]
        component["upstream"]["commit"] = self.first
        with self.assertRaisesRegex(a.Failure, "explicit review"):
            self.build()
        proof = self.root / "review.txt"
        proof.write_text("upstream one equals feature one; reviewed\n")
        checksum = a.digest(proof.read_bytes())
        self.manifest["inputs"] = [{"path": "review.txt", "sha256": checksum}]
        component["features"][1]["reviews"] = {self.first: {
            "kind": "ancestor", "matching_commit": self.first, "reason": "Already upstream",
            "evidence_sha256": checksum}}
        _, receipt = self.build()
        self.assertEqual(receipt["components"]["example"]["changes"][0]["action"], "reviewed-upstream")
        proof.write_text("changed")
        with self.assertRaisesRegex(a.Failure, "checksum"):
            self.build()

    def test_equivalent_different_sha_needs_review(self):
        a.git(self.repo, "switch", "--detach", self.base)
        equivalent = self.change("first", "one\n", "equivalent upstream change")
        component = self.manifest["components"]["example"]
        component["upstream"]["commit"] = equivalent
        with self.assertRaisesRegex(a.Failure, "explicit review"):
            self.build()
        proof = self.root / "review.txt"
        proof.write_text("byte-identical reverse apply reviewed")
        checksum = a.digest(proof.read_bytes())
        self.manifest["inputs"] = [{"path": "review.txt", "sha256": checksum}]
        component["features"][1]["reviews"] = {self.first: {
            "kind": "reverse-apply", "matching_commit": equivalent,
            "reason": "Equivalent delta in different commit", "evidence_sha256": checksum}}
        _, receipt = self.build()
        self.assertEqual(receipt["components"]["example"]["changes"][0]["equivalence"]["kind"], "reverse-apply")

    def test_upstream_coverage_without_replay(self):
        component = self.manifest["components"]["example"]
        component["upstream"]["commit"] = self.first
        item = component["features"][1]
        item["ordered_commits"] = []
        item["status"] = "upstream-integrated"
        with self.assertRaisesRegex(a.Failure, "empty series"):
            self.build()
        proof = self.root / "review.txt"
        proof.write_text("coverage retained at exact upstream")
        checksum = a.digest(proof.read_bytes())
        self.manifest["inputs"] = [{"path": "review.txt", "sha256": checksum}]
        item["upstream_review"] = {"upstream_commit": self.first, "reason": "reviewed source",
                                   "evidence_sha256": checksum}
        self.build()

    def test_promotion_requires_fresh_candidate_evidence_and_compare_and_swap(self):
        work, receipt = self.build()
        evidence = self.evidence(work, receipt)
        incorrect = a.load(evidence)
        incorrect["components"]["example"]["head"] = self.first
        evidence.write_text(json.dumps(incorrect))
        with self.assertRaisesRegex(a.Failure, "exact"):
            a.validate(work, evidence)
        evidence = self.evidence(work, receipt)
        a.validate(work, evidence)
        target = "refs/heads/integration/all-fixes"
        a.git(self.repo, "update-ref", target, self.base)
        with self.assertRaises(a.Failure):
            a.promote(work, "example", target, self.first)
        a.promote(work, "example", target, self.base)
        self.assertEqual(a.text(self.repo, "rev-parse", target), receipt["components"]["example"]["head"])
        (self.root / "proof.txt").write_text("stale")
        with self.assertRaisesRegex(a.Failure, "changed"):
            a.promote(work, "example", target, receipt["components"]["example"]["head"])

    def test_protected_refs_and_checked_out_targets(self):
        work, receipt = self.build()
        a.validate(work, self.evidence(work, receipt))
        with self.assertRaisesRegex(a.Failure, "protected"):
            a.promote(work, "example", "refs/heads/feature/one", self.first)
        a.git(self.repo, "switch", "-c", "integration/all-fixes", self.base)
        with self.assertRaisesRegex(a.Failure, "checked out"):
            a.promote(work, "example", "refs/heads/integration/all-fixes", self.base)
        ref = "refs/tags/release-original/test"
        a.immutable(self.repo, ref, self.second)
        a.immutable(self.repo, ref, self.second)
        with self.assertRaisesRegex(a.Failure, "Immutable"):
            a.immutable(self.repo, ref, self.first)
        self.assertEqual(a.text(self.repo, "rev-parse", ref), self.second)

    def test_refresh_and_experiments_never_inherit_validation(self):
        work, receipt = self.build(refresh={"example": self.base})
        self.assertEqual(receipt["refresh"], {"example": self.base})
        self.assertEqual(receipt["review_tasks"][0]["kind"], "upstream-refresh")
        with self.assertRaisesRegex(a.Failure, "new reviewed manifest"):
            a.validate(work, self.evidence(work, receipt))
        component = self.manifest["components"]["example"]
        component["features"].append({"id": "experiment", "ordered_commits": [self.first],
                                      "experimental": True, "default": True})
        with self.assertRaisesRegex(a.Failure, "default"):
            self.build()

    def test_source_bytes_and_executable_modes_materialize(self):
        a.git(self.repo, "switch", "--detach", self.second)
        script = self.repo / "script"
        script.write_text("#!/bin/sh\nexit 0\n")
        script.chmod(0o755)
        os.symlink("script", self.repo / "link")
        a.git(self.repo, "add", "script", "link")
        a.git(self.repo, "commit", "-qm", "mode and symlink")
        final = a.text(self.repo, "rev-parse", "HEAD")
        component = self.manifest["components"]["example"]
        component["features"].append({"id": "modes", "ordered_commits": [final],
                                      "depends_on": ["two"], "default": True})
        component["expected_tree"] = a.text(self.repo, "rev-parse", "HEAD^{tree}")
        work, _ = self.build()
        output = self.root / "exports"
        result = a.materialize(work, output)
        self.assertEqual(result["example"]["tree"], component["expected_tree"])
        self.assertTrue((output / "example" / "script").stat().st_mode & 0o111)
        self.assertEqual(os.readlink(output / "example" / "link"), "script")
        with self.assertRaises(FileExistsError):
            a.materialize(work, output)

    def test_candidate_manifest_installed_and_stale_manifest_rejected(self):
        component = self.manifest["components"]["example"]
        component.pop("expected_tree")
        component["manifest_destination"] = "workflow/manifest.json"
        work, receipt = self.build()
        self.assertEqual((work / "example" / "workflow" / "manifest.json").read_bytes(),
                         self.manifest_path.read_bytes())
        evidence = self.evidence(work, receipt)
        (work / "manifest.json").write_text("{}")
        with self.assertRaisesRegex(a.Failure, "manifest changed"):
            a.validate(work, evidence)

    def test_modes_are_part_of_source_parity(self):
        component = self.manifest["components"]["example"]
        component.pop("expected_tree")
        component["parity"] = [{"commit": self.base}]
        with self.assertRaisesRegex(a.Failure, "bytes/modes"):
            self.build()

    def test_explicit_recipe_file_checksum_is_required(self):
        component = self.manifest["components"]["example"]
        component["files"] = [{"path": "first", "mode": "100644",
                              "sha256": a.digest(b"one\n")}]
        self.build()
        component["files"][0]["sha256"] = "0" * 64
        with self.assertRaisesRegex(a.Failure, "source checksum"):
            self.build()


if __name__ == "__main__":
    unittest.main()
