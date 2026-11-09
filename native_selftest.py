"""Real SendInput integration tests against our own disposable white window.

Run with py native_selftest.py. No input is sent unless that window owns focus.
"""
import ctypes
from ctypes import wintypes as w
import datetime as dt
import json
from pathlib import Path
import tempfile
import time
import tkinter as tk
from unittest.mock import patch

from autoclicker import App, Hotkeys, KEYS, parse_keys, validate_action
from click_tester import ClickTester


def action(key='A', kind='键盘按键', count=1, delay=.02, x='', y=''):
    return validate_action(dict(key=key, kind=kind, count=count, delay=delay, x=x, y=y))


def main():
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
    root = tk.Tk()
    root.withdraw()
    results = []
    native = []
    api = ctypes.WinDLL('user32', use_last_error=True)
    api.GetAncestor.argtypes = [w.HWND, w.UINT]
    api.GetAncestor.restype = w.HWND
    api.GetForegroundWindow.restype = w.HWND
    api.GetFocus.restype = w.HWND
    proc_type = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, w.HWND, w.UINT, w.WPARAM, w.LPARAM)
    api.SetWindowLongPtrW.argtypes = [w.HWND, ctypes.c_int, ctypes.c_void_p]
    api.SetWindowLongPtrW.restype = ctypes.c_void_p
    api.CallWindowProcW.argtypes = [ctypes.c_void_p, w.HWND, w.UINT, w.WPARAM, w.LPARAM]
    api.CallWindowProcW.restype = ctypes.c_ssize_t
    with tempfile.TemporaryDirectory() as folder, patch('autoclicker.CONFIG', Path(folder)/'config.json'):
        app = App(root)
        app.listener.close()  # Full key sweep must include F8/F9 without invoking global controls.
        app.open_tester()
        target, tester = app.test_window, app.tester
        target.title('自动验收测试 — 请勿切换窗口')
        target.lift()
        tester.canvas.focus_force()
        root.update()
        top = api.GetAncestor(tester.canvas.winfo_id(), 2)
        hwnd = api.GetFocus()
        previous_procs = {}

        @proc_type
        def proc(hwnd, msg, wp, lp):
            if msg in (0x100, 0x101, 0x104, 0x105):
                native.append(('down' if msg in (0x100, 0x104) else 'up', int(wp)))
            if msg in (0x201, 0x202, 0x204, 0x205, 0x207, 0x208):
                native.append(('mouse', msg))
            # Suppress system menu activation by a standalone Alt/F10 in this test window.
            if msg == 0x112 and (wp & 0xfff0) == 0xf100:
                return 0
            return api.CallWindowProcW(previous_procs[hwnd], hwnd, msg, wp, lp)

        for handle in {hwnd, tester.canvas.winfo_id()}:
            previous_procs[handle] = api.SetWindowLongPtrW(handle, -4, ctypes.cast(proc, ctypes.c_void_p))

        def pump(seconds=.06):
            end = time.monotonic()+seconds
            while time.monotonic() < end:
                root.update()
                time.sleep(.002)

        def focus():
            target.lift()
            tester.canvas.focus_force()
            pump(.02)
            assert api.GetForegroundWindow() == top, '测试窗口没有前台焦点，已禁止发送输入'

        def send(a):
            assert api.GetForegroundWindow() == top, '焦点离开测试窗口，已禁止发送输入'
            app.backend.perform(a)

        def check(name, fn):
            try:
                focus()
                fn()
                results.append(dict(name=name, passed=True))
            except Exception as e:
                app.runner.stop()
                results.append(dict(name=name, passed=False, error=repr(e)))

        def key_case(name):
            native.clear()
            before = tester.key_count
            send(action(name))
            pump()
            keys = parse_keys(name)
            expected = [('down', k) for k in keys]+[('up', k) for k in reversed(keys)]
            assert native == expected, f'expected={expected}, actual={native}, tester={tester.key_count-before}'
            assert tester.key_count > before, f'测试器未显示 {name}'

        try:
            # Windows may deny foreground activation to a process launched in the background.
            deadline = time.monotonic()+30
            while api.GetForegroundWindow() != top and time.monotonic() < deadline:
                pump(.05)
            for name in KEYS:
                check('按键 '+name, lambda name=name: key_case(name))
            for name in ['Ctrl+A', 'Ctrl+Shift+A', 'Shift+A', 'Ctrl+Alt+B']:
                check('组合键 '+name, lambda name=name: key_case(name))

            def mouse_case(kind, num, messages):
                tester.reset()
                native.clear()
                x = tester.canvas.winfo_rootx()+80
                y = tester.canvas.winfo_rooty()+80
                send(action(kind=kind, x=x, y=y))
                pump()
                assert tester.counts[num] == 1, tester.counts
                assert native == [('mouse', msg) for msg in messages], native
                # Repeat with coordinates omitted, using the current pointer position.
                send(action(kind=kind))
                pump()
                assert tester.counts[num] == 2, tester.counts
            for kind, num, messages in [('鼠标左键', 1, (0x201, 0x202)), ('鼠标右键', 3, (0x204, 0x205)), ('鼠标中键', 2, (0x207, 0x208))]:
                check(kind+'（固定坐标及当前位置）', lambda kind=kind,num=num,messages=messages: mouse_case(kind,num,messages))

            app.listener = Hotkeys(app.events)
            app.register_hotkeys()
            app.runner.backend = type('FocusedInput', (), {'perform': staticmethod(send)})()

            def wait_done(timeout=3):
                deadline = time.monotonic()+timeout
                while app.runner.running and time.monotonic() < deadline:
                    pump(.01)
                assert not app.runner.running, '任务超时'
                pump(.04)

            def loops():
                tester.reset()
                app.actions = [action('A', count=3), action('空格', count=2)]
                app.loops.set('4')
                app.countdown.set('0')
                app.infinite.set(False)
                app.toggle()
                wait_done()
                assert tester.key_count == 20, tester.key_count
            check('顺序、动作重复、指定4轮（20次实际按键）', loops)

            def hotkey_controls():
                tester.reset()
                app.actions = [action('A', delay=.04)]
                app.infinite.set(True)
                send(action('F8'))
                pump(.25)
                assert app.runner.running and tester.key_count >= 3
                send(action('F8'))
                pump(.10)
                assert app.runner.paused.is_set()
                count = tester.key_count
                pump(.20)
                assert tester.key_count == count, '暂停后仍有输入'
                send(action('F8'))
                pump(.2)
                assert not app.runner.paused.is_set() and tester.key_count > count
                send(action('F9'))
                pump(.1)
                assert not app.runner.running
                count = tester.key_count
                pump(.15)
                assert tester.key_count == count, '停止后仍有输入'
            check('F8启动、暂停、恢复；无限循环；F9停止', hotkey_controls)

            def countdown():
                tester.reset()
                app.infinite.set(False)
                app.loops.set('1')
                app.countdown.set('.4')
                app.toggle()
                pump(.15)
                assert tester.key_count == 0
                app.stop()
                wait_done()
                assert tester.key_count == 0
            check('倒计时期间无输入、紧急取消', countdown)

            def schedule():
                tester.reset()
                app.countdown.set('0')
                app.schedule_text.set((dt.datetime.now()+dt.timedelta(seconds=2)).strftime('%Y-%m-%d %H:%M:%S'))
                app.schedule()
                assert app.scheduled is not None
                pump(.1)
                assert tester.key_count == 0
                pump(2.1)
                wait_done()
                assert tester.key_count == 1 and app.scheduled is None, tester.key_count
                app.schedule_text.set((dt.datetime.now()+dt.timedelta(seconds=3)).strftime('%Y-%m-%d %H:%M:%S'))
                app.schedule()
                app.cancel_schedule()
                assert app.scheduled is None
            check('定时启动实际输入、取消预约', schedule)

            def wait_action():
                tester.reset()
                app.actions = [action(kind='等待', delay=.15), action('B')]
                app.toggle()
                pump(.08)
                assert tester.key_count == 0
                wait_done()
                assert tester.key_count == 1
            check('等待动作不发送输入', wait_action)

            def edit_and_save():
                app.actions = []
                app.kind.set('键盘按键')
                app.key.set('C')
                app.add()
                root.update()
                assert app.actions[0]['key'] == 'C'
                app.key.set('D')
                app.update()
                assert app.actions[0]['key'] == 'D'
                app.key.set('E')
                app.add()
                root.update()
                app.move(-1)
                assert [a['key'] for a in app.actions] == ['E', 'D']
                app.move(1)
                assert [a['key'] for a in app.actions] == ['D', 'E']
                app.delete()
                assert len(app.actions) == 1
                app.save()
                path = Path(folder)/'export.json'
                with patch('autoclicker.filedialog.asksaveasfilename', return_value=str(path)):
                    app.export()
                app.actions = []
                with patch('autoclicker.filedialog.askopenfilename', return_value=str(path)):
                    app.import_config()
                assert app.actions[0]['key'] == 'D'
            check('添加、更新、上移、下移、删除、保存、导入导出', edit_and_save)

            def reset():
                tester.reset()
                assert tester.key_count == 0 and sum(tester.counts.values()) == 0
                assert tester.canvas.itemcget(tester.total_text, 'text') == '0'
            check('测试器清零', reset)
        finally:
            app.runner.stop()
            if app.runner.thread:
                app.runner.thread.join(1)
            for handle, previous in previous_procs.items():
                api.SetWindowLongPtrW(handle, -4, previous)
            app.close()

    report = dict(time=dt.datetime.now().isoformat(), passed=sum(r['passed'] for r in results),
                  total=len(results), results=results)
    Path('测试结果.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0 if report['passed'] == report['total'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
