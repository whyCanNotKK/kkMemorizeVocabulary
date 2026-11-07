"""Read-only history projection; missing periods are UI rows, never database rows."""
from datetime import date, datetime, timedelta
from math import ceil

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QRect, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPen
from PySide6.QtWidgets import (QAbstractItemView, QFrame, QGridLayout, QHeaderView,
                              QLabel, QStyledItemDelegate, QTableView, QVBoxLayout, QWidget)

from .learning import StudyContext, format_study_duration, study_duration_parts
from .theme import ACCENT, GOLD, MUTED, icon
from .widgets import button


DURATION_PARTS_ROLE = Qt.ItemDataRole.UserRole


class HistoryModel(QAbstractTableModel):
    HEADERS = ('日期', '时段', '开始时间', '结束时间', '有效用时', '复习词', '新词', '状态')
    PERIODS = (('sun', GOLD, '上午'), ('moon', '#a5baf3', '下午'))
    STATUS = {
        'missing': ('task_cross', MUTED, '未完成', '完全没背单词'),
        'completed': ('task_check', ACCENT, '已完成', '已完成'),
        'unfinished': ('task_cross', '#ef929a', '未完成', '历史任务未完成'),
        'current': ('task_forward', GOLD, '进行中', '当前任务未完成'),
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.records = {}
        self.today = StudyContext.now()
        self.current_context = self.today
        self.first = self.last = self.today.day

    def load(self, records, today, current_context=None):
        self.beginResetModel()
        self.records = {(record.task_date, record.afternoon): record for record in records}
        self.today = today
        self.current_context = current_context or today
        dates = [date.fromisoformat(record.task_date) for record in records]
        self.first = min([today.day, *dates])
        self.last = today.day
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else ((self.last - self.first).days + 1) * 2 - int(not self.today.afternoon)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.HEADERS)

    def period(self, row):
        if not self.today.afternoon:
            if row == 0:
                return self.last, 0
            row += 1
        return self.last - timedelta(days=row // 2), row % 2

    def task_status(self, day, afternoon, record):
        if record is None:
            return 'missing'
        if record.status:
            return 'completed'
        if day == self.current_context.day and afternoon == self.current_context.afternoon:
            return 'current'
        return 'unfinished'

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.HEADERS[section]

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        day, afternoon = self.period(index.row())
        record = self.records.get((day.isoformat(), afternoon))
        if index.column() in (1, 7):
            if index.column() == 1:
                name, color, tooltip = self.PERIODS[afternoon]
                accessible_text = tooltip
            else:
                name, color, tooltip, accessible_text = self.STATUS[self.task_status(day, afternoon, record)]
            if role == Qt.ItemDataRole.DecorationRole:
                return icon(name, color)
            if role == Qt.ItemDataRole.ToolTipRole:
                return tooltip
            if role == Qt.ItemDataRole.AccessibleTextRole:
                return accessible_text
        if role == Qt.ItemDataRole.BackgroundRole:
            return QColor('#1e242d' if (self.last - day).days % 2 == 0 else '#222b35')
        if role == Qt.ItemDataRole.ForegroundRole:
            return QColor(MUTED)
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return Qt.AlignmentFlag.AlignCenter
        if role == Qt.ItemDataRole.ToolTipRole and index.column() == 0:
            return '周' + '一二三四五六日'[day.weekday()]
        if role == DURATION_PARTS_ROLE and index.column() == 4 and record:
            return study_duration_parts(record.duration_seconds)
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if index.column() == 0:
            return day.isoformat() if not afternoon else ''
        if index.column() in (1, 7):
            return ''
        if record is None:
            return '—'
        if index.column() in (2, 3):
            value = record.started_at if index.column() == 2 else record.ended_at
            if value is None:
                return '—'
            instant = datetime.fromisoformat(value)
            clock = f'{instant.hour:02d}：{instant.minute:02d}'
            return clock if instant.date() == day else f'{instant:%m-%d} {clock}'
        if index.column() == 4:
            return format_study_duration(record.duration_seconds)
        if index.column() == 5:
            return f'{record.review_done} / {record.review_total}'
        return f'{record.new_done} / {record.new_total}'


class HistoryDelegate(QStyledItemDelegate):
    def _paint_duration(self, painter, option, index, parts):
        metrics = QFontMetricsF(option.font, painter.device())
        number_font = QFont(option.font)
        number_font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, -0.5)
        number_metrics = QFontMetricsF(number_font, painter.device())
        two_digits = ceil(max(number_metrics.horizontalAdvance(char * 2) for char in '0123456789'))
        gap = ceil(metrics.horizontalAdvance(' '))
        unit_width = ceil(max(metrics.horizontalAdvance(unit) for unit in ('h', 'm', 's')))
        fixed_width = len(parts) * (unit_width + gap) + (len(parts) - 1) * (two_digits + gap)
        area = option.rect.adjusted(4, 0, -4, 0)
        first_width = max(two_digits, ceil(number_metrics.horizontalAdvance(str(parts[0][1]))))
        first_width = min(first_width, area.width() - fixed_width)
        if first_width < two_digits:
            super().paint(painter, option, index)
            return

        x = area.x() + (area.width() - fixed_width - first_width) // 2
        for i, (unit, number) in enumerate(parts):
            number_width = first_width if i == 0 else two_digits
            unit_x = x + number_width + gap
            value = str(number)
            if number_metrics.horizontalAdvance(value) > number_width:
                value = number_metrics.elidedText(value, Qt.TextElideMode.ElideRight, number_width)
            painter.setFont(number_font)
            painter.drawText(QRect(x, area.y(), number_width, area.height()),
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, value)
            painter.setFont(option.font)
            painter.drawText(QRect(unit_x, area.y(), unit_width, area.height()),
                             Qt.AlignmentFlag.AlignCenter, unit)
            x = unit_x + unit_width + gap

    def _paint_number_pair(self, painter, option, index, text):
        clock = index.column() in (2, 3)
        separator = '：' if clock else ' / '
        left, _, right = text.partition(separator)
        prefix, space, left = left.rpartition(' ')
        prefix += space
        metrics = QFontMetricsF(option.font, painter.device())
        separator_font = option.font
        separator_metrics = metrics
        if clock:
            separator_font = QFont(option.font)
            if separator_font.pixelSize() > 0:
                separator_font.setPixelSize(separator_font.pixelSize() + 2)
            else:
                separator_font.setPointSizeF(separator_font.pointSizeF() + 1.5)
            separator_metrics = QFontMetricsF(separator_font, painter.device())
        digits = 2 if clock else 3
        minimum = ceil(max(metrics.horizontalAdvance(char * digits) for char in '0123456789'))
        prefix_width = ceil(metrics.horizontalAdvance(prefix))
        separator_width = ceil(separator_metrics.horizontalAdvance(separator))
        area = option.rect.adjusted(4, 0, -4, 0)
        number_width = max(minimum, ceil(metrics.horizontalAdvance(left)), ceil(metrics.horizontalAdvance(right)))
        number_width = min(number_width, (area.width() - prefix_width - separator_width) // 2)
        if number_width < minimum:
            super().paint(painter, option, index)
            return

        width = prefix_width + 2 * number_width + separator_width
        x = area.x() + (area.width() - width) // 2
        y, height = area.y(), area.height()
        if prefix:
            painter.drawText(QRect(x, y, prefix_width, height),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, prefix)
        x += prefix_width
        painter.drawText(QRect(x, y, number_width, height),
                         Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                         metrics.elidedText(left, Qt.TextElideMode.ElideRight, number_width))
        x += number_width
        marker_rect = QRect(x, y, separator_width, height)
        if clock:
            # YaHei's full-width colon has most of its blank space on the right.
            ink = separator_metrics.tightBoundingRect(separator)
            marker_rect.translate(round((separator_width - ink.width()) / 2 - ink.x()), 0)
        painter.setFont(separator_font)
        painter.drawText(marker_rect, Qt.AlignmentFlag.AlignCenter, separator.strip())
        painter.setFont(option.font)
        x += separator_width
        painter.drawText(QRect(x, y, number_width, height),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         metrics.elidedText(right, Qt.TextElideMode.ElideRight, number_width))

    def paint(self, painter, option, index):
        column = index.column()
        parts = index.data(DURATION_PARTS_ROLE) if column == 4 else None
        pair = index.data() if column in (2, 3, 5, 6) else None
        if column in (1, 7):
            painter.fillRect(option.rect, index.data(Qt.ItemDataRole.BackgroundRole))
            target = QRect(0, 0, 20, 20)
            target.moveCenter(option.rect.center())
            index.data(Qt.ItemDataRole.DecorationRole).paint(painter, target)
        elif parts or (pair and pair != '—'):
            painter.save()
            painter.fillRect(option.rect, index.data(Qt.ItemDataRole.BackgroundRole))
            painter.setClipRect(option.rect, Qt.ClipOperation.IntersectClip)
            painter.setFont(option.font)
            painter.setPen(index.data(Qt.ItemDataRole.ForegroundRole))
            if parts:
                self._paint_duration(painter, option, index, parts)
            else:
                self._paint_number_pair(painter, option, index, pair)
            painter.restore()
        else:
            super().paint(painter, option, index)
        # The merged date cell and the last period share a day-bottom separator.
        model, row = index.model(), index.row()
        if (index.column() == 0 or row + 1 == model.rowCount()
                or model.period(row)[0] != model.period(row + 1)[0]):
            painter.save()
            painter.setPen(QPen(QColor('#36404e')))
            painter.drawLine(option.rect.bottomLeft(), option.rect.bottomRight())
            painter.restore()


class StudyHistoryPanel(QFrame):
    def __init__(self, repository, timer, parent=None):
        super().__init__(parent)
        self.repository = repository
        self.timer = timer
        self.collapsed = True
        self.setObjectName('panel')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 10, 16, 12)
        layout.setSpacing(8)
        header = QWidget()
        header.setFixedHeight(32)
        header_layout = QGridLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        self.title = QLabel('背单词记录')
        self.title.setObjectName('section')
        header_layout.addWidget(self.title, 0, 0, Qt.AlignmentFlag.AlignLeft)
        self.collapse_button = button('', quiet=True)
        self.collapse_button.setFixedSize(72, 32)
        self.collapse_button.setIconSize(QSize(40, 26))
        self.collapse_button.setCheckable(True)
        self.collapse_button.clicked.connect(self.set_collapsed)
        header_layout.addWidget(self.collapse_button, 0, 1, Qt.AlignmentFlag.AlignCenter)
        header_layout.setColumnStretch(0, 1)
        header_layout.setColumnStretch(2, 1)
        header_layout.setColumnMinimumWidth(2, self.title.sizeHint().width())
        self.total_count = QLabel()
        self.total_count.setTextFormat(Qt.TextFormat.RichText)
        self.total_count.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.total_count.setToolTip('包括已经背完的和正在背的')
        header_layout.addWidget(self.total_count, 0, 2, Qt.AlignmentFlag.AlignRight)
        self.update_total_count(0)
        layout.addWidget(header)
        self.model = HistoryModel(self)
        self.table = QTableView()
        self.table.setModel(self.model)
        self.model.modelReset.connect(self.update_date_spans)
        self.table.setIconSize(QSize(20, 20))
        self.table.setItemDelegate(HistoryDelegate(self.table))
        self.table.setShowGrid(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setSortingEnabled(False)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(34)
        self.table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        self.table.horizontalHeader().setSectionsClickable(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.ensurePolished()
        metrics = QFontMetricsF(self.table.font(), self.table)
        # Include room for the larger colon and the cell's side padding.
        time_width = ceil(metrics.horizontalAdvance('88-88 88：88')) + 14
        for column, width in ((0, 100), (1, 48), (2, time_width), (3, time_width), (7, 60)):
            self.table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
            self.table.setColumnWidth(column, width)
        layout.addWidget(self.table)
        self.timer.changed.connect(self.refresh)
        self._live = QTimer(self)
        self._live.setInterval(1000)
        self._live.timeout.connect(self.update_live)
        self.set_collapsed(True)

    def update_date_spans(self):
        self.table.clearSpans()
        row = 0
        while row < self.model.rowCount():
            day, _ = self.model.period(row)
            end = row + 1
            while end < self.model.rowCount() and self.model.period(end)[0] == day:
                end += 1
            if end - row > 1:
                self.table.setSpan(row, 0, end - row, 1)
            row = end

    def set_collapsed(self, collapsed):
        self.collapsed = collapsed
        self.table.setVisible(not collapsed)
        self.collapse_button.setChecked(collapsed)
        self.collapse_button.setIcon(icon('chevron_up' if collapsed else 'chevron_down'))
        label = f'{"展开" if collapsed else "收起"}背单词记录区域'
        self.collapse_button.setToolTip(label)
        self.collapse_button.setAccessibleName(label)
        self.refresh()
        if not collapsed:
            if self.isVisible():
                self._live.start()
        else:
            self._live.stop()
        self.update_height()

    def update_height(self):
        height = 56
        if not self.collapsed:
            height += 8 + self.table.horizontalHeader().sizeHint().height() + min(6, self.model.rowCount()) * 34
            parent = self.parentWidget()
            if parent is not None:
                height = min(height, parent.height() // 2)
        self.setFixedHeight(height)

    def update_total_count(self, count):
        self.total_count.setText(f'<span style="color:{MUTED}">合计 </span>'
                                 f'<span style="color:{ACCENT};font-size:22px;font-weight:700">{count}</span>'
                                 f'<span style="color:{MUTED}"> 个单词</span>')
        self.total_count.setAccessibleName(f'合计 {count} 个单词')

    def refresh(self):
        if not self.isVisible():
            return
        position = self.table.verticalScrollBar().value()
        try:
            self.update_total_count(self.repository.study_participant_count())
            if self.collapsed:
                return
            self.model.load(self.repository.study_task_histories(), StudyContext.now(), self.timer.context)
        except Exception as error:
            self.timer.error.emit(str(error))
            return
        self.table.verticalScrollBar().setValue(position)
        self.update_height()

    def update_live(self):
        if self.model.today != StudyContext.now():
            self.refresh()

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()
        if not self.collapsed:
            self._live.start()

    def hideEvent(self, event):
        self._live.stop()
        super().hideEvent(event)
