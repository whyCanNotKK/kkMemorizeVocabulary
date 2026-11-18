"""Local study dates and immutable page-operation context, independent of Qt."""
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from functools import cached_property

from .domain import ValidationError, WordEntry


BEIJING = timezone(timedelta(hours=8))
STAGES = (1, 2, 4, 7, 15)
STAGE_GAPS = {following: following - previous for previous, following in zip(STAGES, STAGES[1:])}


@dataclass(frozen=True)
class SpellingHideSettings:
    mode: str = 'off'
    by_stage: bool = False
    by_important: bool = False
    by_learned: bool = False
    minimum_stage: int = 15

    def __post_init__(self):
        flags = (self.by_stage, self.by_important, self.by_learned)
        if any(type(flag) is not bool for flag in flags):
            raise ValidationError('拼写隐藏子设置必须是布尔值。')
        if type(self.minimum_stage) is not int or self.minimum_stage not in STAGES:
            raise ValidationError('拼写隐藏阶段必须是1、2、4、7或15。')
        if (self.mode not in ('off', 'all', 'conditional')
                or (self.mode == 'off' and any(flags))
                or (self.mode == 'all' and not all(flags))
                or (self.mode == 'conditional' and not any(flags))):
            raise ValidationError('拼写隐藏模式与子设置不一致。')

    @classmethod
    def from_enabled(cls, enabled, minimum_stage=15):
        if type(enabled) is not bool:
            raise ValidationError('拼写时隐藏单词：必须是布尔值。')
        return cls('all' if enabled else 'off', enabled, enabled, enabled, minimum_stage)

    def hides(self, entry: WordEntry, stage: int) -> bool:
        if self.mode == 'all':
            return True
        if self.mode == 'off':
            return False
        if self.by_stage and stage >= self.minimum_stage:
            return True
        if self.by_important and entry.important:
            return True
        return self.by_learned and entry.learned


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
        # The learning day rolls over at 05:00; after midnight and before
        # 05:00, the afternoon task still belongs to the previous learning day.
        learning_day = (local - timedelta(hours=5)).date()
        afternoon = local.hour >= 15 or local.hour < 5
        return cls(learning_day, afternoon)

    @property
    def label(self):
        return f"{self.day.isoformat()} {'下午' if self.afternoon else '上午'}"


@dataclass(frozen=True)
class StudyTask:
    entry: WordEntry
    group_number: int
    stage: int
    is_review: bool


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

    @cached_property
    def timing(self) -> tuple[int | None, int | None]:
        """Rounded total minutes and efficiency percentage for a finished task."""
        if self.ended_at is None:
            return None, None
        elapsed = datetime.fromisoformat(self.ended_at) - datetime.fromisoformat(self.started_at)
        seconds = elapsed // timedelta(seconds=1)
        if seconds < 0:
            return None, None
        # Round the exact seconds ratio once, without floating-point or display-minute rounding.
        efficiency = (self.duration_seconds * 100 + seconds // 2) // seconds if seconds else None
        return (seconds + 30) // 60, efficiency


def study_timestamp(instant: datetime | None = None) -> str:
    instant = instant or datetime.now(BEIJING)
    if instant.tzinfo is None:
        raise ValueError('学习时间必须包含时区。')
    return instant.astimezone(BEIJING).isoformat(timespec='seconds')


def study_duration_parts(seconds: int) -> tuple[tuple[str, int], ...]:
    if seconds < 60:
        return (('s', seconds),)
    minutes, remaining = divmod(seconds, 60)
    if minutes < 60:
        parts = (('m', minutes),)
        return parts + (('s', remaining),) if remaining else parts
    hours, minutes = divmod(minutes, 60)
    parts = (('h', hours),)
    return parts + (('m', minutes),) if minutes else parts


def format_study_duration_parts(parts: tuple[tuple[str, int], ...]) -> str:
    return ' '.join(f'{value} {unit}' for unit, value in parts)


def format_study_duration(seconds: int) -> str:
    return format_study_duration_parts(study_duration_parts(seconds))


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
