"""Fixed word-page navigation; page changes are committed by the window after loading."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QWidget

from .widgets import button


class _PageInput(QLineEdit):
    cancelled = Signal()
    focusLeft = Signal()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.cancelled.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self.focusLeft.emit()


class PaginationBar(QWidget):
    pageRequested = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.page_number = None
        self.page_count = 0
        self.total = 0
        self.page_size = 500
        self.loading = False
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 4, 12, 4)
        layout.setSpacing(6)
        self.first_button = button("首页", self, quiet=True)
        self.previous_button = button("上一页", self, quiet=True)
        self.page_label = QLabel("第", self)
        self.page_input = _PageInput(self)
        self.page_input.setAccessibleName("跳转到分页")
        self.page_input.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.page_input.setFixedWidth(64)
        self.pages_label = QLabel(self)
        self.next_button = button("下一页", self, quiet=True)
        self.last_button = button("末页", self, quiet=True)
        self.summary_label = QLabel(self)
        self.summary_label.setObjectName("muted")
        self.loading_label = QLabel("正在加载…", self)
        self.loading_label.setObjectName("muted")
        for control in (self.first_button, self.previous_button, self.page_label,
                        self.page_input, self.pages_label, self.next_button, self.last_button):
            layout.addWidget(control)
        layout.addStretch(1)
        layout.addWidget(self.loading_label)
        layout.addWidget(self.summary_label)
        self.first_button.clicked.connect(lambda: self._request(1))
        self.previous_button.clicked.connect(lambda: self._request((self.page_number or 1) - 1))
        self.next_button.clicked.connect(lambda: self._request((self.page_number or 1) + 1))
        self.last_button.clicked.connect(lambda: self._request(self.page_count))
        self.page_input.textChanged.connect(self._filter_digits)
        self.page_input.returnPressed.connect(self._submit)
        self.page_input.editingFinished.connect(self._restore_input)
        self.page_input.cancelled.connect(self._restore_input)
        self.page_input.focusLeft.connect(self._restore_input)
        self.set_page(None, 0)

    def set_page(self, page_number, total, page_size=500):
        if page_size < 1 or total < 0:
            raise ValueError("分页数量无效。")
        self.total, self.page_size = total, page_size
        self.page_count = (total + page_size - 1) // page_size
        self.page_number = (max(1, min(page_number or 1, self.page_count))
                            if self.page_count else None)
        self.pages_label.setText(f"/ {self.page_count} 页")
        if self.page_number is None:
            self.summary_label.setText("共 0 个主词")
        else:
            first = (self.page_number - 1) * page_size + 1
            last = min(self.page_number * page_size, total)
            self.summary_label.setText(f"共 {total} 个主词 · 当前 {first}–{last}")
        self._restore_input()
        self._update_controls()

    def set_loading(self, loading):
        self.loading = bool(loading)
        self._update_controls()

    def _update_controls(self):
        available = bool(self.page_count) and not self.loading
        self.first_button.setEnabled(available and self.page_number > 1)
        self.previous_button.setEnabled(available and self.page_number > 1)
        self.next_button.setEnabled(available and self.page_number < self.page_count)
        self.last_button.setEnabled(available and self.page_number < self.page_count)
        self.page_input.setEnabled(available)
        for control in (self.page_label, self.page_input, self.pages_label):
            control.setVisible(bool(self.page_count))
        self.loading_label.setVisible(self.loading)
        if self.loading:
            self.summary_label.setText("正在更新数量…")
        elif self.page_number is None:
            self.summary_label.setText("共 0 个主词")
        else:
            first = (self.page_number - 1) * self.page_size + 1
            last = min(self.page_number * self.page_size, self.total)
            self.summary_label.setText(f"共 {self.total} 个主词 · 当前 {first}–{last}")

    def _filter_digits(self, value):
        filtered = "".join(character for character in value if "0" <= character <= "9")
        if filtered != value:
            position = self.page_input.cursorPosition()
            before = value[:position]
            cursor = sum("0" <= character <= "9" for character in before)
            self.page_input.setText(filtered)
            self.page_input.setCursorPosition(cursor)

    def _restore_input(self):
        self.page_input.setText("" if self.page_number is None else str(self.page_number))

    def _submit(self):
        value = self.page_input.text()
        if value and not self.loading and self.page_count:
            digits = value.lstrip("0") or "0"
            maximum = str(self.page_count)
            # Compare decimal strings before conversion: arbitrary long input
            # still clamps correctly, including pasted leading zeroes.
            too_large = len(digits) > len(maximum) or (len(digits) == len(maximum) and digits > maximum)
            target = self.page_count if too_large else max(1, int(digits))
            self._request(target)
        else:
            self._restore_input()

    def _request(self, target):
        if not self.loading and self.page_count:
            target = max(1, min(target, self.page_count))
            self.page_input.setText(str(target))
            if target != self.page_number:
                self.pageRequested.emit(target)
