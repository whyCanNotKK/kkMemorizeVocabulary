from copy import deepcopy
import logging
from pathlib import Path

import shiboken6
from PySide6.QtCore import QPoint, QSize, Qt, QTimer
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox,
    QScrollArea, QVBoxLayout, QWidget,
)

from .domain import DuplicateWord, WordEntry
from .import_dialog import ImportConflictDialog
from .shortcuts import SettingsDialog, ShortcutManager
from .table import WordTable, WordTableModel
from .study_page import StudyPage
from .learning import StudyContext
from .study_transfer import read_study_backup, write_study_backup
from .theme import ACCENT, GOLD, MUTED, icon
from .transfer import read_entries, write_entries
from .widgets import INPUT_SPACING, POS_WIDTH, WORD_WIDTH, FloatingNotice, WordBlock, button, confirm_remove


class MainWindow(QMainWindow):
    def __init__(self, repository):
        super().__init__()
        self.repository = repository
        self.revealed = set()
        self.global_hidden = True
        self.filter_name = "pending"
        self._view_filter = "pending"
        self._filter_scroll = {"pending": 0, "all": 0, "learned": 0}
        self.edit_id = None
        self.active_block = None
        self._last_form_focus = None
        self.blocks = []
        self.duplicate_id = None
        self._closing = False
        self.form_collapsed = False
        self._collapsed_focus = None
        self.setWindowTitle("kkMemorizeVocabulary")
        self.setWindowIcon(icon("book", ACCENT))
        self.resize(1040, 820)
        self.setMinimumSize(860, 640)
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(9)
        list_panel = QFrame()
        list_panel.setObjectName("panel")
        list_panel.setMinimumHeight(180)
        list_layout = QVBoxLayout(list_panel)
        list_layout.setContentsMargins(12, 12, 12, 10)
        list_layout.setSpacing(10)
        filters = QHBoxLayout()
        filters.setSpacing(6)
        self.filter_buttons = {}
        for key, label in (("pending", "待记"), ("all", "全部"), ("learned", "已记下"),
                           ("study17", "十七天背单词")):
            control = button(label)
            control.clicked.connect(lambda checked=False, key=key: self.set_filter(key))
            self.filter_buttons[key] = control
            filters.addWidget(control)
        self.settings_button = button("快捷键设置", quiet=True)
        self.settings_button.setIcon(icon("settings"))
        self.settings_button.clicked.connect(self.open_settings)
        filters.addWidget(self.settings_button)
        filters.addStretch()
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("搜索单词或翻译")
        self.search_box.setAccessibleName("搜索单词、翻译、关联词及其翻译")
        self.search_box.setMinimumWidth(150)
        self.search_box.setMaximumWidth(260)
        self.search_box.setClearButtonEnabled(True)
        self.search_box.addAction(icon("search"), QLineEdit.ActionPosition.LeadingPosition)
        self.search_box.textChanged.connect(self.search_changed)
        filters.addWidget(self.search_box, 1)
        self.global_hide_button = button("")
        self.global_hide_button.setIcon(icon("eye"))
        self.global_hide_button.setFixedSize(38, 36)
        self.global_hide_button.setAccessibleName("全局隐藏：开")
        self.global_hide_button.setCheckable(True)
        self.global_hide_button.setChecked(True)
        self.global_hide_button.setToolTip("显示所有词条的词性、翻译和关联词")
        self.global_hide_button.clicked.connect(self.set_global_hidden)
        filters.addWidget(self.global_hide_button)
        self.import_button = button("")
        self.export_button = button("")
        for control, name, label, callback in (
                (self.import_button, "import", "导入单词", self.import_words),
                (self.export_button, "export", "导出当前页面所有单词", self.export_words)):
            control.setIcon(icon(name))
            control.setFixedSize(38, 36)
            control.setToolTip(label)
            control.setAccessibleName(label)
            control.clicked.connect(callback)
            filters.addWidget(control)
        list_layout.addLayout(filters)

        self.model = WordTableModel(self.commit_cell, self)
        self.table = WordTable(self.model, self.before_inline)
        self.table.wordClicked.connect(self.copy_word)
        self.table.revealRequested.connect(self.reveal)
        self.table.actionRequested.connect(self.list_action)
        self.table.moveRequested.connect(self.move_word)
        self.table.shuffleRequested.connect(self.shuffle_words)
        self.table.delegate.editingChanged.connect(self.inline_changed)
        list_layout.addWidget(self.table, 1)
        self.study_page = StudyPage(repository)
        self.study_page.table.wordClicked.connect(self.copy_word)
        self.study_page.error.connect(lambda text: self.show_notice(text, True))
        self.study_page.hide()
        list_layout.addWidget(self.study_page, 1)
        layout.addWidget(list_panel, 1)

        editor_panel = QFrame()
        self.editor_panel = editor_panel
        editor_panel.setObjectName("panel")
        panel_layout = QVBoxLayout(editor_panel)
        panel_layout.setContentsMargins(16, 10, 16, 12)
        panel_layout.setSpacing(8)
        self.editor_header = QWidget()
        self.editor_header.setFixedHeight(32)
        header = QGridLayout(self.editor_header)
        header.setContentsMargins(0, 0, 0, 0)
        header.setHorizontalSpacing(0)
        self.form_title = QLabel("添加单词")
        self.form_title.setObjectName("section")
        header.addWidget(self.form_title, 0, 0, Qt.AlignmentFlag.AlignLeft)
        self.collapse_button = button("", quiet=True)
        self.collapse_button.setFixedSize(72, 32)
        self.collapse_button.setIconSize(QSize(40, 26))
        self.collapse_button.setIcon(icon("chevron_down"))
        self.collapse_button.setCheckable(True)
        self.collapse_button.setToolTip("收起添加单词区域")
        self.collapse_button.setAccessibleName("收起添加单词区域")
        self.collapse_button.clicked.connect(self.set_form_collapsed)
        header.addWidget(self.collapse_button, 0, 1, Qt.AlignmentFlag.AlignCenter)
        header.setColumnStretch(0, 1)
        header.setColumnStretch(2, 1)
        header.setColumnMinimumWidth(2, self.form_title.sizeHint().width())
        panel_layout.addWidget(self.editor_header)
        self.editor_body = QWidget()
        editor_layout = QVBoxLayout(self.editor_body)
        editor_layout.setContentsMargins(0, 0, 0, 0)
        editor_layout.setSpacing(10)
        panel_layout.addWidget(self.editor_body, 1)
        labels = QHBoxLayout()
        labels.setContentsMargins(0, 0, 6, 0)
        labels.setSpacing(INPUT_SPACING)
        for text, width in (("单词", WORD_WIDTH), ("词性", POS_WIDTH), ("翻译", 0)):
            label = QLabel(text)
            label.setObjectName("muted")
            if width:
                label.setFixedWidth(width)
            labels.addWidget(label, 1 if not width else 0)
        editor_layout.addLayout(labels)
        self.form_scroll = QScrollArea()
        self.form_scroll.setWidgetResizable(True)
        self.form_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.form_content = QWidget()
        self.form_layout = QVBoxLayout(self.form_content)
        self.form_layout.setContentsMargins(0, 0, 6, 0)
        self.form_layout.setSpacing(10)
        self.form_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.form_scroll.setWidget(self.form_content)
        self.form_scroll.verticalScrollBar().rangeChanged.connect(
            lambda low, high: QTimer.singleShot(0, self.ensure_current_input_visible))
        editor_layout.addWidget(self.form_scroll, 1)

        self.add_related_button = button("新增关联词", quiet=True)
        self.add_related_button.clicked.connect(self.add_related)
        self.remove_related_button = button("移除末项关联词", quiet=True)
        self.remove_related_button.clicked.connect(self.remove_last_related)
        footer = QHBoxLayout()
        footer.addWidget(self.add_related_button)
        footer.addWidget(self.remove_related_button)
        footer.addStretch()
        self.cancel_button = button("取消")
        self.cancel_button.clicked.connect(self.cancel_form)
        self.cancel_button.hide()
        self.star_button = button("")
        self.star_button.setCheckable(True)
        self.star_button.setChecked(True)
        self.star_button.setFixedSize(38, 36)
        self.star_button.setAccessibleName("切换当前主词是否重点")
        self.star_button.toggled.connect(self.star_changed)
        self.save_button = button("保存")
        self.save_button.setObjectName("primary")
        self.save_button.clicked.connect(self.save_current)
        self.toast = FloatingNotice(root)
        self.toast.actionTriggered.connect(self.locate_duplicate)
        footer.addWidget(self.cancel_button)
        footer.addWidget(self.star_button)
        footer.addWidget(self.save_button)
        editor_layout.addLayout(footer)
        layout.addWidget(editor_panel)
        self._editor_resize_timer = QTimer(self)
        self._editor_resize_timer.setSingleShot(True)
        self._editor_resize_timer.timeout.connect(self.update_editor_height)

        form_actions = {
            "add_related": self.add_related,
            "remove_related": self.remove_last_related,
            "add_sense": self.add_sense,
            "remove_sense": self.remove_sense,
            "save": self.save_current,
            "important": self.toggle_form_star,
        }
        self.shortcuts = ShortcutManager(repository, self, {
            name: lambda action=action: self.run_form_action(action) for name, action in form_actions.items()
        })
        self.shortcuts.changed.connect(self.refresh_shortcut_labels)
        self.reset_form()
        self.refresh()
        QApplication.instance().focusChanged.connect(self.focus_changed)
        QTimer.singleShot(0, self.blocks[0].word.setFocus)

    def copy_word(self, text):
        QApplication.clipboard().setText(text)
        self.toast.show_message(f"已复制：{text}")

    def show_notice(self, text, error=False, duplicate=False):
        if error:
            anchor = (self.study_page.refresh_button if self.filter_name == 'study17' else
                      (self.collapse_button if self.form_collapsed else self.save_button))
            self.toast.show_message(text, anchor, with_action=duplicate)
        else:
            self.toast.dismiss()

    def set_form_collapsed(self, collapsed):
        if self.filter_name == 'study17':
            return
        if collapsed == self.form_collapsed:
            return
        self.toast.dismiss()
        if collapsed:
            focus = QApplication.focusWidget()
            candidate = focus if focus and self.editor_body.isAncestorOf(focus) else self._last_form_focus
            self._collapsed_focus = candidate if (candidate and shiboken6.isValid(candidate)
                                                  and self.editor_body.isAncestorOf(candidate)) else None
            if focus and self.editor_body.isAncestorOf(focus):
                self.collapse_button.setFocus()
            self.editor_body.hide()
        else:
            self.editor_body.show()
        self.form_collapsed = collapsed
        self.update_editor_height()
        self.collapse_button.setChecked(collapsed)
        self.collapse_button.setIcon(icon("chevron_up" if collapsed else "chevron_down"))
        label = "展开添加单词区域" if collapsed else "收起添加单词区域"
        self.collapse_button.setToolTip(label)
        self.collapse_button.setAccessibleName(label)
        if not collapsed and self._collapsed_focus and shiboken6.isValid(self._collapsed_focus):
            widget = self._collapsed_focus
            QTimer.singleShot(0, lambda: self.restore_input_focus(widget))

    def schedule_editor_resize(self):
        if not self._closing:
            self._editor_resize_timer.start(0)

    def update_editor_height(self):
        if self._closing:
            return
        panel = self.editor_panel.layout()
        margins = panel.contentsMargins()
        header_height = (self.editor_header.height() + margins.top() + margins.bottom()
                         + 2 * self.editor_panel.frameWidth())
        if self.form_collapsed:
            height = header_height
        else:
            body = self.editor_body.layout()
            body_margins = body.contentsMargins()
            body_height = body_margins.top() + body_margins.bottom()
            body_height += body.spacing() * (body.count() - 1)
            body_height += sum(body.itemAt(i).sizeHint().height() for i in range(body.count())
                               if body.itemAt(i).widget() is not self.form_scroll)
            body_height += self.form_layout.sizeHint().height() + 2 * self.form_scroll.frameWidth()
            required = header_height + panel.spacing() + body_height
            height = min(required, self.centralWidget().height() // 2)
        self.editor_panel.setFixedHeight(height)
        self.centralWidget().layout().activate()
        QTimer.singleShot(0, self.ensure_current_input_visible)

    def refresh(self):
        studying = self.filter_name == "study17"
        self.search_box.setVisible(not studying)
        self.editor_panel.setVisible(not studying)
        self.editor_panel.setEnabled(not studying)
        for control, label in ((self.import_button, '导入学习进度' if studying else '导入单词'),
                               (self.export_button, '导出学习进度' if studying else '导出当前页面所有单词')):
            control.setToolTip(label)
            control.setAccessibleName(label)
        self.table.setVisible(not studying)
        self.study_page.setVisible(studying)
        if studying:
            for key, control in self.filter_buttons.items():
                control.setProperty("active", key == self.filter_name)
                control.style().unpolish(control)
                control.style().polish(control)
            return
        bar = self.table.verticalScrollBar()
        self._filter_scroll[self._view_filter] = bar.value()
        position = self._filter_scroll[self.filter_name]
        current = self.table.currentIndex().data(Qt.ItemDataRole.UserRole)
        selected_id = current.entry.id if current else None
        selected_part = (current.owner, current.sense, self.table.currentIndex().column()) if current else None
        entries = self.repository.list_entries()
        counts = {"all": len(entries), "pending": sum(not e.learned for e in entries),
                  "learned": sum(e.learned for e in entries)}
        labels = {"all": "全部", "pending": "待记", "learned": "已记下"}
        for key, control in self.filter_buttons.items():
            if key == "study17":
                control.setProperty("active", False)
                control.style().unpolish(control)
                control.style().polish(control)
                continue
            control.setText(f"{labels[key]}  {counts[key]}")
            control.setProperty("active", key == self.filter_name)
            control.style().unpolish(control)
            control.style().polish(control)
        visible = self._filter_entries(entries)
        searching = bool(self.search_box.text().strip())
        self.table.set_page_state(self.filter_name, searching, len(visible))
        auto_scroll = self.table.hasAutoScroll()
        self.table.setUpdatesEnabled(False)
        self.table.setAutoScroll(False)
        try:
            # Search expands only the projection, leaving the review state intact.
            revealed = {entry.id for entry in visible} if searching else self.revealed
            self.model.populate(visible, revealed)
            self.table.update_spans()
            if selected_id is not None:
                matching = [i for i, row in enumerate(self.model.rows) if row.entry.id == selected_id]
                if matching:
                    number = next((i for i in matching if
                                   (self.model.rows[i].owner, self.model.rows[i].sense) == selected_part[:2]),
                                  matching[0])
                    self.table.setCurrentIndex(self.model.index(number, selected_part[2]))
            # Restoring currentIndex with auto-scroll enabled jumps to the old selection.
            self.table.doItemsLayout()
            bar.setValue(position)
            self._view_filter = self.filter_name
        finally:
            self.table.setAutoScroll(auto_scroll)
            self.table.setUpdatesEnabled(True)

    def _filter_entries(self, entries):
        keyword = self.search_box.text().strip().casefold()
        return [entry for entry in entries
                if (self.filter_name == "all" or
                    (entry.learned if self.filter_name == "learned" else not entry.learned))
                and (not keyword or any(keyword in text.casefold()
                     for word in [entry, *entry.related]
                     for text in [word.text, *(sense.translation for sense in word.senses)]))]

    def search_changed(self):
        if not self.table.delegate.submit():
            # Keep the existing projection and query aligned when an invalid editor blocks filtering.
            self.search_box.blockSignals(True)
            self.search_box.setText(getattr(self, "_applied_search", ""))
            self.search_box.blockSignals(False)
            return
        self._applied_search = self.search_box.text()
        self._filter_scroll[self.filter_name] = 0
        self.refresh()
        self.table.verticalScrollBar().setValue(0)

    def move_word(self, entry_id, target_index):
        if self.filter_name == "all" or self.search_box.text().strip():
            return
        try:
            self.repository.move_entry(entry_id, target_index)
        except Exception as error:
            self.show_notice(str(error), True)
        self.refresh()

    def shuffle_words(self):
        if self.filter_name != "pending" or self.search_box.text().strip():
            return
        if not self.table.delegate.submit():
            return
        try:
            self.repository.shuffle_pending()
        except Exception as error:
            self.show_notice(str(error), True)
            return
        self.refresh()
        self.table.scrollToTop()
        self._filter_scroll["pending"] = 0

    def set_filter(self, key):
        if not self.table.delegate.submit():
            return
        if key == "study17" and not self.study_page.reload():
            return
        self.toast.dismiss()
        self.filter_name = key
        self.refresh()

    def reveal(self, entry_id, expanded):
        if self.search_box.text().strip():
            return
        if expanded:
            self.revealed.add(entry_id)
        else:
            self.revealed.discard(entry_id)
        self.refresh()

    def set_global_hidden(self, hidden):
        if self.table.delegate.submit():
            self.global_hidden = hidden
            self.study_page.set_global_hidden(hidden)
            if hidden:
                self.revealed.clear()
            else:
                self.revealed = {entry.id for entry in self.repository.list_entries()}
            self.refresh()
        self.global_hide_button.setChecked(self.global_hidden)
        self.global_hide_button.setAccessibleName(f"全局隐藏：{'开' if self.global_hidden else '关'}")
        self.global_hide_button.setToolTip(
            f"{'显示' if self.global_hidden else '隐藏'}所有词条的词性、翻译和关联词")

    def add_block(self, text="", senses=None, related=False):
        block = WordBlock(text, senses, related)
        block.removeRequested.connect(self.remove_related)
        block.changed.connect(self.form_changed)
        block.structureChanged.connect(self.update_tab_order)
        self.form_layout.addWidget(block)
        self.blocks.append(block)
        self.update_related_actions()
        return block

    def reset_form(self, entry=None):
        if entry is not None:
            self.set_form_collapsed(False)
        self.active_block = None
        for block in self.blocks:
            self.form_layout.removeWidget(block)
            block.hide()
            block.deleteLater()
        self.blocks = []
        self.edit_id = entry.id if entry else None
        entry = entry or WordEntry()
        self.add_block(entry.text, entry.senses)
        for child in entry.related:
            self.add_block(child.text, child.senses, True)
        self.star_button.setChecked(entry.important)
        self.star_changed()
        self._baseline = deepcopy(self.form_value())
        self.form_changed()
        self.refresh_shortcut_labels()
        self.update_tab_order()

    def update_related_actions(self):
        has_related = len(self.blocks) > 1
        self.remove_related_button.setEnabled(has_related)
        self.remove_related_button.setVisible(has_related)

    def form_value(self):
        if not self.blocks:
            return WordEntry()
        main = self.blocks[0].value()
        return WordEntry(main.text, main.senses, [block.value() for block in self.blocks[1:]],
                         self.star_button.isChecked(), id=self.edit_id)

    def is_dirty(self):
        return hasattr(self, "_baseline") and self.form_value() != self._baseline

    def form_changed(self):
        if not self.blocks:
            return
        dirty = self.is_dirty()
        self.cancel_button.setVisible(bool(self.edit_id or dirty))

    def star_changed(self):
        self.star_button.setIcon(icon("star", GOLD if self.star_button.isChecked() else "#596371"))
        if hasattr(self, "shortcuts"):
            key = self.shortcuts.labels()["important"]
            self.star_button.setToolTip(f"{'重点' if self.star_button.isChecked() else '非重点'} · {key} · 空格切换")
        self.form_changed()

    def toggle_form_star(self):
        if self.filter_name != 'study17' and not self.table.delegate.editor:
            self.star_button.toggle()

    def run_form_action(self, action):
        if self.filter_name != 'study17':
            return action()

    def update_tab_order(self):
        self.schedule_editor_resize()
        chain = [*self.filter_buttons.values(), self.settings_button, self.search_box, self.global_hide_button,
                 self.import_button, self.export_button, self.table,
                 self.study_page.refresh_button, self.study_page.table, self.collapse_button]
        for block in self.blocks:
            chain.extend(block.input_order())
        chain.extend([self.add_related_button, self.remove_related_button,
                      self.cancel_button, self.star_button, self.save_button])
        for first, second in zip(chain, chain[1:]):
            QWidget.setTabOrder(first, second)

    def focus_changed(self, old, new):
        if self._closing:
            return
        target = next((block for block in self.blocks if new is block or
                       (new is not None and block.isAncestorOf(new))), None)
        self.active_block = target
        keys = self.shortcuts.labels()
        for block in self.blocks:
            block.set_active(block is target, keys)
        if target:
            self._last_form_focus = new
            self.form_scroll.ensureWidgetVisible(new, 10, 12)
            # Newly added blocks may not have their final layout until the next event.
            QTimer.singleShot(0, lambda: self.ensure_focus_visible(new))

    def ensure_focus_visible(self, widget):
        if (not self._closing and not self.form_collapsed and shiboken6.isValid(widget)
                and widget is QApplication.focusWidget()):
            self.form_layout.activate()
            top = widget.mapTo(self.form_content, QPoint(0, 0)).y()
            bottom = top + widget.height()
            bar = self.form_scroll.verticalScrollBar()
            height = self.form_scroll.viewport().height()
            block = next((item for item in self.blocks if item.isAncestorOf(widget)), None)
            if block and block.height() + 12 <= height:
                top = block.mapTo(self.form_content, QPoint(0, 0)).y()
                bottom = top + block.height()
            if bottom + 12 > bar.value() + height:
                bar.setValue(bottom + 12 - height)
            elif top - 12 < bar.value():
                bar.setValue(max(0, top - 12))

    def ensure_current_input_visible(self):
        widget = QApplication.focusWidget()
        if widget and self.form_content.isAncestorOf(widget):
            self.ensure_focus_visible(widget)

    def refresh_shortcut_labels(self):
        keys = self.shortcuts.labels()
        self.add_related_button.setText(f"新增关联词  {keys['add_related']}")
        self.remove_related_button.setText(f"移除末项关联词  {keys['remove_related']}")
        self.save_button.setText(f"{'保存修改' if self.edit_id else '保存'}  {keys['save']}")
        for block in self.blocks:
            block.set_active(block is self.active_block, keys)
        self.star_changed()

    def _can_change_form(self):
        if self.filter_name == 'study17':
            return False
        if self.table.delegate.editor:
            self.show_notice("请先保存或取消列表中的编辑。", True)
            return False
        self.set_form_collapsed(False)
        return True

    def add_related(self):
        if not self._can_change_form():
            return
        block = self.add_block(related=True)
        self.form_changed()
        self.update_tab_order()
        QTimer.singleShot(0, block.word.setFocus)

    def remove_last_related(self):
        if len(self.blocks) > 1 and self._can_change_form():
            self.remove_related(self.blocks[-1])

    def remove_related(self, block):
        if block not in self.blocks[1:]:
            return
        if block.has_content() and not confirm_remove(self, "移除这个关联词及其全部释义？"):
            return
        self.blocks.remove(block)
        if self.active_block is block:
            self.active_block = None
        self.form_layout.removeWidget(block)
        block.hide()
        block.deleteLater()
        self.update_related_actions()
        self.form_changed()
        self.update_tab_order()
        self.blocks[-1].word.setFocus()

    def add_sense(self):
        block = self.focused_block()
        if block and self._can_change_form():
            block.add_sense()

    def remove_sense(self):
        block = self.focused_block()
        if block and self._can_change_form():
            block.remove_last_sense()

    def focused_block(self):
        focus = QApplication.focusWidget()
        return next((block for block in self.blocks if focus is block
                     or (focus is not None and block.isAncestorOf(focus))), None)

    def _save_form(self):
        is_new = self.edit_id is None
        try:
            saved = self.repository.save(self.form_value())
        except DuplicateWord as error:
            self.duplicate_id = error.entry_id
            self.show_notice(str(error), True, duplicate=True)
            return False
        except Exception as error:
            logging.exception("Could not save entry")
            self.show_notice(str(error), True)
            return False
        self.duplicate_id = None
        if is_new:
            self.revealed.add(saved.id)
            if self.filter_name == "learned":
                self.filter_name = "pending"
        self.reset_form()
        self.refresh()
        if is_new:
            self.scroll_to_new_word(saved.id)
            QTimer.singleShot(0, lambda: self.scroll_to_new_word(saved.id))
        self.show_notice(f"已保存 {saved.text}")
        self.blocks[0].word.setFocus()
        return True

    def scroll_to_new_word(self, entry_id):
        if self._closing or self.table.delegate.editor:
            return
        for number, row in enumerate(self.model.rows):
            if row.entry.id == entry_id and row.first:
                self.table.setCurrentIndex(self.model.index(number, 1))
                self.table.scrollToBottom()
                break

    def save_current(self):
        if self.filter_name == 'study17':
            return False
        if self.table.delegate.editor:
            return self.table.delegate.submit()
        self.set_form_collapsed(False)
        return self._save_form()

    def resolve_dirty(self):
        if not self.is_dirty():
            return True
        dialog = QMessageBox(self)
        dialog.setWindowTitle("保留当前输入？")
        dialog.setText("当前表单还有未保存的内容。")
        save = dialog.addButton("保存", QMessageBox.ButtonRole.AcceptRole)
        discard = dialog.addButton("放弃", QMessageBox.ButtonRole.DestructiveRole)
        cancel = dialog.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        dialog.setDefaultButton(cancel)
        dialog.setEscapeButton(cancel)
        dialog.exec()
        if dialog.clickedButton() is save:
            return self._save_form()
        if dialog.clickedButton() is discard:
            self.reset_form()
            return True
        return False

    def cancel_form(self):
        if self.resolve_dirty():
            self.reset_form()
            self.blocks[0].word.setFocus()

    def before_inline(self):
        if self.is_dirty() and not self.resolve_dirty():
            return False
        if self.edit_id is not None:
            self.reset_form()
        return True

    def inline_changed(self, editing):
        if not editing and not self._closing:
            QTimer.singleShot(0, self.refresh)

    def commit_cell(self, row, column, value):
        try:
            entry = self.repository.get(row.entry.id)
            target = entry if row.owner == -1 else entry.related[row.owner]
            if column == 1:
                target.text = value
            elif column == 2:
                target.senses[row.sense].pos = value
            elif column == 3:
                target.senses[row.sense].translation = value
            self.repository.save(entry)
        except DuplicateWord as error:
            self.duplicate_id = error.entry_id
            self.show_notice(str(error), True, duplicate=True)
            return False
        except Exception as error:
            self.show_notice(str(error), True)
            return False
        self.show_notice("修改已保存")
        return True

    def list_action(self, entry_id, action):
        if not self.table.delegate.submit():
            return
        if action == "edit":
            if not self.resolve_dirty():
                return
            self.reset_form(self.repository.get(entry_id))
            self.blocks[0].word.setFocus()
            return
        if self.edit_id == entry_id:
            if not self.resolve_dirty():
                return
            self.reset_form()
        try:
            entry = self.repository.get(entry_id)
            if action == "important":
                self.repository.set_flag(entry_id, "important", not entry.important)
            elif action == "learned":
                self.repository.set_flag(entry_id, "learned", not entry.learned)
                self.show_notice(f"{entry.text} 已{'恢复待记' if entry.learned else '标记为已记下'}")
            elif action == "trash":
                if not confirm_remove(self, f"删除 {entry.text} 及其全部释义和关联词？"):
                    return
                self.repository.delete(entry_id)
                self.revealed.discard(entry_id)
                self.show_notice(f"已删除 {entry.text}")
            self.refresh()
        except Exception as error:
            self.show_notice(str(error), True)

    def locate_duplicate(self):
        if self.duplicate_id is not None:
            if not self.table.delegate.submit():
                return
            self.search_box.clear()
            self.set_filter("all")
            for number, row in enumerate(self.model.rows):
                if row.entry.id == self.duplicate_id:
                    index = self.model.index(number, 1)
                    self.table.setCurrentIndex(index)
                    self.table.scrollTo(index)
                    break

    def open_settings(self):
        if not self.table.delegate.submit():
            return
        previous = (self._last_form_focus if not self.form_collapsed else None) or QApplication.focusWidget()
        self.shortcuts.set_suspended(True)
        try:
            dialog = SettingsDialog(self.shortcuts, self)
            dialog.exec()
            dialog.deleteLater()
        finally:
            self.shortcuts.set_suspended(False)
            if previous and shiboken6.isValid(previous):
                self.activateWindow()
                previous.setFocus()
                # Qt restores the modal parent's old focus during dialog teardown.
                # Reapply our last input target after that restoration completes.
                QTimer.singleShot(0, lambda: self.restore_input_focus(previous))

    def export_words(self):
        if self.filter_name == 'study17':
            return self.export_study_progress()
        if not self.table.delegate.submit():
            return
        self.shortcuts.set_suspended(True)
        try:
            path, _ = QFileDialog.getSaveFileName(
                self, "导出当前页面所有单词", "kkMemorizeVocabulary.json", "JSON 文件 (*.json)")
            if not path:
                return
            if not Path(path).suffix:
                path += ".json"
            entries = self._filter_entries(self.repository.list_entries())
            write_entries(path, entries)
            page = {"pending": "待记", "learned": "已记下", "all": "全部"}[self.filter_name]
            QMessageBox.information(self, "导出完成", f"已导出“{page}”页面当前筛选结果的 {len(entries)} 个主词。")
        except Exception as error:
            logging.exception("Could not export words")
            QMessageBox.critical(self, "导出失败", str(error))
        finally:
            self.shortcuts.set_suspended(False)

    def import_words(self):
        if self.filter_name == 'study17':
            return self.import_study_progress()
        self.shortcuts.set_suspended(True)
        try:
            path, _ = QFileDialog.getOpenFileName(self, "导入单词", "", "JSON 文件 (*.json)")
            if not path:
                return
            entries = read_entries(path)
            if not self.table.delegate.submit() or not self.resolve_dirty():
                return
            result = self.repository.import_entries(entries)
            if self.edit_id is not None:
                self.reset_form()
            self.refresh()
            replaced_ids = self.resolve_word_conflicts(result)
            self.refresh()
            QMessageBox.information(
                self, "导入完成", f"新增 {result.inserted} 个主词，发现 {len(result.conflicts)} 条重复记录，"
                f"覆盖 {len(replaced_ids)} 个主词。")
        except Exception as error:
            logging.exception("Could not import words")
            QMessageBox.critical(self, "导入失败", str(error))
        finally:
            self.shortcuts.set_suspended(False)

    def resolve_word_conflicts(self, result):
        if not result.conflicts:
            return set()
        dialog = ImportConflictDialog(self.repository, result, self)
        dialog.resolved.connect(self.refresh)
        try:
            dialog.exec()
            return dialog.replaced_names
        finally:
            dialog.deleteLater()

    def export_study_progress(self):
        self.shortcuts.set_suspended(True)
        try:
            path, _ = QFileDialog.getSaveFileName(
                self, '导出学习进度', 'kkMemorizeVocabulary-study17.json', 'JSON 文件 (*.json)')
            if not path:
                return
            if not Path(path).suffix:
                path += '.json'
            backup = self.repository.study_backup()
            write_study_backup(path, backup)
            QMessageBox.information(self, '导出完成', f'已导出 {len(backup.words)} 个主词及全部学习进度。')
        except Exception as error:
            logging.exception('Could not export study progress')
            QMessageBox.critical(self, '导出失败', str(error))
        finally:
            self.shortcuts.set_suspended(False)

    def choose_study_import_mode(self):
        dialog = QMessageBox(self)
        dialog.setWindowTitle('导入学习进度')
        dialog.setText('导入学习进度将覆盖本机全部学习进度，请选择导入方式。')
        full = dialog.addButton('导入单词和进度', QMessageBox.ButtonRole.AcceptRole)
        words = dialog.addButton('仅导入单词', QMessageBox.ButtonRole.ActionRole)
        cancel = dialog.addButton('取消', QMessageBox.ButtonRole.RejectRole)
        dialog.setDefaultButton(cancel)
        dialog.setEscapeButton(cancel)
        dialog.exec()
        if dialog.clickedButton() is full:
            return 'full'
        if dialog.clickedButton() is words:
            return 'words'
        return 'cancel'

    def import_study_progress(self):
        self.shortcuts.set_suspended(True)
        result = None
        try:
            path, _ = QFileDialog.getOpenFileName(self, '导入学习进度', '', 'JSON 文件 (*.json)')
            if not path:
                return
            backup = read_study_backup(path)
            mode = self.choose_study_import_mode() if self.repository.has_study_progress() else 'full'
            if mode == 'cancel':
                return
            if not self.table.delegate.submit() or not self.resolve_dirty():
                return
            result = self.repository.import_entries(backup.words)
            if self.edit_id is not None:
                self.reset_form()
            replaced = self.resolve_word_conflicts(result)
            if mode == 'full':
                self.repository.replace_study_progress(backup)
                self.study_page.revealed.clear()
                self.study_page.load(StudyContext.now())
                status = f'已恢复 {len(backup.records)} 条学习进度。'
            else:
                self.study_page.load(self.study_page.context)
                status = '本机学习进度已保留。'
            self.refresh()
            QMessageBox.information(self, '导入完成',
                f'新增 {result.inserted} 个主词，发现 {len(result.conflicts)} 条重复记录，'
                f'覆盖 {len(replaced)} 个主词。\n{status}')
        except Exception as error:
            logging.exception('Could not import study progress')
            detail = str(error)
            if result is not None:
                self.study_page.load(self.study_page.context)
                self.refresh()
                detail += '\n已导入或确认的词条已保留，本机学习进度未被整体替换，可重新导入。'
            QMessageBox.critical(self, '导入失败', detail)
        finally:
            self.shortcuts.set_suspended(False)

    def restore_input_focus(self, widget):
        if not self._closing and shiboken6.isValid(widget) and widget.isVisible():
            self.activateWindow()
            widget.setFocus(Qt.FocusReason.OtherFocusReason)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "toast"):
            self.toast.dismiss()
        if hasattr(self, "_editor_resize_timer"):
            self.schedule_editor_resize()

    def closeEvent(self, event):
        if not self.table.delegate.submit() or not self.resolve_dirty():
            event.ignore()
            return
        self._closing = True
        event.accept()

