"""Editable comparison drafts; no database writes until Save All succeeds."""
from copy import deepcopy

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QScrollArea, QVBoxLayout, QWidget)

from .domain import RelatedWord, Sense, clean_word_content
from .widgets import FloatingNotice, SenseRow, button


class LinkQuestion(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('关联词内容不一致')
        self.choice = None
        layout = QVBoxLayout(self)
        label = QLabel('新增的关联词有对应的主词，且释义不一致，是否对比编辑？')
        label.setWordWrap(True)
        layout.addWidget(label)
        actions = QHBoxLayout()
        actions.addStretch()
        self.yes = button('是')
        self.no = button('否')
        self.yes.setAutoDefault(True)
        self.no.setAutoDefault(True)
        self.yes.setDefault(True)
        self.yes.clicked.connect(lambda: self.choose(True))
        self.no.clicked.connect(lambda: self.choose(False))
        actions.addWidget(self.yes)
        actions.addWidget(self.no)
        layout.addLayout(actions)
        self.yes.setFocus()

    def choose(self, value):
        self.choice = value
        self.accept()


class ContentEditor(QFrame):
    structureChanged = Signal()

    def __init__(self, word, parent=None):
        super().__init__(parent)
        self.setObjectName('conflictCard')
        self.rows = []
        self.outer = QVBoxLayout(self)
        heading = QHBoxLayout()
        self.spelling = QLineEdit(word.text)
        self.spelling.setReadOnly(True)
        self.spelling.setAccessibleName('拼写（只读）')
        self.phonetic = QLineEdit(word.phonetic)
        self.phonetic.setPlaceholderText('音标')
        self.phonetic.setAccessibleName('音标')
        heading.addWidget(self.spelling, 1)
        heading.addWidget(self.phonetic, 1)
        self.outer.addLayout(heading)
        self.senses = QVBoxLayout()
        self.outer.addLayout(self.senses)
        self.set_content(word)

    def set_content(self, word):
        self.phonetic.setText(word.phonetic)
        for row in list(self.rows):
            self.senses.removeWidget(row)
            row.hide()
            row.deleteLater()
        self.rows = []
        for sense in word.senses:
            self.add_row(deepcopy(sense))

    def add_row(self, sense=None, after=None):
        row = SenseRow(sense or Sense())
        row.copy_button.hide()
        row.copy_button.setSizePolicy(row.add_button.sizePolicy())
        row.drag_handle.hide()
        row.up = button('↑', quiet=True)
        row.down = button('↓', quiet=True)
        for control, step, label in ((row.up, -1, '上移释义'), (row.down, 1, '下移释义')):
            control.setFixedWidth(25)
            control.setToolTip(label)
            control.setAccessibleName(label)
            control.setAutoDefault(False)
            control.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
            control.clicked.connect(lambda checked=False, row=row, step=step: self.move_row(row, step))
            row.layout().addWidget(control, 0, Qt.AlignmentFlag.AlignTop)
        row.add_button.setAutoDefault(False)
        row.remove_button.setAutoDefault(False)
        row.addRequested.connect(lambda: self.add_row(after=row))
        row.removeRequested.connect(lambda: self.remove_row(row))
        index = self.rows.index(after) + 1 if after is not None else len(self.rows)
        self.rows.insert(index, row)
        self.senses.insertWidget(index, row)
        self.refresh_actions()
        return row

    def refresh_actions(self):
        for index, row in enumerate(self.rows):
            row.remove_button.setEnabled(len(self.rows) > 1)
            row.up.setEnabled(index > 0)
            row.down.setEnabled(index + 1 < len(self.rows))
        self.structureChanged.emit()

    def input_order(self):
        return [self.spelling, self.phonetic, *[field for row in self.rows
                                               for field in (row.pos, row.translation)]]

    def remove_row(self, row):
        if len(self.rows) <= 1:
            return
        self.rows.remove(row)
        self.senses.removeWidget(row)
        row.hide()
        row.deleteLater()
        self.refresh_actions()

    def move_row(self, row, step):
        index = self.rows.index(row)
        target = index + step
        if not 0 <= target < len(self.rows):
            return
        self.rows.pop(index)
        self.rows.insert(target, row)
        self.senses.removeWidget(row)
        self.senses.insertWidget(target, row)
        self.refresh_actions()

    def value(self):
        return clean_word_content(RelatedWord(self.spelling.text(), [row.value() for row in self.rows],
                                               self.phonetic.text()))


class RelatedConflictDialog(QDialog):
    def __init__(self, plan, commit, parent=None):
        super().__init__(parent)
        self.plan, self.commit = plan, commit
        self.result_value = None
        self.cards = []
        self.setWindowTitle('关联词对比编辑')
        self.resize(1040, 640)
        self.setMinimumSize(860, 280)
        layout = QVBoxLayout(self)
        summary = QLabel('编辑后可采用任一侧内容，也可保留差异；点击“保存全部”统一保存。')
        summary.setWordWrap(True)
        layout.addWidget(summary)
        headers = QHBoxLayout()
        for title in ('目标主词', '新增关联词'):
            label = QLabel(title)
            label.setObjectName('section')
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            headers.addWidget(label, 1)
        layout.addLayout(headers)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        content = QWidget()
        pairs = QVBoxLayout(content)
        _, targets = plan.drafts()
        for conflict in plan.conflicts:
            pair = QHBoxLayout()
            left = ContentEditor(targets[conflict.target_id])
            right = ContentEditor(plan.draft.related[conflict.related_index])
            for editor, label, other in ((left, '采用左侧', right), (right, '采用右侧', left)):
                control = button(label)
                control.setAutoDefault(False)
                control.clicked.connect(lambda checked=False, source=editor, target=other:
                                        self.adopt(source, target))
                editor.outer.addWidget(control)
                editor.adopt_button = control
                editor.structureChanged.connect(self.update_tab_order)
            pair.addWidget(left, 1, Qt.AlignmentFlag.AlignTop)
            pair.addWidget(right, 1, Qt.AlignmentFlag.AlignTop)
            pairs.addLayout(pair)
            self.cards.append((conflict, left, right))
        pairs.addStretch()
        self.scroll.setWidget(content)
        layout.addWidget(self.scroll, 1)
        actions = QHBoxLayout()
        actions.addStretch()
        self.save_button = button('保存全部')
        self.save_button.setObjectName('primary')
        self.cancel_button = button('取消')
        for control in (self.save_button, self.cancel_button):
            control.setAutoDefault(False)
            actions.addWidget(control)
        self.save_button.clicked.connect(self.save_all)
        self.cancel_button.clicked.connect(self.reject)
        layout.addLayout(actions)
        self.toast = FloatingNotice(self)
        self.update_tab_order()

    def update_tab_order(self):
        if not hasattr(self, 'cancel_button'):
            return
        fields = []
        for _, left, right in self.cards:
            for editor in (left, right):
                fields.extend(editor.input_order())
                fields.append(editor.adopt_button)
        fields.extend((self.save_button, self.cancel_button))
        for first, second in zip(fields, fields[1:]):
            QWidget.setTabOrder(first, second)

    def adopt(self, source, target):
        try:
            target.set_content(source.value())
        except Exception as error:
            self.toast.show_message(str(error), source)

    def save_all(self):
        try:
            draft, targets = self.plan.drafts()
            for conflict, left, right in self.cards:
                local, related = left.value(), right.value()
                target = targets[conflict.target_id]
                target.phonetic, target.senses = local.phonetic, local.senses
                draft.related[conflict.related_index] = related
            self.result_value = self.commit(self.plan, draft, targets)
        except Exception as error:
            self.toast.show_message(str(error), self.save_button)
            return
        self.accept()
