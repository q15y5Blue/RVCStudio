from __future__ import annotations

import ctypes
import configparser
from dataclasses import asdict
from datetime import datetime
import json
import math
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import traceback
import uuid
import webbrowser

from settings import (APP_VERSION, BACKENDS, BEATRICE_SUFFIX, F0_METHODS, LIVE_KEYS, PARAMS,
                      PARAM_BY_NAME, TOGGLES, Settings, data_dir)
import runtime
from routing import cable_pair, restore_device_key

HERE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
INSTALL_DIR = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1] / "build/app"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
# These run under the pinned Applio Python. They must be launched from a directory
# that contains no OpenSSL/Python DLLs (see App.worker_script); keep this list in sync
# with what worker.py imports as plain sibling modules.
WORKER_SUPPORT = ("worker.py", "settings.py", "routing.py", "audio_buffers.py", "formant.py",
                  "beatrice_backend.py", "beatrice_trainer.py")
INT_FIELDS = {"beatrice_speaker": "Beatrice 说话人编号"}
CARD = "#172233"
PARAM_GROUPS = (("core", "核心参数"), ("timbre", "音色"), ("perf", "推理性能"),
                ("noise", "门限、降噪与输出"), ("advanced", "高级"))
GROUP_TOGGLES = {"noise": ("input_denoise", "output_denoise"),
                 "advanced": ("f0_autotune", "proposed_pitch", "phase_vocoder", "wasapi_exclusive")}


