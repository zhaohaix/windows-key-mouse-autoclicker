import ctypes
import queue
import threading
import time
import unittest
import datetime as dt
import tempfile
from pathlib import Path
import tkinter as tk
from unittest.mock import patch
from autoclicker import App
from autoclicker import INPUT, Runner, parse_keys, validate_action


def action(**kwargs):
    return validate_action(dict(kind='键盘按键', key='A', delay=.01, count=1) | kwargs)


class FakeInput:
    def __init__(self):
        self.actions = []
        self.received = threading.Event()

    def perform(self, a):
        self.actions.append(a)
        self.received.set()


class Tests(unittest.TestCase):
    def test_native_layout(self):
        self.assertEqual(ctypes.sizeof(INPUT), 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28)

    def test_validation(self):
        self.assertEqual(parse_keys('Ctrl+a'), [17, 65])
        for kwargs in [dict(delay='nan'), dict(delay=0), dict(count=0), dict(key='未知'), dict(key='F13'), dict(key='F24'), dict(x='2', y='', kind='鼠标左键')]:
            with self.assertRaises(ValueError):
                action(**kwargs)

    def test_order_and_repetition(self):
        backend, events = FakeInput(), queue.Queue()
        runner = Runner(backend, events)
        runner.start([action(count=2), action(key='B')], 2, 0)
        runner.thread.join(2)
        self.assertFalse(runner.running)
        self.assertEqual([a['key'] for a in backend.actions], ['A', 'A', 'B'] * 2)

    def test_cancel_countdown(self):
        backend = FakeInput()
        runner = Runner(backend, queue.Queue())
        runner.start([action()], 0, 60)
        runner.stop()
        runner.thread.join(.5)
        self.assertFalse(runner.running)
        self.assertEqual(backend.actions, [])

    def test_pause_resume_stop(self):
        backend = FakeInput()
        runner = Runner(backend, queue.Queue())
        runner.start([action(delay=.2)], 0, 0)
        self.assertTrue(backend.received.wait(1))
        runner.paused.set()
        time.sleep(.08)
        count = len(backend.actions)
        time.sleep(.25)
        self.assertEqual(len(backend.actions), count)
        runner.paused.clear()
        backend.received.clear()
        self.assertTrue(backend.received.wait(1))
        runner.stop()
        runner.thread.join(.5)
        self.assertFalse(runner.running)

    def test_input_failure_is_reported(self):
        class Broken:
            def perform(self, a):
                raise RuntimeError('test failure')
        events = queue.Queue()
        runner = Runner(Broken(), events)
        runner.start([action()], 1, 0)
        runner.thread.join(1)
        messages = list(events.queue)
        self.assertIn(('error', 'test failure'), messages)
        self.assertEqual(messages[-1], ('done', None))

    def test_gui_configuration_and_schedule(self):
        root = tk.Tk()
        root.withdraw()
        with tempfile.TemporaryDirectory() as folder, patch('autoclicker.CONFIG', Path(folder) / 'config.json'):
            app = App(root)
            try:
                backend = FakeInput()
                app.runner.backend = backend
                app.actions = [action(key='B')]
                app.loops.set('2')
                app.countdown.set('0')
                app.write_config(Path(folder) / 'export.json')
                app.actions = []
                app.read_config(Path(folder) / 'export.json')
                self.assertEqual(app.actions[0]['key'], 'B')
                self.assertEqual(app.loops.get(), '2')
                self.assertEqual(len(app.hotkeys), 2, 'global hotkeys must be available')
                app.schedule_text.set((dt.datetime.now()+dt.timedelta(minutes=1)).strftime('%Y-%m-%d %H:%M:%S'))
                app.schedule()
                self.assertIsNotNone(app.scheduled)
                target, args = app.scheduled
                app.scheduled = (dt.datetime.now()-dt.timedelta(seconds=1), args)
                app.poll()
                app.runner.thread.join(1)
                self.assertIsNone(app.scheduled)
                app.open_tester()
                first = app.test_window
                root.update()
                self.assertTrue(first.winfo_exists())
                app.open_tester()
                self.assertIs(app.test_window, first)
                app.tester.key(type('Key', (), {'keysym': 'space'})())
                self.assertEqual(app.tester.key_count, 1)
                app.tester.reset()
                self.assertEqual(app.tester.key_count, 0)
                app.scheduled = (target, args)
                app.runner.start([action()], 0, 60)
                self.assertTrue(app.runner.running)
                app.close_tester()
                app.runner.thread.join(.5)
                self.assertFalse(app.runner.running)
                self.assertIsNone(app.test_window)
                self.assertTrue(app.runner.stop_event.is_set())
                self.assertIsNone(app.scheduled)
                app.open_tester()
                self.assertIsNot(app.test_window, first)
                root.update()
                self.assertEqual([a['key'] for a in backend.actions], ['B', 'B'])
                app.scheduled = (target, args)
                app.stop()
                self.assertIsNone(app.scheduled)
            finally:
                app.close()


if __name__ == '__main__':
    unittest.main()
