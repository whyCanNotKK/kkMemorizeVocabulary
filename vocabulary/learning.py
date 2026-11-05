"""Local study dates and immutable page-operation context, independent of Qt."""
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from .domain import ValidationError, WordEntry


BEIJING = timezone(timedelta(hours=8))
STAGES = (1, 2, 4, 7, 15)
STAGE_GAPS = {following: following - previous for previous, following in zip(STAGES, STAGES[1:])}


@dataclass(frozen=True)
class StudyDrawSettings:
    words_per_group: int = 10
    groups_per_day: int = 3

    def __post_init__(self):
        for label, value in (("每组单词数", self.words_per_group), ("每日新增组数", self.groups_per_day)):
            if type(value) is not int or not 1 <= value <= 1000:
                raise ValidationError(f"{label}必须是1至1000的整数。")

    @property
    def daily_word_count(self):
        return self.words_per_group * self.groups_per_day


@dataclass(frozen=True)
class StudyContext:
    day: date
    afternoon: bool

    @classmethod
    def now(cls):
        return cls.at(datetime.now(BEIJING))

    @classmethod
    def at(cls, instant: datetime):
        if instant.tzinfo is None:
            raise ValueError("学习时间必须包含时区。")
        local = instant.astimezone(BEIJING)
        return cls(local.date(), local.hour >= 15)

    @property
    def label(self):
        return f"{self.day.isoformat()} {'下午' if self.afternoon else '上午'}"


@dataclass(frozen=True)
class StudyTask:
    entry: WordEntry
    group_number: int
    is_review: bool = False


@dataclass(frozen=True)
class StudyTaskHistory:
    id: int
    task_date: str
    afternoon: int
    started_at: str
    ended_at: str | None
    duration_seconds: int
    status: int
    new_total: int
    review_total: int
    new_done: int
    review_done: int


def study_timestamp(instant: datetime | None = None) -> str:
    instant = instant or datetime.now(BEIJING)
    if instant.tzinfo is None:
        raise ValueError('学习时间必须包含时区。')
    return instant.astimezone(BEIJING).isoformat(timespec='seconds')


def format_study_duration(seconds: int) -> str:
    if seconds < 60:
        return f'{seconds} s'
    minutes, remaining = divmod(seconds, 60)
    if minutes < 60:
        return f'{minutes} min' + (f' {remaining} s' if remaining else '')
    hours, minutes = divmod(minutes, 60)
    return f'{hours} h' + (f' {minutes} min' if minutes else '')


def next_stage(stage: int) -> int:
    index = STAGES.index(stage)
    if index == len(STAGES) - 1:
        return 0
    return STAGES[index + 1]


def study_due(stage: int, last_completed_on: str | None, today: date) -> bool:
    if stage == 0:
        return False
    if stage == 1:
        return True
    return (today - date.fromisoformat(last_completed_on)).days >= STAGE_GAPS[stage]
