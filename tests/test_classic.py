from __future__ import annotations
import ast
from dataclasses import replace
import importlib.abc
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
from app_paths import data_root, calibration_path
from classic_config import load_config
from classic_voice import status_sentence, context_key
from diagnostics import Diagnostics, readable_summary
from event_memory import EventMemory
from voice_engine import VoiceEngine, SpeechDecision
from pet_context import ContextBuilder
from self_test import snap, advance
from sensor_service import SensorService


class ClassicIsolationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.diag = Diagnostics(self.root / 'testlogs')
    def tearDown(self):
        self.diag.close()
        self.tmp.cleanup()

    def test_imports_work_when_model_packages_are_blocked(self):
        program = r"""
import sys, importlib.abc
BLOCKED = {'openvino', 'openvino_genai', 'openvino_tokenizers', 'transformers',
           'torch', 'nncf', 'huggingface_hub', 'gemma_client', 'gemma_paths'}
class Blocker(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in BLOCKED:
            raise AssertionError('Model import attempted: ' + fullname)
sys.meta_path.insert(0, Blocker())
import deskpet, diagnostics_ui, monitor, sensor_service, classic_voice
assert not BLOCKED.intersection(sys.modules)
print('NO_MODEL_IMPORTS')
"""
        result = subprocess.run([sys.executable, '-c', program], cwd=BASE, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('NO_MODEL_IMPORTS', result.stdout)

    def test_distribution_has_no_model_modules_or_runtime_scripts(self):
        forbidden = ['gemma_client.py', 'gemma_worker.py', 'gemma_runtime.py', 'gemma_setup.py',
                     'gemma_download.py', 'gemma_paths.py', 'voice_probe.py', 'requirements-npu.txt',
                     'run_voice_inventory.cmd', 'run_voice_probe_NPU.cmd']
        self.assertFalse([name for name in forbidden if (BASE / name).exists()])

    def test_config_removes_old_model_keys_preserves_user_choices(self):
        path = self.root / 'config.json'
        old = {'schema_version':5, 'x':13, 'y':25, 'pet_name':'콩이', 'transparent':False,
               'gemma': {'enabled':True,'keep_loaded':True},
               'voice': {'mode':'gemma','level':'quiet','family_cooldown_s':45},
               'servers': {'enabled':True,'targets':[{'name':'custom','host':'127.0.0.1','port':7777}]}}
        path.write_text(json.dumps(old))
        cfg = load_config(path, self.diag)
        self.assertEqual(cfg['voice']['mode'], 'rule')
        self.assertEqual(cfg['voice']['level'], 'quiet')
        self.assertNotIn('gemma', cfg)
        self.assertNotIn('servers', cfg)   # server watching moved to ServerCat
        self.assertEqual((cfg['x'],cfg['y'],cfg['pet_name'],cfg['transparent']), (13,25,'콩이',False))
        self.assertEqual(json.loads((self.root/'config.pre-classic.backup.json').read_text()),old)
        self.assertEqual(json.loads(path.read_text()),old)

    def test_config_nan_and_invalid_types_are_safe(self):
        path=self.root/'config.json'
        path.write_text(json.dumps({'voice':{'recent_limit':float('inf'), 'family_cooldown_s':float('nan'), 'level':'wild'},
                                    'x':'wrong', 'y':float('inf'), 'transparent':'false'}))
        cfg=load_config(path,self.diag)
        self.assertEqual(cfg['voice']['recent_limit'],12)
        self.assertEqual(cfg['voice']['family_cooldown_s'],60)
        self.assertEqual(cfg['voice']['level'],'normal')
        self.assertIsNone(cfg['x']); self.assertIsNone(cfg['y'])
        self.assertIs(cfg['transparent'],True)

    def test_default_config_does_not_mutate_other_instances(self):
        one=load_config(self.root/'missing.json',self.diag)
        one['walk']['enabled']=True
        two=load_config(self.root/'missing.json',self.diag)
        self.assertIs(two['walk']['enabled'],False)

    def test_separate_data_root_and_readonly_calibration_migration(self):
        env={'LOCALAPPDATA':str(self.root)}
        old=self.root/'DeskPet'/'temperature_calibration.json'
        old.parent.mkdir()
        payload=b'{"machine_key":"unit", "schema_version":2}'
        old.write_bytes(payload)
        with patch.dict(os.environ,env,clear=True):
            self.assertEqual(data_root(),self.root/'DeskPetClassic')
            new=calibration_path()
            self.assertEqual(new.read_bytes(),payload)
            self.assertEqual(old.read_bytes(),payload)
            new.unlink()  # explicit user reset cannot re-import on restart
            self.assertEqual(calibration_path(),new)
            self.assertFalse(new.exists())
        self.assertEqual(old.read_bytes(),payload)

    def test_existing_calibration_reset_cannot_reimport_old_file(self):
        old=self.root/'DeskPet'/'temperature_calibration.json';old.parent.mkdir();old.write_text('{}')
        new=self.root/'DeskPetClassic'/'temperature_calibration.json';new.parent.mkdir();new.write_text('{}')
        with patch.dict(os.environ,{'LOCALAPPDATA':str(self.root)},clear=True):
            self.assertEqual(calibration_path(),new)
            new.unlink()
            calibration_path()
            self.assertFalse(new.exists())

    def test_explicit_data_directory_never_reads_legacy_settings(self):
        with patch.dict(os.environ,{'DESKPET_CLASSIC_DATA_DIR':str(self.root/'isolated')},clear=True):
            self.assertEqual(data_root(),self.root/'isolated')
            self.assertEqual(calibration_path(),self.root/'isolated/temperature_calibration.json')
            self.assertFalse(calibration_path().exists())

    def test_diagnostics_has_rules_and_npu_sensor_not_model_section(self):
        text=readable_summary({'npu_counter':{'present':True,'counter_valid':False},
                                'voice':{'mode':'rule','catalog_messages':140,'level':'normal'},
                                'gemma':{'status':'ERROR'}})
        self.assertIn('Classic 대사 엔진',text)
        self.assertIn('Windows 센서',text)
        self.assertNotIn('NPU_COMPILE',text)
        self.assertNotIn('EXAONE',text)
        self.assertNotIn('AI 표현 엔진',text)

    def test_actual_catalog_preserved_and_valid(self):
        voice=VoiceEngine(BASE)
        expected=len(json.loads((BASE/'voice_catalog.json').read_text(encoding='utf-8'))['messages'])
        self.assertGreaterEqual(expected,600)
        self.assertEqual(len(voice.catalog.messages),expected)
        self.assertFalse(voice.catalog.errors,voice.catalog.errors)
        for m in voice.catalog.messages:
            self.assertTrue(m.text)
            self.assertLessEqual(len(m.text),30)

    def test_sensor_request_coalesces_without_caller_io(self):
        entered=threading.Event();release=threading.Event();calls=[]
        class Sensor:
            def sample(self):
                calls.append(threading.get_ident());entered.set();release.wait(2)
                return SimpleNamespace(snapshot_id=len(calls))
            def close(self):pass
        service=SensorService(Sensor,diagnostics=self.diag,interval_s=60)
        try:
            self.assertTrue(entered.wait(1))
            for _ in range(1000):service.request_sample()
            self.assertEqual(len(calls),1)
            self.assertNotEqual(calls[0],threading.get_ident())
            release.set()
            limit=time.monotonic()+1
            while len(calls)<2 and time.monotonic()<limit:time.sleep(.005)
            self.assertEqual(len(calls),2)
        finally:
            release.set();service.close()


class ClassicPresentationTests(unittest.TestCase):
    def make_app(self):
        from deskpet import DeskPet
        app=DeskPet.__new__(DeskPet)
        now=time.monotonic()
        s=snap(now)
        app.context=advance(ContextBuilder(),now-12,12)
        app.latest=s
        app.event_memory=EventMemory()
        app.voice=VoiceEngine(BASE)
        app.speech=app.voice.manual(app.context,app.event_memory)
        app._context_key=context_key(app.context,s)
        app._context_epoch=4
        app._last_sample_at=now
        app.annoyed_until=app.pet_until=0
        app._override_message_text=''
        app._status_line='';app._status_until=0;app._status_epoch=-1
        app._manual_display_until=0
        app.diag=Mock();app.monitor=Mock();app._refresh_dialogue=Mock()
        return app

    def test_status_button_works_without_model_and_never_collects_on_ui(self):
        app=self.make_app();app.inspect_status_now()
        self.assertTrue(app._status_line)
        app.monitor.request_sample.assert_called_once()
        app.monitor.sample.assert_not_called()
        self.assertFalse(hasattr(app,'gemma'))
        self.assertEqual(app._status_epoch,app._context_epoch)

    def test_manual_status_invalidated_by_changed_context(self):
        app=self.make_app();app.inspect_status_now();app._status_line='OLD STATE';app._context_epoch+=1
        self.assertNotEqual(app._current_display_message(),'OLD STATE')

    def test_stale_sensor_warning_overrides_manual_sentence(self):
        app=self.make_app();app.inspect_status_now();app._last_sample_at=time.monotonic()-11
        self.assertIn('센서 갱신 지연',app._current_display_message())

    def test_urgent_rule_beats_status_and_petting(self):
        app=self.make_app();app.inspect_status_now();app.pet_until=time.monotonic()+10
        app.voice.visible=Mock(return_value=SimpleNamespace(text='긴급 온도',priority=99))
        self.assertEqual(app._current_display_message(),'긴급 온도')

    def test_petting_survives_ordinary_dialogue_timer(self):
        app=self.make_app();app.pet_until=time.monotonic()+5;app._override_message_text='좋음'
        self.assertEqual(app._current_display_message(),'좋음')

    def test_buttons_respond_before_first_sensor_sample(self):
        app=self.make_app();app.context=None;app.latest=None
        app.ask_rule_voice_now()
        self.assertIn('센서',app._current_display_message())
        app.inspect_status_now()
        self.assertIn('센서',app._current_display_message())

    def test_unknown_temperature_never_claims_stable(self):
        app=self.make_app()
        ctx=replace(app.context,thermal='UNKNOWN',temp_confidence='NONE')
        self.assertIn('온도 확인 필요',status_sentence(ctx,snap(temp_valid=False)))

    def test_dual_load_is_explained_in_status(self):
        app=self.make_app();ctx=replace(app.context,load='BOTH_BUSY',thermal='WARM')
        self.assertIn('CPU·GPU',status_sentence(ctx,app.latest))


if __name__ == '__main__':
    unittest.main()
