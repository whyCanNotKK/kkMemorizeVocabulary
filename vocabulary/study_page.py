"""Dedicated study projection; refreshing is the only way to change its clock."""
from PySide6.QtCore import QModelIndex, Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from .learning import StudyContext
from .table import WordTable, WordTableModel
from .theme import ACCENT, MUTED, icon
from .widgets import button


class StudyPage(QWidget):
    error = Signal(str)

    def __init__(self, repository, edit_callback, parent=None):
        super().__init__(parent)
        self.repository = repository
        self.context = None
        self.tasks = []
        self.total_tasks = 0
        self.revealed = set()
        self._known_ids = set()
        self.global_hidden = True
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        toolbar = QHBoxLayout()
        self.refresh_button = button("", quiet=True)
        self.refresh_button.setIcon(icon("refresh"))
        self.refresh_button.setFixedSize(36, 36)
        self.refresh_button.setAccessibleName("刷新十七天背单词任务")
        self.refresh_button.setToolTip("重新读取任务，并更新操作日期和上午/下午时段")
        self.refresh_button.clicked.connect(self.reload)
        toolbar.addWidget(self.refresh_button)
        self.period_label = QLabel()
        self.period_label.setObjectName("muted")
        toolbar.addWidget(self.period_label)
        toolbar.addStretch()
        self.task_count = QLabel()
        self.task_count.setTextFormat(Qt.TextFormat.RichText)
        self.task_count.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        toolbar.addWidget(self.task_count)
        self.update_task_count()
        layout.addLayout(toolbar)
        self.model = WordTableModel(edit_callback, self)
        self.model.study_groups = {}
        self.table = WordTable(self.model, lambda: True)
        self.table.set_page_state("study17", False, 0)
        self.table.empty_text = "当前时段没有待完成的单词\n切换日期或时段后，请点击刷新"
        self.table.revealRequested.connect(self.reveal)
        self.table.actionRequested.connect(self.complete)
        layout.addWidget(self.table)

    def reload(self):
        return self.load(StudyContext.now(), generate=True)

    def load(self, context, *, generate=False):
        if not self.table.delegate.submit():
            return False
        try:
            tasks = (self.repository.prepare_study(context) if generate else
                     self.repository.study_tasks(context))
            total = self.repository.study_task_total(context)
        except Exception as error:
            self.error.emit(str(error))
            return False
        self.context, self.tasks = context, tasks
        self.total_tasks = total
        ids = {task.entry.id for task in tasks}
        if not self.global_hidden:
            self.revealed.update(ids - self._known_ids)
        self._known_ids.update(ids)
        self.period_label.setText(context.label)
        self.render()
        return True

    def render(self):
        self.update_task_count()
        with self.table.preserve_view():
            self.model.study_groups = {task.entry.id: task.group_number for task in self.tasks}
            # Group numbers can grow beyond the original star column's width.
            width = max((self.table.fontMetrics().horizontalAdvance(str(task.group_number)) + 20
                         for task in self.tasks), default=38)
            self.table.setColumnWidth(0, max(38, width))
            self.model.populate([task.entry for task in self.tasks], self.revealed)
            self.table.update_spans()

    def update_task_count(self):
        remaining = len(self.tasks)
        self.task_count.setText(
            f'<span style="color:{MUTED}">当前单词任务：</span>'
            f'<span style="color:{ACCENT};font-size:22px;font-weight:700">{remaining}</span>'
            f'<span style="color:{MUTED}">/{self.total_tasks}</span>')
        self.task_count.setAccessibleName(f"当前单词任务：{remaining}/{self.total_tasks}")

    def reveal(self, word_id, expanded):
        if expanded:
            self.revealed.add(word_id)
        else:
            self.revealed.discard(word_id)
        self.render()

    def set_global_hidden(self, hidden):
        self.global_hidden = hidden
        ids = {task.entry.id for task in self.tasks}
        if hidden:
            self.revealed.difference_update(ids)
        else:
            self.revealed.update(ids)
        self.render()

    def complete(self, word_id, action):
        position = next((i for i, task in enumerate(self.tasks) if task.entry.id == word_id), None)
        if action != "study" or self.context is None or position is None:
            return
        if not self.table.delegate.submit():
            return
        try:
            self.repository.complete_study(word_id, self.context)
        except Exception as error:
            self.error.emit(str(error))
            return
        self.tasks = [task for task in self.tasks if task.entry.id != word_id]
        self.revealed.discard(word_id)
        self.render()
        if self.tasks:
            target = self.tasks[min(position, len(self.tasks) - 1)]
            self.table.select_entry(target.entry.id, scroll=True)
        else:
            self.table.clearSelection()
            self.table.setCurrentIndex(QModelIndex())
        self.table.setFocus()
