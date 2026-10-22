# -*- coding: utf-8 -*-
"""
文件压缩工具 —— 界面层

设计取向：浅色卡片式布局，信息分层，状态可视化。
关键交互：每文件可单独设目标（双击目标列）、双击行看原图/结果对比、
压缩可中止、设置自动记忆。
"""

import json
import os
import queue
import sys
import threading
import traceback

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from PIL import Image, ImageTk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core

def _app_dirs():
    """打包成 exe 后，资源解压在临时目录，配置要写在 exe 旁边"""
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable)), \
               os.path.dirname(sys.executable)
    here = os.path.dirname(os.path.abspath(__file__))
    return here, here


RES_DIR, BASE = _app_dirs()
CONF_PATH = os.path.join(BASE, "config.json")
APP_NAME = "DENSIFY"            # wordmark，等宽大写
APP_CN = "密排"
APP_TITLE = "%s — %s" % (APP_NAME, APP_CN)
APP_VER = "0.4.1"
# 对话框过滤器直接从 core 的支持列表生成，避免两处各写一遍导致漂移
_IMG_GLOB = " ".join("*" + e for e in sorted(core.IMAGE_EXTS))


# ---------------------------------------------------------------- 配色
#
# 取自 dsh-rhine-splash 的暖白纸色阶 + 莱茵橙，几何是切角而非圆角。
# 亮度足够久看不累，强调色只出现在该抓眼的地方。

class C:
    BG = "#FBFAF7"            # 页面底（暖白纸）
    CARD = "#FFFFFF"          # 面板
    SOFT = "#F6F5F1"          # 内嵌区（表头、日志、输入框）
    SOFT2 = "#F2F1EC"         # 次级内嵌
    BORDER = "#E8E6DF"        # 常规边框
    BORDER_HI = "#DAD7CE"     # 强调边框
    GRID = "#EDEBE5"          # 网格线 / 进度条未点亮
    DOT = "#E3DFD5"           # 档案点阵

    TEXT = "#1D1C1A"          # 主文字（暖黑）
    MUTED = "#7D7971"         # 次文字
    FAINT = "#ACA89E"         # 弱文字

    PRIMARY = "#E46F24"       # 莱茵橙
    PRIMARY_DIM = "#BD5A19"   # 深橙
    PRIMARY_SOFT = "#FDF2EA"  # 橙雾
    PRIMARY_INK = "#FFFFFF"
    AMBER = "#F0893E"         # 亮橙
    LIFE = "#5F9420"          # 生命绿
    CRIMSON = "#D63A4F"       # 绯红

    ROW_OK = "#F7FAEF"        # 成功行底
    ROW_FAIL = "#FDF1F2"      # 失败行底
    ROW_RUN = "#FDF2EA"       # 处理中行底
    ROW_HOVER = "#FBF9F4"     # 悬停
    DISABLED = "#CBC8BE"


F_FAMILY = "Microsoft YaHei UI"
F_MONO = "Consolas"
F_BODY = (F_FAMILY, 10)
F_BOLD = (F_FAMILY, 10, "bold")
F_TITLE = (F_FAMILY, 15, "bold")
F_SUB = (F_FAMILY, 9)
F_TAG = (F_MONO, 8)          # 技术标签 / 英文编号
F_NUM = (F_MONO, 10)         # 数字
F_LOG = (F_MONO, 9)


def _enable_dpi():
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
        return
    except Exception:
        pass
    try:
        from ctypes import windll
        windll.user32.SetProcessDPIAware()
    except Exception:
        pass


_enable_dpi()


def get_desktop():
    try:
        import winreg
        k = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders")
        return winreg.QueryValueEx(k, "Desktop")[0]
    except Exception:
        return os.path.expanduser("~")


def _colorref(hex_rgb):
    """'#121719' → COLORREF(0x00BBGGRR)"""
    h = hex_rgb.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return r | (g << 8) | (b << 16)


def apply_title_style(win, caption=None, text=None, dark=False):
    """让系统标题栏跟随界面配色，否则默认灰条会割裂整体"""
    try:
        from ctypes import windll, byref, c_int, c_uint
        hwnd = windll.user32.GetAncestor(win.winfo_id(), 2)   # GA_ROOT
        if not hwnd:
            return
        # 深浅模式决定系统按钮的描边色
        for attr in (20, 19):
            windll.dwmapi.DwmSetWindowAttribute(
                hwnd, attr, byref(c_int(1 if dark else 0)), 4)
        # Win11 可直接指定标题栏与文字颜色
        if caption:
            windll.dwmapi.DwmSetWindowAttribute(
                hwnd, 35, byref(c_uint(_colorref(caption))), 4)      # DWMWA_CAPTION_COLOR
        if text:
            windll.dwmapi.DwmSetWindowAttribute(
                hwnd, 36, byref(c_uint(_colorref(text))), 4)         # DWMWA_TEXT_COLOR
        RDW_FRAME, RDW_INVALIDATE = 0x0400, 0x0001
        windll.user32.RedrawWindow(hwnd, None, None, RDW_FRAME | RDW_INVALIDATE)
    except Exception:
        pass


def human(n, decimal=False):
    base = 1000.0 if decimal else 1024.0
    if n < base:
        return "%d B" % n
    if n < base * base:
        return "%.1f KB" % (n / base)
    return "%.2f MB" % (n / base / base)


def parse_size(text):
    """'200KB' / '1.5MB' / '300' → 字节数；空字符串返回 None"""
    t = (text or "").strip().upper().replace(" ", "")
    if not t:
        return None
    mult = 1000
    if t.endswith("MB"):
        t, mult = t[:-2], 1000 * 1000
    elif t.endswith("KB"):
        t, mult = t[:-2], 1000
    elif t.endswith("B"):
        t, mult = t[:-1], 1
    try:
        v = float(t)
    except ValueError:
        raise ValueError("无法识别的大小：%s" % text)
    if v <= 0:
        raise ValueError("大小要大于 0")
    return int(v * mult)


# ---------------------------------------------------------------- 组件

def cut_shape(cv, x1, y1, x2, y2, cut, **kw):
    """右上角与左下角切斜，HUD 常见做法"""
    pts = [x1, y1, x2 - cut, y1, x2, y1 + cut, x2, y2, x1 + cut, y2, x1, y2 - cut]
    return cv.create_polygon(pts, **kw)