class Studio(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("RVC Studio · 中文实时变声 · 0.3 离线全量版")
        self.geometry("1060x820")
        self.minsize(940, 760)
        self.configure(bg="#101622")
        self.root_data = data_dir()
        self.config_file = self.root_data / "settings.json"
        self.events = queue.Queue()
        self.process = None
        self.busy = False
        self.cancel = threading.Event()
        self.devices = []
        self.report = {}
        self.last_output = ""
        self.worker_failed = False
        self.worker_completed = False
        self.worker_command = ""
        self.closing = False
        self.log_file = self.root_data / "studio.log"
        self.initial_error = ""
        try:
            self.config = Settings.load(self.config_file)
        except Exception as exc:
            self.config = Settings()
            self.initial_error = f"旧配置无法读取，已使用默认值：{exc}"
        self.vars = {}
        self.bools = {}
        self.live_job = None
        self.manifest = json.loads((HERE / "engine-manifest.json").read_text(encoding="utf-8"))
        self.configure_style()
        self.build_ui()
        self.protocol("WM_DELETE_WINDOW", self.close_app)
        self.after(80, self.drain_events)
        if self.initial_error:
            self.log(self.initial_error)
        if self.config.runtime and "--smoke-test" not in sys.argv:
            self.after(300, self.probe_runtime)

    def configure_style(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        self.option_add("*Font", ("Microsoft YaHei UI", 10))
        style.configure(".", background="#101622", foreground="#e6edf7", font=("Microsoft YaHei UI", 10))
        style.configure("TFrame", background="#101622")
        style.configure("TLabel", background="#101622", foreground="#e6edf7")
        style.configure("Sub.TLabel", foreground="#91a2b9")
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 24, "bold"))
        style.configure("Section.TLabel", font=("Microsoft YaHei UI", 13, "bold"))
        style.configure("Warn.TLabel", foreground="#f0bd72")
        style.configure("TButton", background="#253449", borderwidth=0, padding=(14, 9))
        style.map("TButton", background=[("active", "#344c6b"), ("disabled", "#1c2533")])
        style.configure("Accent.TButton", background="#286ace")
        style.map("Accent.TButton", background=[("active", "#3985ee"), ("disabled", "#243751")])
        style.configure("TEntry", fieldbackground="#1b2739", foreground="#edf3fa", padding=7)
        style.configure("TCombobox", fieldbackground="#1b2739", background="#253449", padding=6)
        style.map("TCombobox", fieldbackground=[("readonly", "#1b2739")], foreground=[("readonly", "#edf3fa")])
        style.configure("TNotebook", background="#101622", borderwidth=0)
        style.configure("TNotebook.Tab", background="#1b2739", padding=(24, 12))
        style.map("TNotebook.Tab", background=[("selected", "#286ace")])
        style.configure("TCheckbutton", background="#101622")
        style.configure("Horizontal.TProgressbar", background="#549af7", troughcolor="#1b2739")
        style.configure("Card.TFrame", background=CARD)
        style.configure("Card.TLabel", background=CARD, foreground="#e6edf7")
        style.configure("CardTitle.TLabel", background=CARD, foreground="#90bcff", font=("Microsoft YaHei UI", 11, "bold"))
        style.configure("CardSub.TLabel", background=CARD, foreground="#8394ab", font=("Microsoft YaHei UI", 9))
        style.configure("Card.TCheckbutton", background=CARD, foreground="#e6edf7")
        style.map("Card.TCheckbutton", background=[("active", CARD)])
        style.configure("Horizontal.TScale", background=CARD, troughcolor="#26354b", borderwidth=0)

    def label(self, parent, text, style="TLabel", **pack):
        widget = ttk.Label(parent, text=text, style=style, wraplength=930, justify="left")
        widget.pack(anchor="w", **pack)
        return widget

    def build_ui(self):
        body = ttk.Frame(self, padding=26)
        body.pack(fill="both", expand=True)
        self.label(body, "RVC Studio", "Title.TLabel")
        self.label(body, "普通话男声 → 女声   /   RVC v2 与 Beatrice v2 双引擎，可随时切换对比", "Sub.TLabel", pady=(3, 14))
        self.label(body, "聊天软件的麦克风请选择 CABLE Output（VB-Audio Virtual Cable）。首次安装驱动后请重启电脑。", "Sub.TLabel", pady=(0, 16))
        book = ttk.Notebook(body)
        book.pack(fill="both", expand=True)
        tabs = []
        for title in ("环境与驱动", "① 模型与声音", "② 变声参数", "③ 实时变声", "使用说明"):
            tab = ttk.Frame(book, padding=20)
            book.add(tab, text=title)
            tabs.append(tab)
        self.env_tab(tabs[0])
        self.model_tab(tabs[1])
        self.params_tab(tabs[2])
        self.audio_tab(tabs[3])
        self.help_tab(tabs[4])
        if self.config.runtime:
            book.select(1)
        self.status = tk.StringVar(value="就绪 · 导入女声模型，选择真实麦克风，即可开始测试")
        ttk.Label(body, textvariable=self.status, wraplength=950, foreground="#90bcff").pack(anchor="w", pady=(16, 8))
        self.log_box = tk.Text(body, height=5, bg="#0b1019", fg="#91a2b9", relief="flat", padx=10, pady=8,
                               font=("Microsoft YaHei UI", 9), state="disabled")
        self.log_box.pack(fill="x")

    def variable(self, name):
        if name not in self.vars:
            self.vars[name] = tk.StringVar(value=self.format_value(name, getattr(self.config, name)))
        return self.vars[name]

    def bool_var(self, name):
        if name not in self.bools:
            self.bools[name] = tk.BooleanVar(value=bool(getattr(self.config, name)))
            self.bools[name].trace_add("write", lambda *_: self.param_changed(name))
        return self.bools[name]

    @staticmethod
    def format_value(name, value):
        p = PARAM_BY_NAME.get(name)
        if p is None or isinstance(value, str):
            return str(value)
        if p.integer:
            return str(int(round(value)))
        return f"{value:.2f}"

    def path_row(self, parent, label, key, button, command):
        self.label(parent, label, "Sub.TLabel", pady=(12, 5))
        row = ttk.Frame(parent)
        row.pack(fill="x")
        ttk.Entry(row, textvariable=self.variable(key), state="readonly").pack(side="left", fill="x", expand=True, padx=(0, 8))
        ttk.Button(row, text=button, command=command).pack(side="right")

    def env_tab(self, tab):
        self.label(tab, "内置引擎与虚拟麦克风", "Section.TLabel")
        self.label(tab, "安装向导会自动配置引擎和 VB-CABLE，无需单独运行其他软件。\n此页面用于检测或维护组件。实时模式需要 NVIDIA 显卡及可用的显卡驱动。", "Sub.TLabel", pady=(10, 10))
        row = ttk.Frame(tab)
        row.pack(fill="x", pady=8)
        ttk.Button(row, text="准备 / 检测内置引擎", command=self.download_runtime, style="Accent.TButton").pack(side="left")
        ttk.Button(row, text="使用本地引擎包", command=self.local_runtime).pack(side="left", padx=8)
        ttk.Button(row, text="取消下载 / 解压", command=self.cancel_operation).pack(side="left")
        self.path_row(tab, "当前运行环境", "runtime", "选择已有环境", self.select_runtime)
        row = ttk.Frame(tab)
        row.pack(fill="x", pady=14)
        ttk.Button(row, text="检测环境与音频设备", command=self.probe_runtime).pack(side="left")
        ttk.Button(row, text="打开数据目录", command=lambda: os.startfile(self.root_data)).pack(side="left", padx=8)
        self.env_status = tk.StringVar(value="尚未检测。VB-CABLE 虚拟麦克风由统一安装包部署。")
        ttk.Label(tab, textvariable=self.env_status, wraplength=880, justify="left").pack(anchor="w", pady=10)
        ttk.Button(tab, text="安装 / 检测虚拟麦克风", command=self.install_driver).pack(anchor="w", pady=6)
        self.label(tab, "VB-CABLE 来自 VB-Audio，是 donationware，欢迎捐赠支持作者。", "Sub.TLabel", pady=4)
        ttk.Button(tab, text="VB-CABLE 官方网站 / 捐赠", command=lambda: webbrowser.open("https://vb-audio.com/Cable/")).pack(anchor="w")

    def backend_selector(self, parent, style="TLabel"):
        """Combobox showing BACKENDS labels, bound to the "backend" key variable."""
        labels = {key: label for key, label in BACKENDS.items()}
        keys = {label: key for key, label in labels.items()}
        if not hasattr(self, "backend_display"):
            self.backend_display = tk.StringVar(value=labels.get(self.variable("backend").get(), labels["rvc"]))
            self.backend_display.trace_add("write", lambda *_: self.variable("backend").set(
                keys.get(self.backend_display.get(), "rvc")))
            def from_key(*_):
                label = labels.get(self.variable("backend").get())
                if label and self.backend_display.get() != label:
                    self.backend_display.set(label)
                self.param_changed("backend")
            self.variable("backend").trace_add("write", from_key)
        ttk.Combobox(parent, values=list(BACKENDS.values()), state="readonly", width=26,
                     textvariable=self.backend_display).pack(side="left", padx=8)

    def toggle_backend(self):
        current = self.variable("backend").get()
        self.variable("backend").set("rvc" if current == "beatrice" else "beatrice")
        if not self.realtime_running():
            self.save_settings()
            self.status.set("当前引擎：" + BACKENDS[self.variable("backend").get()])

    def model_tab(self, tab):
        row = ttk.Frame(tab)
        row.pack(fill="x", pady=(0, 2))
        ttk.Label(row, text="变声引擎", style="Section.TLabel").pack(side="left")
        self.backend_selector(row)
        ttk.Label(row, text="两种模型都设置后，可在「③ 实时变声」边说边 A/B 切换", style="Sub.TLabel").pack(side="left", padx=4)
        self.path_row(tab, "RVC v2 女声模型（.pth）", "model", "导入模型", self.import_model)
        self.path_row(tab, "配套特征索引（.index，可选；缺省时关闭检索）", "index", "导入索引", self.import_index)
        row = ttk.Frame(tab)
        row.pack(fill="x", pady=(8, 2))
        ttk.Button(row, text="清空索引", command=self.clear_index).pack(side="left")
        ttk.Button(row, text="恢复普通话推荐参数", command=self.defaults).pack(side="left", padx=8)
        ttk.Button(row, text="内置主女声（标准 v2 / 200ep）",
                   command=lambda: self.use_bundled_model("ChineseFemale", "ChineseFemale.pth", "ChineseFemale.index")).pack(side="left")
        ttk.Button(row, text="内置备选女声（HQ / Ov2 / 350ep）",
                   command=lambda: self.use_bundled_model("ChineseFemale_HQ", "ChineseFemale_HQ.pth", "ChineseFemale_HQ.index")).pack(side="left", padx=8)
        self.label(tab, "离线版已内置两把自然普通话女声（非卡通 / 非明星 / 非唱歌），点按钮即可一键切换并 A/B 对比。", "Sub.TLabel", pady=(0, 2))
        self.label(tab, "Beatrice v2 模型（beatrice-trainer 2.0.0-rc.0 的训练检查点 checkpoint_*.pt.gz；VST 专用的 paraphernalia 文件夹无法导入）",
                   "Sub.TLabel", pady=(10, 5))
        row = ttk.Frame(tab)
        row.pack(fill="x")
        ttk.Entry(row, textvariable=self.variable("beatrice_model"), state="readonly").pack(side="left", fill="x", expand=True, padx=(0, 8))
        ttk.Label(row, text="说话人编号", style="Sub.TLabel").pack(side="left")
        ttk.Entry(row, width=4, textvariable=self.variable("beatrice_speaker")).pack(side="left", padx=(6, 8))
        self.variable("beatrice_speaker").trace_add("write", lambda *_: self.param_changed("beatrice_speaker"))
        ttk.Button(row, text="导入模型", command=self.import_beatrice).pack(side="right")
        self.label(tab, "音调、共振峰（性别因子）、检索、降噪、采样长度等全部参数在「② 变声参数」页调整；文件转换和录音试听同样使用这些参数。",
                   "Sub.TLabel", pady=(12, 8))
        row = ttk.Frame(tab)
        row.pack(fill="x")
        ttk.Button(row, text="保存参数", command=self.save_settings).pack(side="left")
        ttk.Button(row, text="选择音频文件并转换", command=self.convert_file, style="Accent.TButton").pack(side="left", padx=8)
        ttk.Button(row, text="两种引擎各转换（A/B）", command=lambda: self.convert_file(compare=True)).pack(side="left")
        ttk.Button(row, text="播放最近的结果", command=self.play_result).pack(side="left", padx=8)
        ttk.Button(row, text="打开结果文件夹", command=self.open_results).pack(side="left")

    def params_tab(self, tab):
        head = ttk.Frame(tab)
        head.pack(fill="x")
        ttk.Label(head, text="变声参数", style="Section.TLabel").pack(side="left")
        ttk.Button(head, text="保存参数", command=self.save_settings).pack(side="right")
        ttk.Button(head, text="恢复普通话推荐参数", command=self.defaults).pack(side="right", padx=8)
        self.label(tab, "● 实时变声运行中拖动即生效　○ 需停止后重新开始。名称与原版 RVC 实时 GUI 对应，引擎为内置 Applio 3.6.5。",
                   "Sub.TLabel", pady=(6, 2))
        self.label(tab, "Beatrice 只用：音调、共振峰（按 0.5 取整，-2～+2）、采样长度、淡入淡出、额外推理时长、响应阈值、输入降噪、输出增益、"
                   "独占 WASAPI；其余参数仅对 RVC 生效。", "Sub.TLabel", pady=(0, 8))
        holder = ttk.Frame(tab)
        holder.pack(fill="both", expand=True)
        canvas = tk.Canvas(holder, bg="#101622", highlightthickness=0)
        bar = ttk.Scrollbar(holder, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas)
        window = canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=bar.set)
        canvas.pack(side="left", fill="both", expand=True)
        bar.pack(side="right", fill="y")
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(window, width=e.width))
        wheel = lambda e: canvas.yview_scroll(int(-e.delta / 120) or (-1 if e.delta > 0 else 1), "units")
        canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", wheel))
        canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))
        inner.columnconfigure(0, weight=1, uniform="col")
        inner.columnconfigure(1, weight=1, uniform="col")
        columns = [ttk.Frame(inner), ttk.Frame(inner)]
        columns[0].grid(row=0, column=0, sticky="new", padx=(0, 8))
        columns[1].grid(row=0, column=1, sticky="new", padx=(8, 0))
        self.scales = {}
        toggles = {t[0]: t for t in TOGGLES}
        for i, (group, title) in enumerate(PARAM_GROUPS):
            card = ttk.Frame(columns[0 if i < 3 else 1], style="Card.TFrame", padding=(14, 10))
            card.pack(fill="x", pady=(0, 12))
            ttk.Label(card, text=title, style="CardTitle.TLabel").pack(anchor="w", pady=(0, 4))
            for p in (p for p in PARAMS if p.group == group):
                self.slider_row(card, p)
            if group == "perf":
                row = ttk.Frame(card, style="Card.TFrame")
                row.pack(fill="x", pady=(6, 2))
                ttk.Label(row, text="○ 音高算法", style="Card.TLabel").pack(side="left")
                box = ttk.Combobox(row, values=F0_METHODS, state="readonly", width=11, textvariable=self.variable("f0_method"))
                box.pack(side="right")
                self.variable("f0_method").trace_add("write", lambda *_: self.param_changed("f0_method"))
                ttk.Label(card, text="rmvpe 稳定（推荐）；fcpe 更快；crepe-tiny / crepe 更细腻但更吃显卡。原版 RVC 的 pm 在 Applio 中不可用。",
                          style="CardSub.TLabel", wraplength=420, justify="left").pack(anchor="w")
            for name in GROUP_TOGGLES.get(group, ()):
                _, label, live, hint = toggles[name]
                ttk.Checkbutton(card, text=("● " if live else "○ ") + label, style="Card.TCheckbutton",
                                variable=self.bool_var(name)).pack(anchor="w", pady=(6, 0))
                ttk.Label(card, text=hint, style="CardSub.TLabel", wraplength=420, justify="left").pack(anchor="w", padx=(22, 0))

    def slider_row(self, parent, p):
        row = ttk.Frame(parent, style="Card.TFrame")
        row.pack(fill="x", pady=(6, 2))
        top = ttk.Frame(row, style="Card.TFrame")
        top.pack(fill="x")
        ttk.Label(top, text=("● " if p.live else "○ ") + p.label, style="Card.TLabel").pack(side="left")
        var = self.variable(p.name)
        ttk.Entry(top, width=7, justify="right", textvariable=var).pack(side="right")
        state = {"sync": False}

        def from_scale(value):
            if state["sync"]:
                return
            snapped = round(round((float(value) - p.low) / p.step) * p.step + p.low, 4)
            state["sync"] = True
            var.set(self.format_value(p.name, snapped))
            state["sync"] = False

        scale = ttk.Scale(row, from_=p.low, to=p.high, orient="horizontal", command=from_scale)
        scale.pack(fill="x", pady=(2, 0))

        def from_text(*_):
            try:
                value = float(var.get())
            except ValueError:
                return
            if not state["sync"] and p.low <= value <= p.high:
                state["sync"] = True
                scale.set(value)
                state["sync"] = False
            self.param_changed(p.name)

        var.trace_add("write", from_text)
        from_text()
        ttk.Label(row, text=p.hint, style="CardSub.TLabel", wraplength=420, justify="left").pack(anchor="w")
        self.scales[p.name] = scale

    def realtime_running(self):
        return self.process is not None and self.worker_command == "realtime" and self.process.stdin is not None

    def param_changed(self, name):
        if not hasattr(self, "status") or not self.realtime_running():
            return
        if name in LIVE_KEYS:
            if self.live_job is not None:
                self.after_cancel(self.live_job)
            self.live_job = self.after(150, self.push_live)
        else:
            self.status.set("该参数需停止后重新开始实时变声才会生效")

    def push_live(self):
        self.live_job = None
        if not self.realtime_running():
            return
        try:
            config = self.collect()
        except ValueError:
            return  # half-typed value: wait for the next edit
        try:
            self.process.stdin.write("set " + json.dumps(config.live_values(), ensure_ascii=False) + "\n")
            self.process.stdin.flush()
        except (OSError, ValueError):
            return
        self.config = config
        try:
            config.save(self.config_file)
        except OSError:
            pass

    def audio_tab(self, tab):
        self.label(tab, "选择麦克风与输出设备", "Section.TLabel")
        self.label(tab, "软件会优先选择 VB-CABLE 作为变声输出；请选择你的真实麦克风。试听请戴耳机。", "Sub.TLabel", pady=(8, 4))
        self.device_boxes = {}
        for label, key in (("真实麦克风", "input_device"), ("变声输出（耳机试听 / 已有虚拟音频输出）", "output_device"), ("额外监听耳机（可选）", "monitor_device")):
            self.label(tab, label, "Sub.TLabel", pady=(10, 4))
            box = ttk.Combobox(tab, textvariable=self.variable(key), state="readonly")
            box.pack(fill="x")
            self.device_boxes[key] = box
        ttk.Checkbutton(tab, text="启用额外耳机监听", variable=self.bool_var("monitor")).pack(anchor="w", pady=10)
        grow = ttk.Frame(tab)
        grow.pack(fill="x", pady=(0, 4))
        ttk.Label(grow, text="输出增益（倍）", style="Sub.TLabel").pack(side="left")
        ttk.Entry(grow, width=8, textvariable=self.variable("output_gain")).pack(side="left", padx=8)
        self.label(tab, "输出声音偏小就调大：1.0 为原始，1.5≈+3.5dB，2.0≈+6dB，3.0≈+9.5dB；过高会削顶爆音。变声运行中修改即时生效。",
                   "Sub.TLabel", pady=(0, 6))
        row = ttk.Frame(tab)
        row.pack(fill="x", pady=6)
        ttk.Button(row, text="刷新设备", command=self.probe_runtime).pack(side="left")
        ttk.Button(row, text="录音 15 秒并转换", command=self.record_test).pack(side="left", padx=8)
        ttk.Button(row, text="开始实时变声", command=self.start_realtime, style="Accent.TButton").pack(side="left")
        ttk.Button(row, text="停止", command=self.stop_worker).pack(side="left", padx=8)
        row = ttk.Frame(tab)
        row.pack(fill="x", pady=6)
        ttk.Label(row, text="引擎", style="Sub.TLabel").pack(side="left")
        self.backend_selector(row)
        ttk.Button(row, text="A/B 切换引擎", command=self.toggle_backend, style="Accent.TButton").pack(side="left")
        ttk.Button(row, text="录音 15 秒，两种引擎各转换", command=lambda: self.record_test(compare=True)).pack(side="left", padx=8)
        self.label(tab, "运行中切换：开始前两种引擎的模型都已设置时，会同时加载，点“A/B 切换引擎”即时切换（切换瞬间可能有一次轻微爆音）。",
                   "Sub.TLabel", pady=(0, 4))
        self.metrics = tk.StringVar(value="引擎：—    算法延迟：—    单块推理耗时：—    丢块：—    输出欠载：—")
        ttk.Label(tab, textvariable=self.metrics).pack(anchor="w", pady=(12, 6))
        self.meter = ttk.Progressbar(tab, maximum=100)
        self.meter.pack(fill="x")
        self.label(tab, "微信 / Discord 的麦克风请选择 CABLE Output，扬声器仍选你的耳机。\n“算法延迟”与原版 RVC 同口径（采样长度 + 淡入淡出 + 10 ms + 输入降噪 + 声卡缓冲），不含聊天软件和网络。\n「② 变声参数」中带 ● 的参数运行中即时生效，带 ○ 的需停止并重新开始。", "Sub.TLabel", pady=12)

    def help_tab(self, tab):
        self.label(tab, "从试听开始，再接入通话", "Section.TLabel")
        self.label(tab, "1. 安装完成后，按提示重启电脑。\n2. 导入你有权使用的 RVC v2 普通话女声 .pth，以及配套 .index。\n3. 先转换一段普通话音频，或录音 15 秒后试听。\n4. 实时页面选真实麦克风，变声输出选 CABLE Input。\n5. 微信 / Discord 的麦克风选 CABLE Output，然后开始变声。", pady=14)
        self.label(tab, "关于“自带虚拟麦克风”", "Section.TLabel", pady=(8, 0))
        self.label(tab, "本软件集成普通版 VB-CABLE，系统保留其真实名称 CABLE Input / CABLE Output。\nVB-CABLE 由 VB-Audio 提供，是 donationware，欢迎捐赠支持。\n产品与捐赠：https://vb-audio.com/Cable/", "Sub.TLabel", pady=10)
        self.label(tab, "模型与参数保存在当前用户的数据目录；卸载界面程序时保留这些数据。\n建议测试句：我本来以为今天下午天气会比较好，结果晚上突然下雨了。", "Sub.TLabel", pady=12)
        row = ttk.Frame(tab)
        row.pack(fill="x", pady=10)
        ttk.Button(row, text="打开使用说明", command=lambda: os.startfile(HERE / "USER_GUIDE.txt")).pack(side="left")
        ttk.Button(row, text="导出诊断报告", command=self.export_diagnostics).pack(side="left", padx=8)

    def log(self, text):
        line = datetime.now().strftime("%H:%M:%S") + "  " + str(text)
        self.log_box.configure(state="normal")
        self.log_box.insert("end", line + "\n")
        if int(self.log_box.index("end-1c").split(".")[0]) > 500:
            self.log_box.delete("1.0", "100.0")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")
        try:
            if self.log_file.exists() and self.log_file.stat().st_size > 5 * 1024 ** 2:
                self.log_file.replace(self.log_file.with_suffix(".previous.log"))
            with self.log_file.open("a", encoding="utf-8") as stream:
                stream.write(line + "\n")
        except OSError:
            pass

    def error(self, text):
        self.status.set(str(text))
        self.log(text)
        if not self.closing:
            messagebox.showerror("未能完成", str(text), parent=self)

    def ensure_idle(self):
        if self.busy or self.process is not None:
            messagebox.showinfo("操作进行中", "请先停止当前任务或等待完成。", parent=self)
            return False
        return True

    def collect(self, model=False):
        values = asdict(self.config)
        for name, var in self.vars.items():
            value = var.get().strip()
            p = PARAM_BY_NAME.get(name)
            if p is not None:
                try:
                    number = float(value)
                except ValueError:
                    raise ValueError(f"{p.label} 需要填写数字") from None
                if p.integer:
                    if number != int(number):
                        raise ValueError(f"{p.label} 必须为整数")
                    number = int(number)
                value = number
            elif name in INT_FIELDS:
                try:
                    value = int(value)
                except ValueError:
                    raise ValueError(f"{INT_FIELDS[name]} 需要填写整数") from None
            values[name] = value
        for name, var in self.bools.items():
            values[name] = bool(var.get())
        config = Settings(**values).validate(require_model=model)
        return config

    def save_settings(self):
        try:
            self.config = self.collect()
            self.config.save(self.config_file)
            self.status.set("参数已保存；正在进行的转换保持原参数")
        except Exception as exc:
            self.error(exc)

    def defaults(self):
        defaults = Settings()
        for p in PARAMS:
            self.variable(p.name).set(self.format_value(p.name, getattr(defaults, p.name)))
        self.variable("f0_method").set(defaults.f0_method)
        for name, *_ in TOGGLES:
            self.bool_var(name).set(getattr(defaults, name))
        self.save_settings()

    def clear_index(self):
        self.variable("index").set("")
        self.save_settings()

    def use_bundled_model(self, folder, pth_name, idx_name):
        if not self.ensure_idle():
            return
        base = self.root_data / "models" / folder
        pth = base / pth_name
        idx = base / idx_name
        if not pth.is_file():
            self.error("未找到内置模型文件。请先运行离线安装包完成安装（模型位于数据目录 models）。")
            return
        self.variable("model").set(str(pth))
        self.variable("index").set(str(idx) if idx.is_file() else "")
        self.save_settings()
        self.status.set("已切换到内置女声：" + folder + "（主模型 ChineseFemale / 备选 ChineseFemale_HQ）")
        self.log("内置模型：" + str(pth))

    def background(self, action, kind):
        self.busy = True
        self.cancel.clear()
        def run():
            try:
                result = action()
                self.events.put((kind, result))
            except runtime.Cancelled as exc:
                self.events.put(("cancelled", str(exc)))
            except Exception as exc:
                self.events.put(("task_error", str(exc)))
        threading.Thread(target=run, daemon=True).start()

    def import_model(self):
        self.import_asset("model", ".pth")

    def import_index(self):
        self.import_asset("index", ".index")

    def import_beatrice(self):
        self.import_asset("beatrice_model", BEATRICE_SUFFIX)

    def import_asset(self, key, extension):
        if not self.ensure_idle():
            return
        source = filedialog.askopenfilename(title="导入 " + extension, filetypes=[(extension + " 文件", "*" + extension)])
        if not source:
            return
        source = Path(source)
        if not source.name.lower().endswith(extension) or source.stat().st_size == 0:
            self.error("请选择非空的 " + extension + " 文件")
            return
        destination = self.root_data / "models" / uuid.uuid4().hex / source.name
        self.status.set("正在复制模型到软件数据目录…")
        def copy():
            destination.parent.mkdir(parents=True)
            temporary = destination.with_suffix(destination.suffix + ".part")
            try:
                with source.open("rb") as inp, temporary.open("wb") as out:
                    while block := inp.read(4 * 1024 * 1024):
                        runtime.check_cancel(self.cancel)
                        out.write(block)
                temporary.replace(destination)
            finally:
                temporary.unlink(missing_ok=True)
            return key, str(destination)
        self.background(copy, "imported")

    def progress(self, text):
        self.events.put(("status", text))

    def install_runtime(self, archive=None):
        if not self.ensure_idle():
            return
        target = self.root_data / "runtime" / ("Applio-" + runtime.VERSION)
        if target.exists():
            try:
                runtime.verify_engine(target, self.manifest)
                self.variable("runtime").set(str(target))
                self.save_settings()
                self.probe_runtime()
            except Exception as exc:
                self.error(exc)
            return
        self.status.set("正在准备安装运行环境…")
        def work():
            package = Path(archive) if archive else runtime.download(self.root_data / "cache", self.progress, self.cancel)
            return str(runtime.install(package, target, self.manifest, self.progress, self.cancel))
        self.background(work, "runtime_ready")

    def download_runtime(self):
        self.install_runtime()

    def local_runtime(self):
        if not self.ensure_idle():
            return
        path = filedialog.askopenfilename(title="选择官方 " + runtime.ARCHIVE_NAME, filetypes=[("ZIP", "*.zip")])
        if path:
            self.install_runtime(path)

    def select_runtime(self):
        if not self.ensure_idle():
            return
        path = filedialog.askdirectory(title="选择包含 env/python.exe 的 Applio 3.6.5 文件夹")
        if not path:
            return
        try:
            root = runtime.locate(Path(path))
            runtime.verify_engine(root, self.manifest)
            self.variable("runtime").set(str(root))
            self.save_settings()
            self.probe_runtime()
        except Exception as exc:
            self.error(exc)

    def cancel_operation(self):
        if self.busy:
            self.cancel.set()
            self.status.set("正在取消；网络请求最多可能需要 30 秒返回")

    def install_driver(self):
        if not self.ensure_idle():
            return
        helper = INSTALL_DIR / "RVCSetupHelper.exe"
        if not helper.is_file():
            self.error("缺少安装组件，请重新运行统一安装包")
            return
        result = self.root_data / ("driver-result-" + uuid.uuid4().hex + ".ini")
        self.status.set("正在检测或安装虚拟麦克风，请在 Windows 授权窗口中选择“是”…")
        def work():
            import driver
            env = os.environ.copy()
            env["RVC_HELPER_EXE"] = str(helper)
            env["RVC_HELPER_RESULT"] = str(result)
            # Arguments are quoted by PowerShell as a fixed template; paths come from environment.
            driver.powershell("$a='driver --result ' + [char]34 + $env:RVC_HELPER_RESULT + [char]34; "
                "$p=Start-Process -FilePath $env:RVC_HELPER_EXE -ArgumentList $a -Verb RunAs -WindowStyle Hidden -Wait -PassThru; "
                "Write-Output $p.ExitCode", env=env, timeout=300)
            ini = configparser.ConfigParser(interpolation=None)
            ini.read(result, encoding="utf-16")
            if ini.get("Result", "status", fallback="") != "ok":
                raise RuntimeError(ini.get("Result", "error", fallback="驱动安装未成功，请重新运行统一安装包"))
            reboot = ini.get("Result", "reboot", fallback="0") == "1"
            result.unlink(missing_ok=True)
            return reboot
        self.background(work, "driver_ready")

    def probe_runtime(self):
        self.start_worker("probe")

    def start_realtime(self):
        self.start_worker("realtime")

    def convert_file(self, compare=False):
        if not self.ensure_idle():
            return
        source = filedialog.askopenfilename(title="选择普通话测试音频", filetypes=[("音频", "*.wav *.flac *.mp3 *.m4a *.ogg"), ("所有文件", "*.*")])
        if source:
            self.start_worker("offline", source, compare=compare)

    def record_test(self, compare=False):
        self.start_worker("recordtest", compare=compare)

    def open_results(self):
        results = self.root_data / "recordings"
        results.mkdir(exist_ok=True)
        os.startfile(results)

    def worker_script(self) -> Path:
        """Return a worker.py that lives in a DLL-clean directory.

        When frozen, the launch-script directory (and sys.path[0]) is the PyInstaller
        extraction folder (_MEI…), which ships its own libssl/libcrypto/python312 DLLs.
        The Applio conda Python then binds those mismatched OpenSSL DLLs and fails with
        "DLL load failed while importing _ssl: 找不到指定的程序" (and can deadlock in the
        loader). Copy the small worker scripts to a clean data folder and run that copy.
        In source runs we use studio/ directly.
        """
        if not getattr(sys, "frozen", False):
            return HERE / "worker.py"
        stage = self.root_data / "worker"
        stage.mkdir(parents=True, exist_ok=True)
        for name in WORKER_SUPPORT:
            shutil.copy2(HERE / name, stage / name)
        return stage / "worker.py"

    def engine_env(self, root: Path) -> dict:
        """Environment for the Applio conda Python, isolated from the frozen GUI."""
        env = os.environ.copy()
        envdir = root / "env"
        prepend = [envdir, envdir / "Library" / "bin", envdir / "Library" / "mingw-w64" / "bin",
                   envdir / "Library" / "usr" / "bin", envdir / "Scripts", root]
        existing = [str(p) for p in prepend if p.exists()]
        here_norm = os.path.normcase(os.path.normpath(str(HERE)))
        kept = []
        for part in env.get("PATH", "").split(os.pathsep):
            if not part:
                continue
            norm = os.path.normcase(os.path.normpath(part))
            if norm == here_norm or norm.startswith(here_norm + os.sep) \
                    or os.path.basename(norm).lower().startswith("_mei"):
                continue  # never let the frozen GUI's bundled DLLs shadow Applio's
            kept.append(part)
        env["PATH"] = os.pathsep.join(existing + kept)
        env["PYTHONHOME"] = str(envdir)
        env.pop("PYTHONPATH", None)
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        return env

    def start_worker(self, command, source=None, compare=False):
        if not self.ensure_idle():
            return
        try:
            config = self.collect(model=command != "probe")
            if compare:
                for backend in BACKENDS:
                    try:
                        config.check_model(backend)
                    except ValueError as exc:
                        raise ValueError(f"A/B 对比需要两种引擎的模型都已设置。{BACKENDS[backend]}：{exc}") from None
            root = Path(config.runtime)
            if not config.runtime:
                raise ValueError("请先下载运行环境，或选择已有的 Applio 3.6.5 文件夹")
            runtime.verify_engine(root, self.manifest)
            if command in ("realtime", "recordtest") and not config.input_device:
                raise ValueError("请先选择真实麦克风")
            if command == "realtime" and not config.output_device:
                raise ValueError("请先选择输出设备")
            if command == "realtime" and config.monitor and not config.monitor_device:
                raise ValueError("启用了监听，请选择耳机")
            config.save(self.config_file)
            self.config = config
            session = self.root_data / "sessions" / (uuid.uuid4().hex + ".json")
            config.save(session)
            args = [str(root / "env/python.exe"), "-u", str(self.worker_script()), command,
                    "--runtime", str(root), "--config", str(session)]
            if source:
                args.extend(["--input", source])
            if compare:
                args.append("--compare")
            if command in ("offline", "recordtest"):
                results = self.root_data / "recordings"
                results.mkdir(exist_ok=True)
                dest = results / (datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6] + ".wav")
                args.extend(["--output", str(dest)])
            env = self.engine_env(root)
            # A frozen GUI must not make its DLL search directory leak into Applio.
            if getattr(sys, "frozen", False):
                ctypes.windll.kernel32.SetDllDirectoryW(None)
            try:
                # probe is one-shot and never receives "stop"; a DEVNULL stdin gives the
                # child immediate EOF so it cannot park a reader thread on an open pipe
                # (which deadlocks native PortAudio/torch initialization on Windows).
                stdin_handle = subprocess.DEVNULL if command == "probe" else subprocess.PIPE
                process = subprocess.Popen(args, cwd=root, env=env, stdin=stdin_handle,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                          encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
            finally:
                if getattr(sys, "frozen", False):
                    ctypes.windll.kernel32.SetDllDirectoryW(str(HERE))
            self.process = process
            self.worker_failed = False
            self.worker_completed = False
            self.worker_command = command
            self.status.set("正在检测环境…" if command == "probe" else "正在启动转换引擎…")
            def reader():
                try:
                    for line in process.stdout:
                        if line.startswith("@RVC@"):
                            try:
                                self.events.put(("worker", json.loads(line[5:])))
                            except json.JSONDecodeError:
                                self.events.put(("log", line.strip()))
                        else:
                            self.events.put(("log", line.strip()))
                finally:
                    code = process.wait()
                    process.stdout.close()
                    if process.stdin:
                        process.stdin.close()
                    session.unlink(missing_ok=True)
                    self.events.put(("exited", (process, code)))
            threading.Thread(target=reader, daemon=True).start()
        except Exception as exc:
            self.error(exc)

    def stop_worker(self):
        process = self.process
        if process is None:
            return
        try:
            if process.stdin is not None:
                process.stdin.write("stop\n")
                process.stdin.flush()
        except (OSError, ValueError):
            pass
        self.status.set("正在停止并释放音频设备…")
        # Loading a GPU model may not process control messages promptly.
        self.after(4000, lambda: self.force_stop(process))

    def force_stop(self, process):
        if process.poll() is None:
            process.terminate()

    def worker_event(self, event):
        kind = event["event"]
        if kind == "probe":
            self.worker_completed = True
            self.report = event
            self.devices = event["devices"]
            for key, box in self.device_boxes.items():
                field = "inputs" if key == "input_device" else "outputs"
                values = [d["key"] for d in sorted(self.devices, key=lambda d: ("WASAPI" not in d["host"], d["id"])) if d[field]]
                box["values"] = values
                self.variable(key).set(restore_device_key(self.devices, self.variable(key).get(), field))
            render, capture = cable_pair(self.devices)
            if not self.variable("output_device").get() and render:
                self.variable("output_device").set(render["key"])
            if not self.variable("input_device").get():
                physical = [d for d in self.devices if d["inputs"] and not any(x in d["name"].casefold() for x in ("cable", "voicemeeter", "sound mapper", "primary sound"))]
                physical.sort(key=lambda d: (d["id"] != event.get("default_input", -1), "WASAPI" not in d["host"], d["id"]))
                if physical:
                    self.variable("input_device").set(physical[0]["key"])
            cable_text = "可用：聊天软件选择 CABLE Output" if render and capture else "未发现完整端点，请重启电脑后检测或点击安装虚拟麦克风"
            beatrice = "可用" if event.get("beatrice") else "不可用：" + event.get("beatrice_error", "未检测")
            info = (f"显卡：{event['gpu']}\nRVC 引擎：{'依赖可导入' if event['engine'] else '依赖检测失败'}\n"
                    f"Beatrice 引擎：{beatrice}\n音频端点：{len(self.devices)} 个\n虚拟麦克风：{cable_text}")
            if event.get("error"):
                info += "\n错误：" + event["error"]
            self.env_status.set(info)
            self.save_settings()
            self.status.set("检测完成，请导入模型并选择音频设备")
            self.log(info)
        elif kind == "metrics":
            delay = f"{event['delay_ms']} ms" if "delay_ms" in event else "—"
            engine = BACKENDS.get(event.get("backend"), "—")
            self.metrics.set(f"引擎：{engine}    算法延迟：{delay}    单块推理耗时：{event['inference_ms']} ms    丢块：{event['dropped']}    输出欠载：{event['underruns']}")
            db = 20 * math.log10(max(event["rms"], 1e-6))
            self.meter["value"] = max(0, min(100, (db + 60) / 60 * 100))
        elif kind == "converted":
            self.worker_completed = True
            self.last_output = event["path"]
            engine = BACKENDS.get(event.get("backend"), "")
            self.status.set(f"{engine} 转换完成，点击“播放最近的转换结果”试听；A/B 结果可在“打开转换结果文件夹”中对比")
            self.log(f"已生成（{engine}）：" + self.last_output)
        elif kind == "error":
            self.worker_failed = True
            self.error(event["text"])
        elif kind == "params":
            self.status.set(event["text"])
        elif kind == "warning":
            self.status.set(event["text"])
            self.log(event["text"])
        elif kind in ("status", "started", "stopped"):
            text = event["text"]
            if kind == "started" and event.get("engines"):
                loaded = "、".join(BACKENDS[b] for b in event["engines"])
                self.log("已加载引擎：" + loaded + ("（可运行中 A/B 切换）" if len(event["engines"]) > 1 else ""))
            if event.get("output"):
                text += " → " + event["output"]
            if event.get("delay_ms") is not None:
                text += f"（算法延迟约 {event['delay_ms']} ms）"
            self.status.set(text)
            self.log(text)

    def drain_events(self):
        for _ in range(100):
            try:
                kind, value = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "worker":
                self.worker_event(value)
            elif kind == "status":
                self.status.set(value)
            elif kind == "log":
                if value:
                    self.log(value)
            elif kind == "exited":
                proc, code = value
                if self.process is proc:
                    self.process = None
                self.meter["value"] = 0
                if not self.worker_failed and not self.worker_completed:
                    self.status.set("已停止" if self.worker_command == "realtime" or self.closing else f"任务结束（退出码 {code}）；未产生结果，请查看日志")
                self.log(f"引擎进程结束，退出码 {code}")
            elif kind in ("task_error", "cancelled"):
                self.busy = False
                self.error(value) if kind == "task_error" else self.status.set(value)
            elif kind == "imported":
                self.busy = False
                key, path = value
                self.variable(key).set(path)
                if key == "model":
                    self.variable("index").set("")
                self.save_settings()
                self.status.set("文件已导入。实际模型格式会在首次转换前校验。")
            elif kind == "runtime_ready":
                self.busy = False
                self.variable("runtime").set(value)
                self.save_settings()
                if not self.closing:
                    self.probe_runtime()
            elif kind == "driver_ready":
                self.busy = False
                self.status.set("虚拟麦克风安装完成，请重启电脑后使用" if value else "虚拟麦克风已安装，正在刷新设备")
                self.log(self.status.get())
                if not value and self.config.runtime and not self.closing:
                    self.probe_runtime()
        if self.closing and not self.busy and self.process is None:
            self.destroy()
            return
        self.after(80, self.drain_events)

    def play_result(self):
        if self.last_output and Path(self.last_output).is_file():
            os.startfile(self.last_output)
        else:
            messagebox.showinfo("还没有试听文件", "先转换一段音频，或录音 15 秒并转换。", parent=self)

    def export_diagnostics(self):
        path = filedialog.asksaveasfilename(title="保存诊断报告", defaultextension=".json", initialfile="RVCStudio-diagnostics.json")
        if path:
            try:
                report = {"version": APP_VERSION, "python": sys.version, "platform": sys.platform,
                          "runtime": self.variable("runtime").get(), "probe": self.report,
                          "bundled_virtual_microphone": (INSTALL_DIR / "RVCSetupHelper.exe").is_file()}
                Path(path).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
                self.status.set("诊断报告已导出")
            except OSError as exc:
                self.error(exc)

    def close_app(self):
        if self.closing:
            return
        self.closing = True
        self.cancel.set()
        self.stop_worker()
        self.status.set("正在关闭并释放资源…")
        if not self.busy and self.process is None:
            self.destroy()


def main():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    app = Studio()
    if "--smoke-test" in sys.argv:
        output = Path(sys.argv[sys.argv.index("--smoke-test") + 1])
        def smoke():
            config = app.collect()
            report = {"window_title": app.title(), "geometry": app.geometry(),
                      "settings": asdict(config), "driver_bundled": (INSTALL_DIR / "RVCSetupHelper.exe").is_file(),
                      "worker_resource": (HERE / "worker.py").is_file(),
                      "manifest_files": list(app.manifest), "status": "gui-initialized"}
            output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            app.destroy()
        app.after(1600, smoke)
    app.mainloop()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        error = traceback.format_exc()
        (data_dir() / "startup-error.log").write_text(error, encoding="utf-8")
        ctypes.windll.user32.MessageBoxW(None, error, "RVC Studio 启动失败", 0x10)
        sys.exit(1)
