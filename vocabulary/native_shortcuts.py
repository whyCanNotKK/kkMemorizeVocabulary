"""Receive configured chords before Windows IME processing, in our window only."""
import ctypes
from ctypes import wintypes
import sys

from PySide6.QtCore import QObject, Qt, QTimer
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QApplication


MODIFIER_KEYS = {0x10: 4, 0xA0: 4, 0xA1: 4, 0x11: 2, 0xA2: 2, 0xA3: 2,
                 0x12: 1, 0xA4: 1, 0xA5: 1, 0x5B: 8, 0x5C: 8}


def native_combination(value):
    combination = QKeySequence(value)[0]
    key, modifiers = combination.key(), combination.keyboardModifiers()
    native_modifiers = sum(flag for qt_flag, flag in (
        (Qt.KeyboardModifier.AltModifier, 1), (Qt.KeyboardModifier.ControlModifier, 2),
        (Qt.KeyboardModifier.ShiftModifier, 4), (Qt.KeyboardModifier.MetaModifier, 8))
        if modifiers & qt_flag)
    if Qt.Key.Key_A <= key <= Qt.Key.Key_Z or Qt.Key.Key_0 <= key <= Qt.Key.Key_9:
        return native_modifiers, int(key)
    if Qt.Key.Key_F1 <= key <= Qt.Key.Key_F24:
        return native_modifiers, 0x70 + key - Qt.Key.Key_F1
    native_key = {Qt.Key.Key_Return: 0x0D, Qt.Key.Key_Enter: 0x0D,
                  Qt.Key.Key_Backspace: 0x08, Qt.Key.Key_Delete: 0x2E,
                  Qt.Key.Key_Insert: 0x2D, Qt.Key.Key_Home: 0x24, Qt.Key.Key_End: 0x23,
                  Qt.Key.Key_PageUp: 0x21, Qt.Key.Key_PageDown: 0x22,
                  Qt.Key.Key_Left: 0x25, Qt.Key.Key_Up: 0x26,
                  Qt.Key.Key_Right: 0x27, Qt.Key.Key_Down: 0x28}.get(key)
    return (native_modifiers, native_key) if native_key is not None else None


class KeyboardMessage(ctypes.Structure):
    _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD),
                ("flags", wintypes.DWORD), ("time", wintypes.DWORD), ("extra", ctypes.c_size_t)]


class WindowsKeyboard:
    def __init__(self):
        self.api = ctypes.WinDLL("user32", use_last_error=True)
        result = ctypes.c_ssize_t
        self.callback_type = ctypes.WINFUNCTYPE(result, ctypes.c_int, ctypes.c_size_t, ctypes.c_ssize_t)
        self.api.SetWindowsHookExW.argtypes = [ctypes.c_int, self.callback_type, wintypes.HINSTANCE, wintypes.DWORD]
        self.api.SetWindowsHookExW.restype = wintypes.HANDLE
        self.api.UnhookWindowsHookEx.argtypes = [wintypes.HANDLE]
        self.api.CallNextHookEx.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_size_t, ctypes.c_ssize_t]
        self.api.CallNextHookEx.restype = result
        self.api.GetForegroundWindow.restype = wintypes.HWND
        self.api.GetAsyncKeyState.argtypes = [ctypes.c_int]
        self.api.GetAsyncKeyState.restype = ctypes.c_short
        self.callback = None

    def install(self, callback):
        self.callback = self.callback_type(callback)
        return self.api.SetWindowsHookExW(13, self.callback, None, 0)

    def uninstall(self, handle):
        self.api.UnhookWindowsHookEx(handle)

    def foreground(self):
        return self.api.GetForegroundWindow()

    def modifiers(self):
        return sum(flag for key, flag in ((0x12, 1), (0x11, 2), (0x10, 4), (0x5B, 8))
                   if self.api.GetAsyncKeyState(key) & 0x8000)

    def forward(self, code, message, pointer):
        return self.api.CallNextHookEx(None, code, message, pointer)


class WindowHotkeys(QObject):
    def __init__(self, manager, api=None):
        super().__init__(manager)
        self.manager = manager
        self.api = api
        if api is None and sys.platform == "win32" and QApplication.platformName() == "windows":
            self.api = WindowsKeyboard()
        self.registrations = {}
        self.handle = None
        self.modifiers = 0
        self.pressed = set()
        self.closed = False
        if self.api is not None:
            app = QApplication.instance()
            app.applicationStateChanged.connect(self.context_changed)
            app.focusChanged.connect(self.context_changed)
            app.aboutToQuit.connect(self.close)
            manager.window.destroyed.connect(self.close)
            self.refresh()

    def active(self):
        if self.closed or self.api is None or self.manager.suspended:
            return False
        app, window = QApplication.instance(), self.manager.window
        focus = app.focusWidget()
        return (app.applicationState() == Qt.ApplicationState.ApplicationActive
                and app.activeWindow() is window and not app.activeModalWidget()
                and not app.activePopupWidget() and focus is not None
                and (focus is window or window.isAncestorOf(focus))
                and self.api.foreground() == int(window.winId()))

    def context_changed(self, *args):
        self.refresh()
        QTimer.singleShot(0, self.refresh)

    def clear(self):
        if self.handle:
            self.api.uninstall(self.handle)
            self.handle = None
        self.registrations.clear()
        self.pressed.clear()
        self.modifiers = 0

    def refresh(self, *args):
        if self.closed or self.api is None:
            return
        if not self.active():
            self.clear()
            return
        self.registrations = {combination: action for action, value in self.manager.values.items()
                              if (combination := native_combination(value)) is not None}
        if not self.handle:
            self.modifiers = self.api.modifiers()
            self.handle = self.api.install(self._keyboard_event)

    def dispatch_key(self, key, down):
        if not self.active():
            return False
        if key in MODIFIER_KEYS:
            flag = MODIFIER_KEYS[key]
            self.modifiers = self.modifiers | flag if down else self.modifiers & ~flag
            return False
        if not down:
            if key in self.pressed:
                self.pressed.remove(key)
                return True
            return False
        binding = self.modifiers, key
        action = self.registrations.get(binding)
        if action is None:
            return False
        if key not in self.pressed:
            self.pressed.add(key)
            QTimer.singleShot(0, lambda: self._invoke(action, binding))
        return True

    def _invoke(self, action, binding):
        if self.active() and self.registrations.get(binding) == action:
            self.manager.callbacks[action]()

    def _keyboard_event(self, code, message, pointer):
        try:
            if code >= 0 and message in (0x100, 0x101, 0x104, 0x105):
                event = ctypes.cast(pointer, ctypes.POINTER(KeyboardMessage)).contents
                key = event.vkCode
                # Accessibility tools may send letters as Unicode packets.
                if key == 0xE7 and 0x61 <= event.scanCode <= 0x7A:
                    key = event.scanCode - 0x20
                if self.dispatch_key(key, message in (0x100, 0x104)):
                    return 1
        except Exception:
            # A native callback must always leave unrelated input operational.
            pass
        return self.api.forward(code, message, pointer)

    def close(self, *args):
        self.clear()
        self.closed = True
