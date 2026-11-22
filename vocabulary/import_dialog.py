"""Resolve duplicate aggregates immediately by choosing a complete card."""
from dataclasses import replace
from html import escape

from PySide6.QtCore import QEvent, QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication, QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QMessageBox, QScrollArea, QSizePolicy, QToolButton, QToolTip, QVBoxLayout, QWidget,
)

from .domain import POS_OPTIONS
from .import_merge import AUTO_MERGE_TOOLTIP, AutoMergeStats
from .busy_overlay import BusyOverlay
from .transfer_jobs import run_repository_task
from .theme import ACCENT, GOLD, draw_related_arrow, icon, mouse_action_prefix, tooltip_lines
from .widgets import button


def word_heading(word):
    return word.text + ('\n' + word.phonetic if word.phonetic else '')


def display_phonetic(text):
    return text.strip().strip('/').strip()


NO_ACTION_KEYS = (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter)


class ElidedLabel(QLabel):
    """Keep full text for accessibility and show a single line with literal dots."""
    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.action_hint = ''
        self.hint_only = False
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
        self._update_tooltip()

    def set_action_hint(self, hint, *, only=False):
        self.action_hint, self.hint_only = hint, only
        self._update_tooltip()

    def _update_tooltip(self):
        full = self.text() if self.display_text != self.text() and not self.hint_only else ''
        if self.action_hint:
            lines = ([escape(full).replace('\n', '<br>')] if full else []) + [self.action_hint]
            self.setToolTip(tooltip_lines(*lines))
        else:
            self.setToolTip(full)

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
        self.phonetic_labels = {}
        self.targets = {}
        self.group_cells = {}
        self.sense_labels = {}
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
        self.phonetic_labels = {}
        self.targets = {}
        self.group_cells = {}
        self.sense_labels = {}
        row = 0
        for owner, word in enumerate([entry, *entry.related], -1):
            related = owner >= 0
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
                self.phonetic_labels[owner] = phonetic
            name_layout.addLayout(text_column, 1)
            self.group_cells[owner] = [name_cell]
            for widget in [name_cell, *name_cell.findChildren(QWidget)]:
                self.targets[widget] = (owner, 'name', -1)
            if owner in self.phonetic_labels:
                self.targets[self.phonetic_labels[owner]] = (owner, 'phonetic', -1)
            self.word_labels.append(name)
            self.lines.addWidget(name_cell, row, 0, max(1, len(word.senses)), 1, Qt.AlignmentFlag.AlignTop)
            for offset, sense in enumerate(word.senses):
                self._add_sense(row + offset, sense, badge_width, badge_height, owner, offset)
            row += max(1, len(word.senses))
        self._text = entry_description(entry)
        self.setAccessibleName(self._text)

    def _add_sense(self, row, sense, badge_width, badge_height, owner, offset):
        label = QLabel(sense.pos)
        label.setObjectName("posBadge")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setFixedSize(badge_width, badge_height)
        self.lines.addWidget(label, row, 1, Qt.AlignmentFlag.AlignTop)
        translation = ElidedLabel(sense.translation)
        self.lines.addWidget(translation, row, 2, Qt.AlignmentFlag.AlignTop)
        self.sense_labels[owner, offset] = label, translation
        for widget in (label, translation):
            self.targets[widget] = (owner, 'translation', offset)
            self.group_cells[owner].append(widget)

    def target_at(self, point, widget=None):
        if widget in self.targets:
            return self.targets[widget]
        for owner, cells in self.group_cells.items():
            bounds = QRect()
            for cell in cells:
                bounds = bounds.united(QRect(cell.mapTo(self, QPoint()), cell.size()))
            if bounds.contains(point):
                return owner, 'group', -1
        return None


