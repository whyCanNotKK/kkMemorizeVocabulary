from copy import deepcopy
import logging
from pathlib import Path

import shiboken6
from PySide6.QtCore import QPoint, QSize, Qt, QTimer
from PySide6.QtWidgets import (
    QApplication, QDialog, QDialogButtonBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QMainWindow, QMessageBox,
    QPlainTextEdit, QScrollArea, QVBoxLayout, QWidget,
)

from .domain import SHORTCUT_LABELS, DuplicateWord, RelatedWord, ValidationError, WordContentValidationError, WordEntry
from .busy_overlay import BusyOverlay
from .draft_reset_dialog import DraftResetDialog
from .unsaved_input_dialog import UnsavedInputDialog
from .import_dialog import ImportConflictDialog
from .input_focus import InputFocusPolicy
from .pos_picker import PosPicker
from .form_reorder import FormReorderDrag
from .form_focus import FormFocusContext
from .shortcuts import SettingsDialog, ShortcutManager
from .table import CELL_REMOVED, WordTable, WordTableModel
from .review_navigation import ReviewKeyRouter
from .study_page import StudyPage
from .study_history import StudyHistoryPanel
from .learning import StudyContext
from .study_transfer import StudyBackup, read_study_backup, read_word_or_study_file, write_study_backup
from .theme import ACCENT, GOLD, MUTED, icon
from .transfer import write_entries
from .transfer_jobs import run_repository_task
from .widgets import FloatingNotice, PosCombo, SearchBox, WordBlock, button, confirm_remove
from .word_clipboard import encode


