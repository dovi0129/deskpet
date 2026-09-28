from __future__ import annotations
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import zipfile
from dataclasses import replace

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
from diagnostics import Diagnostics, clean, cleanup_log_sessions
from pet_context import ContextBuilder
from classic_voice import context_key
from self_test import snap, advance
import self_test


class RegressionTests(unittest.TestCase):
    pass
for _name in ('test_coretemp_decode', 'test_context_load_and_tools', 'test_context_power_and_bowl',
              'test_context_temperature', 'test_events_and_voice', 'test_gap_and_accessory_layout',
              'test_temperature_calibration_v23', 'test_catalog_failure_and_model_free_config'):
    setattr(RegressionTests, _name, lambda self, n=_name: getattr(self_test, n)())


class DiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.diag = Diagnostics(self.root)
    def tearDown(self):
        self.diag.close()
        self.tmp.cleanup()
    def test_immediate_log_and_shared_sequence(self):
        self.assertTrue(self.diag.path.exists())
        threads = [threading.Thread(target=lambda c=c: [self.diag.event(c, 'tick', str(i)) for i in range(100)])
                   for c in ('TEMP', 'NPU', 'SENSOR', 'VOICE')]
        for t in threads: t.start()
        for t in threads: t.join()
        self.assertTrue(self.diag.flush())
        rows = [json.loads(x) for x in self.diag.path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(rows), 401)
        self.assertEqual([r['seq'] for r in rows], list(range(1, 402)))
        self.assertEqual({r['session_id'] for r in rows}, {self.diag.session_id})
        self.assertEqual(self.diag.dropped, 0)
    def test_redaction_traceback_and_export(self):
        try:
            raise ValueError(r'C:\Users\Jane Doe\secret\test.py hf_abcdefghijklmnopqrstuvwxyz user@example.com 203.0.113.1')
        except ValueError as exc:
            self.diag.exception('NPU', 'test_error', exc)
        self.diag.event('APP', 'private_fields', api_key='dont-share', terminal='private shell')
        report = self.diag.export(self.root / 'report.zip')
        with zipfile.ZipFile(report) as z:
            content = '\n'.join(z.read(n).decode() for n in z.namelist())
            self.assertIn('summary.txt', z.namelist())
            self.assertIn('deskpet.log', z.namelist())
        for secret in ('Jane Doe', 'hf_abcdefghijkl', 'user@example.com', '203.0.113.1', 'dont-share', 'private shell'):
            self.assertNotIn(secret, content)
        self.assertIn('Traceback', content)
        self.assertIn('<TOKEN>', content)
    def test_rotation_and_ring(self):
        self.diag.close()
        self.diag = Diagnostics(self.root, max_bytes=1500, backups=2)
        for i in range(60):
            self.diag.event('TEMP', 'sample', 'x'*100, sample=i)
        self.diag.flush()
        self.assertLessEqual(len(list(self.diag.directory.glob('deskpet.log*'))), 3)
        self.assertGreater(len(list(self.diag.directory.glob('deskpet.log*'))), 1)
        self.assertEqual(len(self.diag.records('TEMP')), 60)
    def test_detail_auto_expires(self):
        self.diag.event('TEMP', 'filtered', level='DEBUG')
        self.assertFalse(self.diag.records('TEMP'))
        self.diag.enable_detail(1)
        self.diag.event('TEMP', 'detail', level='DEBUG')
        self.assertTrue(self.diag.records('TEMP'))
        self.diag.verbose_until = time.monotonic()-1
        self.assertFalse(self.diag.verbose)
    def test_queue_pressure_is_reported(self):
        self.diag.close()
        self.diag = Diagnostics(self.root, queue_size=1)
        with self.diag._io:
            for i in range(100):
                self.diag.event('NPU', 'flood', 'x')
        self.assertGreater(self.diag.summary()['logger']['dropped_records'], 0)
        self.assertEqual(len(self.diag.records('NPU')), 100)
    def test_log_cleanup_preserves_recent_minimum_and_user_files(self):
        self.diag.close()
        logs=self.root/'logs'
        # Current session plus 24 synthetic historical DeskPet session dirs.
        now=time.time()
        for i in range(24):
            day=1+i//4
            name=f'202601{day:02d}-120000-{i:08x}'
            d=logs/name; d.mkdir(parents=True,exist_ok=True)
            (d/'deskpet.log').write_bytes(b'x'*1024)
            old=now-(30-i)*86400
            os.utime(d/'deskpet.log',(old,old)); os.utime(d,(old,old))
        user_zip=logs/'keep-this-report.zip'
        user_zip.write_bytes(b'user export')
        result=cleanup_log_sessions(logs, keep_days=7, min_sessions=20, max_total_bytes=200*1024**2, now=now)
        self.assertEqual(result['remaining_sessions'],20)
        self.assertEqual(user_zip.read_bytes(),b'user export')
        self.diag=Diagnostics(self.root)

    def test_failed_primary_directory_falls_back(self):
        bad = self.root/'blocked'
        bad.write_text('not a directory')
        d = Diagnostics(bad)
        try:
            self.assertTrue(d.write_error)
            self.assertTrue(d.path.is_file())
            self.assertTrue(d.flush())
        finally:
            d.close()
            import shutil
            shutil.rmtree(d.directory, ignore_errors=True)


