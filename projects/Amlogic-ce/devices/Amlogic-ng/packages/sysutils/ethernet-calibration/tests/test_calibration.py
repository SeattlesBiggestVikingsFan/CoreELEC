# SPDX-License-Identifier: GPL-2.0-or-later

from pathlib import Path
import json
import sys
import tempfile
import types
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'source'))
import calibration as cal
import service


def evidence():
    return {'bps': 940_000_000, 'bytes': 3_525_000_000, 'seconds': 30,
            'retransmits': 0, 'crc_delta': 0}


def profile(index=29, identity=None):
    return {'schema': 1, 'identity': identity or {'backend': cal.BACKEND},
            'index': index, 'verification': {'send': evidence(), 'receive': evidence()}}

def public_profile(index=29, identity=None):
    rounds = [{'bps': 10_000_000, 'bytes': 25_000_000, 'seconds': 20,
               'rx_bytes': 25_000_000, 'crc_delta': 0, 'interface': 'eth0',
               'source': '192.168.2.84', 'index': index, 'generation': generation}
              for generation in (1, 2)]
    return {'schema': 2, 'kind': 'public_error_checked',
            'identity': identity or {'backend': cal.BACKEND}, 'index': index,
            'verification': {'rounds': rounds}}

def slot_bytes(store):
    return {name: path.read_bytes() if path.exists() else None
            for name in ('active', 'previous')
            for path in (store.directory / (name + '.json'),)}


class FakeAdapter:
    identity = {'backend': cal.BACKEND}
    name = 'eth0'

    def __init__(self):
        self.index = 30
        self.original = 30
        self.lease = False
        self.commands = []

    def capabilities(self):
        return {'index': self.index, 'lease': self.lease, 'applied': True}

    def write(self, command):
        self.commands.append(command)
        if command == 'begin':
            self.original = self.index
            self.lease = True
        elif command.startswith(('set ', 'profile ')):
            self.index = int(command.split()[1])
        elif command == 'restore':
            self.index = self.original
            self.lease = False
        elif command == 'accept':
            self.lease = False
        elif command == 'default':
            self.index = -1

    def address(self):
        return '192.168.2.84'

    def wait_ready(self, *_, **__):
        pass

    def link_ready(self):
        return True

    def counters(self):
        return {'rx_bytes': 0, 'tx_bytes': 0, 'rx_packets': 0, 'crc:rx_crc': 0}


class PolicyTests(unittest.TestCase):
    def test_public_recovery_waits_for_ipv4_without_pinning_old_dhcp_address(self):
        adapter = object.__new__(cal.Adapter)
        cancel = mock.Mock()
        cancel.wait.return_value = False
        with mock.patch.object(adapter, 'link_ready', return_value=True), \
                mock.patch.object(adapter, 'address', side_effect=[
                    cal.CalibrationError('DHCP recovering'), '0.0.0.0', '192.168.2.48']) as address:
            adapter.wait_ready(cancel, require_address=True)
        self.assertEqual(address.call_count, 3)

    def test_missing_ipv4_is_actionable_and_transient_recovery_is_retried(self):
        adapter = object.__new__(cal.Adapter)
        adapter.name = 'eth0'
        with mock.patch('fcntl.ioctl', side_effect=OSError('no address')):
            with self.assertRaisesRegex(cal.CalibrationError, 'manual.*Network settings'):
                adapter.address()
        with mock.patch.object(adapter, 'link_ready', return_value=True), \
                mock.patch.object(adapter, 'address', side_effect=[
                    cal.CalibrationError('address recovering'), '192.168.2.84']) as address:
            cancel = mock.Mock()
            cancel.wait.return_value = False
            adapter.wait_ready(cancel, '192.168.2.84')
        self.assertEqual(address.call_count, 2)

    def test_endpoint_requires_public_consent_and_valid_port(self):
        self.assertEqual(cal.endpoint('192.168.2.10', 5201), ('192.168.2.10', 5201))
        for address, port in [('1.1.1.1', 5201), ('127.0.0.1', 5201),
                              ('239.0.0.1', 5201), ('192.168.2.10', True),
                              ('192.168.2.10', 65536)]:
            with self.subTest(address=address, port=port), self.assertRaises(cal.CalibrationError):
                cal.endpoint(address, port)
        self.assertEqual(cal.endpoint('1.1.1.1', 5201, True, True), ('1.1.1.1', 5201))

    def test_windows_do_not_cross_inversion_banks_or_wrap(self):
        self.assertEqual(cal.choose_window({14, 15, 16, 17}), (14, [14, 15]))
        self.assertEqual(cal.choose_window({0, 1, 30, 31}), (0, [0, 1]))
        self.assertEqual(cal.choose_window({28, 29, 30, 31}), (29, [28, 31]))
        self.assertEqual(cal.choose_window({1, 2, 3, 28, 29, 30}, 29), (29, [28, 30]))

    def test_narrow_windows_prefer_current_but_wider_windows_win(self):
        self.assertEqual(cal.choose_window({31}), (31, [31, 31]))
        self.assertEqual(cal.choose_window({30, 31}, 31), (31, [30, 31]))
        self.assertEqual(cal.choose_window({30, 31}, 29), (30, [30, 31]))
        self.assertEqual(cal.choose_window({1, 2, 3, 30, 31}, 30), (2, [1, 3]))
        with self.assertRaises(cal.CalibrationError):
            cal.choose_window(set())

    def test_narrow_saved_profiles_require_every_repeated_round(self):
        saved = profile(30)
        saved['window'] = [30, 31]
        with self.assertRaises(cal.CalibrationError):
            cal.validate_profile(saved, saved['identity'])
        saved['verification']['rounds'] = [
            {'send': evidence(), 'receive': evidence()} for _ in range(2)]
        self.assertEqual(cal.validate_profile(saved, saved['identity']), 30)
        saved['verification']['rounds'][0]['receive']['crc_delta'] = 1
        with self.assertRaises(cal.CalibrationError):
            cal.validate_profile(saved, saved['identity'])
        saved['verification']['rounds'][0]['receive']['crc_delta'] = 0
        saved['window'] = [15, 30]
        with self.assertRaises(cal.CalibrationError):
            cal.validate_profile(saved, saved['identity'])

    def test_verification_rejects_errors_low_load_and_wrong_interface(self):
        before = {'rx_bytes': 0, 'tx_bytes': 0, 'rx_packets': 0, 'crc:rx_crc': 12}
        after = dict(before, rx_bytes=4_000_000_000, tx_bytes=4_000_000_000)
        result = {'end': {'sum_sent': evidence(),
                          'sum_received': evidence()}}
        for summary in result['end'].values():
            summary['bits_per_second'] = summary.pop('bps')
        cal.verify_measurement(result, before, after, True, 30)
        with self.assertRaises(cal.CandidateError):
            cal.verify_measurement(result, before, dict(after, **{'crc:rx_crc': 13}), True, 30)
        with self.assertRaises(cal.CalibrationError):
            cal.verify_measurement(result, before, dict(after, rx_bytes=1), True, 30)
        with self.assertRaises(cal.CalibrationError):
            cal.verify_measurement(result, before, dict(after, **{'crc:rx_crc': 0}), True, 30)
        result['end']['sum_received']['bits_per_second'] = 1_000_000
        with self.assertRaises(cal.CandidateError):
            cal.verify_measurement(result, before, after, True, 30)

    def test_malformed_or_missing_verification_is_not_a_pass(self):
        for result in ({}, {'end': {}}, {'end': {'sum_received': evidence()}}):
            with self.assertRaises(cal.CalibrationError):
                cal.verify_measurement(result, {}, {}, True, 30)
        saved = profile()
        saved['verification']['send']['bps'] = float('nan')
        with self.assertRaises(cal.CalibrationError):
            cal.validate_profile(saved, saved['identity'])

    def test_reopen_is_detected_even_when_crc_counters_were_zero(self):
        with self.assertRaises(cal.CalibrationError):
            cal.counter_delta({'generation': 1, 'crc:rx_crc': 0},
                              {'generation': 2, 'crc:rx_crc': 0})

    def test_profile_identity_and_range_are_revalidated(self):
        saved = profile()
        for changed in ({'backend': 'other'},):
            with self.assertRaises(cal.CalibrationError):
                cal.validate_profile(saved, changed)
        for index in (-1, 32, True):
            with self.assertRaises(cal.CalibrationError):
                cal.validate_profile(profile(index), saved['identity'])


class TransactionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = cal.ProfileStore(self.temporary.name)
        self.adapter = FakeAdapter()

    def test_failed_driver_accept_restores_previous_profile(self):
        previous = profile(30)
        self.store.replace('active', previous)
        with self.assertRaises(OSError):
            self.store.save(profile(), self.adapter.identity,
                            mock.Mock(side_effect=OSError('driver refused')))
        self.assertEqual(self.store.load(), previous)
        self.assertIsNone(self.store.load('previous'))

    def test_failed_first_save_does_not_create_active_profile(self):
        with mock.patch.object(self.store, 'replace', side_effect=OSError('full disk')):
            with self.assertRaises(OSError):
                self.store.save(profile(), self.adapter.identity, mock.Mock())
        self.assertIsNone(self.store.load())

    def test_failed_accept_restores_both_original_slot_bytes(self):
        for name, value in (('active', profile(30)), ('previous', profile(31))):
            (self.store.directory / (name + '.json')).write_text(
                json.dumps(value, indent=2) + '\n')
        before = slot_bytes(self.store)
        with self.assertRaises(OSError):
            self.store.save(profile(), self.adapter.identity,
                            mock.Mock(side_effect=OSError('accept failed')))
        self.assertEqual(slot_bytes(self.store), before)

    def test_clear_oversized_profile_fails_before_driver_or_slot_changes(self):
        self.store.directory.joinpath('active.json').write_bytes(b'x' * (64 * 1024 + 1))
        self.store.replace('previous', profile(30))
        before = slot_bytes(self.store)
        worker = service.Service(self.store)
        worker.run_control('clear', self.adapter)
        self.assertEqual(worker.result['phase'], 'failed')
        self.assertIn('too large', worker.result['error'])
        self.assertEqual(slot_bytes(self.store), before)
        self.assertEqual(self.adapter.commands, [])

    def test_clear_public_active_preserves_genuine_previous_lan_bytes(self):
        self.store.replace('active', public_profile(29))
        self.adapter.index = 29
        path = self.store.directory / 'previous.json'
        path.write_text(json.dumps(profile(30), indent=2) + '\n')
        before = path.read_bytes()
        with mock.patch.object(service, 'boot_status'):
            service.Service(self.store).control('clear', self.adapter)
        self.assertIsNone(self.store.load())
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(self.adapter.index, -1)

    def test_clear_invalid_active_does_not_replace_verified_previous(self):
        self.store.replace('active', {'schema': 1, 'index': 29})
        self.store.replace('previous', profile(30))
        previous = slot_bytes(self.store)['previous']
        with mock.patch.object(service, 'boot_status'):
            service.Service(self.store).control('clear', self.adapter)
        self.assertIsNone(self.store.load())
        self.assertEqual(slot_bytes(self.store)['previous'], previous)

    def test_failed_default_does_not_change_either_slot(self):
        self.store.replace('active', public_profile(29))
        self.store.replace('previous', profile(30))
        before = slot_bytes(self.store)
        with mock.patch.object(self.adapter, 'write', side_effect=OSError('default refused')):
            with self.assertRaises(OSError):
                service.Service(self.store).control('clear', self.adapter)
        self.assertEqual(slot_bytes(self.store), before)

    def test_clear_failure_after_unlink_restores_slots_and_runtime(self):
        self.store.replace('active', profile(30))
        self.store.replace('previous', profile(29))
        before = slot_bytes(self.store)
        remove = self.store.remove_active
        def failing_remove():
            remove()
            raise OSError('directory fsync failed')
        with mock.patch.object(self.store, 'remove_active', side_effect=failing_remove):
            with self.assertRaises(OSError):
                service.Service(self.store).control('clear', self.adapter)
        self.assertEqual(slot_bytes(self.store), before)
        self.assertEqual(self.adapter.index, 30)

    def test_clear_failure_after_previous_write_restores_both_bytes(self):
        self.store.replace('active', profile(30))
        self.store.replace('previous', profile(29))
        before = slot_bytes(self.store)
        replace = self.store.replace
        def failing_replace(name, value):
            replace(name, value)
            raise OSError('previous fsync failed')
        with mock.patch.object(self.store, 'replace', side_effect=failing_replace):
            with self.assertRaises(OSError):
                service.Service(self.store).control('clear', self.adapter)
        self.assertEqual(slot_bytes(self.store), before)
        self.assertEqual(self.adapter.index, 30)

    def test_restore_waits_for_link_before_completing_without_certifying_health(self):
        previous = profile()
        self.store.replace('previous', previous)
        worker = service.Service(self.store)
        worker.current_adapter = self.adapter
        worker.thread = mock.Mock()
        worker.thread.is_alive.return_value = True
        worker.update({'phase': 'restoring'})

        def recovering(cancel):
            self.assertEqual(self.adapter.index, previous['index'])
            status = worker.status()
            self.assertTrue(status['busy'])
            self.assertEqual(status['phase'], 'restoring')
            self.assertEqual(status['health']['state'], 'reconnecting')

        with mock.patch.object(self.adapter, 'wait_ready', side_effect=recovering), \
                mock.patch.object(service, 'boot_status') as receipt:
            worker.run_control('restore', self.adapter)
        self.assertEqual(worker.result['phase'], 'restored')
        self.assertEqual(worker.health.state['state'], 'unknown')
        receipt.assert_called_once_with({'state': 'applied', 'index': previous['index']})

    def test_restore_reports_failed_link_or_driver_application_not_success(self):
        for failed_link in (True, False):
            with self.subTest(failed_link=failed_link):
                self.store.replace('previous', profile())
                worker = service.Service(self.store)
                with mock.patch.object(self.adapter, 'wait_ready',
                        side_effect=cal.CandidateError('Gigabit link did not recover')
                        if failed_link else None), \
                        mock.patch.object(self.adapter, 'capabilities',
                            return_value={'index': 29, 'applied': False}), \
                        mock.patch.object(service, 'boot_status') as receipt:
                    worker.run_control('restore', self.adapter)
                self.assertEqual(worker.result['phase'], 'failed')
                self.assertIn('did not recover' if failed_link else 'not applied',
                              worker.result['error'])
                receipt.assert_not_called()
                self.assertEqual(self.store.load()['index'], 29)

    def test_invalid_old_identity_does_not_prevent_verified_recalibration(self):
        self.store.replace('active', profile(identity={'backend': 'old'}))
        self.store.save(profile(), self.adapter.identity, mock.Mock())
        self.assertEqual(self.store.load()['identity'], self.adapter.identity)
        self.assertEqual(self.store.load('previous')['identity'], {'backend': 'old'})

    def job(self):
        job = cal.Job(self.adapter, self.store, '192.168.2.10', 5201, False, mock.Mock())
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(cal, 'adapters', return_value=[self.adapter]).start()
        mock.patch.object(cal, 'run_command', return_value='192.168.2.10 dev eth0').start()
        return job

    def test_no_window_rolls_back_without_saving(self):
        job = self.job()
        with mock.patch.object(job, 'test', side_effect=cal.CandidateError('CRC')):
            with self.assertRaises(cal.CalibrationError):
                job.run()
        self.assertEqual(self.adapter.index, 30)
        self.assertFalse(self.adapter.lease)
        self.assertIsNone(self.store.load())

    def test_clean_window_is_verified_and_saved_not_hardcoded(self):
        job = self.job()

        def test(reverse, seconds, source):
            if seconds == 2 and self.adapter.index not in (20, 21, 22, 23):
                raise cal.CandidateError('CRC')
            return evidence()

        with mock.patch.object(job, 'test', side_effect=test):
            job.run()
        self.assertEqual(self.adapter.index, 21)
        self.assertEqual(self.store.load()['index'], 21)
        self.assertFalse(self.adapter.lease)

    def test_lan_scan_uses_fresh_ipv4_after_reopen(self):
        job = self.job()
        address = ['192.168.2.84']
        write = self.adapter.write
        def reopen(command):
            write(command)
            if command.startswith('set '):
                address[0] = '192.168.2.99'
        def ready(cancel, expected=None, poll=None, require_address=False):
            if expected is not None and expected != address[0]:
                raise cal.CandidateError('Obsolete source address')
        sources = []
        def test(reverse, seconds, source):
            self.assertEqual(source, address[0])
            sources.append(source)
            if seconds == 2 and self.adapter.index not in (20, 21, 22, 23):
                raise cal.CandidateError('CRC')
            return evidence()
        with mock.patch.object(self.adapter, 'write', side_effect=reopen), \
                mock.patch.object(self.adapter, 'address', side_effect=lambda: address[0]), \
                mock.patch.object(self.adapter, 'wait_ready', side_effect=ready), \
                mock.patch.object(job, 'test', side_effect=test):
            job.run()
        self.assertEqual(self.store.load()['index'], 21)
        self.assertEqual(set(sources), {'192.168.2.99'})

    def test_lan_narrow_round_reopen_refreshes_ipv4(self):
        job = self.job()
        address = ['192.168.2.84']
        reopens = [0]
        write = self.adapter.write
        def reopen(command):
            write(command)
            if command == 'set 30':
                reopens[0] += 1
                if reopens[0] == 3:
                    address[0] = '192.168.2.99'
        def ready(cancel, expected=None, poll=None, require_address=False):
            if expected is not None and expected != address[0]:
                raise cal.CandidateError('Obsolete source address')
        verified_sources = []
        def test(reverse, seconds, source):
            self.assertEqual(source, address[0])
            if seconds == 2 and self.adapter.index not in (30, 31):
                raise cal.CandidateError('CRC')
            if seconds == 30:
                verified_sources.append(source)
            return evidence()
        with mock.patch.object(self.adapter, 'write', side_effect=reopen), \
                mock.patch.object(self.adapter, 'address', side_effect=lambda: address[0]), \
                mock.patch.object(self.adapter, 'wait_ready', side_effect=ready), \
                mock.patch.object(job, 'test', side_effect=test):
            job.run()
        self.assertEqual(verified_sources,
                         ['192.168.2.84'] * 2 + ['192.168.2.99'] * 2)
        self.assertEqual(len(self.store.load()['verification']['rounds']), 2)

    def test_singleton_and_pair_require_two_bidirectional_rounds(self):
        for clean, expected in (({20}, 20), ({30, 31}, 30)):
            with self.subTest(clean=clean):
                self.adapter.commands.clear()
                job = self.job()
                verification = []

                def test(reverse, seconds, source):
                    if seconds == 2 and self.adapter.index not in clean:
                        raise cal.CandidateError('CRC')
                    if seconds == 30:
                        verification.append(reverse)
                    return evidence()

                with mock.patch.object(job, 'test', side_effect=test):
                    job.run()
                saved = self.store.load()
                self.assertEqual(saved['index'], expected)
                self.assertEqual(verification, [False, True, False, True])
                self.assertEqual(len(saved['verification']['rounds']), 2)
                self.assertEqual(self.adapter.commands.count('set ' + str(expected)), 3)

    def test_failed_midpoint_falls_back_with_recomputed_narrow_margin(self):
        job = self.job()
        verified = []

        def test(reverse, seconds, source):
            if seconds == 2 and self.adapter.index not in (20, 21, 22):
                raise cal.CandidateError('CRC')
            if seconds == 30:
                verified.append(self.adapter.index)
                if self.adapter.index == 21:
                    raise cal.CandidateError('CRC during full verification')
            return evidence()

        with mock.patch.object(job, 'test', side_effect=test):
            job.run()
        saved = self.store.load()
        self.assertEqual(saved['index'], 20)
        self.assertEqual(saved['window'], [20, 20])
        self.assertEqual(verified, [21, 20, 20, 20, 20])
        self.assertEqual(len(saved['verification']['rounds']), 2)

    def test_narrow_last_round_failure_does_not_save_a_partial_pass(self):
        job = self.job()
        verification = []

        def test(reverse, seconds, source):
            if seconds == 2 and self.adapter.index != 30:
                raise cal.CandidateError('CRC')
            if seconds == 30:
                verification.append(reverse)
                if len(verification) == 4:
                    raise cal.CandidateError('CRC in final round')
            return evidence()

        with mock.patch.object(job, 'test', side_effect=test):
            with self.assertRaises(cal.CalibrationError):
                job.run()
        self.assertIsNone(self.store.load())
        self.assertEqual(self.adapter.index, 30)
        self.assertFalse(self.adapter.lease)

    def test_failed_pair_candidate_falls_back_to_repeatedly_verified_singleton(self):
        job = self.job()
        verified = []

        def test(reverse, seconds, source):
            if seconds == 2 and self.adapter.index not in (30, 31):
                raise cal.CandidateError('CRC')
            if seconds == 30:
                verified.append(self.adapter.index)
                if self.adapter.index == 30 and verified.count(30) == 4:
                    raise cal.CandidateError('CRC in second receive round')
            return evidence()

        with mock.patch.object(job, 'test', side_effect=test):
            job.run()
        self.assertEqual(verified, [30] * 4 + [31] * 4)
        self.assertEqual(self.store.load()['index'], 31)
        self.assertEqual(self.store.load()['window'], [31, 31])
        self.assertEqual(len(self.store.load()['verification']['rounds']), 2)

    def test_final_verification_failure_preserves_saved_and_runtime_profile(self):
        original = profile(30)
        self.store.replace('active', original)
        job = self.job()
        with mock.patch.object(job, 'test', side_effect=lambda reverse, seconds, source:
                               evidence() if seconds == 2 else
                               (_ for _ in ()).throw(cal.CandidateError('congestion'))) as test:
            with self.assertRaises(cal.CalibrationError):
                job.run()
        self.assertEqual(self.store.load(), original)
        self.assertEqual(self.adapter.index, 30)
        self.assertEqual(sum(call.args[1] == 30 for call in test.call_args_list), 2)

    def test_server_refusal_aborts_instead_of_repeated_public_probes(self):
        job = self.job()
        with mock.patch.object(job, 'test', side_effect=cal.CalibrationError('server refused')) as test:
            with self.assertRaises(cal.CalibrationError):
                job.run()
        self.assertEqual(test.call_count, 1)
        self.assertEqual(self.adapter.index, 30)

    def test_traffic_budget_is_checked_before_start(self):
        job = self.job()
        job.traffic_seconds = cal.MAX_LAN_TRAFFIC_SECONDS
        with self.assertRaises(cal.CalibrationError):
            job.test(True, 2, self.adapter.address())
        job.traffic_seconds = 0
        job.traffic_bytes = cal.MAX_LAN_TRAFFIC_BYTES
        with self.assertRaises(cal.CalibrationError):
            job.test(True, 2, self.adapter.address())

    def test_public_mode_cannot_run_full_calibration_or_save(self):
        job = self.job()
        job.public = True
        with mock.patch.object(job, 'test') as probe:
            with self.assertRaisesRegex(cal.CalibrationError, 'require.*LAN'):
                job.run()
        probe.assert_not_called()
        self.assertEqual(job.traffic_seconds, 0)
        self.assertIsNone(self.store.load())
        self.assertEqual(self.adapter.index, 30)

    def test_lan_budget_fits_full_scan_and_two_narrow_attempts(self):
        job = self.job()
        job.traffic_seconds = 64 + 120
        job.traffic_bytes = 1_000_000_000 * (128 + 128) // 8
        job.check_budget(120, 4)
        job.deadline = 0
        with self.assertRaisesRegex(cal.CalibrationError, 'time limits'):
            job.check_budget(120, 4)

    def test_route_device_must_match_exactly_not_a_name_prefix(self):
        job = self.job()
        with mock.patch.object(cal, 'run_command', return_value='192.168.2.10 dev eth01'):
            with self.assertRaisesRegex(cal.CalibrationError, 'route does not use'):
                job.test(True, 2, self.adapter.address())

    def test_boot_stages_profile_without_an_ip_address(self):
        self.store.replace('active', profile())
        status = Path(self.temporary.name) / 'boot.json'
        with mock.patch.object(service, 'ProfileStore', return_value=self.store), \
                mock.patch.object(service, 'adapters', return_value=[self.adapter]), \
                mock.patch.object(service, 'BOOT_STATUS', status):
            self.assertEqual(service.apply_profile(), 0)
        self.assertEqual(self.adapter.commands, ['profile 29'])

    def test_boot_requires_all_narrow_rounds_before_driver_application(self):
        saved = profile(30)
        saved['window'] = [30, 31]
        saved['verification']['rounds'] = [
            {'send': evidence(), 'receive': evidence()} for _ in range(2)]
        status = Path(self.temporary.name) / 'boot.json'
        for corrupted in (False, True):
            with self.subTest(corrupted=corrupted):
                saved['verification']['rounds'][0]['send']['crc_delta'] = int(corrupted)
                self.store.replace('active', saved)
                self.adapter.commands.clear()
                with mock.patch.object(service, 'ProfileStore', return_value=self.store), \
                        mock.patch.object(service, 'adapters', return_value=[self.adapter]), \
                        mock.patch.object(service, 'BOOT_STATUS', status), \
                        mock.patch.object(service, 'Job', side_effect=AssertionError('no boot traffic')), \
                        mock.patch.object(cal, 'run_command', side_effect=AssertionError('no boot commands')), \
                        mock.patch.object(self.adapter, 'address', side_effect=AssertionError('boot needs no IP')):
                    self.assertEqual(service.apply_profile(), int(corrupted))
                self.assertEqual(self.adapter.commands, [] if corrupted else ['profile 30'])

    def test_clear_storage_failure_restores_runtime(self):
        self.store.replace('active', profile(30))
        worker = service.Service(self.store)
        with mock.patch.object(service, 'adapters', return_value=[self.adapter]), \
                mock.patch.object(self.store, 'remove_active', side_effect=OSError('read only')):
            with self.assertRaises(OSError):
                worker.control('clear', self.adapter)
        self.assertEqual(self.adapter.index, 30)
        self.assertEqual(self.store.load()['index'], 30)

    def test_timeouts_at_bad_candidates_do_not_prevent_finding_clean_window(self):
        job = self.job()

        def test(reverse, seconds, source):
            if seconds == 2 and self.adapter.index < 28:
                raise cal.CandidateError('Traffic timed out')
            return evidence()

        with mock.patch.object(job, 'test', side_effect=test):
            job.run()
        self.assertEqual(self.adapter.index, 29)

    def test_cancel_before_driver_accept_rolls_back_both_profile_and_runtime(self):
        original = profile(30)
        self.store.replace('active', original)
        job = self.job()
        replace = self.store.replace

        def saving(name, saved):
            replace(name, saved)
            if name == 'active' and saved['index'] != 30:
                job.cancel.set()

        with mock.patch.object(job, 'test', return_value=evidence()), \
                mock.patch.object(self.store, 'replace', side_effect=saving):
            with self.assertRaises(cal.Cancelled):
                job.run()
        self.assertEqual(self.store.load(), original)
        self.assertEqual(self.adapter.index, 30)

    def test_unreadable_profile_has_visible_status_and_can_be_cleared(self):
        (self.store.directory / 'active.json').write_text('broken JSON')
        worker = service.Service(self.store)
        with mock.patch.object(service, 'adapters', return_value=[self.adapter]), \
                mock.patch.object(service, 'BOOT_STATUS', self.store.directory / 'boot.json'):
            status = worker.status()
            self.assertEqual(status['profile']['state'], 'inactive')
            self.assertTrue(status['application_error'])
            worker.control('clear', self.adapter)
        self.assertIsNone(self.store.load())

    def test_driver_open_failure_is_not_displayed_as_pending_success(self):
        self.store.replace('active', profile(30))
        worker = service.Service(self.store)
        capabilities = {'index': 30, 'lease': False, 'applied': False, 'error': -95}
        with mock.patch.object(service, 'adapters', return_value=[self.adapter]), \
                mock.patch.object(self.adapter, 'capabilities', return_value=capabilities):
            self.assertTrue(worker.status()['application_error'])

    def test_saved_narrow_margin_does_not_change_measured_health(self):
        saved = profile(30)
        saved['window'] = [30, 31]
        saved['verification']['rounds'] = [
            {'send': evidence(), 'receive': evidence()} for _ in range(2)]
        self.store.replace('active', saved)
        worker = service.Service(self.store)
        worker.health.state = {'state': 'healthy'}
        with mock.patch.object(service, 'adapters', return_value=[self.adapter]):
            status = worker.status()
        self.assertEqual(status['profile']['margin'], 'reduced')
        self.assertEqual(status['health']['state'], 'healthy')

    def test_busy_progress_and_cancel_do_not_wait_for_driver_reopen(self):
        worker = service.Service(self.store)
        worker.job = self.job()
        worker.thread = mock.Mock()
        worker.thread.is_alive.return_value = True
        worker.update({'phase': 'scanning', 'candidate': 0})
        with mock.patch.object(service, 'adapters', side_effect=AssertionError('driver locked')), \
                mock.patch.object(self.adapter, 'capabilities', side_effect=AssertionError('driver locked')):
            for saved in (False, True):
                if saved:
                    self.store.replace('active', profile(30))
                status = worker.request({'version': 1, 'action': 'status'})
                self.assertEqual(status['phase'], 'scanning')
                self.assertEqual(status['health']['state'], 'calibrating')
                self.assertEqual(status['profile']['state'], 'inactive' if saved else 'not_saved')
                self.assertTrue(status['busy'])
            self.assertTrue(worker.request({'version': 1, 'action': 'cancel'})['cancel_requested'])
        self.assertTrue(worker.job.cancel.is_set())

    def test_reset_returns_before_driver_reopen_and_reports_reconnecting(self):
        worker = service.Service(self.store)
        with mock.patch.object(service, 'adapters', return_value=[self.adapter]), \
                mock.patch.object(service.threading, 'Thread') as thread, \
                mock.patch.object(self.adapter, 'write') as write:
            response = worker.request({'version': 1, 'action': 'clear'})
            self.assertTrue(response['started'])
            write.assert_not_called()
            thread.return_value.start.assert_called_once()
        worker.thread.is_alive.return_value = True
        with mock.patch.object(service, 'adapters', side_effect=AssertionError('driver locked')):
            status = worker.status()
        self.assertEqual(status['phase'], 'clearing')
        self.assertEqual(status['health']['state'], 'reconnecting')

    def test_failed_reset_is_reported_without_clearing_saved_profile(self):
        self.store.replace('active', profile(30))
        worker = service.Service(self.store)
        with mock.patch.object(self.adapter, 'write', side_effect=OSError('driver refused')):
            worker.run_control('clear', self.adapter)
        self.assertEqual(worker.result['phase'], 'failed')
        self.assertEqual(self.store.load()['index'], 30)

    def test_status_samples_new_crc_errors_without_reopening_menu(self):
        worker = service.Service(self.store)
        before = dict(self.adapter.counters(), generation=1)
        corrupt = dict(before, **{'crc:rx_crc': 1})
        with mock.patch.object(service, 'adapters', return_value=[self.adapter]), \
                mock.patch.object(self.adapter, 'counters', side_effect=[before, corrupt]):
            worker.status()
            self.assertEqual(worker.status()['health']['state'], 'corruption')

    def test_last_saved_speeds_are_separate_from_live_health(self):
        self.store.replace('active', profile(30))
        worker = service.Service(self.store)
        with mock.patch.object(service, 'adapters', return_value=[self.adapter]), \
                mock.patch.object(self.adapter, 'link_ready', return_value=False):
            status = worker.status()
        self.assertEqual(status['health']['state'], 'link_down')
        self.assertEqual(status['last_verification']['send_bps'], 940_000_000)
        self.assertEqual(status['last_verification']['state'], 'saved')

    def test_scan_stops_child_on_first_hardware_crc_error(self):
        job = cal.Job(self.adapter, self.store, '192.168.2.10', 5201, False, mock.Mock())
        job.scan_probe = True
        process = mock.Mock()
        process.communicate.side_effect = [cal.subprocess.TimeoutExpired('iperf3', .2), (b'', b'')]
        process.poll.return_value = None
        before = dict(self.adapter.counters(), generation=1)
        corrupt = dict(before, **{'crc:rx_crc': 1})
        run = cal.run_command
        with mock.patch.object(cal.subprocess, 'Popen', return_value=process), \
                mock.patch.object(self.adapter, 'counters', side_effect=[before, corrupt]), \
                mock.patch.object(cal, 'run_command', side_effect=lambda arguments, *args, **kwargs:
                                  '192.168.2.10 dev eth0' if arguments[0] == 'ip' else
                                  run(arguments, *args, **kwargs)):
            with self.assertRaisesRegex(cal.CandidateError, 'CRC errors'):
                job.test(True, 2, self.adapter.address())
        process.terminate.assert_called_once()
        self.assertIsNone(self.store.load())

    def test_wait_ready_does_not_swallow_proven_corruption(self):
        adapter = object.__new__(cal.Adapter)
        cancel = mock.Mock()
        cancel.wait.return_value = False
        with mock.patch.object(adapter, 'link_ready', return_value=False):
            with self.assertRaises(cal.CandidateError):
                adapter.wait_ready(cancel, poll=mock.Mock(side_effect=cal.CandidateError('CRC')))

    def test_final_verification_does_not_use_early_scan_polling(self):
        job = self.job()
        with mock.patch.object(cal, 'run_command', side_effect=['192.168.2.10 dev eth0', '{}']) as command:
            with self.assertRaises(cal.CalibrationError):
                job.test(True, 30, self.adapter.address())
        self.assertIsNone(command.call_args.kwargs['poll'])