class MainWindow(QMainWindow):
    def __init__(self, repository):
        super().__init__()
        self.repository = repository
        self.revealed = set()
        self._global_hidden = {key: True for key in ("pending", "all", "learned")}
        self._search_hidden = {key: set() for key in self._global_hidden}
        self.filter_name = "pending"
        self._view_filter = "pending"
        self._filter_scroll = {"pending": 0, "all": 0, "learned": 0}
        self.edit_id = None
        self.active_block = None
        self._last_form_focus = None
        self._related_focus_target = None
        self.blocks = []
        self.duplicate_id = None
        self._closing = False
        self.form_collapsed = False
        self._collapsed_focus = None
        self.setWindowTitle("kkMemorizeVocabulary")
        self.setWindowIcon(icon("book", ACCENT))
        self.resize(1040, 820)
        self.setMinimumSize(860, 640)
        self.input_focus = InputFocusPolicy(self)
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(9)
        list_panel = QFrame()
        self.list_panel = list_panel
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
        self.settings_button = button("设置", quiet=True)
        self.settings_button.setIcon(icon("settings"))
        self.settings_button.clicked.connect(self.open_settings)
        filters.addWidget(self.settings_button)
        filters.addStretch()
        self.search_box = SearchBox()
        self.search_box.textChanged.connect(self.search_changed)
        filters.addWidget(self.search_box, 1)
        self.global_hide_button = button("")
        self.global_hide_button.setIcon(icon("eye"))
        self.global_hide_button.setFixedSize(38, 36)
        self.global_hide_button.setAccessibleName("全局隐藏：开")
        self.global_hide_button.setCheckable(True)
        self.global_hide_button.setChecked(True)
        self.global_hide_button.clicked.connect(lambda: self.set_global_hidden())
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
        self.table.copyRequested.connect(self.copy_text)
        self.table.revealRequested.connect(self.reveal)
        self.table.actionRequested.connect(self.list_action)
        self.table.moveRequested.connect(self.move_word)
        self.table.relatedMoveRequested.connect(self.move_related_word)
        self.table.senseMoveRequested.connect(self.move_word_sense)
        self.table.shuffleRequested.connect(self.shuffle_words)
        self.table.delegate.editingChanged.connect(self.inline_changed)
        self.table.delegate.contentRemoved.connect(self.inline_content_removed)
        self.table.editPreparationRequested.connect(self.prepare_list_edit)
        list_layout.addWidget(self.table, 1)
        self.study_page = StudyPage(repository, self.commit_cell)
        self.destroyed.connect(self.study_page.timer.dispose)
        self.study_page.table.delegate.editingChanged.connect(self.inline_changed)
        self.study_page.table.delegate.contentRemoved.connect(self.inline_content_removed)
        self.study_page.table.editPreparationRequested.connect(self.prepare_list_edit)
        self.study_page.table.copyRequested.connect(self.copy_text)
        for table in (self.table, self.study_page.table):
            table.wholeWordCopyRequested.connect(self.copy_whole_word)
            table.pasteRejected.connect(self.toast_paste_error)
        self.study_page.table.relatedMoveRequested.connect(self.move_related_word)
        self.study_page.table.senseMoveRequested.connect(self.move_word_sense)
        self.study_page.error.connect(lambda text: self.show_notice(text, True))
        self.study_page.hide()
        list_layout.addWidget(self.study_page, 1)
        layout.addWidget(list_panel, 1)
        self.review_keys = ReviewKeyRouter(
            list_panel, lambda: self.study_page.table if self.filter_name == "study17" else self.table,
            self.search_box)

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
        self.remove_related_button = button("删除关联词", quiet=True)
        self.remove_related_button.clicked.connect(self.remove_focused_related)
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
        self.star_button.setAccessibleName(SHORTCUT_LABELS["important"])
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
        self.history_panel = StudyHistoryPanel(repository, self.study_page.timer, root)
        layout.addWidget(self.history_panel)
        self._editor_resize_timer = QTimer(self)
        self._editor_resize_timer.setSingleShot(True)
        self._editor_resize_timer.timeout.connect(self.update_editor_height)

        form_actions = {
            "add_related": self.add_related,
            "remove_related": self.remove_focused_related,
            "add_sense": self.add_sense,
            "remove_sense": self.remove_sense,
            "save": self.save_current,
            "important": self.toggle_form_star,
            "edit_selected": self.edit_selected,
        }
        callbacks = {
            name: lambda action=action: self.run_form_action(action) for name, action in form_actions.items()
        }
        callbacks["toggle_visibility"] = self.set_global_hidden
        fixed = {}
        for number, key in enumerate(self.filter_buttons, 1):
            action = f"tab_{key}"
            fixed[action] = (f"Alt+{number}",)
            callbacks[action] = lambda key=key: self.set_filter(key)
            self.filter_buttons[key].setToolTip(fixed[action][0])
        scoped = {}
        for number in range(1, 10):
            action = f"copy_word_{number}"
            fixed[action] = (f"Ctrl+{number}", f"Ctrl+Num+{number}")
            callbacks[action] = lambda number=number: self.copy_numbered_word(number)
            scoped[action] = self.can_copy_in_scope
        callbacks = {name: lambda callback=callback: self.run_keyboard_action(callback)
                     for name, callback in callbacks.items()}
        self.shortcuts = ShortcutManager(repository, self, callbacks, fixed=fixed, scoped=scoped)
        self.shortcuts.changed.connect(self.refresh_shortcut_labels)
        self.form_drag = FormReorderDrag(self.form_scroll, lambda: self.blocks, self._can_change_form)
        self.form_drag.relatedMoved.connect(self.move_form_related)
        self.form_drag.senseMoved.connect(lambda block, source, target: block.move_sense(source, target))
        self.form_drag.activeChanged.connect(self.form_drag_changed)
        self.form_focus = FormFocusContext(self.editor_panel, self, self.copy_form_blank)
        self.form_focus.changed.connect(self.related_context_changed)
        self.form_focus.blankPressed.connect(self.clear_related_target)
        self.reset_form()
        self.refresh()
        QApplication.instance().focusChanged.connect(self.focus_changed)
        QTimer.singleShot(0, self.blocks[0].word.setFocus)
        self.busy_overlay = BusyOverlay(self)

    def copy_text(self, text):
        QApplication.clipboard().setText(text)
        self.toast.show_message(f"已复制：{text}")

    def _copy_scope(self, focus):
        if (not self.form_collapsed and self.editor_panel.isVisible()
                and (focus is self.editor_panel or self.editor_panel.isAncestorOf(focus))):
            return "form"
        if (self.current_table().dragging or focus is self.search_box
                or self.search_box.isAncestorOf(focus)):
            return None
        if focus is self.list_panel or self.list_panel.isAncestorOf(focus):
            return "list"
        return None

    def can_copy_in_scope(self, focus):
        popup = QApplication.activePopupWidget()
        return (focus is not None and (popup is None or isinstance(popup.parentWidget(), (PosCombo, PosPicker)))
                and self._copy_scope(focus) is not None)

    def copy_numbered_word(self, number):
        popup = QApplication.activePopupWidget()
        focus = popup.parentWidget() if popup is not None else QApplication.focusWidget()
        scope = self._copy_scope(focus) if focus is not None else None
        if scope == "form" and number <= len(self.blocks):
            self.copy_whole_word(self.blocks[number - 1].value())
        elif scope == "list":
            self.current_table().copy_selected_word(number)

    def copy_form_word(self, block):
        if block in self.blocks:
            self.copy_whole_word(block.value())

    def copy_form_blank(self, point):
        if self.form_collapsed or self.form_drag.active:
            return False
        block = next((block for block in self.blocks if block.copy_blank_hit(point)), None)
        if block is None:
            return False
        self.copy_form_word(block)
        return True

    def copy_whole_word(self, word):
        self.current_table().cancel_pending_copy()
        try:
            content = encode(word)
        except WordContentValidationError as error:
            self.toast.show_message(f"整词复制失败：{error.field}不能为空")
            return
        clipboard = QApplication.clipboard()
        clipboard.setText(content)
        if not clipboard.ownsClipboard() and clipboard.text() != content:
            self.toast.show_message("无法写入剪贴板，请重试")
            return
        self.toast.show_message(f"已复制：{word.text.strip()}的拼写、音标和所有释义")

    def toast_paste_error(self, message):
        self.show_notice(message, True)

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
        label = f"{'展开' if collapsed else '收起'}{self.form_title.text()}区域"
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

    def refresh(self, entries=None):
        studying = self.filter_name == "study17"
        self.search_box.setVisible(not studying)
        self.editor_panel.setVisible(not studying)
        self.editor_panel.setEnabled(not studying)
        self.history_panel.setVisible(studying)
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
            self.update_visibility_button()
            return
        bar = self.table.verticalScrollBar()
        self._filter_scroll[self._view_filter] = bar.value()
        position = self._filter_scroll[self.filter_name]
        if entries is None:
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
        self.search_box.set_result_count(len(visible) if searching else None)
        self.table.set_page_state(self.filter_name, searching, len(visible))
        with self.table.preserve_view(scroll_position=position):
            # Search expands only the projection, leaving the review state intact.
            revealed = ({entry.id for entry in visible} - self._search_hidden[self.filter_name]
                        if searching else self.revealed)
            self.model.populate(visible, revealed)
            self.table.update_spans()
        self._view_filter = self.filter_name
        self.update_visibility_button()

    def _filter_entries(self, entries):
        keyword = self.search_box.text().strip().casefold()
        visible = [entry for entry in entries
                   if (self.filter_name == "all" or
                       (entry.learned if self.filter_name == "learned" else not entry.learned))]
        if not keyword:
            return visible

        def matches(word):
            return (keyword in word.text.casefold() or
                    any(keyword in sense.translation.casefold() for sense in word.senses))

        main_matches, related_matches = [], []
        for entry in visible:
            if matches(entry):
                main_matches.append(entry)
            elif any(matches(word) for word in entry.related):
                related_matches.append(entry)
        return main_matches + related_matches

    def search_changed(self):
        if not self.current_table().delegate.submit():
            # Keep the existing projection and query aligned when an invalid editor blocks filtering.
            self.search_box.blockSignals(True)
            self.search_box.setText(getattr(self, "_applied_search", ""))
            self.search_box.blockSignals(False)
            return
        current = self.table.currentIndex()
        row = current.data(Qt.ItemDataRole.UserRole)
        anchor = None
        if row is not None and self.table.selectionModel().isSelected(current):
            anchor = (row.entry.id, row.owner, row.sense, current.column())
        self._applied_search = self.search_box.text()
        for hidden in self._search_hidden.values():
            hidden.clear()
        self._filter_scroll[self.filter_name] = 0
        self.refresh()
        target = self.table._entry_index(*anchor) if anchor else None
        if target is not None and target.isValid():
            self.table.setCurrentIndex(target)
            self.table.ensure_entry_visible(anchor[0])
        else:
            self.table.verticalScrollBar().setValue(0)
        self._filter_scroll[self.filter_name] = self.table.verticalScrollBar().value()

    def move_word(self, entry_id, target_index):
        if self.filter_name == "all" or self.search_box.text().strip():
            return
        try:
            self.repository.move_entry(entry_id, target_index)
        except Exception as error:
            self.show_notice(str(error), True)
        self.refresh()

    def move_related_word(self, entry_id, source, target):
        try:
            self.repository.move_related(entry_id, source, target)
        except Exception as error:
            self.show_notice(str(error), True)
        self.refresh_after_inline()

    def move_form_related(self, source, target):
        self.blocks.insert(target + 1, self.blocks.pop(source + 1))
        for index, block in enumerate(self.blocks):
            self.form_layout.removeWidget(block)
            self.form_layout.insertWidget(index, block)
        self.update_tab_order()
        self.form_changed()

    def move_word_sense(self, entry_id, owner, source, target):
        try:
            self.repository.move_sense(entry_id, owner, source, target)
        except Exception as error:
            self.show_notice(str(error), True)
        self.refresh_after_inline()

    def form_drag_changed(self, active):
        if active:
            self._shortcuts_before_form_drag = self.shortcuts.suspended
            self.shortcuts.set_suspended(True)
        else:
            self.shortcuts.set_suspended(self._shortcuts_before_form_drag)

    def shuffle_words(self):
        if self.filter_name != "pending" or self.search_box.text().strip():
            return
        if not self.current_table().delegate.submit():
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
        if not self.current_table().delegate.submit():
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
        if expanded:
            self.table.ensure_entry_visible(entry_id)

    @property
    def global_hidden(self):
        if self.filter_name == "study17":
            return self.study_page.global_hidden
        return self._global_hidden[self.filter_name]

    def next_global_hidden(self):
        model = self.study_page.model if self.filter_name == "study17" else self.model
        roots = [row for row in model.rows if row.first]
        if roots and all(not row.hidden for row in roots):
            return True
        if roots and all(row.hidden for row in roots):
            return False
        return not self.global_hidden

    def set_global_hidden(self, hidden=None):
        if self.current_table().delegate.submit():
            if hidden is None:
                hidden = self.next_global_hidden()
            if self.filter_name == "study17":
                self.study_page.set_global_hidden(hidden)
            else:
                self._global_hidden[self.filter_name] = hidden
                ids = {row.entry.id for row in self.model.rows if row.first}
                if hidden:
                    self.revealed.difference_update(ids)
                    self._search_hidden[self.filter_name].update(ids)
                else:
                    self.revealed.update(ids)
                    self._search_hidden[self.filter_name].difference_update(ids)
            self.refresh()
        self.update_visibility_button()

    def update_visibility_button(self):
        self.global_hide_button.setChecked(self.global_hidden)
        self.global_hide_button.setAccessibleName(f"全局隐藏：{'开' if self.global_hidden else '关'}")
        self.global_hide_button.setToolTip(
            f"显示/隐藏 所有释义 · {self.shortcuts.labels()['toggle_visibility']}")

    def add_block(self, text="", senses=None, related=False, *, position=None, phonetic=""):
        block = WordBlock(text, senses, related, phonetic=phonetic)
        block.copyRequested.connect(lambda: self.copy_form_word(block))
        block.pasteRejected.connect(self.toast_paste_error)
        block.changed.connect(self.form_changed)
        block.structureChanged.connect(self.update_tab_order)
        block.heightChanged.connect(self.schedule_editor_resize)
        block.cursorMoved.connect(self.ensure_current_input_visible)
        position = len(self.blocks) if position is None else position
        self.form_layout.insertWidget(position, block)
        self.blocks.insert(position, block)
        self.update_related_actions()
        return block

    def reset_form(self, entry=None):
        self.form_drag.cancel()
        self._related_focus_target = None
        if entry is not None:
            self.set_form_collapsed(False)
        self.active_block = None
        for block in self.blocks:
            self.form_layout.removeWidget(block)
            block.hide()
            block.deleteLater()
        self.blocks = []
        self.edit_id = entry.id if entry else None
        self.form_title.setText("编辑单词" if self.edit_id is not None else "添加单词")
        label = f"{'展开' if self.form_collapsed else '收起'}{self.form_title.text()}区域"
        self.collapse_button.setToolTip(label)
        self.collapse_button.setAccessibleName(label)
        entry = entry or WordEntry()
        self.add_block(entry.text, entry.senses, phonetic=entry.phonetic)
        for child in entry.related:
            self.add_block(child.text, child.senses, True, phonetic=child.phonetic)
        self.star_button.setChecked(entry.important)
        self.star_changed()
        self._baseline = deepcopy(self.form_value())
        self.form_changed()
        self.refresh_shortcut_labels()
        self.update_tab_order()

    def update_related_actions(self):
        form_focused = self.form_focus.active and QApplication.focusWidget() is not self.editor_panel
        self.add_related_button.setVisible(True)
        target = self.related_action_block()
        can_remove = len(self.blocks) > 1
        self.remove_related_button.setEnabled(can_remove)
        self.remove_related_button.setVisible(can_remove)
        keys = self.shortcuts.labels()
        remove_label = ('删除关联词' if target in self.blocks[1:] or not can_remove else '删除末尾关联词')
        for control, key, label in ((self.add_related_button, 'add_related', '新增关联词'),
                                    (self.remove_related_button, 'remove_related', remove_label)):
            text = f'{label} · {keys[key]}' if form_focused else label
            control.setText(text)
            control.setToolTip(text)
            control.setAccessibleName(label)

    def related_context_changed(self):
        if self._closing:
            return
        if not self.form_focus.active:
            self._related_focus_target = None
        self.update_related_actions()

    def clear_related_target(self):
        self._related_focus_target = self.active_block = None
        self._last_form_focus = self._collapsed_focus = None
        self.update_related_actions()

    def form_value(self):
        if not self.blocks:
            return WordEntry()
        main = self.blocks[0].value()
        return WordEntry(main.text, main.senses, [block.value() for block in self.blocks[1:]],
                         self.star_button.isChecked(), id=self.edit_id, phonetic=main.phonetic)

    def is_dirty(self):
        return hasattr(self, "_baseline") and self.form_value() != self._baseline

    def form_changed(self):
        if not self.blocks:
            return
        dirty = self.is_dirty()
        self.cancel_button.setText("取消" if self.edit_id is not None else "清空")
        self.cancel_button.setAccessibleName("取消当前编辑" if self.edit_id is not None else "清空当前输入")
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
        if self.filter_name != 'study17' or action == self.save_current:
            return action()

    def run_keyboard_action(self, callback):
        # Native IME-compatible shortcuts can consume the key before Qt sees it.
        self.study_page.timer.note_activity()
        return callback()

    def current_table(self):
        return self.study_page.table if self.filter_name == 'study17' else self.table

    def update_tab_order(self):
        self.schedule_editor_resize()
        chain = [*self.filter_buttons.values(), self.settings_button, self.search_box, self.global_hide_button,
                 self.import_button, self.export_button, self.table,
                 self.study_page.refresh_button, self.study_page.timer_button, self.study_page.completed_button,
                 self.study_page.table,
                 self.history_panel.collapse_button, self.history_panel.table, self.collapse_button]
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
        if target is not None:
            self._related_focus_target = target
        elif new not in (self.add_related_button, self.remove_related_button):
            self._related_focus_target = None
        keys = self.shortcuts.labels()
        self.update_related_actions()
        for block in self.blocks:
            block.set_shortcuts(keys)
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
            if isinstance(widget, QPlainTextEdit) and widget.height() > height:
                cursor = widget.cursorRect()
                top = widget.viewport().mapTo(self.form_content, cursor.topLeft()).y()
                bottom = top + cursor.height()
            elif block and block.height() + 12 <= height:
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
        self.update_visibility_button()
        self.update_related_actions()
        self.save_button.setText(f"{'保存修改' if self.edit_id else '保存'}  {keys['save']}")
        for block in self.blocks:
            block.set_shortcuts(keys)
        self.star_changed()

    def _can_change_form(self):
        if self.filter_name == 'study17':
            return False
        if self.table.delegate.editor:
            if not self.table.delegate.submit():
                return False
        self.set_form_collapsed(False)
        return True

    def add_related(self):
        target = self.related_action_block()
        if not self._can_change_form():
            return
        position = self.blocks.index(target) + 1 if target in self.blocks else len(self.blocks)
        block = self.add_block(related=True, position=position)
        self.form_changed()
        self.update_tab_order()
        QTimer.singleShot(0, lambda: self.restore_input_focus(block.word))

    def related_action_block(self):
        if not self.form_focus.active:
            return None
        target = self.focused_block()
        if target is not None:
            return target
        # Clicking a footer action moves focus away from its input before clicked is emitted.
        if QApplication.focusWidget() in (self.add_related_button, self.remove_related_button):
            return self._related_focus_target if self._related_focus_target in self.blocks else None
        return None

    def remove_focused_related(self):
        if not self.form_focus.active:
            return
        target = self.related_action_block()
        if target not in self.blocks[1:]:
            target = self.blocks[-1] if len(self.blocks) > 1 else None
        if target in self.blocks[1:] and self._can_change_form():
            self.remove_related(target)

    def remove_related(self, block):
        if block not in self.blocks[1:]:
            return
        if block.has_content() and not confirm_remove(self, "删除当前关联词及其全部释义？"):
            self.restore_input_focus(block.word)
            return
        position = self.blocks.index(block)
        self.blocks.remove(block)
        if self.active_block is block:
            self.active_block = None
        self.form_layout.removeWidget(block)
        block.hide()
        block.deleteLater()
        self.update_related_actions()
        self.form_changed()
        self.update_tab_order()
        self.restore_input_focus(self.blocks[min(position, len(self.blocks) - 1)].word)

    def add_sense(self):
        block = self.focused_block()
        if block and self._can_change_form():
            block.add_sense(after=block.focused_row())

    def remove_sense(self):
        block = self.focused_block()
        row = block.focused_row() if block else None
        if row is not None and self._can_change_form():
            block.remove_sense(row)

    def focused_block(self):
        focus = QApplication.focusWidget()
        # Combo popups are separate windows but retain their owning input as a parent.
        while focus is not None:
            if focus in self.blocks:
                return focus
            focus = focus.parentWidget()
        return None

    def _save_form(self):
        if not self.current_table().delegate.submit():
            return False
        is_new = self.edit_id is None
        try:
            draft = self.form_value()
            saved = self.repository.save(draft)
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
            return self.study_page.table.delegate.submit()
        if self.table.delegate.editor:
            return self.current_table().delegate.submit()
        self.set_form_collapsed(False)
        return self._save_form()

    def resolve_dirty(self):
        if not self.is_dirty():
            return True
        dialog = UnsavedInputDialog(self)
        try:
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return False
            if dialog.choice == 'save':
                return self._save_form()
            if dialog.choice == 'discard':
                self.reset_form()
                return True
            return False
        finally:
            dialog.deleteLater()

    def cancel_form(self):
        if self.edit_id is None and not self.is_dirty():
            return
        prompt = "取消当前编辑？" if self.edit_id is not None else "清空当前输入？"
        dialog = DraftResetDialog(self.cancel_button, prompt, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.reset_form()
            QTimer.singleShot(0, lambda: self.restore_input_focus(self.blocks[0].word))
        else:
            self.restore_input_focus(self.cancel_button)
        dialog.deleteLater()

    def before_inline(self):
        return True

    def inline_changed(self, editing):
        if not editing and not self._closing:
            QTimer.singleShot(0, self.refresh_after_inline)

    def inline_content_removed(self):
        entry_id, owner, _, column = self.current_table().delegate.removed_field
        if owner == -1 and column == 1:
            self.revealed.discard(entry_id)
            self.study_page.revealed.discard(entry_id)
            self.study_page._known_ids.discard(entry_id)
            for hidden in self._search_hidden.values():
                hidden.discard(entry_id)
            if self.duplicate_id == entry_id:
                self.duplicate_id = None
            if self.edit_id == entry_id:
                if self.is_dirty():
                    self.edit_id = None
                    self.form_title.setText("添加单词")
                    self.form_changed()
                else:
                    self.reset_form()
            if self.filter_name != 'study17' and self.study_page.context is not None:
                self.study_page.load(self.study_page.context)
            self.history_panel.refresh()
        self.refresh_after_inline()

    def refresh_after_inline(self):
        if self._closing or self.current_table().delegate.editor is not None:
            return
        if self.current_table().dragging:
            QTimer.singleShot(50, self.refresh_after_inline)
            return
        if self.filter_name == 'study17':
            self.study_page.load(self.study_page.context)
        else:
            self.refresh()

    def prepare_list_edit(self, entry_id):
        self.refresh_after_inline()
        row = next((row for row in self.current_table().model().rows if row.entry.id == entry_id), None)
        if row is None or not row.hidden:
            return
        if self.filter_name == 'study17':
            self.study_page.reveal(entry_id, True)
        elif self.search_box.text().strip():
            self._search_hidden[self.filter_name].discard(entry_id)
            self.refresh()
        else:
            self.reveal(entry_id, True)

    def commit_cell(self, row, column, value):
        try:
            if column == 1:
                spelling = value
                if isinstance(value, RelatedWord):
                    spelling = value.text
                elif isinstance(value, tuple):
                    spelling = value[0]
                if not spelling.strip():
                    self.repository.remove_inline_content(row.entry, row.owner, None, self.study_page.context)
                    self.show_notice(f"已删除 {row.word.text}")
                    return CELL_REMOVED
            if isinstance(value, RelatedWord):
                self.repository.replace_word_content(row.entry, row.owner, value)
            elif column == 3 and not value.strip() and len(row.word.senses) > 1:
                self.repository.remove_inline_content(row.entry, row.owner, row.sense, self.study_page.context)
                self.show_notice("已删除释义")
                return CELL_REMOVED
            else:
                entry = self.repository.get(row.entry.id)
                target = entry if row.owner == -1 else entry.related[row.owner]
                if column == 1:
                    target.text = spelling
                    if isinstance(value, tuple):
                        target.phonetic = value[1]
                elif column == 2:
                    target.senses[row.sense].pos = value
                elif column == 3:
                    target.senses[row.sense].translation = value
                self.repository.save(entry)
        except DuplicateWord as error:
            self.duplicate_id = error.entry_id
            self.show_notice(str(error), True, duplicate=True)
            return False
        except WordContentValidationError as error:
            self.toast.show_message(str(error), self.current_table().delegate.editor)
            return False
        except Exception as error:
            self.show_notice(str(error), True)
            return False
        self.show_notice("修改已保存")
        return True

    def edit_selected(self):
        if self.filter_name == 'study17':
            return
        index = self.table.currentIndex()
        row = index.data(Qt.ItemDataRole.UserRole)
        if row is not None and self.table.selectionModel().isSelected(index):
            self.list_action(row.entry.id, 'edit')

    def list_action(self, entry_id, action):
        delegate = self.current_table().delegate
        if not delegate.submit() or delegate.target_after_removal((entry_id, -1, 0, 1)) is None:
            return
        if action == "edit":
            if self.edit_id == entry_id:
                self.set_form_collapsed(False)
                self.blocks[0].word.setFocus()
                return
            if not self.resolve_dirty():
                return
            self.reset_form(self.repository.get(entry_id))
            self.blocks[0].word.setFocus()
            return
        if self.edit_id == entry_id and action == "trash":
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
                self.repository.delete(entry_id, self.study_page.context or StudyContext.now())
                if self.study_page.context is not None:
                    self.study_page.load(self.study_page.context)
                self.revealed.discard(entry_id)
                self.show_notice(f"已删除 {entry.text}")
            self.refresh()
        except Exception as error:
            self.show_notice(str(error), True)

    def locate_duplicate(self):
        if self.duplicate_id is not None:
            if not self.current_table().delegate.submit():
                return
            self.search_box.clear()
            # Locating a duplicate keeps its rejected draft available for correction.
            self.filter_name = "all"
            self.refresh()
            for number, row in enumerate(self.model.rows):
                if row.entry.id == self.duplicate_id:
                    index = self.model.index(number, 1)
                    self.table.setCurrentIndex(index)
                    self.table.scrollTo(index)
                    break

    def open_settings(self):
        if not self.current_table().delegate.submit():
            return
        previous = (self._last_form_focus if not self.form_collapsed else None) or QApplication.focusWidget()
        self.shortcuts.set_suspended(True)
        try:
            dialog = SettingsDialog(self.shortcuts, self, clear_content=self.clear_content)
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

    def clear_content(self, command):
        self.repository.clear_content(command)
        if command != 'clean vocabulary only':
            # Reset only after commit, so a failed deletion retains the active clock.
            self.study_page.timer.reset()
            self.study_page.revealed.clear()
            self.study_page._known_ids.clear()
        entries = self.repository.list_entries()
        remaining_ids = {entry.id for entry in entries}
        self.revealed.intersection_update(remaining_ids)
        for hidden in self._search_hidden.values():
            hidden.intersection_update(remaining_ids)
        if self.edit_id is not None and self.edit_id not in remaining_ids:
            self.reset_form()
        self.study_page.load(self.study_page.context or StudyContext.now())
        self.history_panel.refresh()
        self.refresh(entries)

    def export_words(self):
        if self.busy_overlay.active:
            return
        if self.filter_name == 'study17':
            return self.export_study_progress()
        if not self.current_table().delegate.submit():
            return
        self.shortcuts.set_suspended(True)
        try:
            path, _ = QFileDialog.getSaveFileName(
                self, "导出当前页面所有单词", "kkMemorizeVocabulary.json", "JSON 文件 (*.json)")
            if not path:
                return
            if not Path(path).suffix:
                path += ".json"
            entries = run_repository_task(self.repository, self.busy_overlay,
                                          '正在导出单词…', 'list_entries')
            entries = self._filter_entries(entries)
            self.busy_overlay.run('正在导出单词…', lambda: write_entries(path, entries))
            page = {"pending": "待记", "learned": "已记下", "all": "全部"}[self.filter_name]
            QMessageBox.information(self, "导出完成", f"已导出“{page}”页面当前筛选结果的 {len(entries)} 个主词。")
        except Exception as error:
            logging.exception("Could not export words")
            QMessageBox.critical(self, "导出失败", str(error))
        finally:
            self.shortcuts.set_suspended(False)

    def import_words(self):
        if self.busy_overlay.active:
            return
        if self.filter_name == 'study17':
            return self.import_study_progress()
        self.shortcuts.set_suspended(True)
        try:
            path, _ = QFileDialog.getOpenFileName(self, "导入单词", "", "JSON 文件 (*.json)")
            if not path:
                return
            try:
                entries = self.busy_overlay.run('正在读取导入文件…', lambda: read_word_or_study_file(path))
            except (ValidationError, UnicodeError):
                QMessageBox.critical(self, '导入失败', '数据结构不合法，导入失败')
                return
            if isinstance(entries, StudyBackup):
                return self.import_study_progress(entries, require_choice=True)
            if not self.current_table().delegate.submit() or not self.resolve_dirty():
                return
            result = run_repository_task(self.repository, self.busy_overlay,
                                         '正在导入单词…', 'import_entries', entries)
            if self.edit_id is not None:
                self.reset_form()
            self.refresh_after_transfer()
            replaced_ids = self.resolve_word_conflicts(result)
            self.refresh_after_transfer()
            QMessageBox.information(
                self, "导入完成", f"新增 {result.inserted} 个主词，发现 {len(result.conflicts)} 条重复记录，"
                f"覆盖 {len(replaced_ids)} 个主词。")
        except Exception as error:
            logging.exception("Could not import words")
            QMessageBox.critical(self, "导入失败", str(error))
        finally:
            self.shortcuts.set_suspended(False)

    def refresh_after_transfer(self, overlay=None):
        overlay = overlay or self.busy_overlay
        entries = (run_repository_task(self.repository, overlay, '正在更新单词列表…', 'list_entries')
                   if self.filter_name != 'study17' else None)
        with overlay.display('正在更新单词列表…'):
            self.refresh(entries)

    def resolve_word_conflicts(self, result):
        if not result.conflicts:
            return set()
        dialog = ImportConflictDialog(self.repository, result, self)
        dialog.resolved.connect(lambda: self.refresh_after_transfer(dialog.busy_overlay))
        try:
            dialog.exec()
            return dialog.replaced_names
        finally:
            dialog.deleteLater()

    def export_study_progress(self):
        if self.busy_overlay.active:
            return
        if not self.current_table().delegate.submit():
            return
        self.shortcuts.set_suspended(True)
        try:
            path, _ = QFileDialog.getSaveFileName(
                self, '导出学习进度', 'kkMemorizeVocabulary-study17.json', 'JSON 文件 (*.json)')
            if not path:
                return
            if not Path(path).suffix:
                path += '.json'
            backup = run_repository_task(self.repository, self.busy_overlay,
                                         '正在导出学习进度…', 'study_backup')
            self.busy_overlay.run('正在导出学习进度…', lambda: write_study_backup(path, backup))
            QMessageBox.information(self, '导出完成', f'已导出 {len(backup.words)} 个主词及全部学习进度。')
        except Exception as error:
            logging.exception('Could not export study progress')
            QMessageBox.critical(self, '导出失败', str(error))
        finally:
            self.shortcuts.set_suspended(False)

    def choose_study_import_mode(self):
        dialog = QMessageBox(self)
        dialog.setWindowTitle('导入学习进度')
        dialog.setText('导入学习进度将覆盖本机全部学习进度及任务历史，请选择导入方式。'
                       if self.repository.has_study_progress() else '文件包含学习进度，请选择导入方式。')
        full = dialog.addButton('导入单词和进度', QMessageBox.ButtonRole.AcceptRole)
        words = dialog.addButton('仅导入单词', QMessageBox.ButtonRole.ActionRole)
        cancel = dialog.addButton('取消', QMessageBox.ButtonRole.RejectRole)
        dialog.findChild(QDialogButtonBox).setCenterButtons(True)
        dialog.setDefaultButton(cancel)
        dialog.setEscapeButton(cancel)
        dialog.exec()
        if dialog.clickedButton() is full:
            return 'full'
        if dialog.clickedButton() is words:
            return 'words'
        return 'cancel'

    def import_study_progress(self, backup=None, *, require_choice=False):
        if self.busy_overlay.active:
            return
        self.shortcuts.set_suspended(True)
        result = None
        try:
            if backup is None:
                path, _ = QFileDialog.getOpenFileName(self, '导入学习进度', '', 'JSON 文件 (*.json)')
                if not path:
                    return
                backup = self.busy_overlay.run('正在读取学习进度…', lambda: read_study_backup(path))
            mode = (self.choose_study_import_mode()
                    if require_choice or self.repository.has_study_progress() else 'full')
            if mode == 'cancel':
                return
            if not self.current_table().delegate.submit() or not self.resolve_dirty():
                return
            if mode == 'full':
                self.study_page.timer.pause()
            result = run_repository_task(self.repository, self.busy_overlay,
                                         '正在导入单词…', 'import_entries', backup.words)
            if self.edit_id is not None:
                self.reset_form()
            replaced = self.resolve_word_conflicts(result)
            if mode == 'full':
                run_repository_task(self.repository, self.busy_overlay,
                                    '正在恢复学习进度…', 'replace_study_progress', backup)
                self.study_page.timer.reset()
                self.study_page.revealed.clear()
                self.study_page.load(StudyContext.now())
                status = f'已恢复 {len(backup.records)} 条学习进度及 {len(backup.task_history)} 条任务历史。'
            else:
                self.study_page.load(self.study_page.context or StudyContext.now())
                status = '本机学习进度已保留。'
            self.refresh_after_transfer()
            QMessageBox.information(self, '导入完成',
                f'新增 {result.inserted} 个主词，发现 {len(result.conflicts)} 条重复记录，'
                f'覆盖 {len(replaced)} 个主词。\n{status}')
        except Exception as error:
            logging.exception('Could not import study progress')
            detail = str(error)
            if result is not None:
                self.study_page.load(self.study_page.context or StudyContext.now())
                self.refresh_after_transfer()
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
        if hasattr(self, 'history_panel'):
            self.history_panel.update_height()

    def closeEvent(self, event):
        if not self.current_table().delegate.submit() or not self.resolve_dirty():
            event.ignore()
            return
        try:
            self.study_page.timer.pause()
        except Exception as error:
            self.show_notice(str(error), True)
            event.ignore()
            return
        self.study_page.timer.dispose()
        self._closing = True
        event.accept()