class Button(tk.Canvas):
    """切角按钮：主按钮实心绿，次按钮描边，ghost 无框"""

    def __init__(self, master, text, command=None, kind="secondary",
                 width=104, height=34, scale=1.0, font=None, bg=None):
        super().__init__(master, width=int(width * scale), height=int(height * scale),
                         highlightthickness=0, bd=0,
                         bg=bg or master.cget("bg"))
        self.command = command
        self.kind = kind
        self.w = int(width * scale)
        self.h = int(height * scale)
        self.cut = max(4, int(7 * scale))
        self.font = font or F_BODY
        self.text = text
        self.enabled = True
        self._hover = False
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self._draw()

    def _colors(self):
        own_bg = self.cget("bg")
        if not self.enabled:
            return (C.SOFT, C.FAINT, C.BORDER)
        if self.kind == "primary":
            return (C.PRIMARY if self._hover else C.PRIMARY_DIM,
                    C.PRIMARY_INK, C.PRIMARY if self._hover else C.PRIMARY_DIM)
        if self.kind == "ghost":
            return (own_bg, C.PRIMARY if self._hover else C.MUTED, "")
        if self.kind == "danger":
            return (own_bg, C.DANGER if self._hover else C.MUTED,
                    C.DANGER if self._hover else C.BORDER)
        return (own_bg, C.PRIMARY if self._hover else C.TEXT,
                C.PRIMARY if self._hover else C.BORDER)

    def _draw(self):
        self.delete("all")
        fill, fg, border = self._colors()
        cut_shape(self, 1, 1, self.w - 1, self.h - 1, self.cut,
                  fill=fill, outline=border or fill, width=1)
        self.create_text(self.w / 2, self.h / 2, text=self.text, fill=fg, font=self.font)

    def _on_enter(self, _):
        if self.enabled:
            self._hover = True
            self.config(cursor="hand2")
            self._draw()

    def _on_leave(self, _):
        self._hover = False
        self.config(cursor="")
        self._draw()

    def _on_press(self, _):
        if self.enabled:
            self._hover = False
            self._draw()

    def _on_release(self, e):
        if not self.enabled:
            return
        self._hover = True
        self._draw()
        if 0 <= e.x <= self.w and 0 <= e.y <= self.h and self.command:
            self.command()

    def set_enabled(self, on):
        self.enabled = bool(on)
        self._hover = False
        self.config(cursor="hand2" if on else "")
        self._draw()


class SegBar(tk.Canvas):
    """分格进度条：科技界面的读数感，比连续条更像仪表"""

    def __init__(self, master, segments=42, height=10, scale=1.0, bg=None):
        super().__init__(master, height=int(height * scale), highlightthickness=0,
                         bd=0, bg=bg or C.BG)
        self.segments = segments
        self.h = int(height * scale)
        self.pct = 0.0
        self.bind("<Configure>", lambda e: self._draw())

    def set(self, pct):
        self.pct = max(0.0, min(100.0, float(pct)))
        self._draw()

    def _draw(self):
        self.delete("all")
        w = self.winfo_width()
        if w < 10:
            return
        gap = 3
        seg_w = (w - gap * (self.segments - 1)) / self.segments
        if seg_w <= 0:
            return
        lit = int(round(self.pct / 100.0 * self.segments))
        for i in range(self.segments):
            x = i * (seg_w + gap)
            on = i < lit
            color = C.PRIMARY if on else C.GRID
            if on and i == lit - 1:
                color = C.AMBER            # 前沿用橙色，读数的"指针"
            self.create_rectangle(x, 1, x + seg_w, self.h - 1,
                                  fill=color, outline="")


class Card(tk.Frame):
    """面板：细边框 + 序号 + 左侧橙条 + 等宽英文标签 + 右侧延伸线"""

    def __init__(self, master, title=None, tag="", index="", scale=1.0, bg=C.CARD):
        super().__init__(master, bg=bg, highlightbackground=C.BORDER,
                         highlightthickness=1, bd=0)
        self.inner_pad = int(14 * scale)
        if title:
            head = tk.Frame(self, bg=bg)
            head.pack(fill="x", padx=self.inner_pad, pady=(int(7 * scale), 0))
            if index:
                tk.Label(head, text=index, bg=bg, fg=C.FAINT, font=F_TAG).pack(
                    side="left", padx=(0, int(8 * scale)))
            tk.Frame(head, bg=C.PRIMARY, width=max(2, int(2 * scale))).pack(
                side="left", fill="y", pady=int(2 * scale))
            tk.Label(head, text=title, bg=bg, fg=C.TEXT, font=F_BOLD).pack(
                side="left", padx=(int(8 * scale), 0))
            if tag:
                tk.Label(head, text=tag, bg=bg, fg=C.FAINT, font=F_TAG).pack(
                    side="left", padx=(int(9 * scale), 0))
            # 延伸线延后 pack，好让外部往标题行右端塞控件
            def _line(h=head):
                if not h.winfo_exists():
                    return
                tk.Frame(h, bg=C.BORDER, height=1).pack(
                    side="left", fill="x", expand=True, padx=(int(10 * scale), 0))
            self.after_idle(_line)
            self.head = head
        self.body = tk.Frame(self, bg=bg)
        self.body.pack(fill="both", expand=True, padx=self.inner_pad,
                       pady=(int(6 * scale), int(9 * scale)))


class PreviewDialog(tk.Toplevel):
    """原图与压缩结果对比"""

    def __init__(self, master, item, default_target, scale=1.0):
        super().__init__(master)
        self.title("预览 · " + os.path.basename(item["path"]))
        self.configure(bg=C.BG)
        w, h = int(880 * scale), int(640 * scale)
        self.geometry("%dx%d" % (w, h))
        self.minsize(int(680 * scale), int(480 * scale))
        self.transient(master)
        self._imgs = []
        self.item = item
        self.after(120, lambda: apply_title_style(self, C.BG, C.MUTED))

        # 头
        head = tk.Frame(self, bg=C.BG)
        head.pack(fill="x", padx=18, pady=(16, 8))
        tk.Label(head, text=os.path.basename(item["path"]), bg=C.BG, fg=C.TEXT,
                 font=(F_FAMILY, 12, "bold")).pack(side="left")
        info = "%s  ·  %s" % (human(item["size"]), item.get("info", ""))
        tk.Label(head, text=info, bg=C.BG, fg=C.MUTED, font=F_SUB).pack(side="left", padx=12)

        body = tk.Frame(self, bg=C.BG)
        body.pack(fill="both", expand=True, padx=18, pady=4)

        self.left = self._panel(body, "原始文件", item["path"])
        self.right = self._panel(body, "压缩结果",
                                 item["result"] if item.get("result") else None)

        foot = tk.Frame(self, bg=C.BG)
        foot.pack(fill="x", padx=18, pady=(6, 16))
        if item.get("result"):
            n = item.get("result_size", 0)
            saved = 100 - n * 100.0 / item["size"] if item["size"] else 0
            txt = "%s → %s，减小 %.0f%%   ·   输出 %s" % (
                human(item["size"]), human(n), saved,
                (item.get("result_fmt") or "").upper())
        else:
            txt = "尚未压缩"
        tk.Label(foot, text=txt, bg=C.BG, fg=C.MUTED, font=F_BODY).pack(side="left")
        Button(foot, "关闭", command=self.destroy, width=84, scale=scale,
               bg=C.BG).pack(side="right")

        self.protocol("WM_DELETE_WINDOW", self.destroy)

    def _panel(self, parent, title, path):
        card = tk.Frame(parent, bg=C.CARD, highlightbackground=C.BORDER,
                        highlightthickness=1)
        card.pack(side="left", fill="both", expand=True, padx=6)
        tk.Label(card, text=title, bg=C.CARD, fg=C.MUTED, font=F_SUB).pack(pady=(10, 4))
        cv = tk.Canvas(card, bg=C.SOFT, highlightthickness=0)
        cv.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        if not path or not os.path.exists(path):
            msg = "尚未生成" if not path else "文件不存在"
            cv.bind("<Configure>", lambda e, c=cv, m=msg: self._placeholder(c, m))
            return cv

        try:
            thumb = core.preview_thumb(path, max_side=900)
            cv._pil = thumb
            cv.bind("<Configure>", lambda e, c=cv: self._fit(c))
            cv.after(80, lambda: self._fit(cv))
        except Exception as e:
            cv.bind("<Configure>",
                    lambda ev, c=cv, m="无法预览：%s" % e: self._placeholder(c, m))
        return cv

    @staticmethod
    def _placeholder(canvas, text):
        cw, ch = canvas.winfo_width(), canvas.winfo_height()
        if cw < 10 or ch < 10:
            return
        canvas.delete("all")
        canvas.create_text(cw / 2, ch / 2, text=text, fill=C.FAINT, font=F_BODY)

    @staticmethod
    def _fit(canvas):
        cw, ch = canvas.winfo_width(), canvas.winfo_height()
        if cw < 20 or ch < 20:
            return
        pil = getattr(canvas, "_pil", None)
        if pil is None:
            return
        iw, ih = pil.size
        k = min(cw / iw, ch / ih, 1.0)
        w, h = max(1, int(iw * k)), max(1, int(ih * k))
        img = pil if k >= 1.0 else pil.resize((w, h), Image.LANCZOS)
        photo = ImageTk.PhotoImage(img)
        canvas._keep = photo
        canvas.delete("all")
        canvas.create_image(cw / 2, ch / 2, image=photo)


