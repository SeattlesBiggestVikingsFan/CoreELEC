# Accepted Jellyfin companion repair

This is an identified, source-only single-file repair, not a complete addon
package, SYSTEM component, automatic migration or replacement addon version.
The original addon licensing continues to apply.

The accepted installation was `plugin.video.jellyfin` version `2.2.0+py3`.
Its manifest SHA256 was
`785d238176b45558407a48e4f2aa9912a8c8e744fc4c2fd5224ad3da0c32c1ea`;
the manifest bytes and complete declared dependency graph were not retained.
An observed websocket version of `1.6.4` is not a dependency-package pin.
Do not invent a source commit or rebuild a whole addon from these observations.

| Input | SHA256 |
| --- | --- |
| `ws_client-original.py` | `7cc8bbce35b2ea0ba889fd8bbe0938cd74fb0f99b8459b7bd33ef9899ab57b39` |
| `ws_client.py` | `acdd54e6c7caf61275ab2405c7bb9b8013583124e185a7e4eb44ac4135dc0b07` |
| `ws-client-ordering.patch` | `2002d2770df89079adcf80c322c6744add69acc8fd16d637dcd0b79b69979ed6` |

A normal native SYSTEM/kernel update preserves existing addon code and
`addon_data`. Leave the repaired user installation, manifest, dependencies,
settings and updater ownership untouched. This preserves an already-repaired
installation; it does not repair fresh or unfixed installations.

Applying the companion to an unfixed matching installation requires separate
explicit authorization and an approved migration mechanism. It must stop Kodi,
verify addon identity/version/manifest and the exact original source checksum,
preserve backup/stat/cache metadata, syntax-check the replacement, perform an
atomic fsynced replacement and readback, then run bounded startup/early/settled
controls with a reversible receipt. An exact already-repaired checksum is an
explicit no-op. Missing or differing code is a blocker, never an overwrite.
No such generic native-upgrade hook is implemented or accepted here.

A later addon update may replace this repair. Maintain a reviewed rebased delta
for that new exact source; do not suppress updates or silently reapply a patch.
Never copy accounts, tokens, servers, user settings or stale sync state.

Run the seven unchanged actual-class regressions against the adjacent exported
original and repaired sources:

```sh
python3 -m unittest discover \
  -s tools/all-fixes/companions/jellyfin-late-keepalive -p 'test_*.py'
```

Only the test's source-directory binding was made portable. No assertion, delay,
join/publication check or original-source negative control was relaxed.

The independent PythonInvoker repair belongs in a rebuilt coherent Kodi SYSTEM.
Its historical binary is not this companion or a drop-in for a new SYSTEM.
During a separately authorized installation, the upgrader must retire the exact
old identity/SYSTEM/kernel-guarded owned Python bind/service while Kodi is
quiescent and preserve recovery artifacts and a transition receipt. Do not
weaken its guards to permit the old binary on a new cohort.
