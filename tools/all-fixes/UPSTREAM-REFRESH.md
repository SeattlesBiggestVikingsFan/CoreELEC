# Maintained upstream refresh

Local lab deployments expose the daily operator entry point at
`workflow/upstream_refresh.py` and its runbook at `workflow/UPSTREAM-REFRESH.txt`,
relative to the lab root, not this source checkout. These local operational files
and their private state are not component source inputs or public dependencies.

Discovery compares the complete component vector with the last **successful**
run, not the last attempted or merely observed vector. Manual and scheduled
invocations share one serialized lock. Discovery alone does not accept a change:
an agent must review upstream equivalence, integrate retained features, resolve
conflicts explicitly, and record fresh source tests and required build evidence
before the operator advances successful state.

Refresh every declared component, including settings, media modules, the selected
AP driver branch and skin. Record exact unchanged tips and recipe/post-unpack
equivalence instead of silently substituting raw upstream for integrated sources.
The AP repository's default branch is not necessarily the selected recipe line.
Preserve canonical source bindings, source modes and accepted runtime defaults.

The upstream-only selection retains Ethernet HTTPS10/UX11 and the separately
identified Jellyfin companion contract. UX12, preroll, silenceES and runtime
experiments are not implicitly selected. Preserving an already repaired
user-installed Jellyfin addon is supported; an automatic repair of fresh installs
is not an accepted SYSTEM migration mechanism.

Use new repositories, candidates, evidence and immutable source-baseline refs.
Never rewrite previous release/source refs or published feature/PR branches.
The refresh entry point does not authorize hardware access, installation, device
settings changes, publication or a build in a shared SDK. Build examples retain
`PROJECT=Amlogic-ce DEVICE=Amlogic-ng ARCH=arm`; actual builds and binary/hardware
qualification remain separate, explicitly owned gates.