class ConflictCard(QFrame):
    chosen = Signal()
    flag_requested = Signal(str)
    phonetic_requested = Signal(int)
    translation_requested = Signal(int, int)
    related_requested = Signal(int)

    def __init__(self, entry, side, parent=None, *, local_entry=None):
        super().__init__(parent)
        self.side = side
        self.local_entry = local_entry or entry
        self._pressed = None
        self.setObjectName("conflictCard")
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        cell = QVBoxLayout(self)
        cell.setContentsMargins(14, 12, 14, 12)
        cell.setSpacing(6)
        self.description = EntryDescription()
        cell.addWidget(self.description, 0, Qt.AlignmentFlag.AlignTop)
        cell.addStretch()
        self.status_badges = QWidget(self)
        self.status_badges.setFixedSize(36, 12)
        self.status_badges.setStyleSheet('background: #202630;')
        self.star = QToolButton(self.status_badges)
        self.star.setGeometry(2, 0, 12, 12)
        self.learned = QToolButton(self.status_badges)
        self.learned.setGeometry(22, 0, 12, 12)
        for control in (self.star, self.learned):
            control.setIconSize(QSize(12, 12))
            control.setStyleSheet('QToolButton { border: none; padding: 0; background: transparent; }'
                                 'QToolButton:focus { background: #414d5b; }')
            control.setCursor(Qt.CursorShape.PointingHandCursor)
        self.set_entry(entry)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.status_badges.move(self.width() - self.status_badges.width() - 14, 0)
        self.status_badges.raise_()

    def set_entry(self, entry):
        self.entry = entry
        self._pressed = None
        self.update_status()
        self.description.set_entry(entry)
        self.word = self.description.word_labels[0]
        # Keep every part clickable after rebuilding a candidate's description.
        self.description.installEventFilter(self)
        for widget in self.findChildren(QWidget):
            widget.installEventFilter(self)
        self.update_context(self.local_entry)
        self.setAccessibleName(f"{'保留当前词条' if self.side == 'left' else '使用导入词条'} {entry.text}")

    def update_status(self):
        entry = self.entry
        self.star.setIcon(icon("star", GOLD if entry.important else "#596371"))
        self.star.setAccessibleName("重点" if entry.important else "非重点")
        self.star.setToolTip(tooltip_lines(mouse_action_prefix('取消重点' if entry.important else '设为重点', 'right')))
        state_icon = QPixmap(12, 12)
        state_icon.fill(Qt.GlobalColor.transparent)
        painter = QPainter(state_icon)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        green = QColor('#68c997')
        painter.setPen(green)
        painter.setBrush(green if entry.learned else Qt.BrushStyle.NoBrush)
        painter.drawEllipse(2, 2, 8, 8)
        painter.end()
        self.learned.setIcon(QIcon(state_icon))
        self.learned.setToolTip(tooltip_lines(mouse_action_prefix(
            '已记住，点击改为待记' if entry.learned else '待记，点击标记为已记下', 'right')))
        self.learned.setAccessibleName('已记住' if entry.learned else '待记')

    def update_context(self, local_entry):
        self.local_entry = local_entry
        names = {word.text.strip().casefold() for word in local_entry.related}
        self.new_related = {owner for owner, word in enumerate(self.entry.related)
                            if word.text.strip().casefold() not in names} if self.side == 'right' else set()
        for widget, (owner, kind, _) in self.description.targets.items():
            hint = ''
            if owner in self.new_related:
                hint = mouse_action_prefix('同步到本地关联词', 'right')
            elif self.side == 'right' and kind in ('phonetic', 'translation'):
                hint = mouse_action_prefix('同步到本地音标' if kind == 'phonetic' else '同步到本地译文', 'right')
            if isinstance(widget, ElidedLabel):
                widget.set_action_hint(hint, only=owner in self.new_related)
            else:
                widget.setToolTip(tooltip_lines(hint) if hint else '')

    def _right_action(self, watched, global_point):
        if watched is self.star:
            return 'flag', 'important', -1
        if watched is self.learned:
            return 'flag', 'learned', -1
        if self.side != 'right':
            return None
        target = self.description.target_at(self.description.mapFromGlobal(global_point), watched)
        if target is None:
            return None
        owner, kind, sense = target
        if owner in self.new_related:
            return 'related', owner, -1
        if kind in ('phonetic', 'translation'):
            return kind, owner, sense
        return None

    def _mouse_event(self, event, watched=None):
        watched = watched or self
        if event.button() not in (Qt.MouseButton.LeftButton, Qt.MouseButton.RightButton):
            return False
        point = event.globalPosition().toPoint()
        action = ('choose', -1, -1) if event.button() == Qt.MouseButton.LeftButton else self._right_action(watched, point)
        if event.type() == QEvent.Type.MouseButtonPress:
            self._pressed = event.button(), action
        elif event.type() == QEvent.Type.MouseButtonRelease:
            pressed, self._pressed = self._pressed, None
            inside = (self.rect().contains(self.mapFromGlobal(point)) if event.button() == Qt.MouseButton.LeftButton
                      else watched.rect().contains(watched.mapFromGlobal(point)))
            if action is not None and pressed == (event.button(), action) and inside:
                kind, owner, sense = action
                if kind == 'choose':
                    self.chosen.emit()
                elif kind == 'flag':
                    self.flag_requested.emit(owner)
                elif kind == 'phonetic':
                    self.phonetic_requested.emit(owner)
                elif kind == 'translation':
                    self.translation_requested.emit(owner, sense)
                elif kind == 'related':
                    self.related_requested.emit(owner)
        else:
            self._pressed = None
        event.accept()
        return True

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.ShortcutOverride, QEvent.Type.KeyPress, QEvent.Type.KeyRelease) and event.key() in NO_ACTION_KEYS:
            event.accept()
            return True
        if event.type() == QEvent.Type.ContextMenu:
            event.accept()
            return True
        if event.type() == QEvent.Type.ToolTip:
            action = self._right_action(watched, event.globalPos())
            if action and action[0] == 'related':
                QToolTip.showText(event.globalPos(), tooltip_lines(mouse_action_prefix('同步到本地关联词', 'right')),
                                  self, QRect(), 5000)
                return True
        if event.type() in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease, QEvent.Type.MouseButtonDblClick):
            return self._mouse_event(event, watched)
        return super().eventFilter(watched, event)

    def mousePressEvent(self, event):
        if not self._mouse_event(event):
            super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if not self._mouse_event(event):
            super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.key() in NO_ACTION_KEYS:
            event.accept()
            return
        super().keyPressEvent(event)


