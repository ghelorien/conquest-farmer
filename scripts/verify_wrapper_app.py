"""Exercise the actual wrapper UI with an isolated, animated native fixture."""
import argparse
import json
from pathlib import Path
import sys
import time
import tkinter as tk
import uuid

import win32gui

from conquest.client_wrapper import ClientCatalog, LaunchWatch, ClientWindow
from conquest.desktop_app import DesktopApp
from conquest.win32 import WindowsBackend
from conquest.window_host import use_unaware_dpi


def fixture(args):
    root = tk.Tk()
    root.title(args.tag)
    root.geometry('800x600+70+70')
    entry = tk.Entry(root,font=('Segoe UI',16))
    entry.pack(fill='x',padx=30,pady=15)
    entry.insert(0,'Keyboard test: ')
    entry.bind('<KeyRelease>',lambda event: Path(args.stop_file+'.typing').write_text(entry.get()))
    canvas = tk.Canvas(root,bg='#101b2b',highlightthickness=0)
    canvas.pack(fill='both',expand=True)
    started = time.monotonic()
    def draw():
        if Path(args.stop_file).exists() or time.monotonic()-started>180:
            root.destroy()
            return
        canvas.delete('all')
        width,height = canvas.winfo_width(),canvas.winfo_height()
        canvas.create_text(width/2,height/2-65,text='Native client surface',fill='#e2ebf5',font=('Segoe UI',24,'bold'))
        canvas.create_text(width/2,height/2-20,text='Wrapper verification · no game connection',fill='#8aa6c0',font=('Segoe UI',13))
        x = 50+(time.monotonic()-started)*90 % max(width-100,1)
        canvas.create_oval(x-12,height/2+35,x+12,height/2+59,fill='#3ed4a0',outline='')
        canvas.create_text(width/2,height-45,text=f'{width} × {height} · rendering live',fill='#8aa6c0',font=('Segoe UI',12))
        root.after(40,draw)
    draw()
    root.mainloop()


class FixtureObserver:
    closed = False
    reads = 0
    def __call__(self):
        self.reads+=1
        return {'monsters':[],'observations_available':True,'focused':False,'minimized':False,
                'blockers':['Test client: game input unavailable']}
    def close(self): self.closed = True


def verify(args):
    tag = 'Conquest wrapper fixture '+uuid.uuid4().hex
    output = Path('reports/wrapper-app-verification')/uuid.uuid4().hex
    output.mkdir(parents=True)
    stop_file = output/'stop-fixture.request'
    backend = WindowsBackend()
    catalog = ClientCatalog(backend,Path(sys.executable).name,title_prefix=tag)
    watch = LaunchWatch(catalog,[sys.executable,str(Path(__file__).resolve()),'--child',
        '--tag',tag,'--stop-file',str(stop_file.resolve())],cwd=Path.cwd())
    observers = []
    def observer(*args):
        item = FixtureObserver()
        observers.append(item)
        return item
    root = tk.Tk()
    app = DesktopApp(root,'profiles/desktop-foreground.local.yaml',catalog=catalog,
        launch_watch=watch,observer_factory=observer,output=output,requires_elevation=False)
    root.title('Conquest Wrapper Preview')
    app.start_button.state(['disabled'])
    app.detail_text.set('Isolated wrapper test. Conquer is not connected to this preview.')
    result = {'live_conquer_tested':False,'scope':'actual DesktopApp with native fixture'}
    started = time.monotonic()
    stage, deadline, saved, candidate = 'launch',0,None,None
    app.launch()
    def step():
        nonlocal stage,deadline,saved,candidate
        try:
            now = time.monotonic()
            if now-started>(180 if args.keyboard_preview else args.preview_seconds+18):
                raise TimeoutError(f'Wrapper verification timed out at {stage}: {app.state_text.get()}')
            if stage=='launch' and app.host.saved:
                saved = app.host.saved
                candidate = ClientWindow(saved.identity,saved.hwnd,tag)
                assert win32gui.GetParent(saved.hwnd)==app.pane.winfo_id()
                assert app.runtime and observers[0].reads>0
                result['launched_and_embedded_before_login']=True
                root.geometry('1400x780')
                deadline,stage = now+max(args.preview_seconds,.6),'resize'
                print('Wrapper preview ready',flush=True)
            elif stage=='resize' and now>=deadline:
                assert win32gui.GetClientRect(saved.hwnd)[2:]==(app.pane.winfo_width(),app.pane.winfo_height())
                result['resized']=True
                assert app.show_game(), app.state_text.get()
                assert app.host.api.contains(saved.hwnd,app.host.api.thread_info(saved.hwnd)[1].hwndFocus)
                result['keyboard_focus_handed_to_client']=True
                if args.keyboard_preview:
                    stage='keyboard_preview'
                    print('Keyboard preview ready: click the test entry and type focusworks',flush=True)
                else:
                    app.release()
                    deadline,stage = now+.3,'released'
            elif stage=='keyboard_preview':
                typed = Path(str(stop_file)+'.typing')
                if typed.exists() and 'focusworks' in typed.read_text():
                    result['native_entry_received_keyboard_text']=True
                    app.release()
                    deadline,stage = now+.3,'released'
            elif stage=='released' and now>=deadline:
                restored = app.host.api.snapshot(saved.hwnd,saved.identity)
                assert app.host.saved is None and restored==saved
                assert observers[0].closed and app.runtime is None
                result['released_with_original_window_and_observers_stopped']=True
                app.embed(candidate)
                assert app.host.saved and app.runtime
                app.control.update({'enabled':True,'target_ids':[123]})
                stop_file.write_text('Fixture close requested')
                stage='client_closed'
            elif stage=='client_closed' and app.host.saved is None:
                assert not app.control.snapshot()['enabled']
                assert app.runtime is None and observers[-1].closed
                assert root.winfo_exists()
                result['reconnected']=True
                result['client_exit_stopped_observers_and_left_wrapper_open']=True
                result['passed']=True
                app.close()
                return
        except Exception as error:
            result.update(passed=False,error=f'{type(error).__name__}: {error}')
            stop_file.write_text('Verification cleanup')
            app.close()
            return
        root.after(100,step)
    root.after(100,step)
    try:
        root.mainloop()
    finally:
        stop_file.write_text('Verification finished')
        if watch.process:
            watch.process.wait(timeout=5)
        result['finished_at']=time.time()
        report = Path('reports/wrapper-app-verification.json')
        report.write_text(json.dumps(result,indent=2))
        print(json.dumps(result),flush=True)
    if not result.get('passed'):
        raise SystemExit(1)


if __name__=='__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--child',action='store_true')
    parser.add_argument('--tag',default='')
    parser.add_argument('--stop-file',default='')
    parser.add_argument('--preview-seconds',type=float,default=0)
    parser.add_argument('--keyboard-preview',action='store_true')
    args = parser.parse_args()
    use_unaware_dpi()
    fixture(args) if args.child else verify(args)
