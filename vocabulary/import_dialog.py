"""Resolve duplicate aggregates immediately by choosing a complete card."""
from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QLabel, QMessageBox, QScrollArea, QVBoxLayout, QWidget,
)

from .domain import POS_OPTIONS
from .theme import ACCENT, GOLD, icon
from .widgets import button


def entry_description(entry):
    """Plain text preserves explicit line breaks and the related-word indentation."""
    lines = []
    for word, indent in [(entry, "")] + [(child, "    ") for child in entry.related]:
        if indent:
            lines.append(indent + word.text)
        for sense in word.senses:
            translations = sense.translation.split("\n")
            lines.append(f"{indent}{sense.pos} {translations[0]}")
            lines.extend(f"{indent}{line}" for line in translations[1:])
    return "\n".join(lines)


class EntryDescription(QWidget):
    """Use actual columns so proportional fonts cannot shift the translations."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.lines = QVBoxLayout(self)
        self.lines.setContentsMargins(0, 0, 0, 0)
        self.lines.setSpacing(3)
        self._text = ""

    def text(self):
        return self._text

    def set_entry(self, entry):
        while self.lines.count():
            item = self.lines.takeAt(0)
            item.widget().hide()
            item.widget().deleteLater()
        metrics = self.fontMetrics()
        indent_width = metrics.horizontalAdvance("    ")
        badge_width = max(metrics.horizontalAdvance(pos) for pos, _ in POS_OPTIONS) + 12
        badge_height = metrics.height() + 6
        for word, related in [(entry, False)] + [(child, True) for child in entry.related]:
            if related:
                self._add_line(indent_width, None, word.text, badge_width, badge_height)
            for sense in word.senses:
                self._add_line(indent_width if related else 0, sense.pos, sense.translation,
                               badge_width, badge_height)
        self._text = entry_description(entry)
        self.setAccessibleName(self._text)

    def _add_line(self, indent, pos, text, badge_width, badge_height):
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(indent, 0, 0, 0)
        layout.setSpacing(6)
        if pos is not None:
            label = QLabel(pos)
            label.setObjectName("posBadge")
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setFixedSize(badge_width, badge_height)
            layout.addWidget(label, 0, Qt.AlignmentFlag.AlignTop)
        translation = QLabel(text)
        translation.setTextFormat(Qt.TextFormat.PlainText)
        translation.setWordWrap(True)
        layout.addWidget(translation, 1, Qt.AlignmentFlag.AlignTop)
        self.lines.addWidget(row)


class ConflictCard(QFrame):
    chosen = Signal()

    def __init__(self, entry, side, parent=None):
        super().__init__(parent)
        self.side = side
        self._pressed = False
        self.setObjectName("conflictCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        cell = QVBoxLayout(self)
        cell.setContentsMargins(14, 12, 14, 12)
        cell.setSpacing(6)
        header = QHBoxLayout()
        header.setSpacing(7)
        self.star = QLabel()
        self.star.setFixedSize(20, 20)
        header.addWidget(self.star)
        self.word = QLabel()
        self.word.setObjectName("section")
        self.word.setTextFormat(Qt.TextFormat.PlainText)
        self.word.setWordWrap(True)
        header.addWidget(self.word, 1)
        self.learned = QLabel()
        self.learned.setFixedSize(22, 22)
        self.learned.setPixmap(icon("circle", ACCENT).pixmap(22, 22))
        self.learned.setToolTip("已记住此单词")
        self.learned.setAccessibleName("已记住此单词")
        header.addWidget(self.learned, 0, Qt.AlignmentFlag.AlignTop)
        cell.addLayout(header)
        self.description = EntryDescription()
        cell.addWidget(self.description)
        cell.addStretch()
        self.set_entry(entry)

    def set_entry(self, entry):
        self.entry = entry
        self.star.setPixmap(icon("star", GOLD if entry.important else "#596371").pixmap(20, 20))
        self.star.setAccessibleName("重点" if entry.important else "非重点")
        self.word.setText(entry.text)
        self.learned.setVisible(entry.learned)
        self.description.set_entry(entry)
        # Keep every part clickable after rebuilding a candidate's description.
        self.description.installEventFilter(self)
        for widget in self.findChildren(QWidget):
            widget.installEventFilter(self)
        self.setAccessibleName(f"{'保留当前词条' if self.side == 'left' else '使用导入词条'} {entry.text}")

    def _mouse_event(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return False
        if event.type() == QEvent.Type.MouseButtonPress:
            self._pressed = True
        else:
            pressed, self._pressed = self._pressed, False
            if pressed and self.rect().contains(self.mapFromGlobal(event.globalPosition().toPoint())):
                self.chosen.emit()
        event.accept()
        return True

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease):
            return self._mouse_event(event)
        return super().eventFilter(watched, event)

    def mousePressEvent(self, event):
        if not self._mouse_event(event):
            super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if not self._mouse_event(event):
            super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space) and not event.isAutoRepeat():
            self.chosen.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class ImportConflictDialog(QDialog):
    resolved = Signal()

    def __init__(self, repository, result, parent=None):
        super().__init__(parent)
        self.repository = repository
        self.conflicts = list(result.conflicts)
        self.replaced_ids = set()
        self.replaced_names = set()
        self.cards = []
        self.rows = []
        self.inserted = result.inserted
        self.setWindowTitle("重复主词对比")
        self.resize(960, 680)
        self.setMinimumSize(720, 460)
        layout = QVBoxLayout(self)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        actions = QHBoxLayout()
        self.all_left_button = button("全部使用左侧")
        self.all_right_button = button("全部使用右侧")
        self.all_left_button.clicked.connect(lambda: self.choose_all(False))
        self.all_right_button.clicked.connect(lambda: self.choose_all(True))
        actions.addWidget(self.all_left_button)
        actions.addWidget(self.all_right_button)
        actions.addStretch()
        layout.addLayout(actions)
        headers = QHBoxLayout()
        for title in ("当前程序中的单词", "导入文件中的单词"):
            label = QLabel(title)
            label.setObjectName("section")
            headers.addWidget(label, 1)
        layout.addLayout(headers)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.content = QWidget()
        self.card_layout = QVBoxLayout(self.content)
        self.card_layout.setContentsMargins(0, 0, 8, 0)
        self.card_layout.setSpacing(8)
        for conflict in self.conflicts:
            row = QWidget()
            pair = QHBoxLayout(row)
            pair.setContentsMargins(0, 0, 0, 0)
            left = ConflictCard(conflict.current, "left")
            right = ConflictCard(conflict.incoming, "right")
            pair.addWidget(left, 1)
            pair.addWidget(right, 1)
            left.chosen.connect(lambda item=conflict: self.choose(item, False))
            right.chosen.connect(lambda item=conflict: self.choose(item, True))
            self.cards.append((left, right))
            self.rows.append(row)
            self.card_layout.addWidget(row)
        self.card_layout.addStretch()
        self.scroll.setWidget(self.content)
        layout.addWidget(self.scroll, 1)
        footer = QHBoxLayout()
        self.cancel_button = button("关闭")
        self.cancel_button.clicked.connect(self.reject)
        footer.addStretch()
        footer.addWidget(self.cancel_button)
        layout.addLayout(footer)
        self._update_summary()

    def _update_summary(self):
        self.summary.setText(f"已导入 {self.inserted} 个新主词，剩余 {len(self.conflicts)} 条重复记录。点击卡片立即采用。")

    def choose(self, conflict, incoming):
        if not any(item is conflict for item in self.conflicts):
            return
        self._resolve([conflict], incoming)

    def choose_all(self, incoming):
        self._resolve(list(self.conflicts), incoming)

    def _resolve(self, selected, incoming):
        try:
            if incoming:
                adopted = self.repository.replace_imports(selected)
        except Exception as error:
            QMessageBox.critical(self, "覆盖失败", f"本次覆盖已回滚，卡片已保留，可重试。\n{error}")
            return
        if incoming:
            self.replaced_names.update(adopted)
            self.replaced_ids = {entry.id for entry in self.repository.list_entries()
                                 if entry.text.casefold() in self.replaced_names}
        position = self.scroll.verticalScrollBar().value()
        selected_objects = {id(item) for item in selected}
        self.scroll.setUpdatesEnabled(False)
        try:
            # Avoid QScrollArea moving to the next focusable card when one disappears.
            self.all_left_button.setFocus(Qt.FocusReason.OtherFocusReason)
            for index in range(len(self.conflicts) - 1, -1, -1):
                if id(self.conflicts[index]) in selected_objects:
                    row = self.rows.pop(index)
                    self.card_layout.removeWidget(row)
                    row.hide()
                    row.deleteLater()
                    self.cards.pop(index)
                    self.conflicts.pop(index)
            if incoming:
                for item, (left, _) in zip(self.conflicts, self.cards):
                    normalized = item.incoming.text.strip().casefold()
                    if normalized in adopted:
                        item.current = adopted[normalized]
                        left.set_entry(item.current)
            self._update_summary()
            self.card_layout.activate()
            self.content.adjustSize()
            self.scroll.verticalScrollBar().setValue(position)
        finally:
            self.scroll.setUpdatesEnabled(True)
        self.resolved.emit()
        if not self.conflicts:
            self.accept()
