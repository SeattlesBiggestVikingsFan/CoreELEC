# Reproducible all-fixes workflow

`upstream-dev` is unmodified published development, not the integration branch.
The manifest pins an exact reviewed upstream commit even when development moves.
Small `feature/*` branches carry explicit ordered commits and dependencies.
Cross-component requirements use `cross_component_depends_on` entries with
`component` and `feature`, for example the Kodi VSIF UI requiring the kernel's
`lldv-vsif-suppression-v3`. Missing/unselected capabilities and cycles fail before
assembly, including explicitly selected experiments. Runtime defaults remain
separate from selecting a source capability.
`default` means selected in the default assembly; `settings_defaults` describes
runtime policy separately. Pending/excluded features are not selected.
Experiments must be declared default-off and selected with
`--experiment component:feature`; their candidates cannot inherit validation.

Run these commands in Linux with Python 3.12+ and Git. No extra packages are needed.
Private repository paths belong in an untracked bindings JSON, never the manifest:

```sh
python3 tools/all-fixes/assemble.py bootstrap \
  --manifest tools/all-fixes/manifest.json --output /new/location/repositories \
  --local-sources /private/location/sealed-ancillary-repositories.json
python3 tools/all-fixes/assemble.py assemble \
  --manifest tools/all-fixes/manifest.json \
  --bindings /new/location/repositories/repositories.private.json \
  --work /new/location/candidate
python3 tools/all-fixes/assemble.py materialize \
  --work /new/location/candidate --output /new/location/sources
```

The managed `source_repository` URLs are the user-owned
`SeattlesBiggestVikingsFan/CoreELEC`, `SeattlesBiggestVikingsFan/xbmc` and
`SeattlesBiggestVikingsFan/linux-amlogic` forks. Bootstrap fetches the named
integration and selected feature refs from those forks and verifies exact pins.
`upstream.repository` remains canonical Pannal provenance, not the fetch location
for new feature/integration commits. Missing or moved published refs fail visibly.
Publication and a fresh public-clone acceptance are separate parent-owned steps;
bootstrap does not create forks, publish, authorize or infer PR activity.
Historic component evidence documents local bundle reconstruction, not public
bootstrap instructions. Original diagnostic fixtures and their raw control logs
remain local-only; their recorded hashes do not make those gates public-runnable.

The four unchanged integrated ancillary sources need explicit sealed local Git
inputs in the private JSON: `media_modules`, `ap6275s`, `settings`, `skin`.
Restore the corresponding hash-pinned source bundles/exports recorded in the
manifest provenance, then provide those Git repository paths. Bootstrap copies
only their exact selected commits into new depth-one repositories and verifies
the selected trees. Ancillary components with reviewed replay topics must also
pin `pinned_source.tree` and provide their exact topic refs/ordered commits in the
sealed local repository. Bootstrap fetches those commits with their parent trees
for delta extraction; assembly verifies the final selected tree. This permits
settings UI updates without importing private history or applying the full
platform patch twice. It never guesses a moving upstream or consumes a build cache.
This explicit immutable-input boundary is not a claim that ancillary integrated
commits or proprietary inputs are available on the three owned forks.

Both paths must be new. Assembly uses detached worktrees and does not change
existing worktree files, PR branches, the upstream branch, or release refs.
Conflicts retain the isolated worktree and a blocked receipt for explicit review.
Exact ancestral, equivalent patch-ID, and reverse-applicable changes require
checksum-verified review evidence, not automatic SHA-based skipping. Coverage of
accepted fixes already upstream remains in the manifest as `upstream-integrated`
with a pinned review. Missing refs, cycles, dependencies, parity and checksums fail.
Deterministic synthetic assembly commits permit repeat source/commit comparison.

Catalogs record exact upstream/feature/ref commits, dependencies, provenance,
runtime defaults, original release anchors and reconstructed source baseline.
Manifest `inputs` are repository-relative SHA256-verified catalogs/evidence.
The framework's `manifest_destination` installs the exact manifest and its inputs
in the candidate. Its containing commit is pinned by the assembly receipt, avoiding
a self-referential commit pin. The original production-tree parity excludes only
this documented tooling/metadata directory and intentional recipe-binding paths.
Public framework parity uses a pinned canonical projection digest over sorted
path/mode/blob IDs and an exact entry count. The original private `55ab` commit is
provenance only and is neither fetched nor required to assemble public sources.
`required_validation` names the mandatory checks. Validation evidence must contain
`manifest_sha256`, every exact component `{head, tree}`, and checks
`{id, passed: true, path, sha256}`. Paths are relative to the evidence file.
These receipts are local evidence, not a signature or an adversarial trust boundary.

