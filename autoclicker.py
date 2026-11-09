"""Windows keyboard/mouse sequence runner. Standard library only."""
import ctypes
from ctypes import wintypes
import datetime as dt
import json
import os
from pathlib import Path
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from click_tester import ClickTester

APP = '全键鼠自动连点'
CONFIG = Path(os.environ.get('APPDATA', str(Path.home()))) / 'FullKeyClicker' / 'config.json'
KEYS = {chr(i): i for i in range(65, 91)} | {str(i): 48+i for i in range(10)}
KEYS.update({f'F{i}': 111+i for i in range(1, 13)})
KEYS.update({'空格': 32, '回车': 13, 'Tab': 9, 'Esc': 27, '退格': 8,
             '上': 38, '下': 40, '左': 37, '右': 39, 'Delete': 46,
             'Home': 36, 'End': 35, 'PageUp': 33, 'PageDown': 34,
             'Shift': 16, 'Ctrl': 17, 'Alt': 18})
KINDS = ['键盘按键', '鼠标左键', '鼠标右键', '鼠标中键', '等待']


def parse_keys(value):
    parts = value.split('+')
    result = []
    for part in parts:
        name = part.strip()
        if len(name) == 1:
            name = name.upper()
        if name not in KEYS:
            raise ValueError(f'不支持的按键：{name}。可用 A-Z、0-9、空格、回车、方向键、F1-F12 等。')
        result.append(KEYS[name])
    if len(result) > 4 or len(set(result)) != len(result):
        raise ValueError('每个组合键最多 4 个不同按键。')
    return result


def number(value, label, low, high, integer=False):
    try:
        n = int(value) if integer else float(value)
        if not low <= n <= high:
            raise ValueError()
        return n
    except (ValueError, TypeError, OverflowError):
        raise ValueError(f'{label}必须在 {low} 到 {high} 之间。') from None


