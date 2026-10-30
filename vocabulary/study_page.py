"""Dedicated study projection; refreshing is the only way to change its clock."""
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from .learning import StudyContext
from .table import WordTable, WordTableModel
from .theme import icon
from .widgets import button


class StudyPage(QWidget):
    error = Signal(str)

    def __init__(self, repository, parent=None):
        super().__init__(parent)
        self.repository = repository
        self.context = None
        self.tasks = []
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
        layout.addLayout(toolbar)
        self.model = WordTableModel(lambda *args: False, self)
        self.model.study_groups = {}
        self.table = WordTable(self.model, lambda: False)
        self.table.set_page_state("study17", False, 0)
        self.table.empty_text = "当前时段没有待完成的单词\n切换日期或时段后，请点击刷新"
        self.table.revealRequested.connect(self.reveal)
        self.table.actionRequested.connect(self.complete)
        layout.addWidget(self.table)

    def reload(self):
        return self.load(StudyContext.now(), generate=True)

    def load(self, context, *, generate=False):
        try:
            tasks = (self.repository.prepare_study(context) if generate else
                     self.repository.study_tasks(context))
        except Exception as error:
            self.error.emit(str(error))
            return False
        self.context, self.tasks = context, tasks
        ids = {task.entry.id for task in tasks}
        if not self.global_hidden:
            self.revealed.update(ids - self._known_ids)
        self._known_ids.update(ids)
        self.period_label.setText(f"{context.label} · 待完成 {len(tasks)} 词")
        self.render()
        self.table.scrollToTop()
        return True

    def render(self):
        position = self.table.verticalScrollBar().value()
        self.model.study_groups = {task.entry.id: task.group_number for task in self.tasks}
        # Group numbers can grow beyond the original star column's width.
        width = max((self.table.fontMetrics().horizontalAdvance(str(task.group_number)) + 20
                     for task in self.tasks), default=38)
        self.table.setColumnWidth(0, max(38, width))
        self.model.populate([task.entry for task in self.tasks], self.revealed)
        self.table.update_spans()
        self.table.verticalScrollBar().setValue(position)

    def reveal(self, word_id, expanded):
        if expanded:
            self.revealed.add(word_id)
        else:
            self.revealed.discard(word_id)
        self.render()

    def set_global_hidden(self, hidden):
        self.global_hidden = hidden
        self.revealed = set() if hidden else {task.entry.id for task in self.tasks}
        self.render()

    def complete(self, word_id, action):
        if action != "study" or self.context is None or not any(
                task.entry.id == word_id for task in self.tasks):
            return
        try:
            self.repository.complete_study(word_id, self.context)
        except Exception as error:
            self.error.emit(str(error))
            return
        self.tasks = [task for task in self.tasks if task.entry.id != word_id]
        self.revealed.discard(word_id)
        self.period_label.setText(f"{self.context.label} · 待完成 {len(self.tasks)} 词")
        self.render()
