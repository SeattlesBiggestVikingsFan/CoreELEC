# Kodi upstream refresh: 2026-11-23

The canonical development branch `pannal/xbmc:aml-4.9-21.3_dev` was inspected
live and pinned to `efcb4dac04f9b2cfeb863154045602e768195ab2`. It has 14
commits beyond the actual previous assembly base
`a4295421f270929315fea42b9e53b00c16e920a5`. The separately observed stable
branch/tag T4c is `e984c6c6749c50887c4f7d2049e9f47ace49fe45`; the GitHub
releases endpoint returned no releases. Test/experiment branches were not selected.

The new source candidate is
`52117bc6027fbca1edfdd508661edba2446a1727`, tree
`f2fc279ff91b8ebaa358aa4e7b9559168048d6d7`, 9,897 entries.
`integration/all-fixes-upstream-20261123` is a fresh linear reconstruction from
the exact development pin, not a merge into an accumulating integration branch.
All previous published branches, immutable tags and source/evidence remain intact.

## What changed

Upstream contributes artwork download/cancellation and path-substitution fixes;
HDR10+ metadata bounds/truncation handling; a legacy VSVDB option-index correction;
DTS parsing, IEC packing, MAT timing and PCM plane/layout bounds; and AML
packet-header/Dolby Vision track-pair validation. These are upstream source
updates, not claims that pending owner playback, reboot or physical-channel
problems are cured.

All 17 prior catalog entries remain covered: 16 accepted feature deltas are
replayed, and the already-upstream dim-black feature is reviewed without replay.
Every new feature ref has ordered delta commits and conservative cumulative
dependencies, with previous provenance/patch IDs retained.

One shared-file conflict was insertion-only: accepted DD+/JOC BSI scanning still
needed the old bounded reader while upstream renamed/generalized that identical
reader for DTS hardening. The explicit `upstream-audio-reader-adapter` reuses
`AudioBitReader` and removes the duplicate old reader. Its definition is proved
byte-identical after renaming. The same topic binds the long-pause host harness to
the real `MAX_IEC61937_PACKET` header after upstream stopped using a literal.
No existing test assertions or diagnostic gates were removed or weakened.

All 69 upstream changed-path deltas are retained exactly, except the EAC3
reader-type substitution above. The only additional changed path is that host
harness. All other accepted entries retain identical content and Git modes.
In particular, settings XML, settings declarations, English strings, Python
shutdown, AML sticky GUI/same-gate implementation, and VSIF policy are unchanged
from the sealed previous `5ee5e77fc07ae84012355e133ef66b530ea43569` candidate.

Manual seamless Off and later-Off persistence remain intact. Generic VSIF
suppression still defaults Off; first-start adoption and saved On policy remain
unchanged. No DV signaling policy is silently changed. Jellyfin remains a
separate addon delivery. UX12, pre-roll and unaccepted owner experiments are not
selected. HDR10+ hidden visibility remains pending; new upstream metadata
validation is distinct from that diagnosis/configuration trial.

## Reconstruct and verify

After the parent publishes the reviewed new refs, start in a **new Linux
repository**. Read `feature-catalog.json`, fetch the exact `upstream.commit`
from canonical Pannal, and fetch included `features[].ref` from each explicit
owned `source_repository`. Do not fetch new feature SHAs from Pannal. Verify
ref heads against catalog pins. Check out the exact upstream pin and cherry-pick
each included feature's `ordered_commits` in dependency order, once. Skip only
the checksum-reviewed `upstream-integrated` entry.

Managed public bootstrap uses `--update-shallow` from the owned fork or a clean,
complete bounded local mirror. Explicit `--depth=2 --update-shallow` with exact
ordered commits/refs is required only when importing the partial-history lab
donor. Selected source/parent-tree/delta closure is verified complete; the
inherited filtered donor graph intentionally lacks unrelated older public
promisor blobs, so its unbounded local-history fetch is unsupported.
`bounded-source-transfer.json` additionally verifies ordinary unfiltered import
of `exports/upstream-new-ref-deltas.bundle` against an independently fetched
canonical depth-1 base. This complete new-ref delta bundle requires exact public
base `efcb4dac04f9b2cfeb863154045602e768195ab2`; it is not a full historical
archive. Do not publish the separate local graph or diagnostic archives.

The result must satisfy:

```sh
test "$(git rev-parse 'HEAD^{tree}')" = f2fc279ff91b8ebaa358aa4e7b9559168048d6d7
test "$(git ls-tree -r HEAD | wc -l)" -eq 9897
python3 -B tools/test-python-forced-shutdown.py --negative-control
python3 -B tools/test-lldv-vsif-suppress-setting.py
python3 -B tools/test-hdr10-seamless-setting.py --negative-control
python3 -B tools/test-seamless-profile-migration.py
python3 -B tools/test-aml-native-settings.py --negative-controls
python3 -B tools/test-aml-native-continuation.py --negative-controls
python3 -B tools/test-activeae-long-pause.py
python3 -B tools/test-dts-dialnorm.py
```

`independent-replay.json` records a second repository rebuilt from complete
checksum-pinned offline payloads with no network remote or alternates. Original
9a90 diagnostic metadata remains local-only, so the unchanged original TrueHD
gate is not claimed runnable from public refs alone. `test-inventory.json`
distinguishes preserved initial findings, final rechecks, native parser/metadata
tests, and inherited pre-existing fixture gaps.

## Build and publication boundary

No production compilation units were added, removed or renamed, and no
CMakeLists/cmake source graph changed. Only five added paths are host Python
tests. There are 32 modified existing production C++ units and two modified
existing C++ test units. The same configured build therefore retains its
1,462-object invariant; all objects must be rebuilt from an empty object cohort.

Framework's source-only recipe remains `file://${COMBINED_KODI_SOURCE}` with
`PKG_SKIP_PATCHES=yes`, GCC 13.3 and CPython 3.13.18. Python requires the coherent
`libpython3.13.so.1.0` provider exporting `PyThreadState_SetAsyncExc`. New
`kodi.bin`, settings XML and English PO must belong to the same source/build
cohort. Historical binaries/overlay recipes are not drop-in candidates.

Source/native-host evidence does not establish ARM compiler/link acceptance,
physical PCM 5.1/7.1 delivery, or new device acceptance. The parent owns full
configuration/link/provider checks, publishing and deployment. This component
performed no production/firmware build, SDK write, device action, push or PR.

Only exact refs in `publication-allowlist.json` are publication candidates.
Original/archive/diagnostic refs, raw local logs and offline payload bundles stay
local. `publication-reachability-audit.json` audits the full newly reachable
commit closure and changed blobs, distinguishing canonical public upstream
commits from new maintained commits.
