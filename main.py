"""Macro Manager: multiple named keyboard/mouse macros on Windows.
Install: pip install pynput
Run: python main.py
F6 record/stop | F7 play loop | F8 stop | F12 exit
"""

import json
import copy
import queue
import re
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox
from pathlib import Path
from pynput import keyboard, mouse

DIR = Path(__file__).resolve().parent / "macros"
DIR.mkdir(exist_ok=True)
HOTKEYS = {keyboard.Key.f6, keyboard.Key.f7, keyboard.Key.f8, keyboard.Key.f12}

class Manager:
    def __init__(self, root):
        self.root = root
        root.title("Macro Manager")
        root.geometry("850x590")
        self.name = tk.StringVar(value="Expedition 33 - Brau")
        self.status = tk.StringVar(value="Ready")
        self.repeat = tk.BooleanVar(value=True)
        self.delay = tk.StringVar(value="0.4")
        self.events = []
        self.clipboard_events = []
        self.recording = False
        self.playing = False
        self.start = 0
        self.pressed_hotkeys = set()
        self.stop = threading.Event()
        self.messages = queue.Queue()
        self.kb = keyboard.Controller()
        self.ms = mouse.Controller()
        self.last_move = -1
        self.held_keys = []
        self.held_buttons = []
        self.build()
        self.refresh()
        self.ml = mouse.Listener(on_click=self.click, on_move=self.move, on_scroll=self.scroll)
        self.kl = keyboard.Listener(on_press=self.press, on_release=self.release)
        self.ml.start()
        self.kl.start()
        root.after(100, self.poll)
        root.protocol("WM_DELETE_WINDOW", self.close)

    def build(self):
        f = ttk.Frame(self.root, padding=14)
        f.pack(fill="both", expand=True)
        ttk.Label(f, text="Macro name").grid(row=0, column=0, sticky="w")
        ttk.Entry(f, textvariable=self.name, width=36).grid(row=0, column=1, sticky="ew", padx=8)
        ttk.Label(f, text="Saved macros").grid(row=1, column=0, sticky="w", pady=10)
        self.combo = ttk.Combobox(f, state="readonly", width=33)
        self.combo.grid(row=1, column=1, sticky="ew", padx=8)
        self.combo.bind("<<ComboboxSelected>>", lambda _: self.name.set(self.combo.get()))
        ttk.Button(f, text="Load", command=self.load).grid(row=1, column=2)
        actions = ttk.Frame(f)
        actions.grid(row=2, column=0, columnspan=3, pady=10)
        ttk.Button(actions, text="Record (F6)", command=self.toggle_record).pack(side="left", padx=3)
        ttk.Button(actions, text="Play (F7)", command=self.play).pack(side="left", padx=3)
        ttk.Button(actions, text="Stop (F8)", command=self.stop_play).pack(side="left", padx=3)
        ttk.Button(actions, text="Delete", command=self.delete).pack(side="left", padx=3)
        ttk.Checkbutton(f, text="Loop continuously", variable=self.repeat).grid(row=3, column=0, columnspan=2, sticky="w")
        ttk.Label(f, text="Delay between loops (seconds)").grid(row=4, column=0, columnspan=2, sticky="w", pady=8)
        ttk.Entry(f, textvariable=self.delay, width=7).grid(row=4, column=2)
        ttk.Label(f, textvariable=self.status, wraplength=450).grid(row=5, column=0, columnspan=3, sticky="w", pady=10)
        ttk.Label(f, text="F6 record/save  •  F7 replay  •  F8 emergency stop  •  F12 exit").grid(row=6, column=0, columnspan=3, sticky="w")
        f.columnconfigure(1, weight=1)
        ttk.Label(f, text="Edit individual event delays (milliseconds before each event):").grid(
            row=7, column=0, columnspan=3, sticky="w", pady=(14, 3))
        editor = ttk.Frame(f)
        editor.grid(row=8, column=0, columnspan=3, sticky="nsew")
        self.tree = ttk.Treeview(editor, columns=("idx", "delay", "kind", "value"),
                                 show="headings", height=11, selectmode="extended")
        for col, width in (("idx", 45), ("delay", 105), ("kind", 130), ("value", 460)):
            self.tree.heading(col, text={"idx": "#", "delay": "Delay (ms)", "kind": "Event",
                                         "value": "Details"}[col])
            self.tree.column(col, width=width, anchor="w")
        self.tree.pack(side="left", fill="both", expand=True)
        bar = ttk.Scrollbar(editor, orient="vertical", command=self.tree.yview)
        bar.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=bar.set)
        self.tree.bind("<Double-1>", self.edit_delay)
        self.tree.bind("<Control-c>", self.copy_selected)
        self.tree.bind("<Control-v>", self.paste_selected)
        self.tree.bind("<Control-x>", self.cut_selected)
        self.tree.bind("<Delete>", self.delete_selected)
        buttons = ttk.Frame(f)
        buttons.grid(row=9, column=0, columnspan=3, sticky="w", pady=8)
        ttk.Button(buttons, text="Save edited macro", command=self.save).pack(side="left", padx=3)
        ttk.Button(buttons, text="Refresh events", command=self.refresh_events).pack(side="left", padx=3)
        ttk.Button(buttons, text="Copy", command=self.copy_selected).pack(side="left", padx=3)
        ttk.Button(buttons, text="Paste", command=self.paste_selected).pack(side="left", padx=3)
        ttk.Button(buttons, text="Delete selected", command=self.delete_selected).pack(side="left", padx=3)
        ttk.Label(buttons, text="Ctrl+C / Ctrl+V / Ctrl+X • multi-select rows").pack(side="left", padx=8)
        f.rowconfigure(8, weight=1)

    def path(self):
        name = self.name.get().strip()
        if not name or not re.fullmatch(r"[\w \-().]+", name, re.UNICODE):
            raise ValueError("Use a name with letters, numbers, spaces, -, _ or parentheses.")
        return DIR / (name + ".json")

    def refresh(self):
        self.combo["values"] = sorted(p.stem for p in DIR.glob("*.json"))

    def load(self):
        if self.recording or self.playing:
            return self.status.set("Stop recording/playback before loading.")
        try:
            data = json.loads(self.path().read_text(encoding="utf-8"))
            if not isinstance(data, list):
                raise ValueError("Invalid macro format")
            self.events = data
            self.refresh_events()
            self.status.set(f"Loaded {len(data)} events: {self.name.get()}")
        except Exception as exc:
            self.status.set(f"Load error: {exc}")

    def save(self):
        if self.recording or self.playing:
            return self.status.set("Stop recording/playback before saving.")
        try:
            path = self.path()
            path.write_text(json.dumps(self.events, ensure_ascii=False, indent=2),
                            encoding="utf-8")
            self.refresh()
            self.status.set(f"Saved {len(self.events)} events to {path.name}")
        except Exception as exc:
            self.status.set(f"Save error: {exc}")

    def refresh_events(self):
        for item in self.tree.get_children():
            self.tree.delete(item)
        prev = 0.0
        for i, event in enumerate(self.events):
            t = float(event["t"])
            delay_ms = round(max(0, t - prev) * 1000, 1)
            self.tree.insert("", "end", iid=str(i),
                             values=(i + 1, delay_ms, event["kind"],
                                     str(event["value"])[:110]))
            prev = t

    def edit_delay(self, event):
        from tkinter import simpledialog
        if self.recording or self.playing:
            return
        row = self.tree.identify_row(event.y)
        col = self.tree.identify_column(event.x)
        if not row or col != "#2":
            return
        i = int(row)
        previous = float(self.events[i - 1]["t"]) if i else 0.0
        old_ms = round((float(self.events[i]["t"]) - previous) * 1000, 1)
        new_ms = simpledialog.askfloat("Edit event delay",
            f"Delay before event #{i + 1} (milliseconds):",
            initialvalue=old_ms, minvalue=0, maxvalue=3600000, parent=self.root)
        if new_ms is None:
            return
        delta = new_ms / 1000.0 - old_ms / 1000.0
        for item in self.events[i:]:
            item["t"] = round(float(item["t"]) + delta, 5)
        self.refresh_events()
        self.tree.selection_set(str(i))
        self.tree.see(str(i))
        self.status.set("Delay changed. Click 'Save edited macro' to persist.")

    def selected_indices(self):
        return sorted(int(item) for item in self.tree.selection())

    def editable(self):
        if self.recording or self.playing:
            self.status.set("Stop recording/playback before editing.")
            return False
        return True

    def to_relative_events(self):
        previous = 0.0
        result = []
        for event in self.events:
            item = copy.deepcopy(event)
            timestamp = float(item.pop("t"))
            item["delay"] = max(0.0, timestamp - previous)
            previous = timestamp
            result.append(item)
        return result

    def from_relative_events(self, relative):
        current = 0.0
        self.events = []
        for item in relative:
            current += max(0.0, float(item["delay"]))
            event = {k: copy.deepcopy(v) for k, v in item.items() if k != "delay"}
            event["t"] = round(current, 5)
            self.events.append(event)
        self.refresh_events()

    def copy_selected(self, event=None):
        if not self.editable():
            return "break"
        selected = self.selected_indices()
        if not selected:
            return "break"
        relative = self.to_relative_events()
        self.clipboard_events = copy.deepcopy([relative[i] for i in selected])
        self.status.set(f"Copied {len(selected)} events. Select a row and press Ctrl+V.")
        return "break"

    def paste_selected(self, event=None):
        if not self.editable() or not self.clipboard_events:
            return "break"
        relative = self.to_relative_events()
        selected = self.selected_indices()
        insert_at = selected[-1] + 1 if selected else len(relative)
        copied = copy.deepcopy(self.clipboard_events)
        relative[insert_at:insert_at] = copied
        self.from_relative_events(relative)
        self.tree.selection_set([str(i) for i in range(insert_at, insert_at + len(copied))])
        self.tree.see(str(insert_at))
        self.status.set(f"Pasted {len(copied)} events. Save to keep changes.")
        return "break"

    def delete_selected(self, event=None):
        if not self.editable():
            return "break"
        selected = self.selected_indices()
        if not selected:
            return "break"
        relative = self.to_relative_events()
        # Preserve elapsed time of deleted commands in the next remaining event.
        for i in reversed(selected):
            removed_delay = relative[i]["delay"]
            del relative[i]
            if i < len(relative):
                relative[i]["delay"] += removed_delay
        self.from_relative_events(relative)
        self.status.set(f"Deleted {len(selected)} events. Save to keep changes.")
        return "break"

    def cut_selected(self, event=None):
        if not self.editable():
            return "break"
        if self.selected_indices():
            self.copy_selected()
            self.delete_selected()
        return "break"

    def toggle_record(self):
        if self.playing:
            return
        if self.recording:
            self.recording = False
            try:
                path = self.path()
                path.write_text(json.dumps(self.events, ensure_ascii=False), encoding="utf-8")
                self.refresh()
                self.refresh_events()
                self.status.set(f"Saved {len(self.events)} events to {path.name}")
            except Exception as exc:
                self.status.set(f"Save error: {exc}")
        else:
            try:
                self.path()
            except Exception as exc:
                return self.status.set(str(exc))
            self.events = []
            self.last_move = -1
            self.start = time.perf_counter()
            self.recording = True
            self.status.set("Recording... press F6 to save.")

    def add(self, kind, value):
        if self.recording and not self.playing:
            t = time.perf_counter() - self.start
            if kind == "move" and t - self.last_move < .025:
                return
            if kind == "move":
                self.last_move = t
            self.events.append({"t": round(t, 5), "kind": kind, "value": value})

    @staticmethod
    def encode(key):
        if isinstance(key, keyboard.KeyCode):
            return {"char": key.char, "vk": key.vk}
        return {"special": key.name}

    @staticmethod
    def decode(value):
        if "special" in value:
            return getattr(keyboard.Key, value["special"])
        if value.get("char") is not None:
            return keyboard.KeyCode.from_char(value["char"])
        return keyboard.KeyCode.from_vk(value["vk"])

    def press(self, key):
        if key in HOTKEYS:
            if key in self.pressed_hotkeys:
                return
            self.pressed_hotkeys.add(key)
            self.messages.put({keyboard.Key.f6: "record", keyboard.Key.f7: "play",
                               keyboard.Key.f8: "stop", keyboard.Key.f12: "exit"}[key])
            return
        self.add("key_down", self.encode(key))

    def release(self, key):
        if key in HOTKEYS:
            self.pressed_hotkeys.discard(key)
        else:
            self.add("key_up", self.encode(key))

    def click(self, x, y, button, down):
        self.add("mouse_down" if down else "mouse_up", {"x": x, "y": y, "button": button.name})

    def move(self, x, y):
        self.add("move", {"x": x, "y": y})

    def scroll(self, x, y, dx, dy):
        self.add("scroll", {"dx": dx, "dy": dy})

    def play(self):
        if self.recording or self.playing:
            return
        if not self.events:
            self.load()
        if not self.events:
            return
        try:
            gap = max(0, float(self.delay.get()))
        except ValueError:
            return self.status.set("Invalid loop delay.")
        self.stop.clear()
        self.playing = True
        self.status.set("Playing... F8 to stop.")
        threading.Thread(target=self.worker, args=(list(self.events), self.repeat.get(), gap), daemon=True).start()

    def worker(self, events, loop, gap):
        try:
            while not self.stop.is_set():
                prev = 0
                for event in events:
                    if self.stop.wait(max(0, event["t"] - prev)):
                        return
                    prev = event["t"]
                    kind, v = event["kind"], event["value"]
                    if kind == "key_down":
                        k = self.decode(v)
                        self.kb.press(k)
                        self.held_keys.append(k)
                    elif kind == "key_up":
                        k = self.decode(v)
                        self.kb.release(k)
                        if k in self.held_keys:
                            self.held_keys.remove(k)
                    elif kind in ("mouse_down", "mouse_up"):
                        self.ms.position = (v["x"], v["y"])
                        b = getattr(mouse.Button, v["button"])
                        if kind == "mouse_down":
                            self.ms.press(b)
                            self.held_buttons.append(b)
                        else:
                            self.ms.release(b)
                            if b in self.held_buttons:
                                self.held_buttons.remove(b)
                    elif kind == "move":
                        self.ms.position = (v["x"], v["y"])
                    elif kind == "scroll":
                        self.ms.scroll(v["dx"], v["dy"])
                if not loop or self.stop.wait(gap):
                    break
        except Exception as exc:
            self.messages.put(("error", str(exc)))
        finally:
            for k in self.held_keys:
                try: self.kb.release(k)
                except Exception: pass
            for b in self.held_buttons:
                try: self.ms.release(b)
                except Exception: pass
            self.held_keys.clear()
            self.held_buttons.clear()
            self.messages.put("finished")

    def stop_play(self):
        self.stop.set()
        self.status.set("Stopping playback...")

    def delete(self):
        if self.recording or self.playing:
            return
        try:
            path = self.path()
            if path.exists() and messagebox.askyesno("Delete macro", f"Delete {path.stem}?"):
                path.unlink()
                self.events = []
                self.refresh()
                self.refresh_events()
                self.status.set("Macro deleted.")
        except Exception as exc:
            self.status.set(str(exc))

    def poll(self):
        try:
            while True:
                msg = self.messages.get_nowait()
                if msg == "record": self.toggle_record()
                elif msg == "play": self.play()
                elif msg == "stop": self.stop_play()
                elif msg == "exit":
                    self.close()
                    return
                elif msg == "finished":
                    self.playing = False
                    self.status.set("Playback stopped.")
                elif isinstance(msg, tuple):
                    self.status.set("Playback error: " + msg[1])
        except queue.Empty:
            pass
        self.root.after(100, self.poll)

    def close(self):
        self.stop.set()
        self.recording = False
        self.kl.stop()
        self.ml.stop()
        self.root.destroy()

if __name__ == "__main__":
    root = tk.Tk()
    Manager(root)
    root.mainloop()
