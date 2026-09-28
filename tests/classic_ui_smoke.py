"""Real Tk + local sensor test with forbidden-model imports and UI boundedness."""
from __future__ import annotations
import importlib.abc
import json
import os
import platform
from pathlib import Path
import sys
import tempfile
import time
import tkinter as tk

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BASE))
BLOCKED={'openvino','openvino_genai','openvino_tokenizers','torch','transformers',
         'nncf','huggingface_hub','gemma_client','gemma_paths'}
class DenyModels(importlib.abc.MetaPathFinder):
    def find_spec(self,fullname,path=None,target=None):
        if fullname.split('.')[0] in BLOCKED:
            raise AssertionError('Forbidden runtime import: '+fullname)
sys.meta_path.insert(0,DenyModels())

from deskpet import DeskPet
config_path=BASE/'config.json'
original=config_path.read_bytes()
app=None
failures=[]
checks=[]

def check(name, fn):
    try:
        fn(); checks.append(name)
    except Exception as exc:
        import traceback
        failures.append((name,traceback.format_exc()))

try:
    app=DeskPet(tk_scale=96/72)
    check('startup button feedback',lambda: (app.rule_button.invoke(),app.status_button.invoke()))
    assert not hasattr(app,'gemma')
    assert app.status_button.cget('text')=='상태'
    app.root.after(350,app.show_diagnostics)

    def exercise():
        assert app.latest is not None, 'No basic sensor sample'
        assert app.context is not None
        app.rule_button.invoke()
        assert app._current_display_message()
        app.status_button.invoke()
        assert app._status_line
        app.toggle_details()
        app.root.update()
        labels=[r.label for r in app._rows]
        assert '작업' in labels and '서버' not in labels, labels   # servers moved to ServerCat
        assert app.root.winfo_height()==app.card.height, 'window does not match card height'
        assert app.card.card_bbox[3] <= app.card.height, 'card clipped'
        rb=app.stage.bbox('row')
        assert rb and rb[3] <= app.card.card_bbox[3]+1, ('row text clipped', rb)
        # Filter away sensor noise for deterministic timeline accounting.
        view=app._diag_window
        view.category.set('UI')
        for i in range(750):app.diag.event('UI','stress_row','line1\nline2',index=i)
        view._tick()
        assert len(view._timeline_rows)==500
        kept_first=view._timeline_rows[0][0]
        assert f'#{kept_first} ' in view.timeline.get('1.0','end')
        assert f'#{kept_first-1} ' not in view.timeline.get('1.0','end')
        for i in range(30):app.diag.event('UI','append_row',str(i))
        view._tick()
        assert len(view._timeline_rows)==500
        text=view.timeline.get('1.0','end')
        assert f'#{view._timeline_rows[0][0]} ' in text
        assert f'#{kept_first} ' not in text
        before=text
        view._tick()
        assert view.timeline.get('1.0','end')==before, 'Duplicate timeline rows'
        view.category.set('TEMP');view._tick()
        assert 'stress_row' not in view.timeline.get('1.0','end')
        view.category.set('ALL');view._tick()
        summary=view.summary.get('1.0','end')
        assert 'Classic 대사 엔진' in summary and 'EXAONE' not in summary
        assert not BLOCKED.intersection(sys.modules)
        # Optional screenshot for human visual inspection, not measured sensor evidence.
        capture=os.environ.get('DESKPET_QA_SCREENSHOT')
        if capture:
            from PIL import ImageGrab
            app.root.update()
            ImageGrab.grab().save(capture)
        app.diag.export(Path(os.environ['DESKPET_CLASSIC_DATA_DIR'])/'classic-smoke.zip')

    app.root.after(2600,lambda:check('buttons, sensor, timeline append/trim/filter, diagnostics export',exercise))
    app.root.after(3800,app.close)
    app.run()
    assert not BLOCKED.intersection(sys.modules)
    errors=[r for r in app.diag.records() if r['level'] in {'ERROR','CRITICAL'}]
    assert not errors,errors
    assert not app.resource_monitor._thread.is_alive()
    if failures: raise AssertionError(failures)
    print('PASS: model import denylist throughout startup, sensors, buttons, diagnostics, shutdown')
    for name in checks:print('PASS:',name)
    print('PASS: 500-record incremental log retention and filter rebuilding')
    print(f'Scope: {platform.system()} Tk and local baseline sensors for ~4 s; not long-running use or NPU compilation.')
finally:
    if app is not None and not app._closed:app.close()
    config_path.write_bytes(original)
