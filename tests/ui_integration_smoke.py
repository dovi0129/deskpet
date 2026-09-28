from pathlib import Path
import platform
import sys
BASE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BASE))
from deskpet import DeskPet
p=(BASE/'config.json'); original=p.read_bytes()
app=None
try:
    app=DeskPet(tk_scale=96/72)
    app.root.after(350, app.show_diagnostics)
    app.root.after(2400, app.close)
    app.run()
    errors=[x for x in app.diag.records() if x['level'] in {'ERROR','CRITICAL'}]
    assert not errors, errors
    assert app.latest is not None, 'real basic monitor never produced a snapshot'
    assert not hasattr(app, 'gemma'), 'Classic has model client'
    print(f'Live Tk mainloop + real basic {platform.system()} sensor + unified diagnostic window + clean shutdown: PASS')
    print('Scope: a ~2 s run; long-running sensor behaviour and NPU counter correctness were not exercised.')
finally:
    if app is not None and not app._closed:
        app.close()
    p.write_bytes(original)
