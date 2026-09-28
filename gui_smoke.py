from pathlib import Path
import os
import tempfile
# Never write affection/daily files into the real user data folder.
os.environ.setdefault('DESKPET_CLASSIC_DATA_DIR', tempfile.mkdtemp(prefix='deskpet-gui-'))
import sys
import time
from types import SimpleNamespace
import tkinter as tk
import deskpet as d
from pet_context import ContextBuilder

CONFIG=Path(__file__).with_name("config.json")
ORIGINAL_CONFIG=CONFIG.read_bytes() if CONFIG.exists() else None
d.DeskPet._start_monitoring=lambda self: None

def inside(child, parent):
    x=child.winfo_rootx()-parent.winfo_rootx()
    y=child.winfo_rooty()-parent.winfo_rooty()
    return (x >= -1 and y >= -1 and x+child.winfo_width() <= parent.winfo_width()+1
            and y+child.winfo_height() <= parent.winfo_height()+1)

try:
    for factor in (1.0,1.25,1.5,2.0):
        app=d.DeskPet(tk_scale=(96/72)*factor)
        app._apply_cat_size('small',save=False)  # layout checks start small whatever the user chose
        app.root.geometry(f"{app.PET_WIDTH}x{app.PET_HEIGHT}+20+20")
        now=time.monotonic()
        s = SimpleNamespace(
            snapshot_id=1, session_id='x', received_at_mono=now, wall_time=time.time(),
            cpu=100.0, cpu_valid=True, ram=99.9, ram_valid=True,
            gpu=100.0, gpu_present=True, gpu_valid=True, gpu_name='Intel(R) Arc(TM) 140V GPU (16GB)',
            gpu_3d=100.0, gpu_compute=0.0, gpu_video_decode=0.0, gpu_video_encode=0.0, gpu_copy=0.0,
            npu_present=True, npu=None, npu_valid=False, npu_source='mock',
            battery_percent=100.0, plugged=True, battery_present=True, battery_flow_w=33.3, battery_flow_valid=True,
            battery_charging_flag=True, battery_discharging_flag=False,
            charged_session_wh=12.345, discharged_session_wh=67.890,
            idle_seconds=0.0, uptime_seconds=100,
            cpu_temp_c=99.0, cpu_temp_confidence='HIGH', cpu_temp_source='Core Temp shared memory',
            cpu_temp_sensor_name='Max core #1 of 8', temp_valid=True, temp_acquired_at=now,
            cpu_temp_raw_thermal_c=95.0, cpu_temp_raw_thermal_source='x', cpu_temp_raw_thermal_sensor_name='x', cpu_temp_calibration_summary='x',
            cpu_temp_diagnostics='', cpu_temp_reliable=True,
            battery_remaining_wh=50.0, battery_design_wh=70.0, battery_full_wh=65.0, battery_health_percent=92.8,
            battery_cycle_count=123, battery_voltage_v=8.0, battery_flow_source='mock',
            tools_present=frozenset({'Claude','Codex','SSH'}), tools_scan_status='OK', tools_scan_seq=1,
            ai_running=True, ssh_running=True, tools_text='Claude / Codex / SSH', probe_ok=True, probe_error='',
            gpu_kind='integrated', gpu_has_discrete=False, gpu_other_count=0, gpu_usage_scope='all-adapters', advanced_age_s=0.1,
        )
        app.latest=s
        app._last_sample_at=now
        builder=ContextBuilder()
        for i in range(13):
            app.context=builder.update(s,now-12+i)
        app.state_machine.update_context(app.context)
        app.speech=app.voice.manual(app.context,app.event_memory)
        app._update_details()
        app.toggle_details()
        app.root.update_idletasks()
        c=app.stage
        W,H=int(c.cget('width')),app.card.height
        assert app.root.winfo_width()==app.DETAIL_WIDTH
        assert app.root.winfo_height()==app._detail_height()==H, (factor,app.root.winfo_height(),H)
        labels=[r.label for r in app._rows]
        assert labels==['CPU','RAM','GPU','NPU','온도','밥','기분','작업'], labels
        npu=[r for r in app._rows if r.label=='NPU'][0]
        assert npu.segments[0][0]=='--', 'unknown NPU must not read as a number'
        x0,y0,x1,y1=app.card.card_bbox
        assert 0<=x0 and x1<=W and y1<=H, (factor,'card outside window')
        rb=c.bbox('row')
        assert rb and x0<=rb[0] and rb[2]<=x1 and rb[3]<=y1+1, (factor,rb,app.card.card_bbox,'row text outside card')
        cb=app.card.cat_bbox
        assert 0<=cb[0] and cb[2]<=W, (factor,'cat clipped')
        # Paws touch the edge but no glyph crosses it: the edge is at/below the last baseline
        # and within the line's descent area.
        assert app.card.paw_baseline<y0<=cb[3]+1, (factor,app.card.paw_baseline,y0,cb,'card edge crosses the cat')
        if app.card.bubble_bbox:
            bb=app.card.bubble_bbox
            assert 0<=bb[0] and bb[2]<=W and bb[3]<=cb[1]+1, (factor,'bubble overlaps cat or window')
        for button in (app.rule_button,app.status_button):
            assert not button.bind('<ButtonPress-1>'), 'quick button inherited drag binding'
        c.itemconfigure(app._buttons_item,state='normal');app.root.update()
        assert app.rule_button.winfo_rooty()+app.rule_button.winfo_height() <= c.winfo_rooty()+cb[1]+1, 'buttons overlap cat'
        c.itemconfigure(app._buttons_item,state='hidden')
        cx=(cb[0]+cb[2])/2
        for pct,watts in ((15,-5),(75,20),(95,20)):
            s.battery_percent=pct;s.battery_flow_w=watts;s.plugged=watts>0
            s.battery_charging_flag=watts>0;s.battery_discharging_flag=watts<0
            b=ContextBuilder()
            for i in range(12):
                app.context=b.update(s,now-11+i)
            app._cat_text=app._compose_stage(app.state_machine.frame(0))
            app._render_card();app.root.update_idletasks()
            nb=app.card.cat_bbox
            assert (nb[0]+nb[2])/2==cx, 'accessory moved cat'
        app.ask_rule_voice_now()
        assert not hasattr(app, 'gemma'), 'no language-model client'
        app.inspect_status_now()
        assert app.status_button.cget('text') == '상태'
        s.npu=0.0;s.npu_valid=True
        app._update_details()
        assert [r for r in app._rows if r.label=='NPU'][0].segments[0][0]=='0%'
        s.npu_present=False
        app._update_details()
        app.root.update_idletasks()
        assert 'NPU' not in [r.label for r in app._rows]
        assert app.root.winfo_height()==app._detail_height()
        # Two GPUs (iGPU + dGPU): one row each, the card grows by one row and still fits.
        one_gpu_height=app._detail_height()
        s.gpu_split=(('iGPU',3.0),('dGPU',None));s.npu_present=True
        app._update_details();app.root.update_idletasks()
        labels=[r.label for r in app._rows]
        assert labels[:5]==['CPU','RAM','iGPU','dGPU','NPU'] and 'GPU' not in labels, labels
        assert [r for r in app._rows if r.label=='iGPU'][0].segments[0][0]=='3%'
        assert [r for r in app._rows if r.label=='dGPU'][0].segments[0][0]=='--', 'unread GPU must not read as 0%'
        assert app.root.winfo_height()==app._detail_height()==app.card.height>one_gpu_height, (factor,'two-GPU height')
        rb=c.bbox('row')
        assert rb and rb[3]<=app.card.card_bbox[3]+1 and rb[2]<=app.card.card_bbox[2], (factor,rb,'two-GPU rows clipped')
        s.gpu_split=();s.npu_present=False
        app._update_details();app.root.update_idletasks()
        assert 'GPU' in [r.label for r in app._rows] and app.root.winfo_height()==one_gpu_height
        app.toggle_details()
        app.root.update_idletasks()
        assert app.root.winfo_height()==app.PET_HEIGHT==app.card.height
        assert app._summary, "collapsed summary empty"
        assert (app.card.cat_bbox[0]+app.card.cat_bbox[2])/2==cx
        # Menu check marks mirror the real state, and toggling flips both.
        def menu_index(label):
            for i in range(app.menu.index('end') + 1):
                try:
                    if app.menu.entrycget(i, 'label').startswith(label):
                        return i
                except tk.TclError:
                    pass
            raise AssertionError(label)
        assert app.menu.type(menu_index('산책')) == 'checkbutton'
        app.set_walk_interval(3)
        assert app.config['walk']['interval_min'] == 3 == app.walk_interval_var.get()
        before = bool(app.config['walk']['enabled'])
        assert app.walk_var.get() == before
        app.menu.invoke(menu_index('산책'))
        assert app.walk_var.get() == bool(app.config['walk']['enabled']) == (not before)
        app.menu.invoke(menu_index('산책'))
        assert app.walk_var.get() == before
        assert app.topmost_var.get() == app._topmost_enabled()
        assert app.cat_size_var.get() == app.config['cat_size'] == 'small', (app.cat_size_var.get(), app.config.get('cat_size'))
        # Summary table: a real grid, numbers right-aligned in their own column.
        win=app.show_daily();win.update_idletasks()
        cells=app._daily_grid.grid_slaves()
        assert len(cells) >= 6, len(cells)
        for w in cells:
            info=w.grid_info()
            if int(info['column'])>0:
                assert w.cget('anchor')=='e', 'numeric cell not right-aligned'
        win.destroy()
        # Large cat mode: same bounds, and the card edge still never crosses the cat.
        app._apply_cat_size('large')
        app.root.update_idletasks()
        W=int(app.stage.cget('width'));cb=app.card.cat_bbox
        assert app.root.winfo_width()==app.PET_WIDTH==W, (factor,'large width')
        assert 0<=cb[0] and cb[2]<=W, (factor,cb,W,'large cat clipped')
        assert app.card.paw_baseline<app.card.card_bbox[1]<=cb[3]+1, (factor,'large card edge crosses the cat')
        assert app.root.winfo_height()==app.PET_HEIGHT==app.card.height, (factor,'large collapsed height')
        app.toggle_details();app.root.update_idletasks()
        assert app.root.winfo_height()==app._detail_height()==app.card.height, (factor,'large expanded height')
        rb=app.stage.bbox('row')
        assert rb and rb[3]<=app.card.card_bbox[3]+1 and rb[2]<=W, (factor,'large rows clipped')
        app.toggle_details();app._apply_cat_size('small');app.root.update_idletasks()
        assert app.root.winfo_width()==app.PET_WIDTH and app.root.winfo_height()==app.PET_HEIGHT
        print(f'{int(factor*100)}% Tk font/geometry simulation: card/bubble/cat bounds, buttons, NPU unknown/zero/absent, iGPU+dGPU rows, fixed cat center, large cat OK')
        app.close()
finally:
    if ORIGINAL_CONFIG is None:
        CONFIG.unlink(missing_ok=True)
    else:
        CONFIG.write_bytes(ORIGINAL_CONFIG)