def validate_action(raw):
    a = dict(raw)
    if a.get('kind') not in KINDS:
        raise ValueError('未知动作类型。')
    a['key'] = str(a.get('key', '空格'))
    if a['kind'] == '键盘按键':
        parse_keys(a['key'])
    a['delay'] = number(a.get('delay'), '动作后间隔（秒）', .01, 86400)
    a['count'] = number(a.get('count'), '动作重复次数', 1, 1000000, True)
    x, y = str(a.get('x', '')).strip(), str(a.get('y', '')).strip()
    if a['kind'].startswith('鼠标') and (x or y):
        a['x'] = number(x, 'X 坐标', -100000, 100000, True)
        a['y'] = number(y, 'Y 坐标', -100000, 100000, True)
    else:
        a['x'] = a['y'] = ''
    return a


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [('dx', wintypes.LONG), ('dy', wintypes.LONG), ('mouseData', wintypes.DWORD),
                ('dwFlags', wintypes.DWORD), ('time', wintypes.DWORD), ('dwExtraInfo', ctypes.c_size_t)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [('wVk', wintypes.WORD), ('wScan', wintypes.WORD), ('dwFlags', wintypes.DWORD),
                ('time', wintypes.DWORD), ('dwExtraInfo', ctypes.c_size_t)]


class INPUTUNION(ctypes.Union):
    _fields_ = [('mi', MOUSEINPUT), ('ki', KEYBDINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ('u',)
    _fields_ = [('type', wintypes.DWORD), ('u', INPUTUNION)]


class WindowsInput:
    def __init__(self):
        self.api = ctypes.WinDLL('user32', use_last_error=True)
        self.api.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
        self.api.SendInput.restype = wintypes.UINT

    def send(self, event):
        if self.api.SendInput(1, ctypes.byref(event), ctypes.sizeof(INPUT)) != 1:
            raise RuntimeError('系统未接受模拟输入。目标程序以管理员身份运行时，请以相同权限启动本软件。')

    def perform(self, action):
        if action['kind'] == '等待':
            return
        if action['kind'] == '键盘按键':
            pressed = []
            try:
                for key in parse_keys(action['key']):
                    flags = 1 if key in (33, 34, 35, 36, 37, 38, 39, 40, 45, 46) else 0
                    self.send(INPUT(type=1, ki=KEYBDINPUT(wVk=key, dwFlags=flags)))
                    pressed.append((key, flags))
            finally:
                for key, flags in reversed(pressed):
                    self.send(INPUT(type=1, ki=KEYBDINPUT(wVk=key, dwFlags=flags | 2)))
        else:
            if action['x'] != '':
                if not self.api.SetCursorPos(action['x'], action['y']):
                    raise RuntimeError('无法移动鼠标到指定坐标。')
            down, up = {'鼠标左键': (2, 4), '鼠标右键': (8, 16), '鼠标中键': (32, 64)}[action['kind']]
            try:
                self.send(INPUT(type=0, mi=MOUSEINPUT(dwFlags=down)))
            finally:
                self.send(INPUT(type=0, mi=MOUSEINPUT(dwFlags=up)))


class Runner:
    def __init__(self, backend, events):
        self.backend, self.events = backend, events
        self.stop_event = threading.Event()
        self.paused = threading.Event()
        self.thread = None

    @property
    def running(self):
        return self.thread is not None and self.thread.is_alive()

    def wait(self, seconds):
        remaining = seconds
        while remaining > 0:
            if self.stop_event.is_set():
                return False
            if self.paused.is_set():
                self.stop_event.wait(.02)
                continue
            start = time.monotonic()
            self.stop_event.wait(min(.02, remaining))
            remaining -= time.monotonic() - start
        return not self.stop_event.is_set()

    def start(self, actions, loops, countdown):
        if self.running:
            return
        self.stop_event.clear()
        self.paused.clear()
        self.thread = threading.Thread(target=self.run, args=(actions, loops, countdown), daemon=True)
        self.thread.start()

    def run(self, actions, loops, countdown):
        try:
            if not self.wait(countdown):
                return
            cycle = 0
            while not self.stop_event.is_set() and (loops == 0 or cycle < loops):
                cycle += 1
                for index, action in enumerate(actions):
                    self.events.put(('progress', f'第 {cycle} 轮 · 动作 {index+1}/{len(actions)}'))
                    for _ in range(action['count']):
                        if not self.wait(.001):
                            return
                        self.backend.perform(action)
                        if not self.wait(action['delay']):
                            return
            self.events.put(('progress', f'已完成 {cycle} 轮'))
        except Exception as e:
            self.events.put(('error', str(e)))
        finally:
            self.events.put(('done', None))

    def stop(self):
        self.stop_event.set()
        self.paused.clear()


class Hotkeys:
    """Own the Win32 message queue outside Tk's message dispatcher."""
    def __init__(self, events):
        self.events = events
        self.commands = queue.Queue()
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def configure(self, start, stop):
        ready, result = threading.Event(), []
        self.commands.put(('configure', start, stop, ready, result))
        if not ready.wait(2):
            raise RuntimeError('快捷键监听线程未响应。')
        return result

    def run(self):
        api = ctypes.WinDLL('user32', use_last_error=True)
        registered = []
        msg = wintypes.MSG()
        while True:
            try:
                command = self.commands.get(timeout=.01)
            except queue.Empty:
                command = None
            if command:
                for ident in registered:
                    api.UnregisterHotKey(None, ident)
                registered = []
                if command[0] == 'close':
                    return
                _, start, stop, ready, result = command
                for ident, name in [(1, start), (2, stop)]:
                    if api.RegisterHotKey(None, ident, 0x4000, KEYS[name]):
                        registered.append(ident)
                result.extend(registered)
                ready.set()
            while api.PeekMessageW(ctypes.byref(msg), None, 0x312, 0x312, 1):
                self.events.put(('hotkey', int(msg.wParam)))

    def close(self):
        self.commands.put(('close',))
        self.thread.join(timeout=1)


class App:
    def __init__(self, root):
        self.root = root
        self.backend = WindowsInput()
        self.events = queue.Queue()
        self.listener = Hotkeys(self.events)
        self.runner = Runner(self.backend, self.events)
        self.actions = []
        self.scheduled = None
        self.hotkeys = []
        self.test_window = None
        self.tester = None
        self.root.title(APP)
        self.root.geometry('1020x760')
        self.root.minsize(920, 700)
        self.root.protocol('WM_DELETE_WINDOW', self.close)
        self.build()
        self.load_initial()
        self.register_hotkeys()
        self.root.after(30, self.poll)

    def build(self):
        style = ttk.Style()
        style.theme_use('clam')
        style.configure('.', font=('Microsoft YaHei UI', 10))
        style.configure('Treeview', rowheight=30)
        outer = ttk.Frame(self.root, padding=20)
        outer.pack(fill='both', expand=True)
        heading = ttk.Frame(outer)
        heading.pack(fill='x')
        ttk.Label(heading, text=APP, font=('Microsoft YaHei UI', 22, 'bold')).pack(side='left')
        ttk.Button(heading, text='打开测试窗口', command=self.open_tester).pack(side='right', padx=4)
        ttk.Label(outer, text='按顺序执行动作；每轮结束后从头循环。先用少量次数试运行。').pack(anchor='w', pady=(3, 12))
        editor = ttk.LabelFrame(outer, text='动作编辑', padding=10)
        editor.pack(fill='x')
        self.kind = tk.StringVar(value=KINDS[0])
        self.key = tk.StringVar(value='空格')
        self.delay = tk.StringVar(value='0.1')
        self.count = tk.StringVar(value='1')
        self.x, self.y = tk.StringVar(), tk.StringVar()
        fields = [('类型', self.kind, KINDS, 12), ('按键 / 组合键', self.key, list(KEYS), 15),
                  ('间隔（秒）', self.delay, None, 10), ('重复次数', self.count, None, 9),
                  ('鼠标 X（可空）', self.x, None, 12), ('鼠标 Y（可空）', self.y, None, 12)]
        for col, (label, variable, choices, width) in enumerate(fields):
            ttk.Label(editor, text=label).grid(row=0, column=col, sticky='w', padx=4)
            widget = ttk.Combobox(editor, textvariable=variable, values=choices, width=width,
                                  state='readonly' if col == 0 else 'normal') if choices else ttk.Entry(editor, textvariable=variable, width=width)
            widget.grid(row=1, column=col, padx=4, pady=4, sticky='ew')
            editor.columnconfigure(col, weight=1)
        ttk.Label(editor, text='组合键示例 Ctrl+A；鼠标坐标留空时点击当前位置；“等待”仅使用间隔。').grid(row=2, column=0, columnspan=6, sticky='w', pady=5)
        buttons = ttk.Frame(outer)
        buttons.pack(fill='x', pady=8)
        for label, cmd in [('添加动作', self.add), ('更新选中', self.update), ('删除', self.delete), ('上移', lambda: self.move(-1)), ('下移', lambda: self.move(1)), ('3秒后取鼠标坐标', self.capture)]:
            ttk.Button(buttons, text=label, command=cmd).pack(side='left', padx=(0, 6))
        frame = ttk.Frame(outer)
        frame.pack(fill='both', expand=True)
        self.tree = ttk.Treeview(frame, columns=('n', 'kind', 'key', 'delay', 'count', 'pos'), show='headings', selectmode='browse', height=7)
        for col, label, width in [('n', '序号', 50), ('kind', '类型', 120), ('key', '按键', 160), ('delay', '动作后间隔 / 秒', 150), ('count', '重复次数', 100), ('pos', '鼠标坐标', 170)]:
            self.tree.heading(col, text=label)
            self.tree.column(col, width=width, anchor='center')
        self.tree.pack(side='left', fill='both', expand=True)
        scroll = ttk.Scrollbar(frame, command=self.tree.yview)
        scroll.pack(side='right', fill='y')
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.bind('<<TreeviewSelect>>', self.select)
        settings = ttk.LabelFrame(outer, text='运行设置', padding=10)
        settings.pack(fill='x', pady=10)
        self.loops = tk.StringVar(value='10')
        self.infinite = tk.BooleanVar(value=False)
        self.countdown = tk.StringVar(value='3')
        self.schedule_text = tk.StringVar(value=(dt.datetime.now()+dt.timedelta(minutes=5)).strftime('%Y-%m-%d %H:%M:%S'))
        self.start_key, self.stop_key = tk.StringVar(value='F8'), tk.StringVar(value='F9')
        ttk.Label(settings, text='循环轮数').grid(row=0, column=0, sticky='w')
        ttk.Entry(settings, textvariable=self.loops, width=8).grid(row=0, column=1, padx=6)
        ttk.Checkbutton(settings, text='无限循环', variable=self.infinite).grid(row=0, column=2, padx=8)
        ttk.Label(settings, text='启动倒计时（秒）').grid(row=0, column=3)
        ttk.Entry(settings, textvariable=self.countdown, width=7).grid(row=0, column=4, padx=6)
        ttk.Label(settings, text='启动 / 暂停').grid(row=1, column=0, pady=10)
        ttk.Combobox(settings, textvariable=self.start_key, values=[f'F{i}' for i in range(1, 12)], state='readonly', width=6).grid(row=1, column=1)
        ttk.Label(settings, text='紧急停止').grid(row=1, column=2)
        ttk.Combobox(settings, textvariable=self.stop_key, values=[f'F{i}' for i in range(1, 12)], state='readonly', width=6).grid(row=1, column=3)
        ttk.Button(settings, text='应用快捷键', command=self.apply_hotkeys).grid(row=1, column=4, padx=6)
        ttk.Label(settings, text='定时启动').grid(row=2, column=0)
        ttk.Entry(settings, textvariable=self.schedule_text, width=23).grid(row=2, column=1, columnspan=2)
        ttk.Button(settings, text='预约启动', command=self.schedule).grid(row=2, column=3, padx=6)
        ttk.Button(settings, text='取消预约', command=self.cancel_schedule).grid(row=2, column=4)
        bar = ttk.Frame(outer)
        bar.pack(fill='x', pady=(0, 8))
        for label, cmd in [('▶ 启动 / 暂停', self.toggle), ('■ 紧急停止', self.stop), ('保存配置', self.save), ('导出配置', self.export), ('导入配置', self.import_config)]:
            ttk.Button(bar, text=label, command=cmd).pack(side='left', padx=(0, 8))
        self.status = tk.StringVar(value='就绪')
        self.hotkey_status = tk.StringVar()
        ttk.Label(outer, textvariable=self.status, foreground='#1260a0', font=('Microsoft YaHei UI', 12, 'bold')).pack(anchor='w')
        ttk.Label(outer, textvariable=self.hotkey_status).pack(anchor='w', pady=4)
        ttk.Label(outer, text='关闭窗口会停止任务。定时启动需保持软件运行且电脑唤醒；配置保存在当前用户目录。').pack(anchor='w')
        # Reserve space for the controls; only the action list shrinks on small displays.
        children = outer.winfo_children()
        for child in children:
            child.pack_forget()
        for row, child in enumerate(children):
            child.grid(row=row, column=0, sticky='nsew' if child is frame else 'ew',
                       pady=(4 if row else 0, 4))
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(4, weight=1)

    def guard(self):
        if self.runner.running or self.scheduled:
            raise ValueError('请先停止运行或取消预约，再修改动作和配置。')

    def open_tester(self):
        if self.test_window is None or not self.test_window.winfo_exists():
            self.test_window = tk.Toplevel(self.root)
            self.tester = ClickTester(self.test_window, hotkey_status=self.hotkey_status,
                                      on_back=self.show_main)
            self.test_window.title('内置点击测试器 — 全键鼠自动连点')
            self.test_window.protocol('WM_DELETE_WINDOW', self.close_tester)
        self.test_window.deiconify()
        self.test_window.lift()
        self.tester.canvas.focus_set()

    def show_main(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_set()

    def close_tester(self):
        # Stop before removing the input target so a loop cannot spill into another window.
        self.stop()
        if self.tester is not None:
            self.tester.close()
        self.test_window = None
        self.tester = None
        self.show_main()

    def report(self, fn):
        try:
            fn()
        except Exception as e:
            messagebox.showerror(APP, str(e), parent=self.root)

    def editor_action(self):
        return validate_action(dict(kind=self.kind.get(), key=self.key.get(), delay=self.delay.get(), count=self.count.get(), x=self.x.get(), y=self.y.get()))

    def refresh(self, selected=None):
        self.tree.delete(*self.tree.get_children())
        for i, a in enumerate(self.actions):
            pos = f"{a['x']}, {a['y']}" if a['x'] != '' else '当前位置'
            self.tree.insert('', 'end', iid=str(i), values=(i+1, a['kind'], a['key'] if a['kind'] == '键盘按键' else '—', a['delay'], a['count'], pos if a['kind'].startswith('鼠标') else '—'))
        if selected is not None and self.actions:
            self.tree.selection_set(str(min(selected, len(self.actions)-1)))

    def selected(self):
        selection = self.tree.selection()
        if not selection:
            raise ValueError('请先选择一个动作。')
        return int(selection[0])

    def select(self, _=None):
        if not self.tree.selection():
            return
        a = self.actions[self.selected()]
        for name in ('kind', 'key', 'delay', 'count', 'x', 'y'):
            getattr(self, name).set(a[name])

    def add(self):
        def work():
            self.guard()
            self.actions.append(self.editor_action())
            self.refresh(len(self.actions)-1)
        self.report(work)

    def update(self):
        def work():
            self.guard()
            i = self.selected()
            self.actions[i] = self.editor_action()
            self.refresh(i)
        self.report(work)

    def delete(self):
        def work():
            self.guard()
            i = self.selected()
            del self.actions[i]
            self.refresh(i)
        self.report(work)

    def move(self, offset):
        def work():
            self.guard()
            i = self.selected()
            j = i + offset
            if 0 <= j < len(self.actions):
                self.actions[i], self.actions[j] = self.actions[j], self.actions[i]
                self.refresh(j)
        self.report(work)

    def capture(self):
        self.status.set('请在 3 秒内把鼠标移到目标位置…')
        def read():
            p = wintypes.POINT()
            self.backend.api.GetCursorPos(ctypes.byref(p))
            self.x.set(p.x)
            self.y.set(p.y)
            self.status.set(f'已获取坐标：{p.x}, {p.y}；添加或更新鼠标动作后生效')
        self.root.after(3000, read)

    def plan(self):
        if getattr(self, 'registered_keys', None) != (self.start_key.get(), self.stop_key.get()):
            raise ValueError('快捷键设置已修改，请先点击“应用快捷键”。')
        if len(self.hotkeys) != 2:
            raise ValueError('请先解决快捷键占用并应用，确保紧急停止快捷键可用。')
        if not self.actions:
            raise ValueError('请至少添加一个动作。')
        actions = [validate_action(a) for a in self.actions]
        reserved = {KEYS[self.start_key.get()], KEYS[self.stop_key.get()]}
        for a in actions:
            if a['kind'] == '键盘按键' and reserved.intersection(parse_keys(a['key'])):
                raise ValueError('动作不能包含当前启动或停止快捷键，请调整动作或快捷键。')
        loops = 0 if self.infinite.get() else number(self.loops.get(), '循环轮数', 1, 1000000, True)
        countdown = number(self.countdown.get(), '启动倒计时', 0, 86400)
        return actions, loops, countdown

    def toggle(self):
        if self.runner.running:
            if self.runner.paused.is_set():
                self.runner.paused.clear()
                self.status.set('继续运行')
            else:
                self.runner.paused.set()
                self.status.set('已暂停；再次按启动快捷键继续')
        else:
            def work():
                if self.scheduled:
                    raise ValueError('已有预约，请先取消预约。')
                args = self.plan()
                self.runner.start(*args)
                self.status.set(f'启动中，倒计时 {args[2]:g} 秒，请切换到目标窗口')
            self.report(work)

    def stop(self):
        self.scheduled = None
        self.runner.stop()
        self.status.set('已紧急停止，预约已取消')

    def schedule(self):
        def work():
            self.guard()
            args = self.plan()
            target = dt.datetime.strptime(self.schedule_text.get().strip(), '%Y-%m-%d %H:%M:%S')
            if target <= dt.datetime.now():
                raise ValueError('预约时间必须晚于当前时间。格式：2026-11-09 14:30:00')
            self.scheduled = (target, args)
            self.status.set(f'已预约 {target:%Y-%m-%d %H:%M:%S}；到时执行启动倒计时')
        self.report(work)

    def cancel_schedule(self):
        self.scheduled = None
        self.status.set('已取消预约')

    def register_hotkeys(self):
        if self.start_key.get() == 'F12' or self.stop_key.get() == 'F12':
            raise ValueError('F12 是 Windows 保留的控制快捷键，请选择 F1～F11。')
        self.registered_keys = (self.start_key.get(), self.stop_key.get())
        self.hotkeys = self.listener.configure(*self.registered_keys)
        errors = [value for ident, value in enumerate(self.registered_keys, 1) if ident not in self.hotkeys]
        self.hotkey_status.set(f'全局快捷键：{self.start_key.get()} 启动 / 暂停，{self.stop_key.get()} 紧急停止' if not errors else '快捷键被占用：' + ', '.join(errors) + '。请选择其他快捷键并应用。')

    def apply_hotkeys(self):
        def work():
            self.guard()
            if self.start_key.get() == self.stop_key.get():
                raise ValueError('启动和停止快捷键不能相同。')
            self.register_hotkeys()
        self.report(work)

    def poll(self):
        while True:
            try:
                kind, value = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == 'hotkey':
                self.toggle() if value == 1 else self.stop()
            elif kind == 'error':
                self.status.set('运行失败：' + value)
            elif kind == 'progress' and not self.runner.stop_event.is_set() and not self.runner.paused.is_set():
                self.status.set(value)
        if self.scheduled and dt.datetime.now() >= self.scheduled[0]:
            _, args = self.scheduled
            self.scheduled = None
            self.runner.start(*args)
            self.status.set('预约已触发，启动倒计时中')
        self.root.after(30, self.poll)

    def config(self):
        return dict(version=1, actions=self.actions, loops=self.loops.get(), infinite=self.infinite.get(), countdown=self.countdown.get(), start_key=self.start_key.get(), stop_key=self.stop_key.get())

    def write_config(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + '.tmp')
        temporary.write_text(json.dumps(self.config(), ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(path)

    def save(self):
        def work():
            self.write_config(CONFIG)
            self.status.set('配置已保存；下次打开自动恢复，不自动启动')
        self.report(work)

    def export(self):
        path = filedialog.asksaveasfilename(defaultextension='.json', filetypes=[('配置文件', '*.json')])
        if path:
            self.report(lambda: self.write_config(path))

    def read_config(self, path):
        data = json.loads(Path(path).read_text(encoding='utf-8-sig'))
        actions = [validate_action(a) for a in data['actions']]
        number(data.get('loops', '10'), '循环轮数', 1, 1000000, True)
        number(data.get('countdown', '3'), '启动倒计时', 0, 86400)
        start, stop = data.get('start_key', 'F8'), data.get('stop_key', 'F9')
        if start not in [f'F{i}' for i in range(1, 12)] or stop not in [f'F{i}' for i in range(1, 12)] or start == stop:
            raise ValueError('配置中的快捷键无效。')
        self.actions = actions
        for name in ('loops', 'infinite', 'countdown', 'start_key', 'stop_key'):
            if name in data:
                getattr(self, name).set(data[name])
        self.refresh()

    def import_config(self):
        def work():
            self.guard()
            path = filedialog.askopenfilename(filetypes=[('配置文件', '*.json')])
            if path:
                self.read_config(path)
                self.register_hotkeys()
                self.status.set('配置已导入')
        self.report(work)

    def load_initial(self):
        if CONFIG.exists():
            try:
                self.read_config(CONFIG)
            except Exception as e:
                messagebox.showwarning(APP, f'配置读取失败，使用默认设置：{e}')
        if not self.actions:
            self.actions = [validate_action(dict(kind='键盘按键', key='空格', delay=.1, count=1))]
            self.refresh()

    def close(self):
        self.runner.stop()
        if self.runner.thread:
            self.runner.thread.join(timeout=1)
        try:
            self.write_config(CONFIG)
        except OSError:
            pass
        self.listener.close()
        if self.tester is not None:
            self.tester.close()
        self.root.destroy()


if __name__ == '__main__':
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    root = tk.Tk()
    App(root)
    root.mainloop()
