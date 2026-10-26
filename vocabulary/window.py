from copy import deepcopy
import logging

import shiboken6
from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QMainWindow, QMessageBox,
    QScrollArea, QSplitter, QVBoxLayout, QWidget,
)

from .domain import DuplicateWord, WordEntry
from .shortcuts import SettingsDialog, ShortcutManager
from .table import WordTable, WordTableModel
from .theme import ACCENT, GOLD, MUTED, icon
from .widgets import WordBlock, button, confirm_remove


class MainWindow(QMainWindow):
    def __init__(self, repository):
        super().__init__()
        self.repository = repository
        self.revealed = set()
        self.global_hidden = True
        self.filter_name = "pending"
        self.edit_id = None
        self.active_block = None
        self._last_form_focus = None
        self.blocks = []
        self.last_mark = None
        self.duplicate_id = None
        self._closing = False
        self.setWindowTitle("kkMemorizeVocabulary")
        self.setWindowIcon(icon("book", ACCENT))
        self.resize(1040, 820)
        self.setMinimumSize(860, 640)
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(16)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(9)
        layout.addWidget(splitter, 1)
        list_panel = QFrame()
        list_panel.setObjectName("panel")
        list_panel.setMinimumHeight(180)
        list_layout = QVBoxLayout(list_panel)
        list_layout.setContentsMargins(12, 12, 12, 10)
        list_layout.setSpacing(10)
        filters = QHBoxLayout()
        self.filter_buttons = {}
        for key, label in (("pending", "待记"), ("all", "全部"), ("learned", "已记下")):
            control = button(label)
            control.clicked.connect(lambda checked=False, key=key: self.set_filter(key))
            self.filter_buttons[key] = control
            filters.addWidget(control)
        filters.addStretch()
        self.global_hide_button = button("全局隐藏：开")
        self.global_hide_button.setCheckable(True)
        self.global_hide_button.setChecked(True)
        self.global_hide_button.setToolTip("显示所有词条的词性、翻译和关联词")
        self.global_hide_button.clicked.connect(self.set_global_hidden)
        filters.addWidget(self.global_hide_button)
        list_layout.addLayout(filters)

        self.model = WordTableModel(self.commit_cell, self)
        self.table = WordTable(self.model, self.before_inline)
        self.table.revealRequested.connect(self.reveal)
        self.table.actionRequested.connect(self.list_action)
        self.table.delegate.editingChanged.connect(self.inline_changed)
        list_layout.addWidget(self.table, 1)
        notice_row = QHBoxLayout()
        self.notice = QLabel("")
        self.notice.setObjectName("notice")
        self.notice.setWordWrap(True)
        self.notice.setMinimumHeight(24)
        notice_row.addWidget(self.notice, 1)
        self.undo = button("撤销", quiet=True)
        self.undo.clicked.connect(self.undo_mark)
        self.undo.hide()
        self.locate = button("查看已有词", quiet=True)
        self.locate.clicked.connect(self.locate_duplicate)
        self.locate.hide()
        notice_row.addWidget(self.undo)
        notice_row.addWidget(self.locate)
        list_layout.addLayout(notice_row)
        splitter.addWidget(list_panel)

        editor_panel = QFrame()
        editor_panel.setObjectName("panel")
        editor_panel.setMinimumHeight(310)
        editor_layout = QVBoxLayout(editor_panel)
        editor_layout.setContentsMargins(16, 14, 16, 14)
        editor_layout.setSpacing(10)
        self.form_title = QLabel("添加单词")
        self.form_title.setObjectName("section")
        editor_layout.addWidget(self.form_title)
        labels = QHBoxLayout()
        labels.setContentsMargins(12, 0, 12, 0)
        labels.setSpacing(12)
        for text, width in (("单词", 192), ("词性", 136), ("翻译", 0)):
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
        self.remove_related_button = button("移除末项", quiet=True)
        self.remove_related_button.clicked.connect(self.remove_last_related)
        footer = QHBoxLayout()
        self.settings_button = button("快捷键设置", quiet=True)
        self.settings_button.setIcon(icon("settings"))
        self.settings_button.clicked.connect(self.open_settings)
        footer.addWidget(self.settings_button)
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
        footer.addWidget(self.cancel_button)
        footer.addWidget(self.star_button)
        footer.addWidget(self.save_button)
        editor_layout.addLayout(footer)
        splitter.addWidget(editor_panel)
        splitter.setSizes([350, 370])
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)

        self.shortcuts = ShortcutManager(repository, self, {
            "add_related": self.add_related,
            "remove_related": self.remove_last_related,
            "add_sense": self.add_sense,
            "remove_sense": self.remove_sense,
            "save": self.save_current,
            "important": self.toggle_form_star,
        })
        self.shortcuts.changed.connect(self.refresh_shortcut_labels)
        self.reset_form()
        self.refresh()
        QApplication.instance().focusChanged.connect(self.focus_changed)
        QTimer.singleShot(0, self.blocks[0].word.setFocus)

    def show_notice(self, text, error=False):
        self.notice.setStyleSheet("color: #efa0a4;" if error else "color: #a4ddc5;")
        self.notice.setText(text)

    def refresh(self):
        entries = self.repository.list_entries()
        counts = {"all": len(entries), "pending": sum(not e.learned for e in entries),
                  "learned": sum(e.learned for e in entries)}
        labels = {"all": "全部", "pending": "待记", "learned": "已记下"}
        for key, control in self.filter_buttons.items():
            control.setText(f"{labels[key]}  {counts[key]}")
            control.setProperty("active", key == self.filter_name)
            control.style().unpolish(control)
            control.style().polish(control)
        visible = [e for e in entries if self.filter_name == "all" or
                   (e.learned if self.filter_name == "learned" else not e.learned)]
        self.model.populate(visible, self.revealed)
        self.table.update_spans()

    def set_filter(self, key):
        if not self.table.delegate.submit():
            return
        self.filter_name = key
        self.refresh()

    def reveal(self, entry_id, expanded):
        if expanded:
            self.revealed.add(entry_id)
        else:
            self.revealed.discard(entry_id)
        self.refresh()

    def set_global_hidden(self, hidden):
        if self.table.delegate.submit():
            self.global_hidden = hidden
            if hidden:
                self.revealed.clear()
            else:
                self.revealed = {entry.id for entry in self.repository.list_entries()}
            self.refresh()
        self.global_hide_button.setChecked(self.global_hidden)
        self.global_hide_button.setText(f"全局隐藏：{'开' if self.global_hidden else '关'}")
        self.global_hide_button.setToolTip(
            f"{'显示' if self.global_hidden else '隐藏'}所有词条的词性、翻译和关联词")

    def add_block(self, text="", senses=None, related=False):
        block = WordBlock(text, senses, related)
        block.removeRequested.connect(self.remove_related)
        block.changed.connect(self.form_changed)
        block.structureChanged.connect(self.update_tab_order)
        self.form_layout.addWidget(block)
        self.blocks.append(block)
        self.relabel_related()
        return block

    def reset_form(self, entry=None):
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

    def relabel_related(self):
        for index, block in enumerate(self.blocks[1:], 1):
            block.label.setText(f"关联词 {index}")
        self.remove_related_button.setEnabled(len(self.blocks) > 1)

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
        self.form_title.setText(("编辑单词" if self.edit_id else "添加单词") + ("  ·  未保存" if dirty else ""))
        self.cancel_button.setVisible(bool(self.edit_id or dirty))

    def star_changed(self):
        self.star_button.setIcon(icon("star", GOLD if self.star_button.isChecked() else "#596371"))
        if hasattr(self, "shortcuts"):
            key = self.shortcuts.labels()["important"]
            self.star_button.setToolTip(f"{'重点' if self.star_button.isChecked() else '非重点'} · {key} · 空格切换")
        self.form_changed()

    def toggle_form_star(self):
        if not self.table.delegate.editor:
            self.star_button.toggle()

    def update_tab_order(self):
        chain = []
        for block in self.blocks:
            chain.extend(block.input_order())
        chain.extend([self.settings_button, self.add_related_button, self.remove_related_button,
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
        if not self._closing and shiboken6.isValid(widget) and widget is QApplication.focusWidget():
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
        self.remove_related_button.setText(f"移除末项  {keys['remove_related']}")
        self.save_button.setText(f"{'保存修改' if self.edit_id else '保存'}  {keys['save']}")
        for block in self.blocks:
            block.set_active(block is self.active_block, keys)
        self.star_changed()

    def _can_change_form(self):
        if self.table.delegate.editor:
            self.show_notice("请先保存或取消列表中的编辑。", True)
            return False
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
        self.relabel_related()
        self.form_changed()
        self.update_tab_order()
        self.blocks[-1].word.setFocus()

    def add_sense(self):
        if self.active_block and self._can_change_form():
            self.active_block.add_sense()

    def remove_sense(self):
        if self.active_block and self._can_change_form():
            self.active_block.remove_last_sense()

    def _save_form(self):
        is_new = self.edit_id is None
        try:
            saved = self.repository.save(self.form_value())
        except DuplicateWord as error:
            self.duplicate_id = error.entry_id
            self.locate.show()
            self.show_notice(str(error), True)
            return False
        except Exception as error:
            logging.exception("Could not save entry")
            self.show_notice(str(error), True)
            return False
        self.duplicate_id = None
        self.locate.hide()
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
        if self.table.delegate.editor:
            return self.table.delegate.submit()
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
            self.locate.show()
            self.show_notice(str(error), True)
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
                self.last_mark = (entry_id, entry.learned)
                self.undo.show()
                self.show_notice(f"{entry.text} 已{'恢复待记' if entry.learned else '标记为已记下'}")
            elif action == "trash":
                if not confirm_remove(self, f"删除 {entry.text} 及其全部释义和关联词？"):
                    return
                self.repository.delete(entry_id)
                self.revealed.discard(entry_id)
                if self.last_mark and self.last_mark[0] == entry_id:
                    self.last_mark = None
                    self.undo.hide()
                self.show_notice(f"已删除 {entry.text}")
            self.refresh()
        except Exception as error:
            self.show_notice(str(error), True)

    def undo_mark(self):
        if self.last_mark:
            entry_id, previous = self.last_mark
            try:
                self.repository.set_flag(entry_id, "learned", previous)
                self.refresh()
                self.last_mark = None
                self.undo.hide()
                self.show_notice("已撤销记忆状态修改")
            except Exception as error:
                self.show_notice(str(error), True)

    def locate_duplicate(self):
        if self.duplicate_id is not None:
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
        previous = self._last_form_focus or QApplication.focusWidget()
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

    def restore_input_focus(self, widget):
        if not self._closing and shiboken6.isValid(widget):
            self.activateWindow()
            widget.setFocus(Qt.FocusReason.OtherFocusReason)

    def closeEvent(self, event):
        if not self.table.delegate.submit() or not self.resolve_dirty():
            event.ignore()
            return
        self._closing = True
        event.accept()

