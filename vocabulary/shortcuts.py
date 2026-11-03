from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QAbstractButton, QApplication, QDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QSpinBox, QVBoxLayout, QWidget

from .domain import DEFAULT_SHORTCUTS, SHORTCUT_LABELS, ValidationError
from .learning import StudyDrawSettings
from .native_shortcuts import WindowHotkeys
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
    """Bind named callbacks to configurable chords and fixed chord aliases."""

    changed = Signal()

    def __init__(self, repository, window, callbacks, fixed=None):
        super().__init__(window)
        self.repository, self.window, self.callbacks = repository, window, callbacks
        self.fixed_bindings = {sequence: action for action, sequences in (fixed or {}).items()
                               for sequence in sequences}
        self.bindings = []
        self.values = repository.shortcuts()
        self.suspended = False
        self.validate(self.values)
        self._apply(self.values)
        self.native = WindowHotkeys(self)
        QApplication.instance().installEventFilter(self)

    def eventFilter(self, target, event):
        if target is self.window and event.type() in (QEvent.Type.WindowActivate, QEvent.Type.WindowDeactivate):
            self.native.context_changed()
        if event.type() in (QEvent.Type.ShortcutOverride, QEvent.Type.KeyPress):
            app = QApplication.instance()
            if (not self.suspended and app.activeWindow() is self.window
                    and not app.activeModalWidget() and not app.activePopupWidget()
                    and isinstance(target, QWidget)
                    and (target is self.window or self.window.isAncestorOf(target))):
                sequence = QKeySequence(event.keyCombination()).toString(
                    QKeySequence.SequenceFormat.PortableText).replace("Enter", "Return")
                action = self.active_bindings.get(sequence)
                if action is not None:
                    event.accept()
                    if event.type() == QEvent.Type.KeyPress and not event.isAutoRepeat():
                        self.callbacks[action]()
                    return True
        return False

    @staticmethod
    def validate(values):
        if set(values) != set(DEFAULT_SHORTCUTS):
            raise ValidationError("快捷键配置不完整。")
        normalized = {key: normalize_shortcut(value) for key, value in values.items()}
        if len(set(normalized.values())) != len(normalized):
            raise ValidationError("快捷键不能重复，请先为占用它的操作设置其他组合。")
        return normalized

    @property
    def active_bindings(self):
        """Portable chord → action mapping shared by Qt and native dispatch."""
        return self._combined_bindings(self.values)

    def _combined_bindings(self, values):
        # Fixed navigation wins over any conflicting configuration from an older version.
        return {value: action for action, value in values.items()} | self.fixed_bindings

    def _apply(self, values):
        new = []
        try:
            for value, action in self._combined_bindings(values).items():
                shortcut = QShortcut(self.window)
                sequences = [QKeySequence(value)]
                if "Return" in value:
                    sequences.append(QKeySequence(value.replace("Return", "Enter")))
                shortcut.setKeys(sequences)
                shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
                shortcut.setAutoRepeat(False)
                shortcut.setEnabled(False)
                shortcut.activated.connect(self.callbacks[action])
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
        if hasattr(self, "native"):
            self.native.refresh()

    def set_suspended(self, value):
        self.suspended = value
        for shortcut in self.bindings:
            shortcut.setEnabled(not value)
        self.native.refresh()

    def update(self, values):
        validated = self.validate(values)
        if set(validated.values()) & self.fixed_bindings.keys():
            raise ValidationError("这个快捷键已保留给固定导航操作。")
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
        self.setWindowTitle("设置")
        self.setMinimumWidth(610)
        self._recording = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 24, 26, 24)
        layout.setSpacing(15)
        study_title = QLabel("十七天背单词设置")
        study_title.setObjectName("title")
        layout.addWidget(study_title)
        settings = manager.repository.study_draw_settings()
        self.words_per_group = QSpinBox()
        self.groups_per_day = QSpinBox()
        row = QHBoxLayout()
        row.setSpacing(8)
        for label, editor, value, prefix in (
            ("每日新增组数", self.groups_per_day, settings.groups_per_day, "每日新背"),
            ("每组单词数", self.words_per_group, settings.words_per_group, "组，每组"),
        ):
            name = QLabel(prefix)
            editor.setRange(1, 1000)
            editor.setValue(value)
            editor.setFixedWidth(30)
            editor.setAlignment(Qt.AlignmentFlag.AlignCenter)
            editor.setStyleSheet(
                "QSpinBox { padding: 7px 0px; font-size: 11px; }"
                "QSpinBox::up-button, QSpinBox::down-button { width: 0px; height: 0px; }")
            editor.setAccessibleName(label)
            name.setBuddy(editor)
            row.addWidget(name)
            row.addWidget(editor)
        row.addWidget(QLabel("个新单词"))
        row.addStretch()
        layout.addLayout(row)
        title = QLabel("快捷键设置")
        title.setObjectName("title")
        layout.addWidget(title)
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
        footer = QHBoxLayout()
        restore = button("恢复默认快捷键")
        restore.clicked.connect(self.restore)
        done = button("完成")
        done.setObjectName("primary")
        done.setDefault(True)
        done.clicked.connect(self.accept)
        footer.addWidget(restore)
        footer.addStretch()
        footer.addWidget(done)
        layout.addLayout(footer)
        manager.changed.connect(self.refresh)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFocus()
        self.installEventFilter(self)
        for widget in self.findChildren(QWidget):
            widget.installEventFilter(self)

    def showEvent(self, event):
        super().showEvent(event)
        self.setFocus()

    def eventFilter(self, target, event):
        if event.type() == QEvent.Type.MouseButtonPress:
            if target is self:
                target = self.childAt(event.position().toPoint()) or self
            recording = self.editors.get(self._recording)
            if recording is not None and target is not recording:
                self.cancel_recording(restore_focus=False)
            widget = target
            while isinstance(widget, QWidget) and widget is not self:
                if (isinstance(widget, (QSpinBox, QLineEdit, QAbstractButton))
                        and widget.focusPolicy() != Qt.FocusPolicy.NoFocus):
                    widget.setFocus(Qt.FocusReason.MouseFocusReason)
                    break
                widget = widget.parentWidget()
            else:
                self.setFocus(Qt.FocusReason.MouseFocusReason)
        return super().eventFilter(target, event)

    def accept(self):
        self.words_per_group.interpretText()
        self.groups_per_day.interpretText()
        try:
            self.manager.repository.write_study_draw_settings(StudyDrawSettings(
                self.words_per_group.value(), self.groups_per_day.value()))
        except Exception as error:
            QMessageBox.critical(self, "保存失败", f"学习设置保存失败：{error}")
            return
        super().accept()

    def refresh(self):
        for key, text in self.manager.labels().items():
            self.editors[key].setText(text)

    def record(self, key):
        self.cancel_recording()
        self._recording = key
        self.editors[key].clear()
        self.editors[key].setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.editors[key].setFocus()

    def cancel_recording(self, *, restore_focus=True):
        if self._recording:
            key = self._recording
            self.editors[key].setFocusPolicy(Qt.FocusPolicy.NoFocus)
            if restore_focus:
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
            QMessageBox.critical(self, "快捷键修改失败", str(error))
            return
        self.cancel_recording()

    def restore(self):
        try:
            self.manager.update(DEFAULT_SHORTCUTS)
        except Exception as error:
            QMessageBox.critical(self, "恢复默认失败", str(error))
            return
        self.cancel_recording()