class ImportConflictDialog(QDialog):
    def __init__(self, repository, result, parent=None, *, source=None):
        super().__init__(parent)
        self.repository = repository
        self.source = source
        self.import_result = result
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
        self.auto_merge_button = button('自动合并')
        self.auto_merge_button.setToolTip(AUTO_MERGE_TOOLTIP)
        self.auto_merge_button.clicked.connect(self.auto_merge)
        self._auto_merge_tooltip = QLabel(self, Qt.WindowType.ToolTip)
        self._auto_merge_tooltip.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._auto_merge_tooltip.setObjectName('autoMergeToolTip')
        self._auto_merge_tooltip.setTextFormat(Qt.TextFormat.PlainText)
        self._auto_merge_tooltip.setText(AUTO_MERGE_TOOLTIP)
        self._auto_merge_tooltip.setWordWrap(True)
        self._auto_merge_tooltip.setMargin(7)
        self._auto_merge_tooltip.hide()
        headers = QGridLayout()
        headers.setColumnStretch(0, 1)
        headers.setColumnStretch(1, 1)
        self.column_titles = []
        for column, (title, control) in enumerate(
                (("本地单词", self.all_left_button), ("导入单词", self.all_right_button))):
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
            headers.addWidget(group, 0, column, Qt.AlignmentFlag.AlignHCenter)
        headers.addWidget(self.auto_merge_button, 0, 0, 1, 2, Qt.AlignmentFlag.AlignCenter)
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
            right = ConflictCard(conflict.incoming, "right", local_entry=conflict.current)
            pair.addWidget(left, 1)
            pair.addWidget(right, 1)
            left.chosen.connect(lambda item=conflict: self.choose(item, False))
            right.chosen.connect(lambda item=conflict: self.choose(item, True))
            left.flag_requested.connect(lambda flag, item=conflict: self.toggle_flag(item, False, flag))
            right.flag_requested.connect(lambda flag, item=conflict: self.toggle_flag(item, True, flag))
            right.phonetic_requested.connect(lambda owner, item=conflict: self.sync_phonetic(item, owner))
            right.translation_requested.connect(lambda owner, sense, item=conflict: self.sync_translation(item, owner, sense))
            right.related_requested.connect(lambda owner, item=conflict: self.sync_related(item, owner))
            self.cards.append((left, right))
            self.rows.append(row)
            self.card_layout.addWidget(row)
        self.card_layout.addStretch()
        self.scroll.setWidget(self.content)
        layout.addWidget(self.scroll, 1)
        self._update_summary()
        self.busy_overlay = BusyOverlay(self)

    def showEvent(self, event):
        QApplication.instance().installEventFilter(self)
        super().showEvent(event)
        if not self._initial_size_applied:
            self._initial_size_applied = True
            self.layout().activate()
            content_height = max(self.card_layout.totalHeightForWidth(self.scroll.viewport().width()),
                                 self.card_layout.minimumSize().height())
            chrome_height = self.height() - self.scroll.height() + 2 * self.scroll.frameWidth()
            self.resize(self.width(), min(680, self.screen().availableGeometry().height() - 80,
                                          max(self.minimumHeight(), content_height + chrome_height)))

    def hideEvent(self, event):
        self._auto_merge_tooltip.hide()
        QToolTip.hideText()
        QApplication.instance().removeEventFilter(self)
        super().hideEvent(event)

    def eventFilter(self, watched, event):
        if watched is self.auto_merge_button:
            if event.type() == QEvent.Type.ToolTip:
                if not self.busy_overlay.active:
                    self._show_auto_merge_tooltip(event.globalPos())
                event.accept()
                return True
            if event.type() in (QEvent.Type.Leave, QEvent.Type.Hide, QEvent.Type.MouseButtonPress):
                self._auto_merge_tooltip.hide()
        if watched is self and event.type() == QEvent.Type.WindowDeactivate:
            self._auto_merge_tooltip.hide()
        if (event.type() in (QEvent.Type.ShortcutOverride, QEvent.Type.KeyPress, QEvent.Type.KeyRelease)
                and event.key() in NO_ACTION_KEYS and isinstance(watched, QWidget) and watched.window() is self):
            event.accept()
            return True
        return super().eventFilter(watched, event)

    def _show_auto_merge_tooltip(self, position):
        QToolTip.hideText()
        screen = QApplication.screenAt(position) or self.screen()
        available = screen.availableGeometry().adjusted(8, 8, -8, -8)
        tip = self._auto_merge_tooltip
        tip.ensurePolished()
        natural_width = max(tip.fontMetrics().horizontalAdvance(line) for line in tip.text().splitlines())
        natural_width += 2 * (tip.margin() + tip.frameWidth()) + 2
        width = min(natural_width, available.width())
        tip.setFixedWidth(width)
        tip.setWordWrap(width < natural_width)
        tip.resize(width, tip.heightForWidth(width) if tip.hasHeightForWidth() else tip.sizeHint().height())
        tip.move(max(available.left(), min(position.x(), available.right() - tip.width() + 1)),
                 max(available.top(), min(position.y() + 16, available.bottom() - tip.height() + 1)))
        tip.show()
        tip.raise_()

    def _update_summary(self):
        instruction = (mouse_action_prefix('卡片立即采用', 'left') + '，'
                       + mouse_action_prefix('相应位置修改或同步单词信息', 'right'))
        self.summary.setText(
            f'导入新词：<span style="color:{ACCENT}">{self.inserted}</span> 个，'
            f'重复单词：<span style="color:{ACCENT}">{len(self.conflicts)}</span> 个，{instruction}')
        self.summary.setAccessibleName(
            f'导入新词：{self.inserted} 个，重复单词：{len(self.conflicts)} 个，'
            '鼠标左键 卡片立即采用，鼠标右键 相应位置修改或同步单词信息')

    def choose(self, conflict, incoming):
        if not any(item is conflict for item in self.conflicts):
            return
        self._resolve([conflict], incoming)

    def choose_all(self, incoming):
        self._resolve(list(self.conflicts), incoming)

    def auto_merge(self):
        if self.busy_overlay.active or not self.conflicts:
            return
        selected = list(self.conflicts)
        try:
            result = run_repository_task(self.repository, self.busy_overlay,
                                         '正在自动合并重复单词…', 'auto_merge_imports', selected)
        except Exception as error:
            QMessageBox.critical(self, '自动合并失败', f'本次自动合并已全部回滚，卡片已保留，可重试。\n{error}')
            return
        previous = self.import_result.auto_merge_stats or AutoMergeStats()
        self.import_result.auto_merge_stats = AutoMergeStats(
            previous.changed + result.stats.changed, previous.unchanged + result.stats.unchanged)
        self._remove_resolved([selected[index] for index in result.processed_indices],
                              result.entries, focus=self.auto_merge_button, finish=False)
        QMessageBox.information(self, '自动合并完成',
                                f'合并并修改 {result.stats.changed} 条\n'
                                f'无需修改 {result.stats.unchanged} 条\n'
                                f'剩余待人工 {len(self.conflicts)} 条')
        if not self.conflicts:
            self.accept()

    def sync_phonetic(self, conflict, owner):
        word = conflict.incoming if owner == -1 else conflict.incoming.related[owner]
        self._sync_local(conflict, '正在同步音标…', 'set_phonetic',
                         word.phonetic, None if owner == -1 else word.text)

    def sync_translation(self, conflict, owner, sense):
        word = conflict.incoming if owner == -1 else conflict.incoming.related[owner]
        self._sync_local(conflict, '正在同步译文…', 'sync_import_translation',
                         word.senses[sense], None if owner == -1 else word.text)

    def sync_related(self, conflict, owner):
        self._sync_local(conflict, '正在同步关联词…', 'append_import_related', conflict.incoming.related[owner])

    def _sync_local(self, conflict, message, method, *args):
        if self.busy_overlay.active or not any(item is conflict for item in self.conflicts):
            return
        try:
            current = run_repository_task(
                self.repository, self.busy_overlay, message, method, conflict.current.id, *args)
        except Exception as error:
            QMessageBox.critical(self, '同步失败', f'本地信息未修改，卡片已保留，可重试。\n{error}')
            return
        QToolTip.hideText()
        position = self.scroll.verticalScrollBar().value()
        for item, (left, right) in zip(self.conflicts, self.cards):
            if item.current.id == current.id:
                item.current = current
                left.set_entry(current)
                right.update_context(current)
        self.card_layout.activate()
        self._resize_content()
        self.scroll.verticalScrollBar().setValue(position)

    def _resize_content(self):
        # adjustSize() probes natural widths and reflows every card in a large import.
        self.content.resize(self.scroll.viewport().width(),
                            max(self.scroll.viewport().height(), self.card_layout.minimumSize().height()))

    def toggle_flag(self, conflict, incoming, flag):
        if self.busy_overlay.active or not any(item is conflict for item in self.conflicts):
            return
        entry = conflict.incoming if incoming else conflict.current
        value = not getattr(entry, flag)
        try:
            if incoming:
                if self.source is not None:
                    self.busy_overlay.run('正在写回导入文件…',
                                          lambda: self.source.set_flag(conflict.source_index, flag, value))
                conflict.incoming = replace(entry, **{flag: value})
            else:
                run_repository_task(self.repository, self.busy_overlay, '正在保存单词状态…',
                                    'set_flag', entry.id, flag, value)
                current = self.repository.get(entry.id)
        except Exception as error:
            QMessageBox.critical(self, '状态保存失败', f'本次修改未保存，原状态已保留，可重试。\n{error}')
            return
        QToolTip.hideText()
        for item, (left, right) in zip(self.conflicts, self.cards):
            if incoming and item is conflict:
                right.entry = item.incoming
                right.update_status()
            elif not incoming and item.current.id == entry.id:
                item.current = current
                left.entry = current
                left.update_status()

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
            self.replaced_ids.update(entry.id for entry in adopted.values())
        self._remove_resolved(selected, adopted if incoming else {})

    def _remove_resolved(self, selected, updated, *, focus=None, finish=True):
        QToolTip.hideText()
        position = self.scroll.verticalScrollBar().value()
        selected_objects = {id(item) for item in selected}
        # Qt batches painting until this GUI event returns. Disabling the whole scroll
        # area instead walks and invalidates every child in a large comparison.
        # Move focus first so removing a card cannot scroll to the next focusable one.
        (focus or self.all_left_button).setFocus(Qt.FocusReason.OtherFocusReason)
        for index in range(len(self.conflicts) - 1, -1, -1):
            if id(self.conflicts[index]) in selected_objects:
                row = self.rows.pop(index)
                self.card_layout.removeWidget(row)
                row.hide()
                row.deleteLater()
                self.cards.pop(index)
                self.conflicts.pop(index)
        for item, (left, right) in zip(self.conflicts, self.cards):
            normalized = item.incoming.text.strip().casefold()
            if normalized in updated and item.current != updated[normalized]:
                item.current = updated[normalized]
                left.set_entry(item.current)
                right.update_context(item.current)
        self._update_summary()
        self.card_layout.activate()
        self._resize_content()
        self.scroll.verticalScrollBar().setValue(position)
        if finish and not self.conflicts:
            self.accept()
