"""A plain white target window for testing keyboard and mouse automation."""
import ctypes
import time
import tkinter as tk


class ClickTester:
    def __init__(self, root, hotkey_status=None, on_back=None):
        self.root = root
        root.title('点击测试器')
        root.geometry('860x620')
        root.minsize(600, 460)
        root.configure(bg='white')
        self.counts = {1: 0, 2: 0, 3: 0}
        self.key_count = 0
        self.last_key = '尚未收到按键'
        self.last_click = '尚未点击'
        self.started = None
        self.marker = None
        self.marker_timer = None
        header = tk.Frame(root, bg='white', padx=28, pady=18)
        header.pack(fill='x')
        tk.Label(header, text='点击测试器', bg='white', fg='#202020',
                 font=('Microsoft YaHei UI', 21, 'bold')).pack(side='left')
        tk.Button(header, text='清零', command=self.reset, bg='white', fg='#333333',
                  font=('Microsoft YaHei UI', 10), relief='solid', bd=1,
                  padx=18, pady=6, takefocus=False).pack(side='right')
        if on_back is not None:
            tk.Button(header, text='返回连点设置', command=on_back, bg='white', fg='#333333',
                      font=('Microsoft YaHei UI', 10), relief='solid', bd=1,
                      padx=12, pady=6, takefocus=False).pack(side='right', padx=10)
        self.canvas = tk.Canvas(root, bg='white', highlightthickness=0, takefocus=True)
        self.canvas.pack(fill='both', expand=True)
        self.total_text = self.canvas.create_text(0, 0, text='0', fill='#202020',
                                                   font=('Microsoft YaHei UI', 64, 'bold'))
        self.title_text = self.canvas.create_text(0, 0, text='点击下方或任意白色测试区域',
                                                   fill='#555555', font=('Microsoft YaHei UI', 14))
        self.detail_text = self.canvas.create_text(0, 0, fill='#444444', font=('Microsoft YaHei UI', 12))
        self.click_text = self.canvas.create_text(0, 0, fill='#666666', font=('Microsoft YaHei UI', 11))
        self.key_text = self.canvas.create_text(0, 0, fill='#333333', font=('Microsoft YaHei UI', 13))
        self.rate_text = self.canvas.create_text(0, 0, fill='#777777', font=('Microsoft YaHei UI', 10))
        self.canvas.bind('<Configure>', self.layout)
        for button in (1, 2, 3):
            self.canvas.bind(f'<ButtonPress-{button}>', self.click)
        root.bind('<KeyPress>', self.key)
        footer = tk.Frame(root, bg='white', pady=12)
        footer.pack(fill='x')
        tk.Label(footer, text='左键 / 右键 / 中键均可测试；点击白色区域后可测试空格、字母、数字。',
                 bg='white', fg='#777777', font=('Microsoft YaHei UI', 10),
                 justify='center').pack(fill='x')
        tk.Label(footer, textvariable=hotkey_status,
                 text='连点软件默认：F8 启动 / 暂停，F9 紧急停止。' if hotkey_status is None else '',
                 bg='white', fg='#777777', font=('Microsoft YaHei UI', 10)).pack(fill='x')
        if on_back is not None:
            tk.Label(footer, text='关闭此测试窗口会停止连点并取消预约。', bg='white', fg='#777777',
                     font=('Microsoft YaHei UI', 10)).pack(fill='x')
        self.refresh()
        self.canvas.focus_set()
        self.ime_timer = self.root.after_idle(self.disable_ime)
        self.root.protocol('WM_DELETE_WINDOW', self.close)

    def disable_ime(self):
        self.ime_timer = None
        # This is a physical-key test surface, so IME composition must not swallow A-Z.
        if not hasattr(ctypes, 'WinDLL'):
            return
        from ctypes import wintypes
        api = ctypes.WinDLL('user32')
        api.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        api.GetAncestor.restype = wintypes.HWND
        imm = ctypes.WinDLL('imm32')
        imm.ImmAssociateContextEx.argtypes = [wintypes.HWND, wintypes.HANDLE, wintypes.DWORD]
        imm.ImmAssociateContextEx(api.GetAncestor(self.canvas.winfo_id(), 2), None, 1)

    def layout(self, event):
        center, top = event.width / 2, max(50, event.height / 2 - 120)
        for item, offset in [(self.title_text, 0), (self.total_text, 80), (self.detail_text, 150),
                             (self.click_text, 184), (self.key_text, 230), (self.rate_text, 266)]:
            self.canvas.coords(item, center, top + offset)

    def refresh(self):
        total = sum(self.counts.values())
        self.canvas.itemconfigure(self.total_text, text=str(total))
        self.canvas.itemconfigure(self.detail_text,
                                  text=f'左键 {self.counts[1]}    ·    右键 {self.counts[3]}    ·    中键 {self.counts[2]}')
        self.canvas.itemconfigure(self.click_text, text=self.last_click)
        self.canvas.itemconfigure(self.key_text, text=f'键盘 {self.key_count} 次  ·  {self.last_key}')
        elapsed = time.monotonic() - self.started if self.started else 0
        self.canvas.itemconfigure(self.rate_text, text=f'本次点击平均速度：{(total-1)/elapsed:.1f} 次 / 秒' if total > 1 and elapsed else '等待测试输入')

    def click(self, event):
        self.canvas.focus_set()
        if self.started is None:
            self.started = time.monotonic()
        self.counts[event.num] += 1
        label = {1: '左键', 2: '中键', 3: '右键'}[event.num]
        self.last_click = f'最近：{label}  ·  窗口坐标 ({event.x}, {event.y})  ·  屏幕坐标 ({event.x_root}, {event.y_root})'
        self.refresh()
        if self.marker_timer is not None:
            self.root.after_cancel(self.marker_timer)
        self.canvas.delete('marker')
        x, y = event.x, event.y
        self.canvas.create_oval(x-18, y-18, x+18, y+18, fill='#dceeff', outline='#3b82f6', width=2, tags='marker')
        self.canvas.configure(bg='#f6faff')
        self.marker_timer = self.root.after(120, self.clear_marker)

    def clear_marker(self):
        self.marker_timer = None
        self.canvas.delete('marker')
        self.canvas.configure(bg='white')

    def key(self, event):
        self.key_count += 1
        names = {'space': '空格', 'Return': '回车', 'Escape': 'Esc', 'BackSpace': '退格',
                 'Left': '左', 'Right': '右', 'Up': '上', 'Down': '下'}
        self.last_key = '最近按键：' + names.get(event.keysym, event.keysym)
        self.refresh()
        return 'break'

    def reset(self):
        self.counts = {1: 0, 2: 0, 3: 0}
        self.key_count = 0
        self.last_key = '尚未收到按键'
        self.last_click = '尚未点击'
        self.started = None
        if self.marker_timer is not None:
            self.root.after_cancel(self.marker_timer)
        self.clear_marker()
        self.refresh()
        self.canvas.focus_set()

    def close(self):
        for timer in (self.marker_timer, self.ime_timer):
            if timer is not None:
                self.root.after_cancel(timer)
        self.marker_timer = self.ime_timer = None
        self.root.destroy()


if __name__ == '__main__':
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    root = tk.Tk()
    ClickTester(root)
    root.mainloop()
