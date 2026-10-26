from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QLineEdit, QVBoxLayout

from .domain import DEFAULT_SHORTCUTS, SHORTCUT_LABELS, ValidationError
from .widgets import button


def normalize_shortcut(value: str) -> str:
    sequence = QKeySequence.fromString(value, QKeySequence.SequenceFormat.PortableText)
    if sequence.isEmpty() or sequence.count() != 1:
        raise ValidationError("请使用一个包含 Ctrl 或 Alt 的单段快捷键。")
    combination = sequence[0]
    key, modifiers = combination.key(), combination.keyboardModifiers()
    if key == Qt.Key.Key_Enter:
        key = Qt.Key.Key_Return
        sequence = QKeySequence(modifiers | key)
    if not modifiers & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier):
        raise ValidationError("快捷键必须包含 Ctrl 或 Alt。")
    if key in (Qt.Key.Key_unknown, Qt.Key.Key_Control, Qt.Key.Key_Alt, Qt.Key.Key_Shift,
               Qt.Key.Key_Meta, Qt.Key.Key_Tab, Qt.Key.Key_Backtab, Qt.Key.Key_Escape, Qt.Key.Key_Space):
        raise ValidationError("这个按键用于固定导航，请选择其他组合。")
    normalized = sequence.toString(QKeySequence.SequenceFormat.PortableText)
    reserved = {"Ctrl+A", "Ctrl+C", "Ctrl+V", "Ctrl+X", "Ctrl+Z", "Ctrl+Y", "Alt+F4"}
    if normalized in reserved:
        raise ValidationError("这个快捷键已保留给文字编辑或系统操作。")
    return normalized


def display_shortcut(value):
    return QKeySequence(value).toString(QKeySequence.SequenceFormat.NativeText).replace("Return", "Enter")


class ShortcutManager(QObject):
    changed = Signal()

    def __init__(self, repository, window, callbacks):
        super().__init__(window)
        self.repository, self.window, self.callbacks = repository, window, callbacks
        self.bindings = []
        self.values = repository.shortcuts()
        self.suspended = False
        self.validate(self.values)
        self._apply(self.values)

    @staticmethod
    def validate(values):
        if set(values) != set(DEFAULT_SHORTCUTS):
            raise ValidationError("快捷键配置不完整。")
        normalized = {key: normalize_shortcut(value) for key, value in values.items()}
        if len(set(normalized.values())) != len(normalized):
            raise ValidationError("快捷键不能重复，请先为占用它的操作设置其他组合。")
        return normalized

    def _apply(self, values):
        new = []
        try:
            for key, value in values.items():
                shortcut = QShortcut(self.window)
                sequences = [QKeySequence(value)]
                if "Return" in value:
                    sequences.append(QKeySequence(value.replace("Return", "Enter")))
                shortcut.setKeys(sequences)
                shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
                shortcut.setAutoRepeat(False)
                shortcut.setEnabled(False)
                shortcut.activated.connect(self.callbacks[key])
                new.append(shortcut)
        except Exception:
            for shortcut in new:
                shortcut.deleteLater()
            raise
        for shortcut in self.bindings:
            shortcut.setEnabled(False)
            shortcut.deleteLater()
        self.bindings = new
        self.values = dict(values)
        for shortcut in self.bindings:
            shortcut.setEnabled(not self.suspended)

    def set_suspended(self, value):
        self.suspended = value
        for shortcut in self.bindings:
            shortcut.setEnabled(not value)

    def update(self, values):
        validated = self.validate(values)
        old = self.repository.shortcuts()
        self.repository.write_shortcuts(validated)  # Commit before binding or changing any labels.
        try:
            persisted = self.repository.shortcuts()
            self._apply(persisted)
        except Exception:
            self.repository.write_shortcuts(old)
            self._apply(self.repository.shortcuts())
            raise
        self.changed.emit()

    def labels(self):
        return {key: display_shortcut(value) for key, value in self.values.items()}


class ShortcutRecorder(QLineEdit):
    captured = Signal(str)
    cancelled = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setPlaceholderText("按下组合键，Esc 取消")
        self.setAccessibleName("录制快捷键")

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.cancelled.emit()
            return
        if event.key() in (Qt.Key.Key_Control, Qt.Key.Key_Alt, Qt.Key.Key_Shift, Qt.Key.Key_Meta):
            return
        value = QKeySequence(event.keyCombination()).toString(QKeySequence.SequenceFormat.PortableText)
        self.captured.emit(value)


class SettingsDialog(QDialog):
    def __init__(self, manager, parent=None):
        super().__init__(parent)
        self.manager = manager
        self.setWindowTitle("快捷键设置")
        self.setMinimumWidth(610)
        self._recording = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 24, 26, 24)
        layout.setSpacing(15)
        title = QLabel("快捷键设置")
        title.setObjectName("title")
        layout.addWidget(title)
        description = QLabel("修改后自动保存并立即生效")
        description.setObjectName("muted")
        layout.addWidget(description)
        self.editors = {}
        self.buttons = {}
        for key, label in SHORTCUT_LABELS.items():
            row = QHBoxLayout()
            name = QLabel(label)
            name.setMinimumWidth(200)
            editor = ShortcutRecorder()
            editor.setText(manager.labels()[key])
            editor.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            editor.captured.connect(lambda value, key=key: self.capture(key, value))
            editor.cancelled.connect(self.cancel_recording)
            modify = button("修改")
            modify.clicked.connect(lambda checked=False, key=key: self.record(key))
            self.editors[key], self.buttons[key] = editor, modify
            row.addWidget(name)
            row.addWidget(editor, 1)
            row.addWidget(modify)
            layout.addLayout(row)
        self.notice = QLabel("Ctrl+A 等文字编辑按键，以及 Tab 等导航按键保持不变。")
        self.notice.setObjectName("muted")
        self.notice.setWordWrap(True)
        layout.addWidget(self.notice)
        footer = QHBoxLayout()
        restore = button("恢复默认")
        restore.clicked.connect(self.restore)
        done = button("完成")
        done.setObjectName("primary")
        done.clicked.connect(self.accept)
        footer.addWidget(restore)
        footer.addStretch()
        footer.addWidget(done)
        layout.addLayout(footer)
        manager.changed.connect(self.refresh)

    def refresh(self):
        for key, text in self.manager.labels().items():
            self.editors[key].setText(text)

    def record(self, key):
        self.cancel_recording()
        self._recording = key
        self.editors[key].clear()
        self.editors[key].setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.editors[key].setFocus()
        self.notice.setText("请按下新组合键；Esc 取消本次修改。")

    def cancel_recording(self):
        if self._recording:
            key = self._recording
            self.editors[key].setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.buttons[key].setFocus()
            self._recording = None
        self.refresh()

    def capture(self, key, value):
        if self._recording != key:
            return
        values = dict(self.manager.values)
        values[key] = value
        try:
            self.manager.update(values)
        except Exception as error:
            self.notice.setText(str(error))
            return
        self.cancel_recording()
        self.notice.setText("已保存并立即生效。")

    def restore(self):
        try:
            self.manager.update(DEFAULT_SHORTCUTS)
        except Exception as error:
            self.notice.setText(str(error))
            return
        self.cancel_recording()
        self.notice.setText("已恢复默认，并立即生效。")
