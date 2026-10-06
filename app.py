"""Musi Installer — a local, guided desktop experience."""
import asyncio
import os
import queue
import sys
import threading
import time
from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk

from download import download_musi
from installer import check_device, install_package, verify_ipa

VERSION = "2.0"
BG = "#F9F9F9"
PANEL = "#F2F2F2"
CARD = "#FFFFFF"
LINE = "#E1E1E1"
TEXT = "#202020"
MUTED = "#626262"
ACCENT = "#0067C0"
GREEN = "#107C10"
RED = "#C42B1C"
STEPS = [
    ("device", "Connect your iPhone", "USB connection and device authorization"),
    ("signin", "Sign in to Apple", "Your account and two-factor verification"),
    ("download", "Download Musi", "Your original copy, directly from Apple"),
    ("prepare", "Prepare the app", "Check the package and preserve its signature"),
    ("transfer", "Transfer to iPhone", "Copy the app over your USB connection"),
    ("install", "Install Musi", "Let iOS finish the installation"),
]


def duration(seconds):
    seconds = max(0, int(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


def size(value):
    return f"{value / (1024 * 1024):.1f} MB"


def center_dialog(dialog, parent):
    dialog.update_idletasks()
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        user32.GetAncestor.restype = wintypes.HWND
        owner = user32.GetAncestor(parent.winfo_id(), 2)
        window = user32.GetAncestor(dialog.winfo_id(), 2)
        owner_rect, window_rect = wintypes.RECT(), wintypes.RECT()
        user32.GetWindowRect(owner, ctypes.byref(owner_rect))
        user32.GetWindowRect(window, ctypes.byref(window_rect))
        x = owner_rect.left + (owner_rect.right - owner_rect.left - window_rect.right + window_rect.left) // 2
        y = owner_rect.top + (owner_rect.bottom - owner_rect.top - window_rect.bottom + window_rect.top) // 2
        user32.SetWindowPos(window, 0, x, y, 0, 0, 0x0005)
    else:
        dialog.geometry(f"+{parent.winfo_rootx() + (parent.winfo_width() - dialog.winfo_width()) // 2}+{parent.winfo_rooty() + (parent.winfo_height() - dialog.winfo_height()) // 2}")


def friendly_error(error):
    text = str(error).lower()
    if "cancel" in text:
        return "Verification cancelled", "Nothing was installed. Enter your password to try again."
    if "more than one" in text:
        return "Choose one iPhone", "Disconnect the other devices, then try again."
    if any(word in text for word in ("usbmux", "no iphone", "notpaired", "pairing", "connectionrefused", "connectionfailed", "trust")):
        return "iPhone not connected", "Connect it by USB, unlock it, and tap Trust. Make sure Apple Devices or iTunes can see it."
    if "license" in text:
        return "Apple couldn't authorize this download", "Use the Apple account that originally obtained Musi. Apple decides whether the app is still available to download."
    if any(word in text for word in ("password", "credentials", "authentication", "sign-in")):
        return "Apple sign-in didn't finish", "Check your account details and verification code, then try again. Apple may also require a short wait."
    if any(word in text for word in ("network", "timeout", "timed out", "http", "download")):
        return "The download couldn't finish", "Check your internet connection and try again. Apple may be temporarily unavailable."
    if "not musi" in text or "package" in text or "zip" in text:
        return "We couldn't use this app package", "Choose your original Musi IPA, or download a fresh copy using your Apple account."
    return "This step couldn't finish", "Your progress has stopped. Check the details if needed, then try again."


class StepCard(ctk.CTkFrame):
    def __init__(self, parent, number, title, description):
        super().__init__(parent, fg_color=BG, corner_radius=0, border_width=0)
        self.state = "pending"
        self.started = None
        self.fraction = None
        self.grid_columnconfigure(1, weight=1)
        self.number = ctk.CTkLabel(self, text=str(number), width=22, height=20,
                                  corner_radius=11, fg_color=LINE, text_color=MUTED,
                                  font=("Segoe UI", 11))
        self.number.grid(row=0, column=0, padx=(0, 12), pady=(4, 0))
        self.title = ctk.CTkLabel(self, text=title, anchor="w", text_color=TEXT,
                                 font=("Segoe UI", 13), height=20)
        self.title.grid(row=0, column=1, sticky="ew", pady=(4, 0))
        self.status = ctk.CTkLabel(self, text="Pending", text_color=MUTED, font=("Segoe UI", 11), width=80, height=20, anchor="e")
        self.status.grid(row=0, column=2, pady=(4, 0))
        self.detail = ctk.CTkLabel(self, text=description, anchor="w", text_color=MUTED,
                                  font=("Segoe UI", 11), height=16)
        self.detail.grid(row=1, column=1, columnspan=2, sticky="ew")
        self.bar = ctk.CTkProgressBar(self, height=3, fg_color=LINE, progress_color=ACCENT,
                                     corner_radius=0, indeterminate_speed=0.8)
        self.bar.grid(row=2, column=1, columnspan=2, sticky="ew", pady=(4, 6))
        self.bar.set(0)
        self.default_description = description

    def reset(self):
        self.bar.stop()
        self.bar.configure(mode="determinate", progress_color=ACCENT)
        self.bar.set(0)
        self.state, self.started, self.fraction = "pending", None, None
        self.configure(border_color=CARD)
        self.number.configure(text_color=MUTED, fg_color=LINE)
        self.status.configure(text="Pending", text_color=MUTED)
        self.detail.configure(text=self.default_description)

    def set_state(self, state, message=""):
        previous = self.state
        self.state = state
        self.bar.stop()
        if state in ("active", "waiting"):
            if previous not in ("active", "waiting"):
                self.started = time.monotonic()
                self.fraction = None
            self.configure(border_color=ACCENT)
            self.number.configure(text_color="#FFFFFF", fg_color=ACCENT)
            self.status.configure(text="Verify account" if state == "waiting" else "In progress", text_color=ACCENT)
            if self.fraction is None and state != "waiting":
                self.bar.configure(mode="indeterminate", progress_color=ACCENT)
                self.bar.start()
        elif state in ("done", "skipped"):
            self.configure(border_color=CARD)
            self.number.configure(text_color=GREEN if state == "done" else MUTED, fg_color=LINE)
            self.status.configure(text="Done" if state == "done" else "Not needed", text_color=GREEN if state == "done" else MUTED)
            self.bar.configure(mode="determinate", progress_color="#7A7A7A" if state == "done" else LINE)
            self.bar.set(1)
        elif state == "error":
            self.configure(border_color=RED)
            self.status.configure(text="Stopped", text_color=RED)
            self.bar.configure(mode="determinate", progress_color=RED)
            self.bar.set(self.fraction or 0)
        if message:
            self.detail.configure(text=message)

    def progress(self, event):
        self.state = "active"
        self.bar.stop()
        self.fraction = min(1, max(0, event.get("progress", 0)))
        self.bar.configure(mode="determinate", progress_color=ACCENT)
        self.bar.set(self.fraction)
        self.status.configure(text=f"{self.fraction * 100:.0f}%", text_color=ACCENT)
        total, completed = event.get("total", 0), event.get("bytes", 0)
        if total:
            text = f"{size(completed)} of {size(total)}"
            speed, eta = event.get("speed", 0), event.get("eta", 0)
            if speed > 0:
                text += f"  ·  {size(speed)}/s"
            if eta > 0 and self.fraction < 1:
                text += f"  ·  about {duration(eta)} left"
            self.detail.configure(text=text)
        elif self.fraction >= 1:
            self.detail.configure(text="Finishing up on your iPhone…")
        else:
            self.detail.configure(text="Installing on your iPhone · iOS controls this step")


class CodeDialog(ctk.CTkToplevel):
    def __init__(self, parent, callback):
        super().__init__(parent)
        self.callback = callback
        self.title("Verify your Apple account")
        self.geometry("440x340")
        self.resizable(False, False)
        self.configure(fg_color=PANEL)
        self.transient(parent)
        self.protocol("WM_DELETE_WINDOW", lambda: self.finish(None))
        ctk.CTkLabel(self, text="Apple account verification", text_color=ACCENT, font=("Segoe UI", 11, "bold")).pack(pady=(26, 5))
        ctk.CTkLabel(self, text="Verification code", text_color=TEXT, font=("Segoe UI", 21, "bold")).pack()
        ctk.CTkLabel(self, text="Enter the six-digit code from your trusted\nApple device or verification message.",
                     text_color=MUTED, font=("Segoe UI", 13)).pack(pady=(8, 15))
        self.entry = ctk.CTkEntry(self, width=260, height=48, justify="center", font=("Segoe UI", 24),
                                  fg_color=BG, border_color=LINE, placeholder_text="000000")
        self.entry.pack()
        self.hint = ctk.CTkLabel(self, text="", text_color=RED, font=("Segoe UI", 11))
        self.hint.pack(pady=(3, 0))
        ctk.CTkButton(self, text="Verify account", width=260, height=40, fg_color=ACCENT, hover_color="#005AA6",
                      text_color="#FFFFFF", font=("Segoe UI", 13), command=self.submit).pack()
        ctk.CTkButton(self, text="Cancel", fg_color="transparent", hover_color=CARD, text_color=MUTED,
                      command=lambda: self.finish(None)).pack(pady=7)
        self.bind("<Return>", lambda _: self.submit())
        self.after(350, self.activate)

    def activate(self):
        self.grab_set()
        self.entry.focus_set()
        self.after(50, lambda: center_dialog(self, self.master))

    def submit(self):
        code = self.entry.get().replace(" ", "").strip()
        if len(code) != 6 or not code.isascii() or not code.isdigit():
            self.hint.configure(text="Enter a six-digit code.")
            return
        self.finish(code)

    def finish(self, code):
        self.callback(code)
        self.grab_release()
        self.destroy()


class MusiInstaller(ctk.CTk):
    def __init__(self):
        ctk.set_appearance_mode("light")
        super().__init__()
        self.title("Musi Installer")
        self.geometry("1010x770")
        self.minsize(930, 740)
        self.configure(fg_color=BG)
        self.events = queue.Queue()
        self.running = False
        self.started = None
        self.package_path = None
        self.technical_error = ""
        self.mode = ctk.StringVar(value="Apple account")
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(1, weight=1)

        toolbar = ctk.CTkFrame(self, height=44, fg_color=PANEL, corner_radius=0)
        toolbar.grid(row=0, column=0, columnspan=2, sticky="ew")
        toolbar.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(toolbar, text="Musi Installer", text_color=TEXT, font=("Segoe UI", 14, "bold"), height=36).grid(row=0, column=0, padx=20, pady=4)
        ctk.CTkLabel(toolbar, text=f"Version {VERSION}", text_color=MUTED, font=("Segoe UI", 11)).grid(row=0, column=2, padx=20)

        left = ctk.CTkFrame(self, width=190, corner_radius=0, fg_color=PANEL)
        left.grid(row=1, column=0, sticky="nsew")
        left.grid_propagate(False)
        left.grid_columnconfigure(0, weight=1)
        left.grid_rowconfigure(4, weight=1)
        ctk.CTkLabel(left, text="Install from", anchor="w", text_color=MUTED, font=("Segoe UI", 11)).grid(row=0, column=0, sticky="ew", padx=18, pady=(24, 8))
        self.account_nav = ctk.CTkButton(left, text="Apple account", anchor="w", height=36, corner_radius=4,
                                        fg_color="#E0EBF5", hover_color="#D4E5F4", text_color=TEXT,
                                        font=("Segoe UI", 13), command=lambda: self.change_mode("Apple account"))
        self.account_nav.grid(row=1, column=0, sticky="ew", padx=10, pady=2)
        self.local_nav = ctk.CTkButton(left, text="Local IPA file", anchor="w", height=36, corner_radius=4,
                                      fg_color="transparent", hover_color="#E6E6E6", text_color=TEXT,
                                      font=("Segoe UI", 13), command=lambda: self.change_mode("Local IPA"))
        self.local_nav.grid(row=2, column=0, sticky="ew", padx=10, pady=2)
        ctk.CTkLabel(left, text="Requirements", anchor="w", text_color=TEXT, font=("Segoe UI", 12, "bold")).grid(row=5, column=0, sticky="ew", padx=18, pady=(10, 5))
        ctk.CTkLabel(left, text="Keep Apple Devices open\nthroughout the process.\n\nConnect your iPhone by USB.\nUnlock it and tap Trust.\n\nKeep the cable connected\nuntil installation finishes.", anchor="w", justify="left", text_color=MUTED,
                     font=("Segoe UI", 11)).grid(row=6, column=0, sticky="ew", padx=18, pady=(0, 25))

        main = ctk.CTkFrame(self, fg_color="transparent")
        main.grid(row=1, column=1, sticky="nsew", padx=30, pady=(20, 18))
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(6, weight=1)
        self.heading = ctk.CTkLabel(main, text="Install Musi", anchor="w", text_color=TEXT, font=("Segoe UI", 23, "bold"))
        self.heading.grid(row=0, column=0, sticky="ew")
        self.subtitle = ctk.CTkLabel(main, text="Download your copy from Apple and install it on your iPhone.", anchor="w", text_color=MUTED, font=("Segoe UI", 12))
        self.subtitle.grid(row=1, column=0, sticky="ew", pady=(2, 14))
        self.form = ctk.CTkFrame(main, fg_color=CARD, border_width=1, border_color=LINE, corner_radius=5)
        self.form.grid(row=2, column=0, sticky="ew")
        self.form.grid_columnconfigure((0, 1), weight=1)
        self.email_label = ctk.CTkLabel(self.form, text="Apple account", anchor="w", text_color=TEXT, height=20, font=("Segoe UI", 12))
        self.email_label.grid(row=0, column=0, sticky="ew", padx=(16, 10), pady=(12, 5))
        self.email_entry = ctk.CTkEntry(self.form, placeholder_text="Email address", height=34,
                                       fg_color=CARD, border_color="#ADADAD", corner_radius=4, font=("Segoe UI", 12))
        self.email_entry.grid(row=1, column=0, sticky="ew", padx=(16, 10), pady=(0, 12))
        self.password_label = ctk.CTkLabel(self.form, text="Password", anchor="w", text_color=TEXT, height=20, font=("Segoe UI", 12))
        self.password_label.grid(row=0, column=1, sticky="ew", padx=(10, 16), pady=(12, 5))
        self.password_entry = ctk.CTkEntry(self.form, placeholder_text="Apple account password", show="•", height=34,
                                          fg_color=CARD, border_color="#ADADAD", corner_radius=4, font=("Segoe UI", 12))
        self.password_entry.grid(row=1, column=1, sticky="ew", padx=(10, 16), pady=(0, 12))
        self.file_button = ctk.CTkButton(self.form, text="Choose IPA…", height=34, width=120, corner_radius=4, fg_color=PANEL,
                                         border_width=1, border_color=LINE, hover_color="#E6E6E6", text_color=TEXT, command=self.choose_file)
        self.file_label = ctk.CTkLabel(self.form, text="No file selected", text_color=MUTED, font=("Segoe UI", 12), anchor="w")
        self.form_hint = ctk.CTkLabel(main, text="You may be asked for a verification code. Your credentials are not saved.",
                                     anchor="w", text_color=MUTED, font=("Segoe UI", 11))
        self.form_hint.grid(row=3, column=0, sticky="ew", pady=(6, 10))
        self.inline_hint = ctk.CTkLabel(main, text="", anchor="w", text_color=RED, font=("Segoe UI", 11), height=18)
        self.inline_hint.grid(row=4, column=0, sticky="ew")
        ctk.CTkLabel(main, text="Installation progress", anchor="w", text_color=TEXT, font=("Segoe UI", 14, "bold"), height=24).grid(row=5, column=0, sticky="ew", pady=(0, 4))
        tracker = ctk.CTkFrame(main, fg_color="transparent")
        tracker.grid(row=6, column=0, sticky="nsew")
        tracker.grid_columnconfigure(0, weight=1)
        self.cards = {}
        for number, (key, title, description) in enumerate(STEPS, 1):
            card = StepCard(tracker, number, title, description)
            card.grid(row=number - 1, column=0, sticky="ew")
            self.cards[key] = card

        footer = ctk.CTkFrame(main, fg_color="transparent")
        footer.grid(row=7, column=0, sticky="ew", pady=(8, 0))
        footer.grid_columnconfigure(0, weight=1)
        self.count_label = ctk.CTkLabel(footer, text="0 of 6 steps completed", anchor="w", text_color=MUTED, font=("Segoe UI", 11), height=18)
        self.count_label.grid(row=0, column=0, sticky="ew")
        self.elapsed_label = ctk.CTkLabel(footer, text="", text_color=MUTED, font=("Segoe UI", 11), height=18)
        self.elapsed_label.grid(row=0, column=1)
        self.overall = ctk.CTkProgressBar(footer, height=3, fg_color=LINE, progress_color=ACCENT, corner_radius=0)
        self.overall.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(5, 9))
        self.overall.set(0)
        self.result_label = ctk.CTkLabel(footer, text="The app will keep its original App Store signature.", anchor="w", justify="left", wraplength=700,
                                        text_color=MUTED, font=("Segoe UI", 11), height=20)
        self.result_label.grid(row=2, column=0, columnspan=2, sticky="ew")
        bottom = ctk.CTkFrame(main, fg_color="transparent")
        bottom.grid(row=8, column=0, sticky="ew", pady=(10, 0))
        bottom.grid_columnconfigure(0, weight=1)
        self.details_button = ctk.CTkButton(bottom, text="Error details", fg_color="transparent", hover_color=PANEL,
                                            text_color=ACCENT, width=110, height=32, corner_radius=4, command=self.show_error_details)
        self.action = ctk.CTkButton(bottom, text="Install Musi", width=135, height=34, corner_radius=4, fg_color=ACCENT,
                                    hover_color="#005AA6", text_color="#FFFFFF", font=("Segoe UI", 12), command=self.start)
        self.action.grid(row=0, column=1, sticky="e")
        self.after(80, self.drain_events)
        self.after(1000, self.tick)
        self.after(100, self.fit_window)

    def fit_window(self):
        self.update_idletasks()
        scale = self._get_window_scaling()
        available_height = self.winfo_screenheight() - 110
        available_width = self.winfo_screenwidth() - 50
        fit = min(1, available_width / (1010 * scale), available_height / (770 * scale))
        if fit < 1:
            ctk.set_widget_scaling(fit)
            ctk.set_window_scaling(fit)
            self.update_idletasks()
        x = max(0, (self.winfo_screenwidth() - self.winfo_width()) // 2)
        y = max(0, (self.winfo_screenheight() - self.winfo_height() - 80) // 2)
        self.geometry(f"+{x}+{y}")

    def change_mode(self, value):
        if self.running:
            return
        self.mode.set(value)
        local = value == "Local IPA"
        self.account_nav.configure(fg_color="transparent" if local else "#E0EBF5")
        self.local_nav.configure(fg_color="#E0EBF5" if local else "transparent")
        for widget in (self.email_label, self.email_entry, self.password_label, self.password_entry):
            widget.grid_remove() if local else widget.grid()
        if local:
            self.file_button.grid(row=0, column=0, sticky="w", padx=16, pady=16)
            self.file_label.grid(row=0, column=1, sticky="ew", padx=16, pady=16)
        else:
            self.file_button.grid_remove()
            self.file_label.grid_remove()
        self.form_hint.configure(text="Choose an original Musi IPA downloaded with your own Apple account." if local else
                                  "You may be asked for a verification code. Your credentials are not saved.")
        self.action.configure(text="Install IPA" if local else "Install Musi")
        self.inline_hint.configure(text="")

    def choose_file(self):
        selected = filedialog.askopenfilename(title="Choose your original Musi IPA", filetypes=[("iOS app package", "*.ipa")])
        if selected:
            self.package_path = Path(selected)
            self.file_label.configure(text=self.package_path.name)

    def set_running(self, running):
        self.running = running
        for widget in (self.account_nav, self.local_nav, self.email_entry, self.password_entry, self.file_button, self.action):
            widget.configure(state="disabled" if running else "normal")
        if running:
            self.action.configure(text="Installing…")

    def start(self):
        if self.running:
            return
        local = self.mode.get() == "Local IPA"
        email, password = self.email_entry.get().strip(), self.password_entry.get()
        if local and self.package_path is None:
            self.inline_hint.configure(text="Choose your Musi IPA to get started.")
            return
        if not local and (not email or not password):
            self.inline_hint.configure(text="Enter your Apple account and password.")
            return
        self.password_entry.delete(0, "end")
        self.inline_hint.configure(text="")
        self.details_button.grid_remove()
        self.technical_error = ""
        self.started = time.monotonic()
        for card in self.cards.values():
            card.reset()
        self.heading.configure(text="Installing Musi", text_color=TEXT)
        self.subtitle.configure(text="Keep Apple Devices open and your iPhone connected.")
        self.result_label.configure(text="You can follow the live progress below.", text_color=MUTED)
        self.set_running(True)
        threading.Thread(target=self.worker, args=(local, email, password, self.package_path), daemon=True).start()

    def request_code(self):
        reply = {"type": "request_code", "ready": threading.Event(), "code": None}
        self.events.put(reply)
        reply["ready"].wait()
        return reply["code"]

    def worker(self, local, email, password, path):
        try:
            asyncio.run(check_device(self.events.put))
            if local:
                for step in ("signin", "download"):
                    self.events.put({"type": "stage", "stage": step, "state": "skipped", "message": "Using your existing IPA"})
                self.events.put({"type": "stage", "stage": "prepare", "state": "active", "message": "Checking your Musi package"})
            else:
                path = download_musi(email, password, self.request_code, self.events.put)
            password = None
            verify_ipa(path)
            self.events.put({"type": "stage", "stage": "prepare", "state": "done", "message": "Your original Musi package is ready"})
            asyncio.run(install_package(path, self.events.put))
            self.events.put({"type": "success", "path": str(path)})
        except Exception as error:
            self.events.put({"type": "failure", "message": str(error)})

    def handle(self, event):
        kind = event.get("type")
        if kind == "request_code":
            def reply(code):
                event["code"] = code
                event["ready"].set()
            CodeDialog(self, reply)
        elif kind in ("stage", "progress"):
            key = event.get("stage")
            if key not in self.cards:
                return
            card = self.cards[key]
            if kind == "stage":
                # A device reconnection doesn't undo the completed preflight.
                if key == "device" and card.state == "done" and event.get("state") == "active":
                    return
                card.set_state(event.get("state", "active"), event.get("message", ""))
            else:
                card.progress(event)
            if key == "device" and event.get("device"):
                self.subtitle.configure(text="iPhone connected · Keep the USB cable plugged in.")
        elif kind == "success":
            self.set_running(False)
            self.heading.configure(text="Musi is installed", text_color=GREEN)
            self.subtitle.configure(text="Installation complete. You can open Musi on your iPhone.")
            self.result_label.configure(text="You can unplug your iPhone. Restore in-app purchases in Musi using the same Apple account.", text_color=GREEN)
            self.action.configure(text="Install again")
        elif kind == "failure":
            self.set_running(False)
            self.technical_error = event.get("message", "Unknown error")
            title, description = friendly_error(self.technical_error)
            self.heading.configure(text=title, text_color=RED)
            self.subtitle.configure(text="The operation has stopped. Check the information below.")
            self.result_label.configure(text=description, text_color=MUTED)
            for card in self.cards.values():
                if card.state in ("active", "waiting"):
                    card.set_state("error", "This step needs your attention")
            self.details_button.grid(row=0, column=0, sticky="w")
            self.action.configure(text="Try again")
        completed = sum(card.state in ("done", "skipped") for card in self.cards.values())
        self.count_label.configure(text=f"{completed} of 6 steps completed")
        self.overall.set(completed / 6)

    def drain_events(self):
        try:
            while True:
                self.handle(self.events.get_nowait())
        except queue.Empty:
            pass
        self.after(80, self.drain_events)

    def tick(self):
        if self.running and self.started:
            self.elapsed_label.configure(text=f"Elapsed {duration(time.monotonic() - self.started)}")
            for key, card in self.cards.items():
                if card.state == "active" and card.fraction is None and card.started:
                    elapsed = duration(time.monotonic() - card.started)
                    text = "Waiting for Apple" if key in ("signin", "download") else "Working"
                    card.status.configure(text=elapsed)
                    if key == "signin":
                        card.detail.configure(text=f"{text} · timing varies · {elapsed} elapsed")
        self.after(1000, self.tick)

    def show_error_details(self):
        dialog = ctk.CTkToplevel(self)
        dialog.title("Error details")
        dialog.geometry("580x290")
        dialog.configure(fg_color=PANEL)
        dialog.transient(self)
        ctk.CTkLabel(dialog, text="Details for troubleshooting", text_color=TEXT, font=("Segoe UI", 18, "bold")).pack(pady=18)
        field = ctk.CTkTextbox(dialog, fg_color=BG, text_color=MUTED, height=150, wrap="word")
        field.pack(fill="both", expand=True, padx=20)
        field.insert("1.0", self.technical_error)
        field.configure(state="disabled")
        ctk.CTkButton(dialog, text="Close", command=dialog.destroy, fg_color=ACCENT, text_color="#FFFFFF").pack(pady=15)
        dialog.after(350, lambda: center_dialog(dialog, self))

    def close(self):
        if self.running:
            self.inline_hint.configure(text="Please keep this window open until the current operation finishes.")
            return
        self.destroy()

    def preview(self, state):
        """Visual QA only: does not contact Apple or an iPhone."""
        if state == "download":
            for step in ("device", "signin"):
                self.handle({"type": "stage", "stage": step, "state": "done", "message": "Completed"})
            self.handle({"type": "stage", "stage": "download", "state": "active", "message": "Downloading your original copy"})
            self.handle({"type": "progress", "stage": "download", "progress": 0.64, "bytes": 33_280_000,
                         "total": 52_000_000, "speed": 2_600_000, "eta": 8})
            self.heading.configure(text="Installing Musi")
        elif state == "success":
            for step, _, _ in STEPS:
                self.handle({"type": "stage", "stage": step, "state": "done", "message": "Completed"})
            self.handle({"type": "success"})
        elif state == "error":
            self.handle({"type": "stage", "stage": "device", "state": "active"})
            self.handle({"type": "failure", "message": "No iPhone found"})
        elif state == "2fa":
            CodeDialog(self, lambda _: None)


if __name__ == "__main__":
    app = MusiInstaller()
    if len(sys.argv) == 3 and sys.argv[1] == "--preview":
        app.after(200, lambda: app.preview(sys.argv[2]))
    app.mainloop()
