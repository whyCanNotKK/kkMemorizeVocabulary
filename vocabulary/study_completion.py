"""Centered empty-list notice with two refresh controls sharing one request."""
from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QToolButton, QVBoxLayout, QWidget


def _format_minutes(minutes):
    if minutes is None:
        return '—'
    hours, remaining = divmod(minutes, 60)
    return f'{hours} h {remaining} m' if hours else f'{remaining} m'


class StudyCompletionNotice(QWidget):
    refreshRequested = Signal()

    def __init__(self, refresh_icon, icon_size, parent):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.title = QLabel()
        self.title.setObjectName('muted')
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.title)
        line = QHBoxLayout()
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(0)
        line.addStretch()
        self.instruction = QLabel('在新时段点击')
        self.instruction.setObjectName('muted')
        line.addWidget(self.instruction)
        self.refresh_icon = QToolButton()
        self.refresh_icon.setIcon(refresh_icon)
        self.refresh_icon.setIconSize(icon_size)
        self.refresh_icon.setFixedSize(icon_size.width() + 8, icon_size.height() + 8)
        self.refresh_icon.setStyleSheet('QToolButton { background: transparent; border: none; padding: 0; }')
        self.refresh_link = QPushButton('刷新')
        self.refresh_link.setAutoDefault(False)
        font = self.refresh_link.font()
        font.setUnderline(True)
        self.refresh_link.setFont(font)
        self.refresh_link.setStyleSheet(
            'QPushButton { color: #58a6ff; '
            'background: transparent; border: none; padding: 0; border-radius: 0; }')
        for control in (self.refresh_icon, self.refresh_link):
            control.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            control.setCursor(Qt.CursorShape.PointingHandCursor)
            control.setToolTip('刷新')
            control.setAccessibleName('刷新十七天背单词任务')
            control.clicked.connect(self.refreshRequested)
            line.addWidget(control)
        self.suffix = QLabel('，开启新任务')
        self.suffix.setObjectName('muted')
        line.addWidget(self.suffix)
        line.addStretch()
        layout.addLayout(line)
        parent.installEventFilter(self)
        self.hide()

    def set_empty(self):
        self._set_content('当前暂无待背单词', '点击', '任务')

    def set_history(self, history):
        total, efficiency = history.timing
        total_text = _format_minutes(total)
        effective_text = _format_minutes(history.duration_seconds // 60)
        efficiency_text = f'{efficiency}%' if efficiency is not None else '—'
        title = f'当前时段任务完成，总时长 {total_text}，有效用时 {effective_text}，效率 {efficiency_text}'
        self._set_content(title, '在新时段点击', '，开启新任务')

    def _set_content(self, title, instruction, suffix):
        if (self.title.text(), self.instruction.text(), self.suffix.text()) != (title, instruction, suffix):
            self.title.setText(title)
            self.instruction.setText(instruction)
            self.suffix.setText(suffix)
            if self.isVisible():
                self.reposition()

    def reposition(self):
        self.adjustSize()
        viewport = self.parentWidget()
        self.move(max(0, (viewport.width() - self.width()) // 2),
                  max(0, (viewport.height() - self.height()) // 2))

    def showEvent(self, event):
        super().showEvent(event)
        self.reposition()
        self.raise_()

    def eventFilter(self, source, event):
        if source is self.parentWidget() and event.type() == QEvent.Type.Resize:
            self.reposition()
        return False