class PublicCatalogueTests(unittest.TestCase):
    def test_all_pages_are_bounded_visible_and_do_not_probe_adapters(self):
        import json
        from public_servers import PUBLIC_SERVERS, catalogue_metadata
        worker = service.Service(mock.Mock())
        worker.thread = mock.Mock()
        worker.thread.is_alive.return_value = True
        entries = []
        with mock.patch.object(service, 'adapters') as adapters:
            for region in catalogue_metadata()['regions']:
                offset = 0
                while offset is not None:
                    page = worker.request({'version': 1, 'action': 'catalogue',
                                           'region': region, 'offset': offset})
                    self.assertLess(len(json.dumps(page).encode()), service.MAX_MESSAGE)
                    self.assertLessEqual(len(page['servers']), 8)
                    entries.extend(page['servers'])
                    offset = page['next_offset']
            adapters.assert_not_called()
        self.assertEqual({item['id'] for item in entries}, {item['id'] for item in PUBLIC_SERVERS})
        self.assertEqual(sum(item['status'].startswith('OOS') for item in entries), 3)
        self.assertEqual(sum(bool(item['unsupported_reason']) for item in entries), 2)

    def test_port_ranges_single_ports_and_unsupported_families_are_enforced(self):
        for hostname, first, last in (('iperf3.moji.fr', 5200, 5240),
                                       ('speedtest1.serverius.net', 5002, 5002),
                                       ('nl.iperf.014.fr', 10415, 10420)):
            for port in (first, last):
                self.assertEqual(cal.public_server(hostname, port)['port'], port)
            for port in (first - 1, last + 1, True):
                with self.assertRaises(cal.CalibrationError):
                    cal.public_server(hostname, port)
        for hostname in ('ping6.online.net', 'ping6-90ms.online.net'):
            with self.assertRaisesRegex(cal.CalibrationError, 'IPv6-only'):
                cal.public_server(hostname)
        from public_servers import unsupported_reason
        self.assertIn('TCP', unsupported_reason({'ip_versions': ['IPv4'], 'protocols': ['UDP']}))

    def test_invalid_directory_pages_are_explicit_errors(self):
        worker = service.Service(mock.Mock())
        for region, offset in (('unknown', 0), ('Europe', True), ('Europe', -1), ('Europe', 999)):
            with self.assertRaises(cal.CalibrationError):
                worker.request({'version': 1, 'action': 'catalogue', 'region': region, 'offset': offset})


