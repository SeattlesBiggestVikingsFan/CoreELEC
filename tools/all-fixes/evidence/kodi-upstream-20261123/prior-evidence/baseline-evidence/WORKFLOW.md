# Kodi accepted-feature workflow

`upstream-dev` is an unmodified tracking ref for Pannal development. The
validated assembly base is pinned separately. A newer tracked head is an
**unvalidated refresh candidate**, never a replacement for the release base.

The machine-readable catalog selects coherent `fix/` and `feature/` deltas.
Each delta commit has an explicit base and prerequisites. The seek queue helper
and normal-speed user-seek guard are one shared prerequisite for both seek UX
and trickplay reanchoring. The fixture-union topic combines the accepted test
harness interfaces after their production dependencies; it does not remove
assertions or introduce new playback policy.

Keep published PR heads immutable under `archive/published-pr-*`. The accepted
Dim/Black fix is already in the pinned upstream base and is archived separately;
do not replay it. Integration is for local assembly, **not upstream submission**.
Submit only a reviewed topic with its prerequisite series. A topic that upstream
accepts is moved to the accepted-upstream archive in a future catalog revision;
reconstruct integration from the new reviewed base and remaining deltas. Never
reset published PR branches, merge arbitrary refreshes into the release, or
infer equivalent coverage from commit names alone.

## Reconstruct the pinned baseline

Use a brand-new destination. Keep the three original bundles and workflow graph
together. The graph deliberately excludes unrelated historical blob payloads.
The current-tree and diagnostic companions supply the complete accepted source
and the original mandatory `9a90` fixture, respectively.

With `EXPORTS` pointing to the original three-bundle directory:

```sh
git init "$NEW_KODI"
git -C "$NEW_KODI" fetch "$EXPORTS/kodi-current-source-tree.bundle" \
  refs/heads/source-export-panni-kodi-complete-0aff06e:refs/heads/archive/current-tree-payload
git -C "$NEW_KODI" fetch "$EXPORTS/kodi-diagnostic-fixture.bundle" \
  refs/heads/combined-playback-hdr-20261122-diagnostic-fixture-payload:refs/heads/archive/diagnostic-fixture-payload
git -C "$NEW_KODI" fetch "$EVIDENCE/exports/workflow-payload.bundle" \
  '+refs/heads/*:refs/heads/*'
git -C "$NEW_KODI" -c remote.offline.promisor=true \
  -c remote.offline.partialclonefilter=blob:none \
  -c remote.offline.url="$EVIDENCE/exports/workflow-graph.bundle" \
  fetch --filter=blob:none offline '+refs/heads/*:refs/heads/*' '+refs/tags/*:refs/tags/*'
git -C "$NEW_KODI" checkout -b reconstructed-check upstream-validated
```

Read the catalog in topological order and cherry-pick **only** each selected
feature's `ordered_commits`, once per commit, after its dependencies. Do not
cherry-pick prerequisite assembly merges or source snapshot roots. Features with
status `upstream-integrated` have no replay delta. Compare the resulting tree
with `expected_tree` and the original release: all 9,889 entries, including
executable modes, must match. `reconstruct.py` implements this decomposition;
`feature-catalog.json` freezes its resulting delta refs. The assembly owner can
consume the catalog directly without rerunning decomposition or fetching a
different base.

## Validation and release identity

Run `validate.py NEW_KODI EVIDENCE` using host Python and the host C++ compiler.
It executes unchanged checked-in source fixtures, including mandatory original
diagnostic comparison, CRCs and negative controls. These are source/component
tests, not hardware, HDMI timing or a firmware build. The two historical optional
helpers retain their original failures; the frozen source also has a skin-reload
fixture compile gap after the timed-text union. All were reproduced on the exact
original source. See the exact outcome receipts; none is called passing.

Host dependencies are isolated under the evidence directory, not installed in
the SDK or global caches. The numbered-frame tests require NumPy, Pillow and
host FFmpeg/FFprobe. `host-dependencies.json` pins the restored inputs. The
additional twelve DD+ regressions freshly compile the complete current parser
and unchanged GoogleTest tests using the public host CRC dependency recorded in
`host-parser-boundary.json`. Diagnostic-only pre-existing DTS unused-variable
warnings remain visible rather than becoming host-fixture compile errors.

`release/original-0aff-source` names the exact historical build source.
`release/reconstructed-0aff-source-equivalent` names a different commit with
the same tree. No old `09d980` or other historical binary is claimed to have been
built from new commit IDs. No binary was rebuilt here.

Runtime policy and catalog default selection are different fields. HDR remains
default On; first-launch saved-Off migration persists marker 1, while a later
manual Off persists and drives the same guarded gate writer. JVC sticky SDR
applies only to DV Mode On and does not force a user's DV mode.

Active shutdown/VFM stress candidates remain pending and are not acceptance
inputs. There is no accepted post-seek, pre-roll, timeline-flash, IVTC or Atmos
gain-recovery claim. DD+ Atmos retains its original attenuation; PCM 5.1/7.1
remains unverified. Kernel AFBC scanout retention belongs to the kernel catalog;
upstream Kodi GUI AFBC buffering is not its substitute.

## Publish review

Only new source topic refs and reviewed catalog/workflow documents are candidates
for publication. The content audit covers new changed blobs and commit metadata
for private keys, tokens, personal absolute paths, local device addresses and
compiled/proprietary payloads. Evidence stays local; raw test logs and complete
historical graph bundles are not automatically public. Inherited history may
contain contributor identity metadata and must be reviewed by the parent before
selecting a fork/publishing boundary.
