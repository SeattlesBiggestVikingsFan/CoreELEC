# Native Ethernet calibration

This package backs **Settings > CoreELEC > Network > Calibrate Ethernet**.
Initial support is the verified AM6B+ onboard G12B/RTL8211F combination.
Health, saved calibration and last verified upload/download speeds have
separate single-line rows above the action button.

Use a known-good cable and gigabit switch, and preferably a wired LAN machine
running `iperf3 -s` (TCP port 5201 by default). Enter that machine's IPv4 address
and port in the dialog. LAN calibration verifies gigabit upload and download.
The public-server option can repair corrupt timing without a local server,
but its saved result is explicitly **public error checked, not gigabit verified**.
Its region/host/port picker includes the complete dated
[iPerf.fr public iPerf3 directory](https://iperf.fr/iperf-servers.php), plus custom
IPv4 entry. It is an offline snapshot, not a live availability check.
Internet congestion can make public verification inconclusive.
Calibration interrupts onboard networking and may disrupt playback/transfers.
The onboard port needs an IPv4 address; if bad timing prevents DHCP, first
configure a suitable manual address in the existing Network connection dialog.
Custom LAN/public addresses use a dedicated CoreELEC numeric modal, since the
stock Kodi IP dialog does not expose a visible Cancel button. IP octets have
Previous/Next controls; digits, Delete, OK and **Cancel** are visible. Port entry
uses the same modal with a fresh 5201 default; IP entry starts at 0.0.0.0.
Cancel and remote Back both discard entry without starting a job.
Factory-setting and previous-calibration operations acknowledge immediately
and report progress while the driver reopens the port, rather than timing out
the UI's control socket. A successful factory reset does not promise that the
factory timing is healthy; recent corruption is reported independently.
Restoring a previous profile reports reconnecting until the gigabit link
recovers (up to 20 seconds after driver reopen) and driver application is
confirmed. Failure is explicit, not a successful restore notification.
This manual restore wait does not require DHCP or run traffic tests.

LAN calibration scans timing indices 0-31 in separate inversion banks, prefers the
widest clean window and selects its lower midpoint when at least three indices
are clean. A one- or two-index window is also usable: prefer the current timing
if it is clean, otherwise its lower midpoint. Wider windows always win; ties
prefer the window containing the current setting.
During short scan probes, new hardware CRC errors reject the candidate without
waiting for the remaining traffic duration. Polling also covers link recovery.
The owned traffic process is terminated before proceeding. Clean-candidate
final verification still runs for its full required duration.
Early rejection saves unnecessary traffic, not PHY-reopen or address-recovery
time. Each setting can still require up to 20 seconds for link/address recovery;
a complete LAN scan can take several minutes. A coordinated AM6B+ trial took
about 12 minutes including both narrow-window verification rounds. The pre-test
dialog states that estimate and the 25-minute policy limit. No recovery
or verification gate is shortened merely to make the progress bar finish sooner.

Wide windows require 30 seconds in each direction. Narrow windows require two
complete 30-second bidirectional rounds, with a PHY reopen between rounds.
Every round must pass; narrower demonstrated timing margin is displayed
separately from measured Ethernet health, not as corruption or unverified health.
LAN readiness captures an available onboard IPv4 after each PHY reopen,
including between narrow-window verification rounds. DHCP need not restore its
old address. Source/route/counter identity remains strict within each measurement;
an address change during traffic is still inconclusive, never a clean result.
Saving requires at least 800 Mbps both ways,
zero new CRC errors/retransmissions, and actual onboard interface byte growth.
Verification failure removes that candidate and recomputes the remaining clean
windows; at most one alternative is attempted, with the verification strength
required for its remaining window. No verified candidate or insufficient server
capacity means **no new saved profile**; previous timing/profile are retained.
Server refusal, identity/route/counter-reset errors and cancellation still abort
without trying to hide inconclusive measurements.

Public repair first checks the current setting at 10 Mbps for 20 seconds,
about 25 MB. If no CRC corruption is observed, it does not change or save timing.
Only measured hardware CRC triggers a temporary, guarded timing search over
supported indices0-31. Candidates get three-second receive screens; corrupt
candidates terminate early. A promising candidate must then pass two complete
20-second receive checks, with a PHY reopen between them. Each full check needs
at least20MB,18-21 seconds, an actual8-12Mbps receive rate, zero CRC, and matching
onboard interface/source/index/generation evidence. Public congestion, busy
servers, DNS failure, timeout, low traffic and link/identity changes are
inconclusive: they do not reject timing and cause restoration rather than retries.
The first repeatedly verified candidate is automatically saved, with an explicit
reduced-evidence profile type; this does not certify gigabit throughput.
After each reopen, recovery waits for both gigabit carrier and an available
onboard IPv4 address, allowing DHCP to recover without forcing its old address.
Fresh hardware CRC during this recovery rejects that timing early. A candidate's
DHCP/link timeout is inconclusive, not corruption or a clean result. The job
restores the original timing, positively rechecks its carrier, IPv4, identity,
driver generation/application and onboard route, then leases that proven
starting state again before trying the next untested candidate. Failed original
recovery aborts; server, route, identity, counter and lease failures remain fatal.
Recovery consumes the existing wall budget and does not refund payload reservations.
A valid same-device previous profile can prioritize a candidate, but it still
requires fresh screening and both full verification rounds. First-time users
without that hint use the same bounded search and recovery path.
Release-default runtime state is distinct from an explicitly selected timing.
In particular, the vendor's default index0 can retain a DT/inherited delay;
explicit index0 sets delay0. A fresh-device search therefore still tests explicit0
when the original runtime state is release-default rather than a selected index.

The bounded receiver stops at each duration or requested payload and independently
enforces32MiB per attempt. The whole job reserves full next-attempt payloads
before starting, including both final rounds together, with a256MiB hard payload
budget and20-minute wall deadline. An initial check, all32 short screens and one
two-round verification request about195MB; early corruption often uses less.
Reservations still count failed/short attempts, so late failures cannot bypass
the budget. Control messages are separately bounded.
TCP headers, retransmissions and unrelated interface traffic are not test
payload. A failed or cancelled repair restores the original runtime setting and
profile state. Passing an unchanged current setting does not authorize saving.
Public evidence cannot turn live health green by itself. Too little traffic, a server that
ignores the rate, unsupported protocol/authentication, and changed link/source
or generation are reported honestly.
LAN jobs allow 320 seconds / 48 GiB, sufficient for a full scan and two narrow
attempts. Full verification rounds must fit the remaining budget before starting
a candidate; otherwise the result is explicitly inconclusive (use a LAN peer).
Jobs have a 1500-second policy deadline as well as connection/command deadlines.
There are no scheduled public tests.
Preset DNS lookup occurs only after selection and explicit test consent, in the
asynchronous worker with a five-second lookup deadline and cancellable owned
resolver process. Every returned IPv4 must be public; private, loopback,
multicast and invalid results are rejected. Custom entry remains numeric-only.
No alternate IP, port or server is silently tried on failure. DNS, busy/refused,
unavailable and unsupported-protocol errors are explicit; a preset is not a
promise that a server is currently available.

Directory snapshot: fetched2026-11-22T00:36:11Z (UTC),12 table rows/30 hosts in
Europe, Africa, Asia and Americas; source test dates are10/2026. The full source
SHA256 is `e358d78ccde56fb9f81c902c0f9c745f32c69926808da807b755f6934a326493`.
All listed hosts, TCP/UDP distinctions and port ranges are retained.
Two IPv6-only Scaleway hosts remain visible with an unsupported explanation;
the [operator's test-sites page](http://ping.scaleway.com/) confirms their
address families. Three out-of-service listings also remain visible, with their
dated status and an explicit warning before testing. There are28 protocol/family
compatible entries, not28 independently tested or guaranteed available servers.
Reverse-mode, rate-limit or authentication refusal is reported honestly.
No DNS or traffic probe occurs while listing servers. Catalogue responses are
paginated to stay within the local8KiB control-message bound.

Before releasing an updated snapshot, fetch the complete table and independently
compare every hostname, provider, region, address-family annotation and listed
port/range with the compiled catalogue. Retain source date/attribution and status,
including unsupported/out-of-service entries. Do not substitute resolved IPv4
addresses or introduce live background fetching or silent fallback.
Scan progress and cancellation stay responsive during PHY reopen; traffic probes
wait for an available onboard link/IPv4 after reopening.
Factory-setting/profile transactions already in progress are not cancellable;
closing their progress UI does not undo an acknowledged driver/storage change.

Profiles live in `/storage/.config/ethernet-calibration/active.json` and
`previous.json`, covered by the existing user-configuration backup. They contain
hardware/backend identity and verification evidence, not server addresses.
Existing schema1 LAN profiles retain their full bidirectional acceptance policy.
Schema2 `public_error_checked` profiles require two measured receive rounds and
distinct reopen generations; they cannot masquerade as LAN gigabit profiles.
Both types apply offline at boot through the same guarded driver interface.
Public saves keep a previous LAN profile when replacing a public profile, and
clearing a public active profile also retains validated previous LAN history.
An invalid active document is not promoted over verified previous history.
Clear does not modify either slot before the driver accepts release-default
timing. Later storage failure restores both original slot bytes and runtime.
Failed save acceptance restores both profile slots byte-for-byte, including
original serialization rather than reconstructed JSON.
The boot service validates and stages a compatible profile before ConnMan.
Boot validation checks stored evidence/schema and adapter compatibility only;
it does not run traffic tests, contact a server or require an IPv4 address.
Server availability and speed-test throughput never gate normal startup.
The driver applies it after PHY initialization and before traffic, including
reopen/resume. Profiles survive ordinary upgrades without modifying DTBs.
An incompatible profile stays inactive and requires recalibration; an unreadable
profile within the 64KiB profile limit is reported and can be explicitly cleared
in the UI. An oversized file is refused before any driver/slot change; it requires
an independent backup and manual removal rather than an unbounded rollback read.

Recent health uses traffic/error deltas, not lifetime CRC totals or the mere
existence of a profile. A green "No recent packet errors" indication requires
64 MiB received in a 60-second window; it is not a throughput certification.
Successful LAN verification seeds that window with its actual receive
traffic, so the result is visible immediately. Idle traffic ages out normally.
Counter resets, driver generation, adapter identity and link changes invalidate
the window. The separate last-test row retains validated saved speed evidence,
not a certification of the current link. Public profiles instead show their
error-checked type and "Gigabit speed not verified", without invented upload/
download speeds or immediately green health. Status is sampled on request and
refreshed in place while the Network settings page is visible, without requiring
navigation away and back or rebuilding the selected row.
The refresher retains Python-owned status items created with the menu; page
rebuilds invalidate that registry. It never borrows native list/selection items
from the polling thread, avoiding invalid item pointers during navigation.
Each settings lifecycle owns a separate stop event and worker; rapid reopening
does not reuse a worker already committed to exiting. A stopped worker discards
a delayed status response rather than overwriting the new lifecycle's rows.
Pressing OK on these informational rows is a no-op: it preserves focus and
selection rather than navigating or opening another dialog.
A recovered link clears a stale "No gigabit link" indication on the next
sample; fresh traffic is still required to establish current health.

The service uses a root-only local Unix socket, not a network listener. The
kernel's root-only `ce_calibration` ABI guards board/PHY/mode identity and
rejects unsupported indices. Temporary scan settings have a renewable
10-second driver rollback lease. Cancellation, worker death and unsuccessful
verification restore accepted timing. Power loss uses only atomically written,
fully verified profiles at the next boot.
An entered watchdog callback rechecks the current expiry after acquiring RTNL,
so it cannot roll back a renewed or newly begun lease. Resume reapplies timing
through the normal checked reopen after NAPI/device attachment is balanced;
application failure returns an error with the interface administratively down,
not an up-but-disabled interface that can hang a subsequent close.
This checked resume path is gated on the registered calibration callback;
other adapters retain their vendor resume path.
If rollback cannot reopen the accepted timing, the port is left down with that
timing pending and an explicit error, never running an unverified candidate.

The initial backend recognizes only `g12b_AM6b_b4g`. Its vendor DT omits
`phy-mode`; the driver preserves that known legacy interface representation
instead of changing unrelated platform behavior, while validating the actual
PHY delay bits. Explicit unsupported modes and other board variants are rejected.

For focused policy/controller tests on Linux:

```sh
python3 -m unittest discover -s projects/Amlogic-ce/devices/Amlogic-ng/packages/sysutils/ethernet-calibration/tests
```

The actual-C host regressions additionally require `cc` and
`CE_CALIBRATION_KERNEL_SOURCE` pointing to the pinned, patched kernel tree.
`CE_CALIBRATION_REFERENCE_SOURCE` can point to the previous native tree for
negative controls. These tests simulate RTNL interleaving and NAPI/admin state;
they do not replace coordinated driver or suspend/resume hardware validation.

The NG package patches use the pinned `pannal/service.coreelec.settings` and
`pannal/linux-amlogic` sources. The addon patch includes focused Kodi-client
tests runnable with its existing unittest harness. Before release, coordinate
on-box validation of the local UI, all cancellation/lease paths, cold boot DHCP,
suspend/resume, and an ordinary update that replaces the DTB. Do not install or
reboot a shared device without coordination.