```sh
python3 tools/all-fixes/assemble.py validate \
  --work /new/location/candidate --evidence /reviewed/location/validation.json
python3 tools/all-fixes/assemble.py promote --work /new/location/candidate \
  --component framework --expected-old <exact-current-integration-commit>
```

Promotion only updates `integration/all-fixes`, using Git compare-and-swap.
Use forty zeroes for an absent target. It refuses a checked-out target, stale
manifest/candidate/evidence, and all PR/upstream/release refs. Run it per component
with that repository's known old integration SHA; a multi-repository promotion is
not an atomic transaction. Do not publish until all receipts are reviewed.

For an upstream refresh, fetch only into an independent repository, resolve an
exact SHA, then supply `--refresh /reviewed/location/refresh.json` (component to
SHA). This produces an **unvalidated candidate**, with conflicts or explicit
already-upstream review tasks as needed. It never promotes or rewrites PR branches.
After review, revise the manifest pin/feature selection and run fresh validation.
See [UPSTREAM-REFRESH.md](UPSTREAM-REFRESH.md) for local daily-entry-point discovery,
last-success comparison, serialized execution and the upstream-only selection.

```sh
python3 tools/all-fixes/assemble.py immutable --repo <repository> \
  --ref refs/tags/release-original/<release> --commit <original-build-source-sha>
python3 tools/all-fixes/assemble.py immutable --repo <repository> \
  --ref refs/tags/source-baseline/<baseline> --commit <reconstructed-source-sha>
```

Those namespaces are create-only (same-value repeats are allowed). Git itself
cannot prevent an authorized manual overwrite; do not use force pushes. Existing
published PR refs are never workflow outputs. Original private release ancestry
and evidence remain local unless separately reviewed for publication.

## Native source and build boundaries

Materialization verifies the complete exported namespace, blob bytes, executable
modes and symlink targets, and records archive hashes. It
exports Git trees directly, including files marked `export-ignore` and preserving
`export-subst` bytes; its deterministic source-tar hash uses PAX format and epoch
zero rather than Git's filtered release-archive behavior. `materialization.json`
contains private absolute paths and must not be published. Its environment binds
the six canonical Amlogic-ce recipes through `COMBINED_*_SOURCE`.
`source-env.sh` exports those verified bindings and all three target variables;
source it only from your new local materialization directory.
Integrated component exports already include their recorded platform/post-unpack patches;
the recipe binding feature avoids applying them twice. AP's canonical bus rename
is checked, not repeated. The native Ethernet adapter changes exactly the two
installed service units from CRLF to LF; original source units stay byte-identical.

Only use a new private framework source/build workspace; do not share mutable
SDK/cache paths. Export all six recorded source variables. Every build explicitly
uses `PROJECT=Amlogic-ce DEVICE=Amlogic-ng ARCH=arm`; the kernel internally selects
arm64. The canonical native entry points are:

```sh
PROJECT=Amlogic-ce DEVICE=Amlogic-ng ARCH=arm scripts/build kodi
PROJECT=Amlogic-ce DEVICE=Amlogic-ng ARCH=arm scripts/build linux
```

These are instructions, not executed builds or permission to run a full firmware
build. Use the original exact component build/configuration/packaging receipts
for SDK coherence, modules/DTs, runtime selection and external binary inputs.
`package_update.py` requires private input paths/hashes and new work/output paths;
it never installs or reboots. Its adjacent fixtures remain unchanged.
Proprietary binaries and SDK/rootfs exports are not checked in or published.
Source parity with the previously validated image is **not** new binary, ABI,
hardware, installability or reproducible firmware certification. Keep the old
image's original release label and distinguish source-equivalent reconstruction.
Any future rebuilt package needs a new release ID and fresh build evidence; do not
reuse the original image's label or source/binary validation receipts.

Focused tooling tests:

```sh
python3 -m unittest discover -s tools/all-fixes -p 'test_*.py'
```

The preserved native packaging fixtures check root-owned added inodes. Run them
as the ordinary user in an unprivileged root-mapped user namespace (no sudo or
host privilege escalation), otherwise extraction ownership checks fail:

```sh
unshare --user --map-root-user -- python3 -m unittest discover \
  -s tools/combined-playback-hdr -p 'test_*.py'
```
