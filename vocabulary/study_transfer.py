"""Study interchange: words/progress by name, aggregate task history with IDs."""
from dataclasses import dataclass, field
from datetime import date, datetime
import json
from pathlib import Path

from .domain import ValidationError, WordEntry
from .learning import STAGES, StudyTaskHistory, study_timestamp
from .transfer import FORMAT as WORD_FORMAT, VERSION as WORD_VERSION
from .transfer import parse_entries, serialize_entries, write_text_atomically


FORMAT = 'kkMemorizeVocabulary.study17'
VERSION = 6
RECORD_FIELDS = ('group_number', 'stage', 'morning_done', 'afternoon_done', 'last_completed_on')


@dataclass
class StudyBackup:
    words: list[WordEntry]
    records: dict[str, dict]
    study_last_add_new_vocabulary: str | None
    group_counter: int
    task_history: list[dict] = field(default_factory=list)


def validate_task_history(records):
    if not isinstance(records, list):
        raise ValidationError('任务历史必须是数组。')
    ids, periods = set(), set()
    for record in records:
        _fields(record, StudyTaskHistory.__dataclass_fields__, '任务历史')
        for key in ('id', 'duration_seconds', 'new_total', 'review_total', 'new_done', 'review_done'):
            _integer(record[key], 1 if key == 'id' else 0, 9223372036854775807, f'任务历史 {key}')
        for key in ('afternoon', 'status'):
            _integer(record[key], 0, 1, f'任务历史 {key}')
        _day(record['task_date'], '任务历史日期')
        period = (record['task_date'], record['afternoon'])
        if record['id'] in ids or period in periods:
            raise ValidationError('任务历史ID或日期时段重复。')
        ids.add(record['id'])
        periods.add(period)
        if ((record['status'] == 0) != (record['ended_at'] is None) or
                record['new_done'] > record['new_total'] or record['review_done'] > record['review_total']):
            raise ValidationError('任务历史状态、结束时间或数量不一致。')
        for key in ('started_at', 'ended_at'):
            value = record[key]
            if key == 'ended_at' and value is None:
                continue
            try:
                instant = datetime.fromisoformat(value)
                if instant.tzinfo is None or study_timestamp(instant) != value:
                    raise ValueError()
            except (TypeError, ValueError):
                raise ValidationError('任务时间必须是含+08:00的北京时间，精确到秒。') from None


def _fields(value, expected, label):
    if not isinstance(value, dict) or set(value) != set(expected):
        raise ValidationError(f'{label}：文件字段不完整或含未知字段。')


def _integer(value, minimum, maximum, label):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValidationError(f'{label}：必须是{minimum}至{maximum}的整数。')


def _day(value, label):
    try:
        result = date.fromisoformat(value)
        if result.isoformat() != value:
            raise ValueError()
        return result
    except (TypeError, ValueError):
        raise ValidationError(f'{label}：日期必须是有效的YYYY-MM-DD。') from None


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValidationError(f'JSON含重复字段或单词：{key}。')
        result[key] = value
    return result


def parse_study_backup(content: str) -> StudyBackup:
    try:
        document = json.loads(content, object_pairs_hook=_unique_object)
    except (ValueError, RecursionError) as error:
        raise ValidationError(f'学习进度文件不是有效的JSON：{error}') from error
    _fields(document, ('format', 'version', 'words', 'study_records', 'study_settings', 'task_history'), '学习进度文件')
    if document['format'] != FORMAT or type(document['version']) is not int or document['version'] != VERSION:
        raise ValidationError(f'这不是支持的学习进度文件，请使用学习进度version: {VERSION}。')
    words = parse_entries(json.dumps({'format': WORD_FORMAT, 'version': WORD_VERSION, 'words': document['words']}))
    names = [word.text.strip().casefold() for word in words]
    if len(set(names)) != len(names):
        raise ValidationError('学习进度文件中存在重复主词。')
    records = document['study_records']
    if not isinstance(records, dict) or set(records) != set(names):
        raise ValidationError('学习记录必须与主词一一对应，并以去首尾空格、忽略大小写的单词为键。')
    for name, record in records.items():
        _fields(record, RECORD_FIELDS, name)
        _integer(record['group_number'], 1, 9223372036854775807, f'{name} 组号')
        if type(record['stage']) is not int or record['stage'] not in (0, *STAGES):
            raise ValidationError(f'{name}：学习阶段必须是0、1、2、4、7或15。')
        for flag in ('morning_done', 'afternoon_done'):
            if type(record[flag]) is not bool:
                raise ValidationError(f'{name} {flag}：必须是true或false。')
        last = None if record['last_completed_on'] is None else _day(
            record['last_completed_on'], f'{name} 最后完成日期')
        if record['morning_done'] and record['afternoon_done']:
            raise ValidationError(f'{name}：上下午都完成的轮次应当已结算。')
        valid = (last is None) if record['stage'] == 1 else (last is not None)
        if record['stage'] == 0:
            valid = valid and not record['morning_done'] and not record['afternoon_done']
        if not valid:
            raise ValidationError(f'{name}：学习阶段、日期或完成标记不一致。')
    settings = document['study_settings']
    _fields(settings, ('study_last_add_new_vocabulary', 'group_counter'), '学习设置')
    last_draw = settings['study_last_add_new_vocabulary']
    if last_draw is not None:
        _day(last_draw, '最近抽词日期')
    if records and last_draw is None:
        raise ValidationError('学习记录缺少最近抽词日期。')
    _integer(settings['group_counter'], 0, 9223372036854775807, '组号计数')
    if settings['group_counter'] < max((record['group_number'] for record in records.values()), default=0):
        raise ValidationError('组号计数不能小于已有组号。')
    validate_task_history(document['task_history'])
    return StudyBackup(words, records, last_draw, settings['group_counter'], document['task_history'])


def serialize_study_backup(backup: StudyBackup) -> str:
    words = json.loads(serialize_entries(backup.words))['words']
    document = {'format': FORMAT, 'version': VERSION, 'words': words, 'task_history': backup.task_history,
                'study_records': backup.records,
                'study_settings': {'study_last_add_new_vocabulary': backup.study_last_add_new_vocabulary,
                                   'group_counter': backup.group_counter}}
    content = json.dumps(document, ensure_ascii=False, indent=2) + '\n'
    parse_study_backup(content)
    return content


def read_study_backup(path: str | Path) -> StudyBackup:
    return parse_study_backup(Path(path).read_text(encoding='utf-8-sig'))


def read_word_or_study_file(path: str | Path) -> list[WordEntry] | StudyBackup:
    """Classify only to select a parser; a format label alone never bypasses validation."""
    content = Path(path).read_text(encoding='utf-8-sig')
    try:
        document = json.loads(content)
    except (ValueError, RecursionError) as error:
        raise ValidationError('文件不是有效的JSON。') from error
    if isinstance(document, dict) and document.get('format') == FORMAT:
        return parse_study_backup(content)
    return parse_entries(content)


def write_study_backup(path: str | Path, backup: StudyBackup):
    write_text_atomically(path, serialize_study_backup(backup))
