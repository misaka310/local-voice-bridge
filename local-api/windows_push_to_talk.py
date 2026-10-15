from __future__ import annotations

import ctypes
import ctypes.wintypes
import os
import threading
from typing import Any, Callable

VK_LCONTROL = 0xA2
VK_RCONTROL = 0xA3
VK_OEM_102 = 0xE2
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WH_KEYBOARD_LL = 13
WM_QUIT = 0x0012


class GlobalRightCtrlHook:
    def __init__(self, callback: Callable[[int, bool], bool]) -> None:
        self.callback = callback
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._hook: Any = None
        self._proc: Any = None
        self._started = threading.Event()
        self._stop = threading.Event()

    def start(self) -> None:
        if os.name != "nt" or (self._thread is not None and self._thread.is_alive()):
            return
        self._stop.clear()
        self._started.clear()
        self._thread = threading.Thread(target=self._run, name="local-voice-push-to-talk", daemon=True)
        self._thread.start()
        self._started.wait(timeout=3)

    def _run(self) -> None:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        self._thread_id = int(kernel32.GetCurrentThreadId())
        low_level_proc = ctypes.WINFUNCTYPE(
            ctypes.c_ssize_t, ctypes.c_int, ctypes.c_size_t, ctypes.c_void_p
        )
        kernel32.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
        kernel32.GetModuleHandleW.restype = ctypes.c_void_p
        user32.SetWindowsHookExW.argtypes = [ctypes.c_int, low_level_proc, ctypes.c_void_p, ctypes.c_uint32]
        user32.SetWindowsHookExW.restype = ctypes.c_void_p
        user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_size_t, ctypes.c_void_p]
        user32.CallNextHookEx.restype = ctypes.c_ssize_t
        user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
        user32.UnhookWindowsHookEx.restype = ctypes.c_int

        class KBDLLHOOKSTRUCT(ctypes.Structure):
            _fields_ = [
                ("vkCode", ctypes.c_uint32),
                ("scanCode", ctypes.c_uint32),
                ("flags", ctypes.c_uint32),
                ("time", ctypes.c_uint32),
                ("dwExtraInfo", ctypes.c_void_p),
            ]

        @low_level_proc
        def hook_proc(code: int, wparam: int, lparam: int) -> int:
            if code >= 0 and lparam:
                data = ctypes.cast(lparam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                key = int(data.vkCode)
                if key in {VK_RCONTROL, VK_OEM_102}:
                    try:
                        if int(wparam) in {WM_KEYDOWN, WM_SYSKEYDOWN}:
                            if self.callback(key, True):
                                return 1
                        elif int(wparam) in {WM_KEYUP, WM_SYSKEYUP}:
                            if self.callback(key, False):
                                return 1
                    except Exception:
                        pass
            return int(user32.CallNextHookEx(self._hook, code, wparam, lparam))

        self._proc = hook_proc
        self._hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, hook_proc, kernel32.GetModuleHandleW(None), 0)
        self._started.set()
        if not self._hook:
            return
        message = ctypes.wintypes.MSG()
        while not self._stop.is_set() and user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(message))
            user32.DispatchMessageW(ctypes.byref(message))
        if self._hook:
            user32.UnhookWindowsHookEx(self._hook)
            self._hook = None

    def stop(self) -> None:
        self._stop.set()
        if os.name == "nt" and self._thread_id:
            ctypes.windll.user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        thread = self._thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=3)
        self._thread = None
        self._thread_id = 0
