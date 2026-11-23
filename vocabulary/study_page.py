"""Study projection with a frozen date/period and a separate live task clock."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta

from PySide6.QtCore import QModelIndex, QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from .learning import BEIJING, StudyContext, format_study_duration
from .study_timer import StudyTimer
from .study_completion import StudyCompletionNotice
from .table import WordTable, WordTableModel
from .theme import ACCENT, MUTED, icon
from .widgets import button


class StudyPage(QWidget):
    error = Signal(str)
    visibilityChanged = Signal(bool)

    def __init__(self, repository, edit_callback, parent=None):
        super().__init__(parent)
        self.repository = repository
        self.context = None
        self._last_study_end = None
        self.tasks = []
        self._entry_cache = {}
        self._content_revision = getattr(repository, 'content_revision', 0)
        self._spelling_settings = repository.study_spelling_hide_settings()
        self._spelling_settings_revision = getattr(repository, 'spelling_settings_revision', 0)
        self.completed_tasks = []
        self.show_completed = False
        self.total_tasks = 0
        self.revealed = set()
        self._known_ids = set()
        self.global_hidden = True
        self.timer = StudyTimer(repository, self)
        self.timer.error.connect(self.error)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(12, 0, 12, 0)
        self.refresh_button = button("", quiet=True)
        self.refresh_button.setIcon(icon("refresh"))
        self.refresh_button.setFixedSize(36, 36)
        self.refresh_button.setAccessibleName("刷新十七天背单词任务")
        self.refresh_button.setToolTip("刷新")
        self.refresh_button.clicked.connect(self.reload)
        toolbar.addWidget(self.refresh_button)
        self.timer_button = button('', quiet=True)
        self.timer_button.setFixedSize(36, 36)
        self.timer_button.clicked.connect(self.toggle_timer)
        self.period_label = QLabel()
        self.period_label.setObjectName("muted")
        toolbar.addWidget(self.period_label)
        self.elapsed_icon = QLabel()
        self.elapsed_icon.setPixmap(icon('stopwatch').pixmap(20, 20))
        self.elapsed_icon.setFixedSize(20, 20)
        self.elapsed_icon.setToolTip('用时')
        self.elapsed_icon.setAccessibleName('用时')
        toolbar.addWidget(self.elapsed_icon)
        self.elapsed_label = QLabel('0 s')
        self.elapsed_label.setObjectName('muted')
        toolbar.addWidget(self.elapsed_label)
        self.interval_label = QLabel()
        self.interval_label.setObjectName('muted')
        toolbar.addWidget(self.timer_button)
        toolbar.addWidget(self.interval_label)
        toolbar.addStretch()
        self.review_count = QLabel()
        self.new_count = QLabel()
        for label in (self.review_count, self.new_count):
            label.setTextFormat(Qt.TextFormat.RichText)
            label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            toolbar.addWidget(label)
        self.completed_button = button('', quiet=True)
        self.completed_button.setFixedSize(36, 36)
        self.completed_button.setIconSize(QSize(20, 20))
        self.completed_button.setCheckable(True)
        self.completed_button.clicked.connect(self.toggle_completed)
        toolbar.addWidget(self.completed_button)
        layout.addLayout(toolbar)
        self.timer.changed.connect(self.update_timer_button)
        self.timer.changed.connect(self.update_task_count)
        self.timer.changed.connect(self.update_elapsed)
        self.update_timer_button()
        self.update_task_count()
        self.update_completed_button()
        self.model = WordTableModel(edit_callback, self, study=True)
        self.table = WordTable(self.model, lambda: True)
        self.table.set_hide_word_while_spelling(self._spelling_settings)
        self.table.set_page_state("study17", False, 0)
        self.table.empty_text = ''
        self.completion_notice = StudyCompletionNotice(
            self.refresh_button.icon(), self.refresh_button.iconSize(), self.table.viewport())
        self.completion_notice.refreshRequested.connect(self.reload)
        self.table.revealRequested.connect(self.reveal)
        self.table.actionRequested.connect(self.complete)
        layout.addWidget(self.table)
        self._clock_display = QTimer(self)
        self._clock_display.setInterval(1000)
        self._clock_display.timeout.connect(self.update_elapsed)
        self._interval_display = QTimer(self)
        self._interval_display.setInterval(70_000)
        self._interval_display.timeout.connect(self.update_interval)

    def reload(self):
        return self.load(StudyContext.now(), generate=True)

    def load(self, context, *, generate=False):
        self.table.reset_study_input()
        if not self.table.delegate.submit():
            return False
        try:
            revision = getattr(self.repository, 'content_revision', 0)
            if revision != self._content_revision:
                self._entry_cache.clear()
            spelling_settings = self.repository.study_spelling_hide_settings()
            tasks = (self.repository.prepare_study(context, entries_by_id=self._entry_cache) if generate else
                     self.repository.study_tasks(context, entries_by_id=self._entry_cache))
            total = self.repository.study_task_total(context)
            completed = self.repository.study_completed_tasks(context, entries_by_id=self._entry_cache) if self.show_completed else []
            last_study_end = self.repository.latest_study_end()
            self.timer.bind(context)
            if not tasks:
                self.timer.pause()
        except Exception as error:
            self.error.emit(str(error))
            return False
        self._content_revision = getattr(self.repository, 'content_revision', 0)
        self._spelling_settings = spelling_settings
        self._spelling_settings_revision = getattr(self.repository, 'spelling_settings_revision', 0)
        needed = {task.entry.id for task in tasks + completed}
        self._entry_cache = {key: value for key, value in self._entry_cache.items() if key in needed}
        self.context, self.tasks = context, tasks
        self._last_study_end = last_study_end
        self.completed_tasks = completed
        self.total_tasks = total
        ids = {task.entry.id for task in tasks}
        if not self.global_hidden:
            self.revealed.update(ids - self._known_ids)
        self._known_ids.update(ids)
        if self.show_completed:
            self.revealed.update(task.entry.id for task in completed)
        self.period_label.setText(context.label)
        self.render()
        self.update_interval()
        return True

    def render(self):
        revision = getattr(self.repository, 'spelling_settings_revision', 0)
        if revision != self._spelling_settings_revision:
            self._spelling_settings = self.repository.study_spelling_hide_settings()
            self._spelling_settings_revision = revision
        self.table.set_hide_word_while_spelling(self._spelling_settings)
        self.update_task_count()
        self.update_timer_button()
        self.update_elapsed()
        visible = self.tasks + (self.completed_tasks if self.show_completed else [])
        with self.table.preserve_view():
            self.model.study_tasks = {task.entry.id: task for task in visible}
            self.model.study_completed_ids = {task.entry.id for task in self.completed_tasks}
            # Group numbers can grow beyond the original star column's width.
            width = max((self.table.fontMetrics().horizontalAdvance(str(task.group_number)) + 20
                         for task in visible), default=38)
            self.table.setColumnWidth(0, max(38, width))
            self.model.populate([task.entry for task in visible], self.revealed)
            self.table.update_spans()
        history = self.timer.history
        show_notice = self.model.rowCount() == 0
        if show_notice:
            if history is not None and history.status:
                self.completion_notice.set_history(history)
            else:
                self.completion_notice.set_empty()
        self.completion_notice.setVisible(show_notice)
        self.table.viewport().update()

    def update_task_count(self):
        review = sum(task.is_review for task in self.tasks)
        history = self.timer.history
        for label, text, remaining, total in (
                (self.review_count, '复习', review, history.review_total if history else review),
                (self.new_count, '新词', len(self.tasks) - review,
                 history.new_total if history else len(self.tasks) - review)):
            label.setText(f'<span style="color:{MUTED}">{text}：</span>'
                          f'<span style="color:{ACCENT};font-size:22px;font-weight:700">{remaining}</span>'
                          f'<span style="color:{MUTED}">/{total}</span>')
            label.setAccessibleName(f'{text}：剩余{remaining}/{total}')

    def update_elapsed(self):
        duration = format_study_duration(self.timer.duration_seconds())
        self.elapsed_label.setText(duration)
        self.elapsed_label.setAccessibleName(f'用时：{duration}')

    def update_interval(self):
        interval = '—'
        if self._last_study_end is not None:
            minutes = (datetime.now(BEIJING) - self._last_study_end) // timedelta(minutes=1)
            if minutes >= 0:
                days, minutes = divmod(minutes, 24 * 60)
                hours, minutes = divmod(minutes, 60)
                interval = ' '.join(f'{value} {unit}' for value, unit in
                                    ((days, 'd'), (hours, 'h'), (minutes, 'm')) if value) or '0 m'
        self.interval_label.setText(f'学习间隔： {interval}')

    def update_completed_button(self):
        text = '隐藏已完成' if self.show_completed else '显示已完成'
        self.completed_button.setIcon(icon('circle_check' if self.show_completed else 'circle',
                                           ACCENT if self.show_completed else MUTED))
        self.completed_button.setToolTip(text)
        self.completed_button.setAccessibleName(text)
        self.completed_button.setChecked(self.show_completed)
        self.completed_button.setProperty('active', self.show_completed)
        self.completed_button.style().unpolish(self.completed_button)
        self.completed_button.style().polish(self.completed_button)

    def toggle_completed(self, show):
        if not self.table.delegate.submit():
            self.completed_button.setChecked(self.show_completed)
            return
        try:
            completed = self.repository.study_completed_tasks(self.context, entries_by_id=self._entry_cache) if show and self.context else []
        except Exception as error:
            self.completed_button.setChecked(self.show_completed)
            self.error.emit(str(error))
            return
        self.show_completed = show
        self.completed_tasks = completed
        self.revealed.update(task.entry.id for task in completed)
        self.update_completed_button()
        self.render()

    def reveal(self, word_id, expanded):
        if expanded and word_id not in self.revealed and any(task.entry.id == word_id for task in self.tasks):
            if not self.start_timer():
                return
        if expanded:
            self.revealed.add(word_id)
        else:
            self.revealed.discard(word_id)
        ids = {task.entry.id for task in self.tasks + (self.completed_tasks if self.show_completed else [])}
        if ids and ids <= self.revealed:
            self.global_hidden = False
        elif ids and not ids.intersection(self.revealed):
            self.global_hidden = True
        self.visibilityChanged.emit(not self.global_hidden)
        entry = self._entry_cache.get(word_id)
        if entry is not None:
            self.table.replace_entry(entry, expanded)
        else:
            self.render()
        if expanded:
            self.table.ensure_entry_visible(word_id)

    def set_spelling_settings(self, settings):
        self._spelling_settings = settings
        self._spelling_settings_revision = getattr(self.repository, 'spelling_settings_revision', 0)
        self.table.set_hide_word_while_spelling(settings)

    def apply_sync(self, plan):
        change = plan.change
        if plan.invalidate_study:
            self.invalidate_contents()
        if change.sort_orders:
            self.patch_sort_orders(change.sort_orders)
        if change.after is not None:
            self.patch_entry(change.after)
        elif change.before is not None:
            key = change.before.id
            self._entry_cache.pop(key, None)
            self.tasks = [task for task in self.tasks if task.entry.id != key]
            self.completed_tasks = [task for task in self.completed_tasks if task.entry.id != key]
            self.revealed.discard(key)
            self._known_ids.discard(key)

    def invalidate_contents(self):
        self._entry_cache.clear()

    def patch_sort_orders(self, orders):
        self._content_revision = getattr(self.repository, 'content_revision', 0)
        for entry_id in self._entry_cache.keys() & orders.keys():
            self._entry_cache[entry_id] = replace(self._entry_cache[entry_id], sort_order=orders[entry_id])
        def updated(task):
            return replace(task, entry=replace(task.entry, sort_order=orders[task.entry.id])) if task.entry.id in orders else task
        self.tasks = [updated(task) for task in self.tasks]
        self.completed_tasks = [updated(task) for task in self.completed_tasks]
        self.model.study_tasks = {task.entry.id: task for task in self.tasks + self.completed_tasks}

    def patch_entry(self, entry):
        self._content_revision = getattr(self.repository, 'content_revision', 0)
        if entry.id in self._entry_cache:
            self._entry_cache[entry.id] = deepcopy(entry)
        self.tasks = [replace(task, entry=deepcopy(entry)) if task.entry.id == entry.id else task for task in self.tasks]
        self.completed_tasks = [replace(task, entry=deepcopy(entry)) if task.entry.id == entry.id else task for task in self.completed_tasks]
        self.model.study_tasks = {task.entry.id: task for task in self.tasks + self.completed_tasks}

    def set_global_hidden(self, hidden):
        self.global_hidden = hidden
        self.visibilityChanged.emit(not hidden)
        ids = {task.entry.id for task in self.tasks + (self.completed_tasks if self.show_completed else [])}
        if hidden:
            self.revealed.difference_update(ids)
        else:
            self.revealed.update(ids)
        self.render()

    def complete(self, word_id, action):
        if action != "study" or self.context is None:
            return
        self.table.reset_study_input()
        if not self.table.delegate.submit():
            return
        if word_id not in self.revealed:
            return
        position = next((i for i, task in enumerate(self.tasks) if task.entry.id == word_id), None)
        if position is None:
            return
        # A paused task resumes as a lifecycle transition, including a retry of a
        # failed pause save. The first mark still creates its record atomically.
        if self.timer.history is not None and not self.timer.running and not self.start_timer():
            return
        try:
            # A first mark creates history and applies progress in one transaction.
            # Do not create the record separately as a prerequisite of this write.
            duration = self.timer.duration_seconds()
            result = self.repository.complete_study(word_id, self.context, duration_seconds=duration)
            if not result:
                self.load(self.context)
                return
            history, stage = result
            if history.status:
                self._last_study_end = datetime.fromisoformat(history.ended_at)
            self.timer.completed(history)
        except Exception as error:
            self.error.emit(str(error))
            return
        completed = replace(self.tasks[position], stage=stage)
        self.tasks = [task for task in self.tasks if task.entry.id != word_id]
        if self.show_completed:
            self.completed_tasks.append(completed)
            self.completed_tasks.sort(key=lambda task: (task.group_number, task.entry.id))
            self.revealed.add(word_id)
        else:
            self.revealed.discard(word_id)
        self.render()
        if self.tasks:
            target = self.tasks[min(position, len(self.tasks) - 1)]
            self.table.select_entry(target.entry.id, scroll=True)
        else:
            self.table.clearSelection()
            self.table.setCurrentIndex(QModelIndex())
        self.table.setFocus()

    def update_timer_button(self):
        history = self.timer.history
        completed = history is not None and bool(history.status)
        for control in (self.elapsed_icon, self.elapsed_label, self.timer_button):
            control.setVisible(not completed)
        show_interval = self.context is not None and not self.timer.running
        if show_interval and self.interval_label.isHidden():
            self.update_interval()
        self.interval_label.setVisible(show_interval)
        text = ('暂停' if self.timer.running else '继续' if history is not None else '开始背单词')
        if completed:
            text = '任务已完成'
        self.timer_button.setIcon(icon('pause' if self.timer.running else 'play'))
        self.timer_button.setToolTip(text)
        self.timer_button.setAccessibleName(text)
        self.timer_button.setEnabled(bool(self.tasks) and not completed)

    def start_timer(self):
        try:
            return self.timer.start()
        except Exception as error:
            self.error.emit(str(error))
            return False

    def toggle_timer(self):
        if not self.table.delegate.submit():
            return
        if self.timer.running:
            self.timer.suspend()
        else:
            self.start_timer()

    def showEvent(self, event):
        super().showEvent(event)
        self.update_elapsed()
        self.update_interval()
        self._clock_display.start()
        self._interval_display.start()

    def hideEvent(self, event):
        self._clock_display.stop()
        self._interval_display.stop()
        super().hideEvent(event)
