#!/usr/bin/env python3
"""Pinned, isolated Git assembly with explicit source bootstrap; no builds or deployment."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tarfile


class Failure(RuntimeError):
    pass


def run(args, cwd=None, data=None, ok=(0,)):
    result = subprocess.run(args, cwd=cwd, input=data, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE)
    if result.returncode not in ok:
        raise Failure(f"{' '.join(map(str, args))}: {result.stderr.decode(errors='replace')}")
    return result


def git(repo, *args, data=None, ok=(0,)):
    return run(["git", "-C", str(repo), *args], data=data, ok=ok)


def text(repo, *args):
    return git(repo, *args).stdout.decode().strip()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def load(path):
    return json.loads(Path(path).read_text())


def write(path, value):
    path = Path(path)
    with path.open("x") as stream:
        json.dump(value, stream, sort_keys=True, indent=2)
        stream.write("\n")


def sha(value, label):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{40}", value):
        raise Failure(f"{label}: exact 40-character commit required")
    return value


def commit(repo, value):
    sha(value, "commit")
    resolved = text(repo, "rev-parse", "--verify", value + "^{commit}")
    if resolved != value:
        raise Failure(f"Commit did not resolve exactly: {value}")
    return resolved


def safe_path(root, relative):
    root = Path(root).resolve()
    path = (root / relative).resolve()
    if Path(relative).is_absolute() or not path.is_relative_to(root):
        raise Failure(f"Nonportable evidence path: {relative}")
    return path


def validate_manifest(path):
    data = load(path)
    if data.get("schema") != 1:
        raise Failure("Unsupported manifest schema")
    if data.get("target") != {"PROJECT": "Amlogic-ce", "DEVICE": "Amlogic-ng", "ARCH": "arm"}:
        raise Failure("Explicit Amlogic-ce/Amlogic-ng/arm target required")
    if not data.get("components"):
        raise Failure("No components")
    root = Path(path).resolve().parent
    for record in data.get("inputs", []):
        actual = digest(safe_path(root, record["path"]).read_bytes())
        if actual != record["sha256"]:
            raise Failure(f"Input checksum mismatch: {record['path']}")
    for name, component in data["components"].items():
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", name):
            raise Failure(f"Invalid component name: {name}")
        sha(component["upstream"]["commit"], name + " upstream")
        if not component["upstream"].get("repository", "").startswith("https://github.com/"):
            raise Failure(f"{name}: public repository URL required")
        order(component)
    cross_component_dependencies(data)
    return data, digest(Path(path).read_bytes())


def order(component, experimental=()):
    features = component.get("features", [])
    by_id = {item["id"]: item for item in features}
    if len(by_id) != len(features):
        raise Failure("Duplicate feature ID")
    selected = {item["id"] for item in features if item.get("default", False)}
    for key in experimental:
        if key not in by_id or not by_id[key].get("experimental", False):
            raise Failure(f"Not a declared experiment: {key}")
        selected.add(key)
    for item in features:
        if item.get("experimental", False) and item.get("default", False):
            raise Failure(f"Experiment enabled by default: {item['id']}")
        if item.get("status") in ("pending", "excluded") and item.get("default", False):
            raise Failure(f"Unaccepted feature enabled by default: {item['id']}")
        for parent in item.get("depends_on", []):
            if parent not in by_id:
                raise Failure(f"Missing dependency: {parent}")
    visiting, visited, result = set(), set(), []

    def visit(key):
        if key in visiting:
            raise Failure(f"Dependency cycle: {key}")
        if key in visited:
            return
        visiting.add(key)
        item = by_id[key]
        for parent in item.get("depends_on", []):
            visit(parent)
        visiting.remove(key)
        visited.add(key)
        if key in selected:
            missing = set(item.get("depends_on", [])) - selected
            if missing:
                raise Failure(f"{key}: unselected dependencies {sorted(missing)}")
            result.append(item)

    for key in by_id:
        visit(key)
    return result


def cross_component_dependencies(manifest, experiments=()):
    components = manifest["components"]
    selected = {
        (name, feature["id"])
        for name, component in components.items()
        for feature in order(component, [item.split(":", 1)[1] for item in experiments
                                         if item.startswith(name + ":")])
    }
    features = {(name, feature["id"]): feature
                for name, component in components.items()
                for feature in component.get("features", [])}
    graph = {}
    for key, feature in features.items():
        dependencies = [(key[0], item) for item in feature.get("depends_on", [])]
        for dependency in feature.get("cross_component_depends_on", []):
            if (not isinstance(dependency, dict) or
                    not isinstance(dependency.get("component"), str) or
                    not isinstance(dependency.get("feature"), str)):
                raise Failure(f"{key}: cross-component dependency requires component and feature")
            target = (dependency["component"], dependency["feature"])
            if target not in features:
                raise Failure(f"{key}: Missing cross-component dependency {target}")
            if key in selected and target not in selected:
                raise Failure(f"{key}: Unselected cross-component dependency {target}")
            dependencies.append(target)
        graph[key] = dependencies
    visiting, visited = set(), set()

    def visit(key):
        if key in visiting:
            raise Failure(f"Dependency cycle across components: {key}")
        if key in visited:
            return
        visiting.add(key)
        for dependency in graph[key]:
            visit(dependency)
        visiting.remove(key)
        visited.add(key)

    for key in graph:
        visit(key)


def patch_id(repo, revision):
    patch = git(repo, "show", "--format=", "--binary", revision).stdout
    result = run(["git", "patch-id", "--stable"], data=patch).stdout.decode().split()
    return result[0] if result else None


def review_check(feature, revision, equivalence):
    review = feature.get("reviews", {}).get(revision)
    if not review or not review.get("reason") or not review.get("evidence_sha256"):
        raise Failure(f"{feature['id']}: already integrated/equivalent {revision}; "
                      "explicit review with reason and evidence_sha256 required")
    if not re.fullmatch(r"[0-9a-f]{64}", review["evidence_sha256"]):
        raise Failure("Invalid review evidence checksum")
    if review.get("kind") != equivalence["kind"] or review.get("matching_commit") != equivalence.get("matching_commit"):
        raise Failure(f"{feature['id']}: review does not match detected equivalence")
    return review


def equivalent(repo, revision):
    ancestor = git(repo, "merge-base", "--is-ancestor", revision, "HEAD", ok=(0, 1))
    if ancestor.returncode == 0:
        return {"kind": "ancestor", "matching_commit": revision}
    patch = git(repo, "show", "--format=", "--binary", revision).stdout
    if patch and git(repo, "apply", "--reverse", "--check", data=patch, ok=(0, 1)).returncode == 0:
        return {"kind": "reverse-apply", "matching_commit": text(repo, "rev-parse", "HEAD")}
    cherry = text(repo, "cherry", "HEAD", revision, revision + "^")
    if cherry.startswith("-"):
        identity = patch_id(repo, revision)
        for upstream in text(repo, "rev-list", "--no-merges", "HEAD", "^" + revision + "^").splitlines():
            if patch_id(repo, upstream) == identity:
                return {"kind": "patch-id", "matching_commit": upstream, "patch_id": identity}
        raise Failure(f"Equivalent patch lacks reviewable upstream commit: {revision}")
    return None


def entries(repo, revision, exclude=()):
    output = git(repo, "ls-tree", "-r", "-z", revision).stdout
    records = {}
    for item in output.split(b"\0"):
        if not item:
            continue
        info, name = item.split(b"\t", 1)
        path = name.decode()
        if any(path == prefix or path.startswith(prefix.rstrip("/") + "/") for prefix in exclude):
            continue
        records[path] = info.decode()
    return records


def tree_check(repo, head, component):
    checks = []
    if component.get("expected_tree"):
        actual = text(repo, "rev-parse", head + "^{tree}")
        if actual != component["expected_tree"]:
            raise Failure(f"Tree parity failed: {actual} != {component['expected_tree']}")
        checks.append({"kind": "full-tree", "tree": actual})
    for record in component.get("parity", []):
        excluded = record.get("exclude", [])
        actual = entries(repo, head, excluded)
        if record.get("entries_sha256"):
            identity = digest(json.dumps(actual, sort_keys=True, separators=(",", ":")).encode())
            if identity != record["entries_sha256"] or len(actual) != record["entries_count"]:
                raise Failure("Portable source projection checksum/count mismatch")
            checks.append({"kind": "projected-tree-digest", "entries_sha256": identity,
                           "exclude": excluded, "entries": len(actual),
                           "reference_commit": record.get("reference_commit")})
        else:
            reference = commit(repo, record["commit"])
            if actual != entries(repo, reference, excluded):
                raise Failure(f"Source bytes/modes/paths differ from {reference}")
            checks.append({"kind": "projected-tree", "reference": reference, "exclude": excluded,
                           "entries": len(actual)})
    for record in component.get("files", []):
        listing = text(repo, "ls-tree", head, "--", record["path"])
        if not listing or listing.split()[0] != record["mode"]:
            raise Failure(f"Required source mode/path mismatch: {record['path']}")
        actual = digest(git(repo, "show", head + ":" + record["path"]).stdout)
        if actual != record["sha256"]:
            raise Failure(f"Required source checksum mismatch: {record['path']}")
        checks.append({"kind": "file", **record})
    return checks


def commit_index(repo, message):
    tree = text(repo, "write-tree")
    parent = text(repo, "rev-parse", "HEAD")
    environment = os.environ.copy()
    environment.update(GIT_AUTHOR_DATE="2000-01-01T00:00:00Z",
                       GIT_COMMITTER_DATE="2000-01-01T00:00:00Z")
    result = subprocess.run(["git", "-C", str(repo), "-c", "user.name=Assembly",
                             "-c", "user.email=assembly@users.noreply.github.com",
                             "commit-tree", tree, "-p", parent],
                            input=(message + "\n\nCo-authored-by: Copilot App "
                                   "<223556219+Copilot@users.noreply.github.com>\n").encode(),
                            env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode:
        raise Failure(result.stderr.decode())
    head = result.stdout.decode().strip()
    git(repo, "reset", "--soft", head)
    if text(repo, "status", "--porcelain"):
        raise Failure("Assembly produced unexpected dirty files")
    return head


def bootstrap(manifest_path, destination, local_sources_path=None):
    manifest, manifest_hash = validate_manifest(manifest_path)
    local = load(local_sources_path) if local_sources_path else {}
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=False)
    receipt = {"schema": 1, "manifest_sha256": manifest_hash, "status": "preparing",
               "components": {}, "private_local_paths": True}
    bindings = {}
    try:
        for name, component in manifest["components"].items():
            repo = destination / name
            git_args = ["git", "init", "--quiet", str(repo)]
            run(git_args)
            if component.get("pinned_source"):
                if name not in local or not Path(local[name]).is_absolute():
                    raise Failure(f"{name}: explicit sealed local Git source required; "
                                  "no upstream/latest fallback for integrated ancillary sources")
                source = str(Path(local[name]).resolve())
                revision = sha(component["pinned_source"]["commit"], name + " source")
                git(repo, "fetch", "--quiet", "--depth=1", source, revision)
                commit(repo, revision)
                tree_check(repo, revision, component)
                receipt["components"][name] = {"source_kind": "sealed-local-ancillary",
                                               "commit": revision,
                                               "tree": text(repo, "rev-parse", revision + "^{tree}")}
            else:
                source = component.get("source_repository")
                if not source or not source.startswith("https://github.com/"):
                    raise Failure(f"{name}: explicit owned source_repository URL required; "
                                  "upstream.repository is provenance, not a new-commit fetch source")
                git(repo, "remote", "add", "origin", source)
                git(repo, "remote", "add", "upstream", component["upstream"]["repository"])
                integration = component.get("integration", {})
                integration_ref = integration.get("ref", "refs/heads/integration/all-fixes")
                if not integration_ref.startswith("refs/"):
                    integration_ref = "refs/heads/" + integration_ref
                refs = {integration_ref}
                selected = order(component)
                for feature in selected:
                    revisions = feature.get("commits", feature.get("ordered_commits", []))
                    if revisions:
                        if not feature.get("ref"):
                            raise Failure(f"{name}/{feature['id']}: published feature ref required")
                        ref = feature["ref"]
                        refs.add(ref if ref.startswith("refs/") else "refs/heads/" + ref)
                for ref in refs:
                    if not ref.startswith("refs/heads/"):
                        raise Failure(f"Bootstrap requires explicit branch refs: {ref}")
                    git(repo, "check-ref-format", ref)
                git(repo, "fetch", "--quiet", "--no-tags", "--update-shallow", "origin",
                    *[ref + ":" + ref for ref in sorted(refs)])
                commit(repo, component["upstream"]["commit"])
                revision = text(repo, "rev-parse", "--verify", integration_ref)
                if integration.get("head") and revision != integration["head"]:
                    raise Failure(f"{name}: integration ref moved from pinned catalog head")
                for feature in selected:
                    revisions = feature.get("commits", feature.get("ordered_commits", []))
                    for item in revisions:
                        commit(repo, item)
                    if revisions and text(repo, "rev-parse", feature["ref"]) != revisions[-1]:
                        raise Failure(f"{name}: published feature ref moved: {feature['ref']}")
                receipt["components"][name] = {"source_kind": "owned-fork",
                                               "source_repository": source,
                                               "upstream_repository": component["upstream"]["repository"],
                                               "fetched_refs": sorted(refs), "commit": revision}
            git(repo, "switch", "--quiet", "--detach", revision)
            if text(repo, "status", "--porcelain"):
                raise Failure(f"{name}: bootstrapped source is dirty")
            bindings[name] = str(repo)
    except (Failure, OSError, ValueError, KeyError) as error:
        receipt.update(status="blocked", error=str(error))
        write(destination / "bootstrap.private.json", receipt)
        raise
    receipt["status"] = "ready"
    write(destination / "repositories.private.json", bindings)
    write(destination / "bootstrap.private.json", receipt)
    return receipt


def assemble(manifest_path, bindings_path, work, experiments=(), refresh=None):
    manifest, manifest_hash = validate_manifest(manifest_path)
    cross_component_dependencies(manifest, experiments)
    bindings = load(bindings_path)
    work = Path(work).resolve()
    work.mkdir(parents=True, exist_ok=False)
    receipt = {"schema": 1, "manifest_sha256": manifest_hash, "status": "unvalidated",
               "build_run": False, "binary_claim": False, "components": {},
               "experimental_selection": list(experiments), "refresh": refresh or {},
               "review_tasks": [{"component": name, "kind": "upstream-refresh",
                                 "new_upstream": revision,
                                 "required": ["upstream-delta/equivalence review", "new manifest",
                                              "fresh source/build/runtime validation as applicable"]}
                                for name, revision in (refresh or {}).items()]}
    with (work / "manifest.json").open("xb") as stream:
        stream.write(Path(manifest_path).read_bytes())
    refresh = refresh or {}
    try:
        for name, component in manifest["components"].items():
            repo = Path(bindings[name]).resolve()
            if name in refresh and component.get("pinned_source"):
                raise Failure(f"{name}: integrated ancillary source needs an explicit new recipe/source manifest")
            upstream = commit(repo, refresh.get(name, component.get("pinned_source", {}).get(
                "commit", component["upstream"]["commit"])))
            selected = order(component, [item.split(":", 1)[1] for item in experiments
                                         if item.startswith(name + ":")])
            destination = work / name
            git(repo, "worktree", "add", "--quiet", "--detach", str(destination), upstream)
            changes = []
            for feature in selected:
                revisions = feature.get("commits", feature.get("ordered_commits", []))
                if not revisions:
                    review = feature.get("upstream_review", {})
                    verified_hashes = {item["sha256"] for item in manifest.get("inputs", [])}
                    if (feature.get("status") != "upstream-integrated" or
                            review.get("upstream_commit") != upstream or
                            not review.get("reason") or review.get("evidence_sha256") not in verified_hashes):
                        raise Failure(f"{name}/{feature['id']}: empty series needs checksum-verified upstream review")
                    changes.append({"feature": feature["id"], "action": "reviewed-upstream", "review": review})
                    continue
                for revision in revisions:
                    commit(destination, revision)
                    if feature.get("ref") and text(repo, "rev-parse", "--verify", feature["ref"]) != revisions[-1]:
                        raise Failure(f"Feature ref moved: {feature['ref']}")
                    if len(text(destination, "rev-list", "--parents", "-n", "1", revision).split()) != 2:
                        raise Failure(f"Feature commit is not a single-parent delta: {revision}")
                    found = equivalent(destination, revision)
                    if found:
                        review = review_check(feature, revision, found)
                        if review["evidence_sha256"] not in {item["sha256"] for item in manifest.get("inputs", [])}:
                            raise Failure("Review evidence is not a checksum-verified manifest input")
                        changes.append({"feature": feature["id"], "commit": revision,
                                        "action": "reviewed-upstream", "equivalence": found, "review": review})
                        continue
                    git(destination, "-c", "user.name=Assembly", "-c",
                        "user.email=assembly@users.noreply.github.com", "cherry-pick", "--no-commit", revision)
                    new_head = commit_index(destination, f"Assemble {name}/{feature['id']}\n\nSource-commit: {revision}")
                    changes.append({"feature": feature["id"], "commit": revision,
                                    "action": "applied", "candidate_commit": new_head})
            if component.get("manifest_destination"):
                target = safe_path(destination, component["manifest_destination"])
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(Path(manifest_path).read_bytes())
                installed = [str(target.relative_to(destination))]
                for record in manifest.get("inputs", []):
                    source = safe_path(Path(manifest_path).parent, record["path"])
                    target_input = safe_path(target.parent, record["path"])
                    target_input.parent.mkdir(parents=True, exist_ok=True)
                    target_input.write_bytes(source.read_bytes())
                    installed.append(str(target_input.relative_to(destination)))
                git(destination, "add", "--", *installed)
                commit_index(destination, f"Record exact assembly manifest\n\nManifest-SHA256: {manifest_hash}")
            head = text(destination, "rev-parse", "HEAD")
            checks = [] if name in refresh else tree_check(destination, head, component)
            receipt["components"][name] = {"upstream": upstream, "head": head,
                                           "tree": text(destination, "rev-parse", "HEAD^{tree}"),
                                           "selected": [item["id"] for item in selected],
                                           "changes": changes, "parity": checks}
        unknown = set(refresh) - set(manifest["components"])
        declared = {name + ":" + item["id"] for name, c in manifest["components"].items()
                    for item in c.get("features", []) if item.get("experimental", False)}
        if unknown or set(experiments) - declared:
            raise Failure("Unknown refresh component or experiment selection")
    except (Failure, OSError, ValueError, KeyError) as error:
        receipt["status"] = "blocked"
        receipt["error"] = str(error)
        receipt["review_tasks"].append({"kind": "assembly-blocker", "error": str(error),
                                        "required": ["inspect retained isolated worktree", "explicit resolution/review"]})
        write(work / "assembly.json", receipt)
        raise
    write(work / "assembly.json", receipt)
    return receipt


def validate(work, evidence_path):
    work = Path(work)
    assembly = load(work / "assembly.json")
    manifest = load(work / "manifest.json")
    if digest((work / "manifest.json").read_bytes()) != assembly["manifest_sha256"]:
        raise Failure("Candidate manifest changed")
    if assembly["status"] != "unvalidated":
        raise Failure("Only a successfully assembled candidate can be validated")
    if assembly["refresh"] or assembly["experimental_selection"]:
        raise Failure("Refresh/experimental candidates require a new reviewed manifest")
    evidence = load(evidence_path)
    if evidence.get("manifest_sha256") != assembly["manifest_sha256"] or evidence.get("components") != {
            name: {"head": value["head"], "tree": value["tree"]}
            for name, value in assembly["components"].items()}:
        raise Failure("Validation evidence does not match exact manifest/candidate")
    required = set(manifest["required_validation"])
    records = evidence.get("checks", [])
    if len({record["id"] for record in records}) != len(records):
        raise Failure("Duplicate validation check")
    passed = set()
    for record in records:
        source = safe_path(Path(evidence_path).parent, record["path"])
        if digest(source.read_bytes()) != record["sha256"] or record.get("passed") is not True:
            raise Failure(f"Invalid/failed validation record: {record['id']}")
        passed.add(record["id"])
    if not required or not required.issubset(passed):
        raise Failure(f"Missing required validation: {sorted(required - passed)}")
    for name, record in assembly["components"].items():
        if text(work / name, "rev-parse", "HEAD") != record["head"] or text(work / name, "status", "--porcelain"):
            raise Failure(f"Candidate changed: {name}")
        tree_check(work / name, record["head"], manifest["components"][name])
    validation = {"schema": 1, "assembly_sha256": digest((work / "assembly.json").read_bytes()),
                  "manifest_sha256": assembly["manifest_sha256"],
                  "evidence_sha256": digest(Path(evidence_path).read_bytes()),
                  "evidence_path": str(Path(evidence_path).resolve()), "passed": True}
    write(work / "validation.json", validation)
    return validation


def promote(work, component, target, expected_old):
    work = Path(work)
    assembly = load(work / "assembly.json")
    validation = load(work / "validation.json")
    if (validation.get("passed") is not True or
            validation["assembly_sha256"] != digest((work / "assembly.json").read_bytes()) or
            validation["manifest_sha256"] != digest((work / "manifest.json").read_bytes()) or
            validation["evidence_sha256"] != digest(Path(validation["evidence_path"]).read_bytes())):
        raise Failure("Stale validation")
    evidence = load(validation["evidence_path"])
    for record in evidence["checks"]:
        if digest(safe_path(Path(validation["evidence_path"]).parent, record["path"]).read_bytes()) != record["sha256"]:
            raise Failure("Validation record changed after approval")
    if target != "refs/heads/integration/all-fixes":
        raise Failure("Only integration/all-fixes can be promoted; PR/upstream/release refs are protected")
    repo = work / component
    expected_old = sha(expected_old, "expected target (zero SHA for absent)")
    head = assembly["components"][component]["head"]
    if text(repo, "rev-parse", "HEAD") != head or text(repo, "status", "--porcelain"):
        raise Failure("Candidate changed after validation")
    checked_out = text(repo, "worktree", "list", "--porcelain")
    if f"branch {target}\n" in checked_out + "\n":
        raise Failure("Target is checked out; refusing to mutate another worktree")
    git(repo, "update-ref", target, head, expected_old)
    return {"component": component, "ref": target, "head": head, "expected_old": expected_old}


def immutable(repo, ref, revision):
    if not ref.startswith(("refs/tags/release-original/", "refs/tags/source-baseline/")):
        raise Failure("Immutable refs must be release-original/* or source-baseline/* tags")
    commit(repo, revision)
    git(repo, "check-ref-format", ref)
    existing = git(repo, "rev-parse", "--verify", ref, ok=(0, 128))
    if existing.returncode == 0:
        if existing.stdout.decode().strip() != revision:
            raise Failure(f"Immutable ref already exists: {ref}")
        return
    git(repo, "update-ref", ref, revision, "0" * 40)


class ArchiveHash:
    def __init__(self):
        self.hash = hashlib.sha256()

    def write(self, data):
        self.hash.update(data)
        return len(data)


def materialize(work, destination):
    work, destination = Path(work), Path(destination).resolve()
    assembly = load(work / "assembly.json")
    if assembly["status"] != "unvalidated":
        raise Failure("Blocked assembly cannot be materialized")
    destination.mkdir(parents=True, exist_ok=False)
    manifest = load(work / "manifest.json")
    environment = dict(manifest["target"])
    receipts = {}
    for name, record in assembly["components"].items():
        repo = work / name
        if text(repo, "rev-parse", "HEAD") != record["head"] or text(repo, "status", "--porcelain"):
            raise Failure(f"Candidate changed: {name}")
        source = destination / name
        source.mkdir()
        expected = entries(repo, record["head"])
        object_ids = [information.split()[2] for information in expected.values()]
        blobs = git(repo, "cat-file", "--batch", data=("\n".join(object_ids) + "\n").encode()).stdout
        offset = 0
        archive = ArchiveHash()
        with tarfile.open(fileobj=archive, mode="w|", format=tarfile.PAX_FORMAT) as bundle:
            for relative, information in expected.items():
                mode, kind, object_id = information.split()
                if kind != "blob" or mode not in ("100644", "100755", "120000"):
                    raise Failure(f"Unsupported source tree entry: {relative}")
                path = safe_path(source, relative)
                path.parent.mkdir(parents=True, exist_ok=True)
                end = blobs.index(b"\n", offset)
                header = blobs[offset:end].decode().split()
                if len(header) != 3 or header[:2] != [object_id, "blob"]:
                    raise Failure(f"Missing materialized source object: {relative}")
                size = int(header[2])
                blob = memoryview(blobs)[end + 1:end + 1 + size]
                offset = end + size + 2
                member = tarfile.TarInfo(relative)
                if mode == "120000":
                    link = bytes(blob).decode()
                    target = (path.parent / link).resolve()
                    if Path(link).is_absolute() or not target.is_relative_to(source):
                        raise Failure(f"Unsafe source symlink: {relative}")
                    os.symlink(link, path)
                    member.type, member.linkname, member.mode = tarfile.SYMTYPE, link, 0o777
                    bundle.addfile(member)
                else:
                    path.write_bytes(blob)
                    path.chmod(0o755 if mode == "100755" else 0o644)
                    member.mode, member.size = int(mode[-3:], 8), size
                    bundle.addfile(member, io.BytesIO(blob))
                data = os.readlink(path).encode() if path.is_symlink() else path.read_bytes()
                if digest(data) != digest(blob):
                    raise Failure(f"Materialized content mismatch: {relative}")
                actual_mode = "120000" if path.is_symlink() else ("100755" if path.stat().st_mode & 0o111 else "100644")
                if mode != actual_mode:
                    raise Failure(f"Materialized mode mismatch: {relative}")
        actual_paths = {str(path.relative_to(source)) for path in source.rglob("*")
                        if not path.is_dir() or path.is_symlink()}
        if actual_paths != set(expected):
            raise Failure(f"Materialized namespace mismatch: {name}")
        receipts[name] = {"head": record["head"], "tree": record["tree"],
                          "archive_sha256": archive.hash.hexdigest(), "entries": len(expected),
                          "archive_format": "complete-git-tree-pax-tar-v1/epoch-zero"}
        variable = manifest["components"][name].get("source_variable")
        if variable:
            if not re.fullmatch(r"COMBINED_[A-Z0-9_]+_SOURCE", variable) or variable in environment:
                raise Failure(f"Invalid/duplicate source binding variable: {variable}")
            environment[variable] = str(source)
    required = {variable for component in manifest["components"].values()
                for variable in component.get("required_source_variables", [])}
    if not required.issubset(environment):
        raise Failure(f"Missing canonical source bindings: {sorted(required - set(environment))}")
    shell = "".join(f"export {key}={shlex.quote(value)}\n" for key, value in environment.items())
    with (destination / "source-env.sh").open("x") as stream:
        stream.write(shell)
    write(destination / "materialization.json", {"manifest_sha256": assembly["manifest_sha256"],
                                                "sources": receipts, "environment": environment,
                                                "source_env_sha256": digest(shell.encode()),
                                                "build_run": False})
    return receipts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    item = sub.add_parser("bootstrap")
    item.add_argument("--manifest", required=True)
    item.add_argument("--output", required=True)
    item.add_argument("--local-sources", help="Private JSON: ancillary component -> exact sealed local Git source")
    item = sub.add_parser("assemble")
    item.add_argument("--manifest", required=True)
    item.add_argument("--bindings", required=True, help="Private JSON: component -> local Git repository")
    item.add_argument("--work", required=True)
    item.add_argument("--experiment", action="append", default=[], help="Explicit component:feature opt-in")
    item.add_argument("--refresh", help="JSON: component -> exact new upstream SHA; never promotes")
    item = sub.add_parser("validate")
    item.add_argument("--work", required=True)
    item.add_argument("--evidence", required=True)
    item = sub.add_parser("promote")
    item.add_argument("--work", required=True)
    item.add_argument("--component", required=True)
    item.add_argument("--target", default="refs/heads/integration/all-fixes")
    item.add_argument("--expected-old", required=True)
    item = sub.add_parser("immutable")
    item.add_argument("--repo", required=True)
    item.add_argument("--ref", required=True)
    item.add_argument("--commit", required=True)
    item = sub.add_parser("materialize")
    item.add_argument("--work", required=True)
    item.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        if args.command == "bootstrap":
            result = bootstrap(args.manifest, args.output, args.local_sources)
        elif args.command == "assemble":
            result = assemble(args.manifest, args.bindings, args.work, args.experiment,
                              load(args.refresh) if args.refresh else None)
        elif args.command == "validate":
            result = validate(args.work, args.evidence)
        elif args.command == "promote":
            result = promote(args.work, args.component, args.target, args.expected_old)
        elif args.command == "immutable":
            result = immutable(args.repo, args.ref, args.commit)
        else:
            result = materialize(args.work, args.output)
    except (Failure, OSError, ValueError, KeyError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
