"""Local study dates and immutable page-operation context, independent of Qt."""
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from .domain import WordEntry


BEIJING = timezone(timedelta(hours=8))
STAGES = (1, 2, 4, 7, 15)


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


def next_stage(stage: int, completed_on: date):
    index = STAGES.index(stage)
    if index == len(STAGES) - 1:
        return None, None
    following = STAGES[index + 1]
    return following, completed_on + timedelta(days=following - stage)
