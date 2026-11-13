"""Resolve duplicate aggregates immediately by choosing a complete card."""
from PySide6.QtCore import QEvent, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import (
    QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QMessageBox, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from .domain import POS_OPTIONS
from .busy_overlay import BusyOverlay
from .transfer_jobs import run_repository_task
from .theme import ACCENT, GOLD, draw_related_arrow, icon
from .widgets import button


def word_heading(word):
    return word.text + ('\n' + word.phonetic if word.phonetic else '')


def display_phonetic(text):
    return text.strip().strip('/').strip()


class ElidedLabel(QLabel):
    """Keep full text for accessibility and show a single line with literal dots."""
    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self._update_display()

    def _update_display(self):
        self.setFixedHeight(self.fontMetrics().height() + 4)
        text = self.text().replace('\r', ' ').replace('\n', ' ')
        metrics, width = self.fontMetrics(), self.contentsRect().width()
        self.display_text = text
        if metrics.horizontalAdvance(text) > width:
            low, high = 0, len(text)
            while low < high:
                middle = (low + high + 1) // 2
                if metrics.horizontalAdvance(text[:middle] + '...') <= width:
                    low = middle
                else:
                    high = middle - 1
            self.display_text = text[:low] + '...'
        self.setToolTip(self.text() if self.display_text != self.text() else '')

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_display()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (QEvent.Type.FontChange, QEvent.Type.StyleChange):
            self._update_display()

    def minimumSizeHint(self):
        return QSize(0, self.fontMetrics().height() + 4)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setPen(self.palette().color(self.foregroundRole()))
        painter.drawText(self.contentsRect(), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         self.display_text)


def entry_description(entry):
    """Plain text preserves explicit line breaks and the related-word indentation."""
    lines = []
    for word, indent in [(entry, "")] + [(child, "    ") for child in entry.related]:
        if indent or word.phonetic:
            lines.append(indent + word_heading(word))
        for sense in word.senses:
            translations = sense.translation.split("\n")
            lines.append(f"{indent}{sense.pos} {translations[0]}")
            lines.extend(f"{indent}{line}" for line in translations[1:])
    return "\n".join(lines)


class RelatedArrow(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(16, 22)
        self.setAccessibleName('关联词')

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        draw_related_arrow(painter, 3, 5)


class EntryDescription(QWidget):
    """Use actual columns so proportional fonts cannot shift the translations."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.lines = QGridLayout(self)
        self.lines.setContentsMargins(0, 0, 0, 0)
        self.lines.setHorizontalSpacing(8)
        self.lines.setVerticalSpacing(6)
        self.lines.setColumnStretch(0, 3)
        self.lines.setColumnStretch(2, 10)
        self.word_labels = []
        self._text = ""

    def text(self):
        return self._text

    def set_entry(self, entry):
        while self.lines.count():
            item = self.lines.takeAt(0)
            item.widget().hide()
            item.widget().deleteLater()
        metrics = self.fontMetrics()
        badge_width = max(metrics.horizontalAdvance(pos) for pos, _ in POS_OPTIONS) + 12
        badge_height = metrics.height() + 6
        self.word_labels = []
        row = 0
        for word, related in [(entry, False)] + [(child, True) for child in entry.related]:
            name_cell = QWidget()
            name_layout = QHBoxLayout(name_cell)
            name_layout.setContentsMargins(0, 0, 0, 0)
            name_layout.setSpacing(4)
            if related:
                name_layout.addWidget(RelatedArrow(), 0, Qt.AlignmentFlag.AlignTop)
            text_column = QVBoxLayout()
            text_column.setContentsMargins(0, 0, 0, 0)
            text_column.setSpacing(2)
            name = ElidedLabel(word.text)
            name.setObjectName('wordCell')
            text_column.addWidget(name)
            if word.phonetic:
                phonetic = ElidedLabel(display_phonetic(word.phonetic))
                phonetic.setObjectName('muted')
                text_column.addWidget(phonetic)
            name_layout.addLayout(text_column, 1)
            self.word_labels.append(name)
            self.lines.addWidget(name_cell, row, 0, max(1, len(word.senses)), 1, Qt.AlignmentFlag.AlignTop)
            for offset, sense in enumerate(word.senses):
                self._add_sense(row + offset, sense, badge_width, badge_height)
            row += max(1, len(word.senses))
        self._text = entry_description(entry)
        self.setAccessibleName(self._text)

    def _add_sense(self, row, sense, badge_width, badge_height):
        label = QLabel(sense.pos)
        label.setObjectName("posBadge")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setFixedSize(badge_width, badge_height)
        self.lines.addWidget(label, row, 1, Qt.AlignmentFlag.AlignTop)
        translation = ElidedLabel(sense.translation)
        self.lines.addWidget(translation, row, 2, Qt.AlignmentFlag.AlignTop)


class ConflictCard(QFrame):
    chosen = Signal()

    def __init__(self, entry, side, parent=None):
        super().__init__(parent)
        self.side = side
        self._pressed = False
        self.setObjectName("conflictCard")
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        cell = QVBoxLayout(self)
        cell.setContentsMargins(14, 12, 14, 12)
        cell.setSpacing(6)
        self.description = EntryDescription()
        cell.addWidget(self.description)
        self.status_badges = QWidget(self)
        self.status_badges.setFixedSize(36, 12)
        self.status_badges.setStyleSheet('background: #202630;')
        self.star = QLabel(self.status_badges)
        self.star.setGeometry(2, 0, 12, 12)
        self.learned = QLabel(self.status_badges)
        self.learned.setGeometry(22, 0, 12, 12)
        self.set_entry(entry)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.status_badges.move(self.width() - self.status_badges.width() - 14, 0)
        self.status_badges.raise_()

    def set_entry(self, entry):
        self.entry = entry
        self.star.setPixmap(icon("star", GOLD if entry.important else "#596371").pixmap(12, 12))
        self.star.setAccessibleName("重点" if entry.important else "非重点")
        self.star.setToolTip(self.star.accessibleName())
        state_icon = QPixmap(12, 12)
        state_icon.fill(Qt.GlobalColor.transparent)
        painter = QPainter(state_icon)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        green = QColor('#68c997')
        painter.setPen(green)
        painter.setBrush(green if entry.learned else Qt.BrushStyle.NoBrush)
        painter.drawEllipse(2, 2, 8, 8)
        painter.end()
        self.learned.setPixmap(state_icon)
        self.learned.setToolTip('已记住' if entry.learned else '待记')
        self.learned.setAccessibleName(self.learned.toolTip())
        self.description.set_entry(entry)
        self.word = self.description.word_labels[0]
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
        self.setWindowTitle("导入重复词对比选取")
        self.resize(960, 680)
        self.setMinimumSize(720, 240)
        self._initial_size_applied = False
        layout = QVBoxLayout(self)
        self.summary = QLabel()
        self.summary.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.all_left_button = button("全部选用")
        self.all_right_button = button("全部选用")
        self.all_left_button.setAccessibleName('全部选用本地单词')
        self.all_right_button.setAccessibleName('全部选用导入单词')
        self.all_left_button.clicked.connect(lambda: self.choose_all(False))
        self.all_right_button.clicked.connect(lambda: self.choose_all(True))
        headers = QHBoxLayout()
        self.column_titles = []
        for title, control in (("本地单词", self.all_left_button), ("导入单词", self.all_right_button)):
            group = QWidget()
            group_layout = QHBoxLayout(group)
            group_layout.setContentsMargins(0, 0, 0, 0)
            group_layout.setSpacing(self.fontMetrics().horizontalAdvance('   '))
            label = QLabel(title)
            label.setObjectName("section")
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.column_titles.append(label)
            group_layout.addWidget(label)
            group_layout.addWidget(control)
            headers.addWidget(group, 1, Qt.AlignmentFlag.AlignHCenter)
        layout.addLayout(headers)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.content = QWidget()
        self.card_layout = QVBoxLayout(self.content)
        self.card_layout.setContentsMargins(0, 0, 8, 0)
        self.card_layout.setSpacing(8)
        for conflict in self.conflicts:
            row = QWidget()
            row.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
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
        self._update_summary()
        self.busy_overlay = BusyOverlay(self)

    def showEvent(self, event):
        super().showEvent(event)
        if not self._initial_size_applied:
            self._initial_size_applied = True
            self.layout().activate()
            content_height = max(self.card_layout.totalHeightForWidth(self.scroll.viewport().width()),
                                 self.card_layout.minimumSize().height())
            chrome_height = self.height() - self.scroll.height() + 2 * self.scroll.frameWidth()
            self.resize(self.width(), min(680, self.screen().availableGeometry().height() - 80,
                                          max(self.minimumHeight(), content_height + chrome_height)))

    def _update_summary(self):
        self.summary.setText(
            f'导入新词：<span style="color:{ACCENT}">{self.inserted}</span> 个，'
            f'重复单词：<span style="color:{ACCENT}">{len(self.conflicts)}</span> 个，点击卡片立即采用')
        self.summary.setAccessibleName(
            f'导入新词：{self.inserted} 个，重复单词：{len(self.conflicts)} 个，点击卡片立即采用')

    def choose(self, conflict, incoming):
        if not any(item is conflict for item in self.conflicts):
            return
        self._resolve([conflict], incoming)

    def choose_all(self, incoming):
        self._resolve(list(self.conflicts), incoming)

    def _resolve(self, selected, incoming):
        if self.busy_overlay.active:
            return
        try:
            if incoming:
                adopted = run_repository_task(self.repository, self.busy_overlay,
                                              '正在导入所选单词…', 'replace_imports', selected)
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