# ---------------------------------------------------------------- 主窗口

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        try:
            dpi = self.winfo_fpixels("1i")
        except Exception:
            dpi = 96
        self.s = max(1.0, dpi / 96.0)
        self.tk.call("tk", "scaling", dpi / 72.0)
        self.geometry("%dx%d" % (int(1180 * self.s), int(645 * self.s)))
        self.minsize(int(980 * self.s), int(580 * self.s))
        self.configure(bg=C.BG)

        self.items = {}          # iid -> dict
        self.q = queue.Queue()
        self.running = False
        self._cancel = False
        self._icon = None

        try:
            ico = os.path.join(RES_DIR, "icon.ico")
            if os.path.exists(ico):
                self.iconbitmap(ico)
                with Image.open(ico) as src:
                    thumb = src.convert("RGBA").resize((38, 38), Image.LANCZOS)
                self._icon = ImageTk.PhotoImage(thumb)
        except Exception:
            pass

        self._init_style()
        self._build()
        self._load_conf()
        if not self._conf.get("seen_welcome"):
            self.after(240, self._show_welcome)
        self.after(100, self._poll)
        self.after(140, lambda: apply_title_style(self, C.BG, C.MUTED))
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # -------------------------------------------------- 样式
    def _init_style(self):
        st = ttk.Style(self)
        try:
            st.theme_use("clam")
        except tk.TclError:
            pass
        st.configure("Files.Treeview",
                     background=C.CARD, fieldbackground=C.CARD, foreground=C.TEXT,
                     rowheight=int(34 * self.s), borderwidth=0, relief="flat",
                     font=F_BODY)
        st.map("Files.Treeview",
               background=[("selected", C.PRIMARY_SOFT)],
               foreground=[("selected", C.TEXT)])
        st.configure("Slim.Vertical.TScrollbar", background=C.BORDER_HI,
                     troughcolor=C.CARD, borderwidth=0, arrowsize=9,
                     arrowcolor=C.CARD, width=int(9 * self.s))
        st.map("Slim.Vertical.TScrollbar",
               background=[("active", C.FAINT), ("pressed", C.FAINT)])

        st.configure("Dark.TCombobox",
                     fieldbackground=C.SOFT, background=C.SOFT, foreground=C.TEXT,
                     arrowcolor=C.PRIMARY, bordercolor=C.BORDER,
                     lightcolor=C.BORDER, darkcolor=C.BORDER,
                     selectbackground=C.SOFT, selectforeground=C.TEXT,
                     padding=(6, 3))
        st.map("Dark.TCombobox",
               fieldbackground=[("readonly", C.SOFT)],
               foreground=[("readonly", C.TEXT)],
               selectbackground=[("readonly", C.SOFT)],
               selectforeground=[("readonly", C.TEXT)],
               bordercolor=[("hover", C.PRIMARY), ("focus", C.PRIMARY)])
        self.option_add("*TCombobox*Listbox.background", C.CARD)
        self.option_add("*TCombobox*Listbox.foreground", C.TEXT)
        self.option_add("*TCombobox*Listbox.selectBackground", C.PRIMARY_DIM)
        self.option_add("*TCombobox*Listbox.selectForeground", C.PRIMARY_INK)
        self.option_add("*TCombobox*Listbox.borderWidth", 1)

    # -------------------------------------------------- 布局
    def _build(self):
        s = self.s

        # ===== 顶部
        head = tk.Frame(self, bg=C.BG)
        head.pack(fill="x", padx=int(18 * s), pady=(int(14 * s), int(10 * s)))

        left = tk.Frame(head, bg=C.BG)
        left.pack(side="left")
        if self._icon:
            ic = tk.Label(left, image=self._icon, bg=C.BG, cursor="hand2")
            ic.pack(side="left", padx=(0, 10))
            ic.bind("<Button-1>", lambda e: self._show_welcome())
        txt = tk.Frame(left, bg=C.BG)
        txt.pack(side="left")
        row = tk.Frame(txt, bg=C.BG)
        row.pack(anchor="w")
        tk.Label(row, text=APP_NAME, bg=C.BG, fg=C.TEXT,
                 font=(F_MONO, 18, "bold")).pack(side="left")
        tk.Label(row, text="   " + APP_CN, bg=C.BG, fg=C.PRIMARY,
                 font=(F_FAMILY, 10)).pack(side="left", pady=(int(7 * s), 0))
        self.lbl_sub = tk.Label(txt, text="", bg=C.BG, fg=C.MUTED, font=F_SUB)
        self.lbl_sub.pack(anchor="w", pady=(1, 0))

        right = tk.Frame(head, bg=C.BG)
        right.pack(side="right")
        Button(right, "添加文件", command=self.on_add_files, width=104, scale=s,
               bg=C.BG, kind="primary").pack(side="right")

        tk.Frame(self, bg=C.BORDER, height=1).pack(fill="x")

        # ===== 文件列表（最后 pack，占据中间剩余空间）
        fcard = Card(self, title="文件列表", tag="// QUEUE", index="01", scale=s)
        self.fcard = fcard
        Button(fcard.head, "清空列表", command=self.on_clear, kind="ghost",
               width=76, height=22, scale=s, bg=C.CARD).pack(side="right")

        # 自绘表头：ttk 主题会把 heading 渲染成灰色块，索性自己做
        hdr = tk.Frame(fcard.body, bg=C.SOFT, height=int(30 * s))
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        hdr.rowconfigure(0, weight=1)

        wrap = tk.Frame(fcard.body, bg=C.CARD)
        wrap.pack(fill="both", expand=True)

        cols = ("name", "type", "size", "target", "status")
        self.tree = ttk.Treeview(wrap, columns=cols, show="",
                                 style="Files.Treeview", selectmode="extended")
        heads = (("name", "文件名", 0, "w"), ("type", "格式", 70, "center"),
                 ("size", "原始大小", 100, "e"), ("target", "目标", 100, "e"),
                 ("status", "状态", 150, "e"))

        for i, (key, label, wpx, anchor) in enumerate(heads):
            if wpx == 0:
                self.tree.column(key, width=int(380 * s), anchor=anchor, stretch=True)
                hdr.columnconfigure(i, weight=1)
            else:
                self.tree.column(key, width=int(wpx * s), anchor=anchor, stretch=False)
                hdr.columnconfigure(i, weight=0, minsize=int(wpx * s))
            cell = tk.Frame(hdr, bg=C.SOFT)
            cell.grid(row=0, column=i, sticky="nsew")
            tk.Label(cell, text=label, bg=C.SOFT, fg="#6E655D", font=(F_FAMILY, 9),
                     anchor=anchor).pack(fill="both", expand=True, padx=8)

        hdr.columnconfigure(len(heads), weight=0, minsize=int(10 * s))   # 让出滚动条
        tk.Frame(hdr, bg=C.SOFT).grid(row=0, column=len(heads), sticky="nsew")

        tk.Frame(fcard.body, bg=C.BORDER, height=1).pack(fill="x")

        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.tree.yview,
                           style="Slim.Vertical.TScrollbar")
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.sb = sb

        self.tree.tag_configure("ok", background=C.ROW_OK)
        self.tree.tag_configure("fail", background=C.ROW_FAIL)
        self.tree.tag_configure("run", background=C.ROW_RUN)

        # 空状态：档案网格底 + 四角定位框 + 编号（覆盖表格区，不影响表头）
        self.empty = tk.Canvas(wrap, bg=C.CARD, highlightthickness=0)
        self.empty.bind("<Configure>", lambda e: self._draw_empty())
        inner = tk.Frame(self.empty, bg=C.CARD)
        self._empty_inner = inner
        self._empty_win = None
        tk.Label(inner, text="// NO FILES", bg=C.CARD, fg=C.PRIMARY_DIM,
                 font=F_TAG).pack()
        tk.Label(inner, text="还没有待处理的文件", bg=C.CARD, fg=C.TEXT,
                 font=(F_FAMILY, 13, "bold")).pack(pady=(8, 0))
        tk.Label(inner, text="支持 PDF 与常见图片格式，可一次加入多个",
                 bg=C.CARD, fg=C.MUTED, font=F_BODY).pack(pady=(6, 0))
        tk.Label(inner, text="点右上角「添加文件」，或按 Ctrl+O",
                 bg=C.CARD, fg=C.FAINT, font=F_SUB).pack(pady=(4, 0))
        self.empty.place(relx=0.5, rely=0.5, anchor="center")

        self.tree.bind("<Double-1>", self._on_dblclick)
        self.tree.bind("<Delete>", lambda e: self.on_remove())
        self.tree.bind("<Button-3>", self._on_right_click)

        self.menu = tk.Menu(self, tearoff=0, font=F_BODY, bg=C.CARD, fg=C.TEXT,
                            activebackground=C.ROW_RUN, activeforeground=C.PRIMARY,
                            bd=1, relief="solid")
        self.menu.add_command(label="查看原图 / 对比", command=self._preview_selected)
        self.menu.add_command(label="设置这个文件的目标…", command=self._edit_selected_target)
        self.menu.add_separator()
        self.menu.add_command(label="从列表移除", command=self.on_remove)

        self.menu_blank = tk.Menu(self, tearoff=0, font=F_BODY, bg=C.CARD, fg=C.TEXT,
                                  activebackground=C.ROW_RUN, activeforeground=C.PRIMARY,
                                  bd=1, relief="solid")
        self.menu_blank.add_command(label="添加文件…", command=self.on_add_files)
        self.menu_blank.add_command(label="添加文件夹…", command=self.on_add_folder)

        self.bind("<Control-o>", lambda e: self.on_add_files())
        self.bind("<Control-O>", lambda e: self.on_add_files())
        self.bind("<Control-Return>", lambda e: self.on_run())
        self.bind("<Escape>", self._on_esc)

        # ===== 设置
        scard = Card(self, title="压缩设置", tag="// CONFIG", index="02", scale=s)

        r1 = tk.Frame(scard.body, bg=C.CARD)
        r1.pack(fill="x", pady=(0, int(8 * s)))
        tk.Label(r1, text="默认目标", bg=C.CARD, fg=C.MUTED, font=F_BODY).pack(side="left")
        self.ent_size = tk.Entry(r1, width=8, font=F_NUM, relief="flat",
                                 bg=C.SOFT, fg=C.TEXT, insertbackground=C.PRIMARY,
                                 highlightthickness=1, highlightbackground=C.BORDER,
                                 highlightcolor=C.PRIMARY)
        self.ent_size.pack(side="left", padx=(10, 6), ipady=int(4 * s))
        self.cmb_unit = ttk.Combobox(r1, values=["KB", "MB"], width=4, state="readonly",
                                     font=F_BODY, style="Dark.TCombobox")
        self.cmb_unit.current(0)
        self.cmb_unit.pack(side="left")
        tk.Label(r1, text="每个输出文件都不超过这个大小", bg=C.CARD,
                 fg=C.FAINT, font=F_SUB).pack(side="left", padx=12)

        tk.Label(r1, text="输出格式", bg=C.CARD, fg=C.MUTED, font=F_BODY).pack(side="left", padx=(20, 0))
        self.cmb_fmt = ttk.Combobox(r1, values=["自动", "jpg", "png", "pdf"], width=6,
                                    state="readonly", font=F_BODY, style="Dark.TCombobox")
        self.cmb_fmt.current(0)
        self.cmb_fmt.pack(side="left", padx=10)

        tk.Label(r1, text="多页 PDF", bg=C.CARD, fg=C.MUTED, font=F_BODY).pack(side="left", padx=(16, 0))
        self.cmb_merge = ttk.Combobox(r1, values=["拼接成一张", "逐页输出"], width=12,
                                      state="readonly", font=F_BODY, style="Dark.TCombobox")
        self.cmb_merge.current(0)
        self.cmb_merge.pack(side="left", padx=10)

        r2 = tk.Frame(scard.body, bg=C.CARD)
        r2.pack(fill="x")
        tk.Label(r2, text="输出目录", bg=C.CARD, fg=C.MUTED, font=F_BODY).pack(side="left")
        self.ent_out = tk.Entry(r2, font=F_BODY, relief="flat", bg=C.SOFT, fg=C.TEXT,
                                insertbackground=C.PRIMARY, highlightthickness=1,
                                highlightbackground=C.BORDER, highlightcolor=C.PRIMARY)
        self.ent_out.pack(side="left", fill="x", expand=True, padx=(10, 8), ipady=int(4 * s))
        Button(r2, "浏览", command=self.on_pick_out, width=64, scale=s,
               bg=C.CARD).pack(side="left")

        # 默认目标变动时，未单独设目标的行要跟着刷新显示
        self.ent_size.bind("<KeyRelease>", lambda e: self._refresh_all_rows())
        self.cmb_unit.bind("<<ComboboxSelected>>", lambda e: self._refresh_all_rows())

        # ===== 操作栏
        act = tk.Frame(self, bg=C.BG)
        self.btn_run = Button(act, "开始压缩", command=self.on_run, kind="primary",
                              width=124, height=38, scale=s, font=F_BOLD, bg=C.BG)
        self.btn_run.pack(side="left")
        self.btn_cancel = Button(act, "取消", command=self.on_cancel, kind="ghost",
                                 width=72, height=38, scale=s, bg=C.BG)
        self.btn_cancel.pack(side="left", padx=8)
        self.btn_cancel.set_enabled(False)

        self.pbar = SegBar(act, segments=46, height=11, scale=s, bg=C.BG)
        self.pbar.pack(side="left", fill="x", expand=True, padx=(16, 12))
        self.lbl_stat = tk.Label(act, text="就绪", bg=C.BG, fg=C.MUTED, font=F_BODY,
                                 width=18, anchor="e")
        self.lbl_stat.pack(side="right")
        Button(act, "打开目录", command=self.on_open_out, width=88, height=38,
               scale=s, bg=C.BG).pack(side="right", padx=(0, 10))

        # ===== 日志
        lcard = Card(self, title="日志", tag="// LOG", index="03", scale=s)
        self.txt = tk.Text(lcard.body, height=3, font=F_LOG, wrap="word",
                           bg=C.SOFT, fg="#6E655D", relief="flat",
                           insertbackground=C.PRIMARY,
                           highlightthickness=1, highlightbackground=C.BORDER,
                           padx=10, pady=6, state="disabled")
        lsb = ttk.Scrollbar(lcard.body, orient="vertical", command=self.txt.yview,
                            style="Slim.Vertical.TScrollbar")
        self.txt.configure(yscrollcommand=lsb.set)
        self.txt.pack(side="left", fill="both", expand=True)
        lsb.pack(side="right", fill="y")

        # ===== 统一布局：底部固定，中间弹性
        lcard.pack(side="bottom", fill="x", padx=int(18 * s), pady=(0, int(10 * s)))
        act.pack(side="bottom", fill="x", padx=int(18 * s), pady=(0, int(8 * s)))
        scard.pack(side="bottom", fill="x", padx=int(18 * s), pady=(0, int(10 * s)))
        fcard.pack(side="top", fill="both", expand=True, padx=int(18 * s),
                   pady=(0, int(10 * s)))

        self._refresh_empty()
        self._log("DENSIFY %s · 就绪" % APP_VER)
        self._log("提示：双击某一行的「目标」列可以给这个文件单独设大小，双击其它位置查看原图。")

    # -------------------------------------------------- 欢迎页
    def _build_welcome(self):
        s = self.s
        self.welcome = tk.Frame(self, bg=C.BG)

        # 顶部：品牌 + 跳过
        top = tk.Frame(self.welcome, bg=C.BG)
        top.pack(fill="x", padx=int(40 * s), pady=(int(30 * s), 0))
        if self._icon:
            tk.Label(top, image=self._icon, bg=C.BG).pack(side="left", padx=(0, 12))
        tk.Label(top, text=APP_NAME, bg=C.BG, fg=C.TEXT,
                 font=(F_MONO, 20, "bold")).pack(side="left")
        tk.Label(top, text="   " + APP_CN, bg=C.BG, fg=C.PRIMARY,
                 font=(F_FAMILY, 10)).pack(side="left", pady=(int(8 * s), 0))
        Button(top, "跳过", command=self._close_welcome, kind="ghost",
               width=64, height=30, scale=s, bg=C.BG).pack(side="right")

        tk.Frame(self.welcome, bg=C.BORDER, height=1).pack(
            fill="x", padx=int(40 * s), pady=(int(12 * s), 0))

        # 主体：左文案 / 右示意图，整体垂直居中且顶部对齐
        body = tk.Frame(self.welcome, bg=C.BG)
        body.pack(fill="both", expand=True, padx=int(40 * s), pady=(int(18 * s), int(24 * s)))
        tk.Frame(body, bg=C.BG).pack(expand=True)
        content = tk.Frame(body, bg=C.BG)
        content.pack(fill="x")

        left = tk.Frame(content, bg=C.BG)
        left.pack(side="left", fill="both", expand=True)

        tk.Label(left, text="把文件压到你要的大小，", bg=C.BG, fg=C.TEXT,
                 font=(F_FAMILY, 21, "bold")).pack(anchor="w")
        tk.Label(left, text="清晰度由你决定。", bg=C.BG, fg=C.TEXT,
                 font=(F_FAMILY, 21, "bold")).pack(anchor="w")
        tk.Label(left, text="PDF 与图片，压完可以并排比对原图和结果。",
                 bg=C.BG, fg=C.MUTED, font=(F_FAMILY, 10)).pack(
            anchor="w", pady=(int(10 * s), int(20 * s)))

        steps = (
            ("01", "加入文件", "拖进来，或点下面的按钮，可一次选多个"),
            ("02", "设定上限", "200 KB、1.5 MB 都行，每个文件还能单独设"),
            ("03", "逐个比对", "压完看原图与结果并排，不满意换个设置重压"),
        )
        for no, ttl, desc in steps:
            row = tk.Frame(left, bg=C.BG)
            row.pack(anchor="w", fill="x", pady=(0, int(12 * s)))
            tk.Label(row, text=no, bg=C.BG, fg=C.PRIMARY, font=(F_MONO, 11, "bold"),
                     width=3, anchor="w").pack(side="left", anchor="n", pady=(int(2 * s), 0))
            txt = tk.Frame(row, bg=C.BG)
            txt.pack(side="left")
            tk.Label(txt, text=ttl, bg=C.BG, fg=C.TEXT, font=F_BOLD).pack(anchor="w")
            tk.Label(txt, text=desc, bg=C.BG, fg=C.MUTED, font=F_SUB).pack(anchor="w")

        Button(left, "选择文件", command=self._welcome_pick, kind="primary",
               width=124, height=40, scale=s, bg=C.BG).pack(
            anchor="w", pady=(int(10 * s), 0))
        tk.Label(left, text="也可以用 Ctrl+O，或直接把文件拖进窗口",
                 bg=C.BG, fg=C.FAINT, font=F_SUB).pack(anchor="w", pady=(int(8 * s), 0))

        # 右侧：体积收缩示意
        right = tk.Frame(content, bg=C.CARD, highlightbackground=C.BORDER,
                         highlightthickness=1)
        right.pack(side="right", anchor="n", padx=(int(24 * s), 0))
        tk.Label(right, text="// SHRINKAGE", bg=C.CARD, fg=C.FAINT,
                 font=F_TAG).pack(anchor="w", padx=int(20 * s), pady=(int(16 * s), 0))
        self.wchart = tk.Canvas(right, width=int(360 * s), height=int(210 * s),
                                bg=C.CARD, highlightthickness=0)
        self.wchart.pack(padx=int(14 * s), pady=(int(4 * s), int(16 * s)))
        self.wchart.bind("<Configure>", lambda e: self._draw_chart(self._wprog))
        tk.Frame(body, bg=C.BG).pack(expand=True)

        self._wprog = 0.0

    def _draw_chart(self, p=1.0):
        c = self.wchart
        w, h = c.winfo_width(), c.winfo_height()
        if w < 80 or h < 80:
            return
        s = self.s
        c.delete("all")
        pad = int(18 * s)
        bar_h = int(30 * s)
        full = w - pad * 2

        # 原始体积
        y1 = int(24 * s)
        c.create_rectangle(pad, y1, pad + full, y1 + bar_h, fill=C.SOFT2, outline="")
        c.create_text(pad + int(10 * s), y1 + bar_h / 2, text="597 KB", anchor="w",
                      fill=C.MUTED, font=(F_MONO, 10))
        c.create_text(pad + full, y1 - int(9 * s), text="原文件", anchor="e",
                      fill=C.FAINT, font=F_TAG)

        # 收缩
        cy = y1 + bar_h + int(26 * s)
        c.create_line(pad + int(6 * s), cy, w - pad - int(6 * s), cy,
                      fill=C.BORDER_HI, arrow="last", arrowshape=(7, 8, 3))
        c.create_text(w / 2, cy - int(11 * s), text="−71%", fill=C.PRIMARY,
                      font=(F_MONO, 12, "bold"))

        # 压缩后
        y2 = cy + int(18 * s)
        w2 = max(int(full * 0.29 * p), int(6 * s))
        c.create_rectangle(pad, y2, pad + w2, y2 + bar_h, fill=C.PRIMARY, outline="")
        c.create_text(pad + w2 + int(10 * s), y2 + bar_h / 2, text="173 KB", anchor="w",
                      fill=C.PRIMARY_DIM, font=(F_MONO, 10))

        c.create_text(pad, h - int(10 * s), text="RL-CMP · 200 KB 上限", anchor="w",
                      fill=C.FAINT, font=F_TAG)

    def _animate_chart(self, step=0, total=18):
        if not self.welcome.winfo_ismapped():
            return
        self._wprog = min(1.0, step / float(total))
        try:
            self._draw_chart(self._wprog)
        except Exception:
            return
        if step < total:
            self.after(14, lambda: self._animate_chart(step + 1, total))

    def _show_welcome(self):
        if not hasattr(self, "welcome"):
            self._build_welcome()
        self.welcome.place(x=0, y=0, relwidth=1, relheight=1)
        self.welcome.lift()
        self._wprog = 0.0
        self.after(60, lambda: self._animate_chart(0))

    def _close_welcome(self):
        if hasattr(self, "welcome"):
            self.welcome.place_forget()
        self._conf["seen_welcome"] = True
        self._save_conf()

    def _on_esc(self, _=None):
        if hasattr(self, "welcome") and self.welcome.winfo_ismapped():
            self._close_welcome()

    def _welcome_pick(self):
        self._close_welcome()
        self.on_add_files()

    # -------------------------------------------------- 配置
    def _load_conf(self):
        conf = {}
        try:
            with open(CONF_PATH, "r", encoding="utf-8") as f:
                conf = json.load(f)
        except Exception:
            pass
        self.ent_size.delete(0, "end")
        self.ent_size.insert(0, conf.get("size", "200"))
        unit = conf.get("unit", "KB")
        if unit in ("KB", "MB"):
            self.cmb_unit.set(unit)
        fmt = conf.get("fmt", "自动")
        if fmt in ("自动", "jpg", "png", "pdf"):
            self.cmb_fmt.set(fmt)
        merge = conf.get("merge", "拼接成一张")
        if merge in ("拼接成一张", "逐页输出"):
            self.cmb_merge.set(merge)
        out = conf.get("outdir") or os.path.join(get_desktop(), "压缩输出")
        self.ent_out.delete(0, "end")
        self.ent_out.insert(0, out)
        # 恢复上次的窗口大小（只取宽高，不动位置，免得跑到屏幕外）
        geo = str(conf.get("geometry", ""))
        try:
            wh = geo.split("+")[0]
            w, h = [int(x) for x in wh.split("x")]
            if 900 <= w <= 3200 and 520 <= h <= 2200:
                self.geometry("%dx%d" % (w, h))
        except Exception:
            pass
        self._conf = conf

    def _save_conf(self):
        try:
            conf = dict(getattr(self, "_conf", {}))
            conf.update({
                "size": self.ent_size.get().strip(),
                "unit": self.cmb_unit.get(),
                "fmt": self.cmb_fmt.get(),
                "merge": self.cmb_merge.get(),
                "outdir": self.ent_out.get().strip(),
                "geometry": self.geometry(),
            })
            self._conf = conf
            with open(CONF_PATH, "w", encoding="utf-8") as f:
                json.dump(conf, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    # -------------------------------------------------- 文件管理
    def _add_path(self, path):
        ext = os.path.splitext(path)[1].lower()
        if ext not in core.SUPPORTED_EXTS:
            return False
        key = os.path.normcase(path)
        if key in self.items:
            return False
        try:
            size = os.path.getsize(path)
            info, pages = core.probe(path)
        except Exception:
            return False
        self.items[key] = {
            "path": path, "size": size, "info": info, "pages": pages,
            "target": None, "status": "pending", "result": None,
            "result_size": 0, "result_fmt": "",
        }
        self.tree.insert("", "end", iid=key, values=self._row_values(key))
        return True

    def _refresh_empty(self):
        if self.items:
            self.empty.place_forget()
            if not self.sb.winfo_ismapped():
                self.sb.pack(side="right", fill="y")
        else:
            self.empty.place(x=0, y=0, relwidth=1, relheight=1)
            self._draw_empty()
            self.sb.pack_forget()
        self._update_summary()

    def _draw_empty(self):
        """铺一层档案网格：点阵 + 四角定位框 + 角落编号"""
        c = self.empty
        w, h = c.winfo_width(), c.winfo_height()
        if w < 80 or h < 80:
            return
        c.delete("bg")
        step = max(18, int(28 * self.s))
        for y in range(step, h - 2, step):
            for x in range(step, w - 2, step):
                c.create_rectangle(x, y, x + 1, y + 1, fill=C.DOT,
                                   outline="", tags="bg")
        L, pad = int(24 * self.s), int(20 * self.s)
        for x, y, dx, dy in ((pad, pad, 1, 1), (w - pad, pad, -1, 1),
                             (pad, h - pad, 1, -1), (w - pad, h - pad, -1, -1)):
            c.create_line(x, y, x + dx * L, y, fill=C.BORDER_HI, width=1, tags="bg")
            c.create_line(x, y, x, y + dy * L, fill=C.BORDER_HI, width=1, tags="bg")
        c.create_text(pad + int(6 * self.s), h - pad - int(9 * self.s),
                      text="RL-CMP  ·  STANDBY", anchor="w",
                      fill=C.FAINT, font=F_TAG, tags="bg")
        if self._empty_win is None:
            self._empty_win = c.create_window(w / 2, h / 2,
                                              window=self._empty_inner, anchor="center")
        else:
            c.coords(self._empty_win, w / 2, h / 2)
        c.tag_lower("bg")

    def _update_summary(self):
        if self.items:
            total = sum(it["size"] for it in self.items.values())
            done = sum(1 for it in self.items.values() if it["status"] == "ok")
            txt = "共 %d 个文件 · 合计 %s" % (len(self.items), human(total))
            if done:
                txt += " · 已完成 %d" % done
        else:
            txt = "PDF 与图片压到指定大小 · 全本地处理，不上传"
        self.lbl_sub.config(text=txt)

    def _row_values(self, key):
        it = self.items[key]
        # 目标列显示实际生效的值，而不是笼统的「默认」
        if it["target"]:
            tgt_txt = human(it["target"], True)
        else:
            try:
                tgt_txt = human(self._read_target(), True)
            except Exception:
                tgt_txt = "—"
        st = it["status"]
        if st == "ok":
            saved = 100 - it["result_size"] * 100.0 / it["size"] if it["size"] else 0
            status = "✓  %s  ↓%.0f%%  " % (human(it["result_size"]), saved)
        elif st == "fail":
            status = "✗  失败  "
        elif st == "run":
            status = "处理中 %d%%  " % int(it.get("pct", 0) * 100)
        else:
            status = "待压缩  "
        return (os.path.basename(it["path"]),
                os.path.splitext(it["path"])[1].lstrip(".").upper(),
                human(it["size"]), tgt_txt, status)

    def _refresh_all_rows(self):
        for k in list(self.items):
            self._refresh_row(k)

    def _refresh_row(self, key):
        if key not in self.items:
            return
        it = self.items[key]
        tags = ()
        if it["status"] == "ok":
            tags = ("ok",)
        elif it["status"] == "fail":
            tags = ("fail",)
        elif it["status"] == "run":
            tags = ("run",)
        self.tree.item(key, values=self._row_values(key), tags=tags)

    def on_add_files(self):
        if self.running:
            return
        paths = filedialog.askopenfilenames(
            title="选择要压缩的文件",
            filetypes=[("支持的文件", "*.pdf " + _IMG_GLOB),
                       ("PDF", "*.pdf"),
                       ("图片", _IMG_GLOB),
                       ("全部文件", "*.*")])
        if not paths:
            return
        added = sum(1 for p in paths if self._add_path(p))
        self._refresh_empty()
        if added == 0:
            self._log("这些文件已在列表中，或格式不支持。")
        else:
            self._log("添加 %d 个文件。" % added)

    def on_add_folder(self):
        if self.running:
            return
        d = filedialog.askdirectory(title="选择文件夹")
        if not d:
            return
        added = 0
        for root, _, names in os.walk(d):
            for n in names:
                if self._add_path(os.path.join(root, n)):
                    added += 1
        self._refresh_empty()
        self._log("从文件夹添加 %d 个文件：%s" % (added, d))

    def on_clear(self):
        if self.running:
            return
        self.tree.delete(*self.tree.get_children())
        self.items.clear()
        self._refresh_empty()
        self.pbar.set(0)
        self.lbl_stat.config(text="就绪")
        self._log("已清空列表。")

    def on_pick_out(self):
        d = filedialog.askdirectory(title="选择输出目录")
        if d:
            self.ent_out.delete(0, "end")
            self.ent_out.insert(0, d)

    def on_open_out(self):
        d = self.ent_out.get().strip()
        if not d:
            return
        try:
            os.makedirs(d, exist_ok=True)
            os.startfile(d)
        except Exception as e:
            messagebox.showerror("打不开目录", str(e), parent=self)

    def on_remove(self):
        if self.running:
            return
        sel = list(self.tree.selection())
        if not sel:
            return
        for key in sel:
            self.tree.delete(key)
            self.items.pop(key, None)
        self._refresh_empty()
        self._log("从列表移除 %d 个文件。" % len(sel))

    def _on_right_click(self, event):
        key = self.tree.identify_row(event.y)
        if not key:
            # 空白处：给"添加文件夹"留个入口，不占界面按钮
            try:
                self.menu_blank.tk_popup(event.x_root, event.y_root)
            finally:
                self.menu_blank.grab_release()
            return
        if key not in self.tree.selection():
            self.tree.selection_set(key)
        try:
            self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu.grab_release()

    def _preview_selected(self):
        sel = self.tree.selection()
        if sel:
            self._open_preview(sel[0])

    def _edit_selected_target(self):
        sel = self.tree.selection()
        if sel:
            self._edit_target(sel[0])

    # -------------------------------------------------- 单元格编辑
    def _on_dblclick(self, event):
        key = self.tree.identify_row(event.y)
        if not key:
            return
        col = self.tree.identify_column(event.x)
        if col == "#4":                       # 目标列
            self._edit_target(key)
        else:
            self._open_preview(key)

    def _edit_target(self, key):
        bbox = self.tree.bbox(key, "#4")
        if not bbox:
            return
        x, y, w, h = bbox
        it = self.items[key]
        cur = it["target"]
        unit = self.cmb_unit.get()
        val = ""
        if cur:
            val = ("%.1f" % (cur / 1000.0)).rstrip("0").rstrip(".")

        ent = tk.Entry(self.tree, font=F_NUM, relief="flat", justify="right",
                       bg=C.CARD, fg=C.PRIMARY, insertbackground=C.PRIMARY,
                       highlightthickness=1,
                       highlightbackground=C.PRIMARY, highlightcolor=C.PRIMARY)
        ent.place(x=x, y=y, width=w, height=h)
        ent.insert(0, val)
        ent.select_range(0, "end")
        ent.focus_set()

        done = {"v": False}

        def commit(_=None):
            if done["v"]:
                return
            done["v"] = True
            raw = ent.get().strip()
            ent.destroy()
            try:
                if raw == "":
                    it["target"] = None
                else:
                    it["target"] = parse_size(raw if raw[-1:].isalpha() else raw + unit)
                self._refresh_row(key)
            except ValueError as e:
                messagebox.showerror("数值有误", str(e), parent=self)

        def cancel(_=None):
            if done["v"]:
                return
            done["v"] = True
            ent.destroy()

        ent.bind("<Return>", commit)
        ent.bind("<Escape>", cancel)
        ent.bind("<FocusOut>", commit)

    def _open_preview(self, key):
        try:
            PreviewDialog(self, self.items[key], None, self.s)
        except Exception as e:
            messagebox.showerror("预览失败", str(e), parent=self)

    # -------------------------------------------------- 运行
    def _log(self, msg):
        self.txt.config(state="normal")
        self.txt.insert("end", msg + "\n")
        self.txt.see("end")
        self.txt.config(state="disabled")

    def _read_target(self):
        raw = self.ent_size.get().strip()
        if not raw:
            raise ValueError("请填写默认目标大小")
        try:
            v = float(raw)
        except ValueError:
            raise ValueError("目标大小要填数字")
        if v <= 0:
            raise ValueError("目标大小要大于 0")
        return int(v * (1000 * 1000 if self.cmb_unit.get() == "MB" else 1000))

    def on_run(self):
        if self.running:
            return
        if not self.items:
            messagebox.showwarning("提示", "请先添加文件", parent=self)
            return
        try:
            default_target = self._read_target()
        except ValueError as e:
            messagebox.showerror("参数有误", str(e), parent=self)
            return

        out_dir = self.ent_out.get().strip()
        if not out_dir:
            messagebox.showwarning("提示", "请填写输出目录", parent=self)
            return
        try:
            os.makedirs(out_dir, exist_ok=True)
        except Exception as e:
            messagebox.showerror("无法创建输出目录", str(e), parent=self)
            return

        fmt_map = {"自动": "auto", "jpg": "jpg", "png": "png", "pdf": "pdf"}
        fmt = fmt_map.get(self.cmb_fmt.get(), "auto")
        merge = (self.cmb_merge.get() == "拼接成一张")

        jobs = []
        for key, it in self.items.items():
            jobs.append((key, it["path"], it["target"] or default_target))

        # 覆盖检查：输出目录里已有同名前缀的文件就先问一声
        try:
            existing = os.listdir(out_dir)
        except Exception:
            existing = []
        clash = set()
        for _, path, _ in jobs:
            stem = os.path.splitext(os.path.basename(path))[0]
            for f in existing:
                if f.startswith(stem) and f != os.path.basename(path) \
                        and os.path.splitext(f)[1].lower() in (".jpg", ".png", ".pdf"):
                    clash.add(f)
        if clash:
            sample = "、".join(sorted(clash)[:3])
            more = "" if len(clash) <= 3 else " 等 %d 个" % len(clash)
            if not messagebox.askyesno(
                    "会覆盖已有文件",
                    "输出目录里已经存在：\n%s%s\n\n继续会覆盖这些文件，确定吗？" % (sample, more),
                    parent=self):
                return

        for key, it in self.items.items():
            it["status"] = "pending"
            it["pct"] = 0
            self._refresh_row(key)

        self._save_conf()
        self.running = True
        self._cancel = False
        self._auto_open_shown = False
        self.btn_run.set_enabled(False)
        self.btn_cancel.set_enabled(True)
        self.pbar.set(0)
        self.lbl_stat.config(text="准备中")

        self._log("")
        self._log("开始处理 %d 个文件，默认上限 %d 字节（%s）"
                  % (len(jobs), default_target, human(default_target, True)))

        threading.Thread(target=self._worker,
                         args=(jobs, out_dir, fmt, merge), daemon=True).start()

    def on_cancel(self):
        if self.running:
            self._cancel = True
            self.btn_cancel.set_enabled(False)
            self.lbl_stat.config(text="正在中止…")
            self._log("已请求中止，当前文件处理完就停。")

    def _worker(self, jobs, out_dir, fmt, merge):
        ok = fail = 0
        total_old = total_new = 0
        total = len(jobs)
        stop = False

        for i, (key, path, target) in enumerate(jobs, 1):
            if self._cancel:
                stop = True
                break
            self.q.put(("begin", key, i, total, os.path.basename(path)))
            try:
                res = core.compress_one(
                    path, out_dir, target, fmt, merge_pages=merge,
                    log=lambda m, k=key: self.q.put(("rowlog", k, m)),
                    cancel=lambda: self._cancel,
                    progress=lambda v, k=key, idx=i: self.q.put(("pct", k, v, idx)))
                n = sum(r.size for r in res)
                used = res[0].fmt if res else fmt
                first = res[0].path if res else None
                ok += 1
                total_old += self.items[key]["size"]
                total_new += n
                self.q.put(("ok", key, n, used, first))
            except core.Cancelled:
                self.q.put(("cancelled", key))
                stop = True
                break
            except Exception as e:
                fail += 1
                self.q.put(("fail", key, "%s: %s" % (type(e).__name__, e)))
            self.q.put(("file_done", i, total))

        self.q.put(("done", ok, fail, total_old, total_new, out_dir, stop))

    def _poll(self):
        try:
            while True:
                msg = self.q.get_nowait()
                kind = msg[0]

                if kind == "begin":
                    _, key, i, total, name = msg
                    self.lbl_stat.config(text="%d/%d 处理中" % (i, total))
                    self._log("[%d/%d] %s" % (i, total, name))
                    it = self.items.get(key)
                    if it:
                        it["status"] = "run"
                        it["pct"] = 0
                        self._refresh_row(key)

                elif kind == "pct":
                    _, key, v, idx = msg
                    it = self.items.get(key)
                    if it:
                        it["pct"] = v
                        if int(v * 100) % 5 == 0:
                            self._refresh_row(key)
                        total = max(1, len(self.items))
                        self.pbar.set(min(100, ((idx - 1) + min(1.0, v)) / total * 100))

                elif kind == "rowlog":
                    self._log("    " + msg[2].strip())

                elif kind == "ok":
                    _, key, n, used, first = msg
                    it = self.items[key]
                    it["status"] = "ok"
                    it["result_size"] = n
                    it["result"] = first
                    it["result_fmt"] = used
                    self._refresh_row(key)
                    self._update_summary()

                elif kind == "fail":
                    _, key, err = msg
                    it = self.items[key]
                    it["status"] = "fail"
                    it["error"] = err
                    self._log("    ✗ %s" % err)
                    self._refresh_row(key)

                elif kind == "cancelled":
                    _, key = msg
                    it = self.items[key]
                    it["status"] = "pending"
                    it["pct"] = 0
                    self._refresh_row(key)

                elif kind == "file_done":
                    pass

                elif kind == "done":
                    _, ok, fail, to, tn, out_dir, stop = msg
                    self.running = False
                    self.btn_run.set_enabled(True)
                    self.btn_cancel.set_enabled(False)
                    if not stop:
                        self.pbar.set(100)
                    if stop:
                        self.lbl_stat.config(text="已中止")
                        self._log("已中止。")
                    else:
                        self.lbl_stat.config(text="完成 %d 成功 / %d 失败" % (ok, fail))
                        self._log("")
                        if to:
                            saved = 100 - tn * 100.0 / to
                            self._log("完成：成功 %d 个，失败 %d 个；体积 %s → %s，减小 %.0f%%"
                                      % (ok, fail, human(to), human(tn), saved))
                        else:
                            self._log("完成：成功 %d 个，失败 %d 个" % (ok, fail))
                        if ok and not self._auto_open_shown:
                            self._auto_open_shown = True
                            if messagebox.askyesno("处理完成",
                                                   "已输出到：\n%s\n\n是否打开该目录？" % out_dir,
                                                   parent=self):
                                try:
                                    os.startfile(out_dir)
                                except Exception:
                                    pass

        except queue.Empty:
            pass
        self.after(100, self._poll)

    _auto_open_shown = False

    def _on_close(self):
        if self.running:
            if not messagebox.askyesno("正在处理", "还有文件在处理，确定退出？", parent=self):
                return
            self._cancel = True
        self._save_conf()
        self.destroy()


def main():
    try:
        App().mainloop()
    except Exception:
        err = traceback.format_exc()
        try:
            with open(os.path.join(BASE, "error.log"), "a", encoding="utf-8") as f:
                f.write(err + "\n")
        except Exception:
            pass
        try:
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror("程序出错", err[-1500:])
            root.destroy()
        except Exception:
            pass


if __name__ == "__main__":
    main()
