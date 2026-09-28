from __future__ import annotations
import io
import json
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

BASE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BASE))
from diagnostics import Diagnostics, readable_summary
from temperature_manager import TemperatureManager
from process_utils import bounded_lines, terminate_tree
from sensor_service import SensorService
from resource_monitor import ResourceMonitor
from voice_engine import VoiceEngine, SpeechDecision, CatalogMessage
from event_memory import EventMemory
from self_test import advance
from pet_context import ContextBuilder


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.diag=Diagnostics(self.root)
    def tearDown(self):
        self.diag.close()
        self.temp.cleanup()



    def test_nearest_search_matches_old_reverse_scan_with_ties(self):
        def old(hist,target,tolerance):
            best=None;distance=float('inf')
            for t,value in reversed(hist):
                delta=abs(t-target)
                if delta<=tolerance and delta<distance:
                    best=value;distance=delta
                if t<target-tolerance:break
            return best
        rng=random.Random(230924)
        for _ in range(100):
            hist=sorted((float(rng.randrange(80)),float(i)) for i in range(120))
            for target in [rng.uniform(-2,82) for _ in range(30)]+[10.5,12.,14.5]:
                self.assertEqual(TemperatureManager._nearest_value(hist,target,1.6),old(hist,target,1.6))
        self.assertIsNone(TemperatureManager._nearest_value([],10))

    def test_recent_zero_does_not_return_entire_history(self):
        mem=EventMemory()
        mem.valid_events=Mock(return_value=[1,2,3])
        self.assertEqual(mem.recent(0,0),[])
        self.assertEqual(mem.recent(0,-1),[])
        self.assertEqual(mem.recent(0,1),[3])

    def test_consumed_events_are_pruned_without_losing_valid_events(self):
        v=VoiceEngine(BASE)
        ctx=advance(ContextBuilder(),0,10)
        mem=EventMemory()
        v._consumed_events.update(str(i) for i in range(10000))
        v.choose(ctx,mem,force=True)
        self.assertEqual(len(v._consumed_events),0)

    def test_required_any_rechecked_when_displaying_or_accepting_ai(self):
        v=VoiceEngine(BASE)
        ctx=advance(ContextBuilder(),0,10)
        d=SpeechDecision('test','전기 연결됨','family','RULE',30,0,99,1,(),(),('POWER_CHARGING','POWER_AC_IDLE'))
        with patch.object(v,'fact_truth',return_value=False):
            self.assertFalse(v.decision_valid(d,ctx,[],10))
            self.assertFalse(v._decision_facts_valid(d,ctx,[]))
        with patch.object(v,'fact_truth',side_effect=lambda c,f:f=='POWER_AC_IDLE'):
            self.assertTrue(v.decision_valid(d,ctx,[],10))

    def test_oversized_protocol_records_are_not_parsed_as_partial_lines(self):
        stream=io.StringIO('x'*1000+'\n'+json.dumps({'kind':'READY'})+'\n'+'tail')
        self.assertEqual(list(bounded_lines(stream,50)),['{"kind": "READY"}\n','tail'])
        self.assertEqual(list(bounded_lines(io.StringIO('x'*1000),50)),[])

    def test_export_streams_files_without_read_bytes(self):
        self.diag.event('APP','test','safe')
        with patch.object(Path,'read_bytes',side_effect=AssertionError('unbounded read_bytes')):
            result=self.diag.export(self.root/'export.zip')
        self.assertTrue(result.is_file())

    def test_resource_attribution_and_rendering(self):
        summary=ResourceMonitor(self.diag).collect()
        self.assertTrue(summary['processes'])
        self.assertTrue(any(r['role']=='UI / rules' for r in summary['processes']))
        self.assertNotIn('cmdline',json.dumps(summary))
        self.assertIn('[프로세스별 메모리]',readable_summary(self.diag.summary()))

    def test_sensor_producer_does_not_block_consumer_or_make_backlog(self):
        entered=threading.Event();release=threading.Event();closed=threading.Event();ids=[]
        class SlowMonitor:
            def sample(self):
                entered.set();release.wait(2)
                ids.append(threading.get_ident())
                return SimpleNamespace(snapshot_id=len(ids))
            def close(self):closed.set()
            def reset_temperature_calibration(self):ids.append(threading.get_ident())
            def write_sensor_log_now(self):pass
        svc=SensorService(SlowMonitor,diagnostics=self.diag,interval_s=.05)
        try:
            self.assertTrue(entered.wait(1))
            t=time.perf_counter()
            for _ in range(1000):self.assertIsNone(svc.sample())
            self.assertLess(time.perf_counter()-t,.1)
            svc.reset_temperature_calibration();release.set()
            deadline=time.monotonic()+1
            while svc.sample() is None and time.monotonic()<deadline:time.sleep(.01)
            self.assertIsNotNone(svc.sample())
            time.sleep(.12)
            self.assertEqual(len(set(ids)),1)
            self.assertNotEqual(ids[0],threading.get_ident())
        finally:
            release.set();svc.close()
        self.assertTrue(closed.wait(1))



    def test_owned_child_process_is_terminated(self):
        proc=subprocess.Popen([sys.executable,'-c','import time;time.sleep(20)'])
        terminate_tree(proc,.2)
        self.assertIsNotNone(proc.poll())

    def test_owned_grandchild_is_terminated_together_with_parent(self):
        import psutil
        code = "import subprocess,sys,time; child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(20)']);print(child.pid,flush=True);time.sleep(20)"
        proc=subprocess.Popen([sys.executable,'-u','-c',code],stdout=subprocess.PIPE,text=True)
        child_pid=int(proc.stdout.readline().strip())
        child=psutil.Process(child_pid)
        try:
            terminate_tree(proc,.3)
            deadline=time.monotonic()+1
            while child.is_running() and child.status()!=psutil.STATUS_ZOMBIE and time.monotonic()<deadline:
                time.sleep(.01)
            self.assertTrue(not child.is_running() or child.status()==psutil.STATUS_ZOMBIE)
            self.assertIsNotNone(proc.poll())
        finally:
            terminate_tree(proc,.1)
            proc.stdout.close()
            if child.is_running():
                try:child.kill()
                except psutil.Error:pass




if __name__=='__main__':unittest.main()