class PublicRepairAdapter(FakeAdapter):
    def __init__(self):
        super().__init__()
        self.generation = 1

    def capabilities(self):
        return dict(super().capabilities(), generation=self.generation, default_index=0, error=0)

    def write(self, command):
        super().write(command)
        if command.startswith('set ') or command == 'restore':
            self.generation += 1

    def counters(self):
        return dict(super().counters(), generation=self.generation)


class PublicRepairTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = cal.ProfileStore(self.directory.name)
        self.adapter = PublicRepairAdapter()
        self.original = profile(30, self.adapter.identity)
        self.older = profile(29, self.adapter.identity)
        self.store.replace('active', self.original)
        self.store.replace('previous', self.older)
        self.worker = service.Service(self.store)
        self.job = cal.PublicRepairJob(self.adapter, self.store, '1.1.1.1', 5201, True,
                                      self.worker.update)
        self.job.source = self.adapter.address()
        self.initial = 'corruption'
        self.bad = set()
        self.failure = None

    def measurement(self, seconds=20):
        if self.adapter.lease and self.failure:
            raise self.failure
        state = self.initial if not self.adapter.lease else (
            'corruption' if self.adapter.index in self.bad else 'no_errors')
        before = dict(self.adapter.counters(), generation=self.adapter.generation)
        payload = cal.PUBLIC_RATE * seconds // 8
        after = dict(before, rx_bytes=payload)
        self.job.last_measurement = {'before': before, 'after': after, 'started': 0,
                                     'ended': 1, 'evidence': {'seconds': seconds}}
        return {'state': state, 'bytes': payload, 'seconds': seconds,
                'rate_bps': cal.PUBLIC_RATE, 'crc_delta': int(state == 'corruption')}

    def run_job(self):
        with mock.patch.object(self.job, 'prepare'), \
                mock.patch.object(self.job, 'measure', side_effect=self.measurement), \
                mock.patch.object(cal, 'adapters', return_value=[self.adapter]):
            self.worker.run_job(self.job)

    def test_crc_search_saves_first_twice_verified_public_profile_not_gigabit(self):
        self.store.replace('previous', self.original)
        self.bad = {0, 1}
        self.run_job()
        saved = self.store.load()
        self.assertEqual(saved['index'], 2)
        self.assertEqual(saved['kind'], 'public_error_checked')
        self.assertEqual(len(saved['verification']['rounds']), 2)
        self.assertEqual(cal.validate_profile(saved, self.adapter.identity), 2)
        self.assertGreater(saved['verification']['rounds'][1]['generation'],
                           saved['verification']['rounds'][0]['generation'])
        self.assertEqual(self.store.load('previous'), self.original)
        self.assertEqual(self.worker.result['phase'], 'public_complete')
        self.assertFalse(self.worker.result['gigabit_verified'])
        self.assertEqual(self.worker.health.state['state'], 'insufficient_traffic')
        self.assertFalse(self.adapter.lease)

    def test_raw_factory_default_does_not_skip_explicit_timing_zero(self):
        self.store.remove_active()
        (self.store.directory / 'previous.json').unlink()
        self.adapter.index = -1
        self.bad = set(range(1, 32))
        self.run_job()
        self.assertEqual(self.worker.result['phase'], 'public_complete')
        self.assertEqual(self.store.load()['index'], 0)
        self.assertEqual(len(self.store.load()['verification']['rounds']), 2)
    def test_recovery_crc_advances_but_dhcp_timeout_alone_restores_originals(self):
        self.store.replace('previous', self.original)
        crc = 0
        original_counters = self.adapter.counters

        def counters():
            return dict(original_counters(), **{'crc:rx_crc': crc})

        def recovery(cancel, poll=None, require_address=False):
            nonlocal crc
            self.assertTrue(require_address)
            if self.adapter.index == 0:
                crc += 1
            poll()

        with mock.patch.object(self.adapter, 'counters', side_effect=counters), \
                mock.patch.object(self.adapter, 'wait_ready', side_effect=recovery):
            self.run_job()
        self.assertEqual(self.worker.result['phase'], 'public_complete')
        self.assertEqual(self.store.load()['index'], 1)
        self.store.replace('active', self.original)
        self.store.replace('previous', self.older)
        self.adapter.index = 30
        with mock.patch.object(self.adapter, 'wait_ready',
                               side_effect=cal.CandidateError('DHCP timeout')):
            self.run_job()
        self.assertEqual(self.worker.result['phase'], 'failed')
        self.assertEqual(self.adapter.index, 30)
        self.assertEqual(self.store.load(), self.original)
        self.assertEqual(self.store.load('previous'), self.older)

    def test_changed_timing_during_recovery_is_not_crc_rejection(self):
        def recovery(cancel, poll=None, require_address=False):
            self.adapter.index += 1
            poll()
        with mock.patch.object(self.adapter, 'wait_ready', side_effect=recovery):
            self.run_job()
        self.assertEqual(self.worker.result['phase'], 'failed')
        self.assertIn('timing changed', self.worker.result['error'])
        self.assertEqual(self.adapter.index, 30)
        self.assertEqual(self.store.load(), self.original)

    def test_no_hint_inconclusive_seven_recovers_original_then_verifies_eight(self):
        (self.store.directory / 'previous.json').unlink()
        self.bad = set(range(7))

        def recovery(cancel, poll=None, require_address=False):
            if self.adapter.lease and self.adapter.index == 7:
                raise cal.CandidateError('DHCP timeout')
            if poll is not None:
                poll()

        with mock.patch.object(self.adapter, 'wait_ready', side_effect=recovery), \
                mock.patch.object(cal, 'run_command', return_value='1.1.1.1 dev eth0'):
            self.run_job()
        self.assertEqual(self.worker.result['phase'], 'public_complete')
        self.assertEqual(self.store.load()['index'], 8)
        self.assertNotIn('window', self.store.load())
        self.assertEqual(len(self.store.load()['verification']['rounds']), 2)
        self.assertIn('restore', self.adapter.commands)
        self.assertEqual(self.adapter.commands.count('begin'), 2)

    def test_failed_starting_state_recovery_aborts_without_testing_next_candidate(self):
        (self.store.directory / 'previous.json').unlink()
        self.bad = set(range(7))

        def recovery(cancel, poll=None, require_address=False):
            if not self.adapter.lease or self.adapter.index == 7:
                raise cal.CandidateError('DHCP timeout')
            if poll is not None:
                poll()

        with mock.patch.object(self.adapter, 'wait_ready', side_effect=recovery):
            self.run_job()
        self.assertEqual(self.worker.result['phase'], 'failed')
        self.assertIn('Original Ethernet', self.worker.result['error'])
        self.assertNotIn('set 8', self.adapter.commands)
        self.assertEqual(self.adapter.index, 30)
        self.assertFalse(self.adapter.lease)
        self.assertEqual(self.store.load(), self.original)
        self.assertIsNone(self.store.load('previous'))

    def test_valid_previous_hint_is_freshly_verified_and_invalid_hint_is_not_used(self):
        self.run_job()
        self.assertEqual(self.store.load()['index'], self.older['index'])
        self.assertEqual(len(self.store.load()['verification']['rounds']), 2)
        self.store.replace('previous', profile(29, identity={'backend': 'other'}))
        with self.assertLogs('ethernet-calibration', level='WARNING'):
            candidates = self.job.candidates(30)
        self.assertEqual(candidates[0], 0)

    def test_clean_or_inconclusive_current_timing_never_changes_or_saves_profiles(self):
        for state in ('no_errors', 'inconclusive'):
            self.initial = state
            self.run_job()
            self.assertEqual(self.adapter.commands, [])
            self.assertEqual(self.store.load(), self.original)
            self.assertEqual(self.store.load('previous'), self.older)

    def test_non_crc_network_failures_and_cancellation_restore_without_rejection(self):
        for error in (cal.CalibrationError('busy'), cal.CandidateError('timeout'), cal.Cancelled('Back')):
            self.failure = error
            self.run_job()
            self.assertEqual(self.adapter.index, 30)
            self.assertFalse(self.adapter.lease)
            self.assertEqual(self.store.load(), self.original)
            self.assertEqual(self.store.load('previous'), self.older)
            self.adapter.commands.clear()

    def test_full_next_pair_is_reserved_before_starting_and_deadline_is_hard(self):
        self.job.reserved_bytes = cal.PUBLIC_REPAIR_MAX_BYTES - 40_000_000
        with self.assertRaises(cal.CalibrationError):
            self.job.reserve(40, 2)
        self.assertEqual(self.job.reserved_bytes, cal.PUBLIC_REPAIR_MAX_BYTES - 40_000_000)
        self.job.deadline = 0
        with self.assertRaises(cal.CalibrationError):
            self.job.reserve(3)
        with self.assertRaises(cal.CalibrationError):
            self.job.inspect()

    def test_public_profile_rejects_missing_traffic_reopen_and_gigabit_masquerading(self):
        import copy
        self.run_job()
        saved = self.store.load()
        for field, value in (('bytes', 0), ('crc_delta', 1), ('interface', 'eth1'),
                              ('source', '0.0.0.0'), ('bps', 940_000_000), ('index', True)):
            altered = copy.deepcopy(saved)
            altered['verification']['rounds'][0][field] = value
            with self.assertRaises(cal.CalibrationError):
                cal.validate_profile(altered, self.adapter.identity)
        altered = copy.deepcopy(saved)
        altered['verification']['rounds'][1]['generation'] = altered['verification']['rounds'][0]['generation']
        with self.assertRaises(cal.CalibrationError):
            cal.validate_profile(altered, self.adapter.identity)
        altered = copy.deepcopy(saved)
        altered['schema'] = 1
        with self.assertRaises(cal.CalibrationError):
            cal.validate_profile(altered, self.adapter.identity)

    def test_failed_accept_rolls_back_both_profile_slots(self):
        self.run_job()
        saved = self.store.load()
        self.store.replace('active', self.original)
        self.store.replace('previous', self.older)
        with self.assertRaises(cal.CalibrationError):
            self.store.save(saved, self.adapter.identity,
                            lambda: (_ for _ in ()).throw(cal.CalibrationError('accept failed')))
        self.assertEqual(self.store.load(), self.original)
        self.assertEqual(self.store.load('previous'), self.older)

    def test_repeated_public_repair_preserves_previous_lan_profile(self):
        self.run_job()
        saved = self.store.load()
        self.store.save(saved, self.adapter.identity, lambda: None)
        self.assertEqual(self.store.load('previous'), self.original)

    def test_public_profile_boot_application_is_offline(self):
        self.store.replace('previous', self.original)
        self.run_job()
        with mock.patch.object(service, 'ProfileStore', return_value=self.store), \
                mock.patch.object(service, 'adapters', return_value=[self.adapter]), \
                mock.patch.object(service, 'boot_status') as receipt, \
                mock.patch.object(cal, 'resolve_public_server') as dns, \
                mock.patch.object(cal, 'DiagnosticReceiver') as traffic:
            self.assertEqual(service.apply_profile(), 0)
        dns.assert_not_called()
        traffic.assert_not_called()
        self.assertEqual(self.adapter.commands[-1], 'profile 0')
        receipt.assert_called_with({'state': 'applied', 'index': 0})

    def test_crc_in_final_round_rejects_candidate_then_verifies_another(self):
        self.store.replace('previous', self.original)
        original = self.measurement
        def measurement(seconds=20):
            result = original(seconds)
            if self.adapter.lease and self.adapter.index == 0 and seconds == 20:
                result['state'] = 'corruption'
            return result
        with mock.patch.object(self.job, 'prepare'), \
                mock.patch.object(self.job, 'measure', side_effect=measurement), \
                mock.patch.object(cal, 'adapters', return_value=[self.adapter]):
            self.worker.run_job(self.job)
        self.assertEqual(self.store.load()['index'], 1)
        self.assertEqual(self.worker.result['phase'], 'public_complete')
        self.assertLessEqual(self.job.reserved_bytes, cal.PUBLIC_REPAIR_MAX_BYTES)


class PublicDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.adapter = FakeAdapter()
        self.store = mock.Mock()
        self.worker = service.Service(self.store)
        self.job = cal.DiagnosticJob(self.adapter, self.store, '1.1.1.1', 5201, True,
                                     self.worker.update)
        self.before = dict(self.adapter.counters(), generation=1)
        self.after = dict(self.before, rx_bytes=25_000_000)

    def run_diagnostic(self, error=None, received=25_000_000):
        receiver = mock.Mock(received=received, started=None)
        receiver.run.return_value = 20
        if error:
            receiver.run.side_effect = error
        with mock.patch.object(cal, 'adapters', return_value=[]), \
                mock.patch.object(cal, 'run_command', return_value='1.1.1.1 dev eth0'), \
                mock.patch.object(cal, 'DiagnosticReceiver', return_value=receiver), \
                mock.patch.object(self.adapter, 'counters', side_effect=[self.before, self.after, self.after]):
            self.worker.run_job(self.job)

    def test_preset_resolves_then_uses_the_same_bounded_diagnostic_without_saving(self):
        self.job = cal.DiagnosticJob(self.adapter, self.store, None, None, True,
                                     self.worker.update, preset='scaleway-france')
        with mock.patch.object(cal, 'resolve_public_server', return_value='1.1.1.1') as lookup:
            self.run_diagnostic()
        lookup.assert_called_once_with(cal.public_server('scaleway-france'), self.job.cancel)
        self.assertEqual(self.job.address, '1.1.1.1')
        self.assertEqual(self.worker.result['diagnostic']['state'], 'no_errors')
        self.assertFalse(self.worker.result['diagnostic']['saved'])
        self.assertFalse(self.worker.result['diagnostic']['gigabit_verified'])
        self.assertEqual(self.adapter.commands, [])
        self.store.save.assert_not_called()

    def test_three_second_screen_requires_real_rate_payload_and_counter_generation(self):
        receiver = mock.Mock(received=3_750_000, started=None)
        receiver.run.return_value = 3
        after = dict(self.before, rx_bytes=3_750_000)
        with mock.patch.object(cal, 'run_command', return_value='1.1.1.1 dev eth0'), \
                mock.patch.object(cal, 'DiagnosticReceiver', return_value=receiver), \
                mock.patch.object(self.adapter, 'counters', side_effect=[self.before, after]):
            self.assertEqual(self.job.measure(3)['state'], 'no_errors')
        receiver.received = 1000
        with mock.patch.object(cal, 'run_command', return_value='1.1.1.1 dev eth0'), \
                mock.patch.object(cal, 'DiagnosticReceiver', return_value=receiver), \
                mock.patch.object(self.adapter, 'counters', side_effect=[self.before, after]):
            self.assertEqual(self.job.measure(3)['state'], 'inconclusive')
        after['generation'] = 2
        with mock.patch.object(cal, 'run_command', return_value='1.1.1.1 dev eth0'), \
                mock.patch.object(cal, 'DiagnosticReceiver', return_value=receiver), \
                mock.patch.object(self.adapter, 'counters', side_effect=[self.before, after]):
            with self.assertRaises(cal.CalibrationError):
                self.job.measure(3)

    def test_preset_dns_failure_aborts_before_any_traffic_or_profile_change(self):
        self.job = cal.DiagnosticJob(self.adapter, self.store, None, None, True,
                                     self.worker.update, preset='iperf3.moji.fr')
        with mock.patch.object(cal, 'resolve_public_server',
                               side_effect=cal.CalibrationError('DNS timed out')), \
                mock.patch.object(cal, 'DiagnosticReceiver') as receiver:
            self.worker.run_job(self.job)
        self.assertEqual(self.worker.result['phase'], 'failed')
        self.assertIn('DNS', self.worker.result['error'])
        receiver.assert_not_called()
        self.assertEqual(self.adapter.commands, [])
        self.store.save.assert_not_called()

    def test_preset_request_is_async_and_requires_consent_without_custom_address(self):
        with mock.patch.object(service, 'adapters', return_value=[self.adapter]), \
                mock.patch.object(service.threading, 'Thread'), \
                mock.patch.object(cal, 'resolve_public_server') as lookup:
            self.worker.request({'version': 1, 'action': 'start', 'preset': 'scaleway-france',
                                 'public': True, 'consent': True})
            lookup.assert_not_called()
            self.assertIsInstance(self.worker.job, cal.DiagnosticJob)
            for arguments in ({'public': False}, {'consent': False},
                              {'address': '1.1.1.1', 'port': 5201}, {'preset': 'unknown'}):
                request = {'version': 1, 'action': 'start', 'preset': 'scaleway-france',
                           'public': True, 'consent': True, **arguments}
                with self.assertRaises(cal.CalibrationError):
                    service.Service(self.store).request(request)
        self.store.save.assert_not_called()

    def test_dns_only_accepts_public_ipv4_and_is_bounded(self):
        server = cal.public_server('scaleway-france')
        cancel = cal.threading.Event()
        with mock.patch.object(cal, 'run_command', return_value='["8.8.8.8","1.1.1.1"]') as command:
            self.assertEqual(cal.resolve_public_server(server, cancel), '1.1.1.1')
        self.assertEqual(command.call_args.kwargs, {'timeout': 5, 'cancel': cancel})
        for output in ('[]', '["127.0.0.1"]', '["192.168.2.79"]',
                       '["1.1.1.1","10.0.0.1"]', '["::1"]', '{}'):
            with mock.patch.object(cal, 'run_command', return_value=output):
                with self.assertRaises(cal.CalibrationError):
                    cal.resolve_public_server(server, cancel)

    def test_dns_timeout_and_cancellation_are_explicit_without_fallback(self):
        server = cal.public_server('scaleway-france')
        cancel = cal.threading.Event()
        with mock.patch.object(cal, 'run_command',
                               side_effect=cal.CalibrationError('command timed out')) as command:
            with self.assertRaisesRegex(cal.CalibrationError, 'DNS lookup failed or timed out'):
                cal.resolve_public_server(server, cancel)
        self.assertEqual(command.call_count, 1)
        cancel.set()
        with mock.patch.object(cal, 'run_command') as command:
            with self.assertRaises(cal.Cancelled):
                cal.resolve_public_server(server, cancel)
        command.assert_not_called()

    def test_busy_server_is_reported_without_another_connection(self):
        receiver = cal.DiagnosticReceiver(self.job, self.adapter.address())
        with mock.patch.object(receiver, 'read', return_value=b'\xff') as read:
            with self.assertRaisesRegex(cal.CalibrationError, 'busy or refused'):
                receiver.expect(mock.Mock(), 9)
        read.assert_called_once()

    def test_clean_limited_rate_test_never_retunes_saves_or_certifies_gigabit(self):
        self.run_diagnostic()
        result = self.worker.result['diagnostic']
        self.assertEqual(result['state'], 'no_errors')
        self.assertFalse(result['saved'])
        self.assertFalse(result['gigabit_verified'])
        self.assertEqual(self.adapter.commands, [])
        self.store.save.assert_not_called()
        self.assertEqual(self.worker.health.state['state'], 'insufficient_traffic')

    def test_crc_is_reported_and_does_not_change_existing_calibration(self):
        self.after['crc:rx_crc'] = 1
        self.run_diagnostic(cal.CRCError('CRC'), received=100_000)
        self.assertEqual(self.worker.result['diagnostic']['state'], 'corruption')
        self.assertEqual(self.worker.health.state['state'], 'corruption')
        self.assertEqual(self.adapter.commands, [])
        self.store.save.assert_not_called()

    def test_low_payload_is_inconclusive_not_a_clean_calibration(self):
        self.run_diagnostic(received=100_000)
        self.assertEqual(self.worker.result['diagnostic']['state'], 'inconclusive')
        self.store.save.assert_not_called()

    def test_generation_change_aborts_diagnostic(self):
        self.after['generation'] = 2
        self.run_diagnostic()
        self.assertEqual(self.worker.result['phase'], 'failed')
        self.assertIn('reopened', self.worker.result['error'])
        self.store.save.assert_not_called()

    def test_connection_failure_does_not_hide_crc_observed_during_the_check(self):
        self.after['crc:rx_crc'] = 1
        self.run_diagnostic(OSError('connection timed out'), received=0)
        self.assertEqual(self.worker.result['phase'], 'failed')
        self.assertEqual(self.worker.health.state['state'], 'corruption')
        self.store.save.assert_not_called()

    def test_off_adapter_traffic_is_rejected(self):
        self.after['rx_bytes'] = 0
        self.run_diagnostic()
        self.assertEqual(self.worker.result['phase'], 'failed')
        self.assertIn('onboard', self.worker.result['error'])

    def test_control_lengths_are_bounded_before_receiving(self):
        receiver = cal.DiagnosticReceiver(self.job, self.adapter.address())
        stream = mock.Mock()
        with self.assertRaises(cal.CalibrationError):
            receiver.read(stream, 65_537, cal.time.monotonic() + 5)
        stream.recv.assert_not_called()

    def test_receive_cap_is_enforced_even_if_server_ignores_requested_rate(self):
        job = types.SimpleNamespace(cancel=cal.threading.Event(), inspect=mock.Mock(),
                                    attempt_seconds=20, attempt_payload=cal.PUBLIC_MAX_BYTES + 1_000_000)
        receiver = cal.DiagnosticReceiver(job, '127.0.0.1')
        control, data = mock.Mock(), mock.Mock()
        for stream in (control, data):
            stream.__enter__ = mock.Mock(return_value=stream)
            stream.__exit__ = mock.Mock()
        data.recv.side_effect = lambda size: b'x' * size
        response = cal.json.dumps({'streams': []}).encode()
        with mock.patch.object(receiver, 'connect', side_effect=[control, data]), \
                mock.patch.object(receiver, 'expect'), \
                mock.patch.object(receiver, 'read', side_effect=[
                    cal.struct.pack('!I', len(response)), response]), \
                mock.patch.object(cal.select, 'select', return_value=([data], [], [])):
            receiver.run()
        self.assertEqual(receiver.received, cal.PUBLIC_MAX_BYTES)
        self.assertTrue(all(call.args[0] <= 64 * 1024 for call in data.recv.call_args_list))

    def test_cancel_does_not_read_or_change_calibration(self):
        self.job.cancel.set()
        receiver = cal.DiagnosticReceiver(self.job, self.adapter.address())
        with self.assertRaises(cal.Cancelled):
            receiver.check()
        self.assertEqual(self.adapter.commands, [])

    def test_public_request_selects_diagnostic_instead_of_calibration_worker(self):
        with mock.patch.object(service, 'adapters', return_value=[self.adapter]), \
                mock.patch.object(service.threading, 'Thread'):
            self.worker.request({'version': 1, 'action': 'start', 'address': '1.1.1.1',
                                 'port': 5201, 'public': True, 'consent': True})
        self.assertIsInstance(self.worker.job, cal.DiagnosticJob)