class RulesAndValidationTests(unittest.TestCase):
    def test_percent_bowl_not_watts(self):
        for pct, expected in ((0,r'\___/'), (20,r'\___/'), (21,r'\_._/'), (40,r'\_._/'),
                              (41,r'\.../'), (60,r'\.../'), (61,r'\ooo/'), (80,r'\ooo/'),
                              (81,r'\OOO/'), (100,r'\OOO/')):
            for watts in (1.0, 30.0):
                with self.subTest(pct=pct, watts=watts):
                    c = advance(ContextBuilder(), 0, 10, battery_percent=pct, plugged=True,
                                battery_flow_w=watts, battery_charging_flag=True, battery_discharging_flag=False)
                    self.assertEqual(c.left_accessory, expected)
        c = advance(ContextBuilder(), 0, 10, battery_percent=75, plugged=False, battery_flow_w=-12)
        self.assertEqual(c.left_accessory, '')
        c = advance(ContextBuilder(), 0, 10, battery_percent=100, plugged=True, battery_flow_w=0,
                    battery_charging_flag=False, battery_discharging_flag=False)
        self.assertEqual(c.left_accessory, r'\OOO/')
        # Charge-limit/battery-care mode: AC is connected but charging stops at 80%.
        c = advance(ContextBuilder(), 0, 10, battery_percent=80, plugged=True, battery_flow_w=0,
                    battery_charging_flag=False, battery_discharging_flag=False)
        self.assertEqual(c.power, 'AC_IDLE')
        self.assertEqual(c.left_accessory, r'\ooo/')
    def test_core_index_does_not_reset_hot_hysteresis(self):
        b = ContextBuilder()
        for t in range(12):
            c = b.update(snap(t, cpu_temp_c=90, cpu_temp_sensor_name=f'Max core #{t%8+1} of 8'), t)
        self.assertEqual(c.thermal, 'HOT')
        self.assertNotIn('#', c.temp_source_key)
    def test_legacy_warm_enters_and_releases_without_long_stickiness(self):
        b=ContextBuilder()
        # Low/legacy sensor should become visibly WARM after a short sustained 80+ period.
        for t in range(5):
            c=b.update(snap(t, cpu_temp_c=82.0, cpu_temp_confidence='LEGACY',
                            cpu_temp_source='Calibrated Windows thermal performance counter',
                            cpu_temp_sensor_name=r'\_TZ.THRM'), t)
        self.assertEqual(c.thermal,'WARM')
        # Once it is genuinely below the release band, it should clear in about 5s,
        # not remain stuck for the old 10s + long averaging lag.
        released=False
        for t in range(5,13):
            c=b.update(snap(t, cpu_temp_c=76.5, cpu_temp_confidence='LEGACY',
                            cpu_temp_source='Calibrated Windows thermal performance counter',
                            cpu_temp_sensor_name=r'\_TZ.THRM'), t)
            released |= c.thermal == 'NORMAL'
        self.assertTrue(released, c.thermal)

    def test_unknown_battery_never_invents_fill(self):
        for pct in (None, float('nan')):
            c = advance(ContextBuilder(), 0, 10, battery_percent=pct, plugged=True, battery_flow_w=20,
                        battery_charging_flag=True, battery_discharging_flag=False)
            self.assertEqual(c.left_accessory, '')
    def test_context_epoch_tracks_meaning_not_sampling(self):
        b=ContextBuilder()
        c=advance(b,0,11)
        a=snap(11)
        self.assertEqual(context_key(c,a), context_key(c,snap(12)))
        self.assertNotEqual(context_key(c,a), context_key(c,snap(12,plugged=True)))










class MonitorTests(unittest.TestCase):
    @unittest.skipIf(sys.platform=='win32','Linux smoke intentionally avoids real Windows sensors')
    def test_real_basic_sample_and_cached_diagnostics(self):
        from monitor import SystemMonitor
        monitor=SystemMonitor()
        try:
            result=monitor.sample()
            self.assertIs(monitor.latest_snapshot,result)
            counter=monitor._snapshot_id
            monitor.write_sensor_log_now()
            self.assertEqual(monitor._snapshot_id,counter)
            summary=monitor.diag.summary()
            self.assertIn('temperature',summary)
            self.assertIn('npu_counter',summary)
        finally:
            monitor.close()




if __name__=='__main__':
    unittest.main(verbosity=2)
