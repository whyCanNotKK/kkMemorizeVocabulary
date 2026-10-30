"""Independent, ID-free interchange for complete seventeen-day study snapshots."""
from dataclasses import dataclass
from datetime import date, timedelta
import json
from pathlib import Path

from .domain import ValidationError, WordEntry
from .learning import STAGES
from .transfer import FORMAT as WORD_FORMAT, VERSION as WORD_VERSION
from .transfer import parse_entries, serialize_entries, write_text_atomically


FORMAT = 'kkMemorizeVocabulary.study17'
VERSION = 1
RECORD_FIELDS = ('group_number', 'position', 'enrolled_on', 'stage', 'next_due_on',
                 'morning_done', 'afternoon_done', 'last_completed_on', 'completed')


@dataclass
class StudyBackup:
    words: list[WordEntry]
    records: dict[str, dict]
    generated_dates: list[str]
    group_counter: int


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
    _fields(document, ('format', 'version', 'words', 'study_records', 'study_settings'), '学习进度文件')
    if document['format'] != FORMAT or type(document['version']) is not int or document['version'] != VERSION:
        raise ValidationError('这不是支持的学习进度文件，请使用学习进度version: 1。')
    words = parse_entries(json.dumps({'format': WORD_FORMAT, 'version': WORD_VERSION, 'words': document['words']}))
    names = [word.text.strip().casefold() for word in words]
    if len(set(names)) != len(names):
        raise ValidationError('学习进度文件中存在重复主词。')
    records = document['study_records']
    if not isinstance(records, dict) or set(records) != set(names):
        raise ValidationError('学习记录必须与主词一一对应，并以去首尾空格、忽略大小写的单词为键。')
    occupied = set()
    group_dates = {}
    for name, record in records.items():
        _fields(record, RECORD_FIELDS, name)
        _integer(record['group_number'], 1, 9223372036854775807, f'{name} 组号')
        _integer(record['position'], 1, 10, f'{name} 组内位置')
        if type(record['stage']) is not int or record['stage'] not in STAGES:
            raise ValidationError(f'{name}：学习阶段必须是1、2、4、7或15。')
        for flag in ('morning_done', 'afternoon_done', 'completed'):
            if type(record[flag]) is not bool:
                raise ValidationError(f'{name} {flag}：必须是true或false。')
        enrolled = _day(record['enrolled_on'], f'{name} 入选日期')
        due = _day(record['next_due_on'], f'{name} 到期日期')
        last = None if record['last_completed_on'] is None else _day(
            record['last_completed_on'], f'{name} 最后完成日期')
        if due < enrolled or (last is not None and last < enrolled):
            raise ValidationError(f'{name}：学习日期先后关系不正确。')
        if record['morning_done'] and record['afternoon_done']:
            raise ValidationError(f'{name}：上下午都完成的轮次应当已结算。')
        if record['completed']:
            valid = (record['stage'] == 15 and last == due and last is not None
                     and not record['morning_done'] and not record['afternoon_done'])
        elif record['stage'] == 1:
            valid = last is None and due == enrolled
        else:
            gap = record['stage'] - STAGES[STAGES.index(record['stage']) - 1]
            valid = last is not None and (due - last) == timedelta(days=gap)
        if not valid:
            raise ValidationError(f'{name}：学习阶段、日期或完成标记不一致。')
        place = (record['group_number'], record['position'])
        if place in occupied:
            raise ValidationError('同一分组的组内位置不能重复。')
        occupied.add(place)
        previous = group_dates.setdefault(record['group_number'], enrolled)
        if previous != enrolled:
            raise ValidationError('同一分组必须具有相同的入选日期。')
    settings = document['study_settings']
    _fields(settings, ('generated_dates', 'group_counter'), '学习设置')
    dates = settings['generated_dates']
    if not isinstance(dates, list):
        raise ValidationError('每日抽词日期必须是数组。')
    for value in dates:
        _day(value, '每日抽词日期')
    if len(set(dates)) != len(dates):
        raise ValidationError('每日抽词日期不能重复。')
    if not {record['enrolled_on'] for record in records.values()}.issubset(set(dates)):
        raise ValidationError('学习记录的入选日期缺少对应的每日抽词记录。')
    _integer(settings['group_counter'], 0, 9223372036854775807, '组号计数')
    if settings['group_counter'] < max((record['group_number'] for record in records.values()), default=0):
        raise ValidationError('组号计数不能小于已有组号。')
    return StudyBackup(words, records, sorted(dates), settings['group_counter'])


def serialize_study_backup(backup: StudyBackup) -> str:
    words = json.loads(serialize_entries(backup.words))['words']
    document = {'format': FORMAT, 'version': VERSION, 'words': words,
                'study_records': backup.records,
                'study_settings': {'generated_dates': backup.generated_dates, 'group_counter': backup.group_counter}}
    content = json.dumps(document, ensure_ascii=False, indent=2) + '\n'
    parse_study_backup(content)
    return content


def read_study_backup(path: str | Path) -> StudyBackup:
    return parse_study_backup(Path(path).read_text(encoding='utf-8-sig'))


def write_study_backup(path: str | Path, backup: StudyBackup):
    write_text_atomically(path, serialize_study_backup(backup))