class HealthTests(unittest.TestCase):
    def test_recovered_link_clears_stale_link_down_without_claiming_healthy(self):
        adapter = FakeAdapter()
        health = cal.Health()
        with mock.patch.object(adapter, 'link_ready', side_effect=[False, True, True]):
            health.sample(adapter, 0)
            self.assertEqual(health.state['state'], 'link_down')
            health.sample(adapter, 5)
            self.assertEqual(health.state['state'], 'unknown')
            health.sample(adapter, 6)
            self.assertEqual(health.state['state'], 'unknown')
        with mock.patch.object(adapter, 'link_ready', return_value=False):
            health.sample(adapter, 7)
        self.assertEqual(health.state['state'], 'link_down')

    def test_verified_traffic_seeds_recent_health_but_reopen_invalidates_it(self):
        adapter = FakeAdapter()
        before = dict(adapter.counters(), generation=1)
        after = dict(before, rx_bytes=3_525_000_000)
        health = cal.Health()
        health.verified(adapter, {'before': before, 'after': after, 'ended': 30,
                                  'evidence': evidence()})
        self.assertEqual(health.state['state'], 'healthy')
        with mock.patch.object(adapter, 'counters', return_value=after):
            health.sample(adapter, 31)
        self.assertEqual(health.state['state'], 'healthy')
        with mock.patch.object(adapter, 'counters', return_value=dict(after, generation=2)):
            health.sample(adapter, 32)
        self.assertEqual(health.state['state'], 'unknown')

    def test_old_verified_result_does_not_certify_idle_or_changed_adapter(self):
        adapter = FakeAdapter()
        before = dict(adapter.counters(), generation=1)
        after = dict(before, rx_bytes=3_525_000_000)
        health = cal.Health()
        health.verified(adapter, {'before': before, 'after': after, 'ended': 30,
                                  'evidence': evidence()})
        with mock.patch.object(adapter, 'counters', return_value=after):
            health.sample(adapter, 91)
        self.assertEqual(health.state['state'], 'insufficient_traffic')
        adapter.identity = {'backend': 'other'}
        with mock.patch.object(adapter, 'counters', return_value=after):
            health.sample(adapter, 92)
        self.assertEqual(health.state['state'], 'unknown')
    def test_new_errors_remove_green_status_before_window_finishes(self):
        adapter = FakeAdapter()
        health = cal.Health()
        before = adapter.counters()
        after = dict(before, rx_bytes=cal.MIN_HEALTH_BYTES)
        corrupted = dict(after, **{'crc:rx_crc': 1})
        with mock.patch.object(adapter, 'counters', side_effect=[before, after, corrupted]):
            health.sample(adapter, 0)
            health.sample(adapter, 60)
            self.assertEqual(health.state['state'], 'healthy')
            health.sample(adapter, 65)
        self.assertEqual(health.state['state'], 'corruption')

    def test_lifetime_errors_do_not_poison_new_health_window(self):
        adapter = FakeAdapter()
        health = cal.Health()
        baseline = adapter.counters()
        baseline['crc:rx_crc'] = 500
        after = dict(baseline, rx_bytes=cal.MIN_HEALTH_BYTES)
        with mock.patch.object(adapter, 'counters', side_effect=[baseline, after]):
            health.sample(adapter, 0)
            health.sample(adapter, 60)
        self.assertEqual(health.state['state'], 'healthy')

    def test_no_traffic_and_reset_are_not_healthy(self):
        adapter = FakeAdapter()
        health = cal.Health()
        health.sample(adapter, 0)
        health.sample(adapter, 60)
        self.assertEqual(health.state['state'], 'insufficient_traffic')
        baseline = dict(adapter.counters(), **{'crc:rx_crc': 5})
        with mock.patch.object(adapter, 'counters', side_effect=[baseline, adapter.counters()]):
            health.reset()
            health.sample(adapter, 0)
            health.sample(adapter, 60)
        self.assertEqual(health.state['state'], 'unknown')


if __name__ == '__main__':
    unittest.main()
