"""Confirmed merge examples, transactional safety and both import flows."""
from copy import deepcopy
from dataclasses import replace
from datetime import date
import sqlite3

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QHelpEvent, QTextDocument
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QMessageBox

from vocabulary.domain import RelatedWord, Sense, WordEntry
from vocabulary.import_dialog import ImportConflictDialog
from vocabulary.import_merge import AutoMergeStats, plan_auto_merge
from vocabulary.learning import StudyContext
from vocabulary.storage import Repository
from vocabulary.study_transfer import parse_word_or_study_file, write_study_backup
from vocabulary.transfer import ImportSource, write_entries


@pytest.fixture(autouse=True)
def merge_messages(monkeypatch):
    messages = []

    def information(parent, title, text):
        messages.append((title, text, parent.isVisible()))

    monkeypatch.setattr(QMessageBox, 'information', information)
    return messages


def word(text='learn', translation='学习', *, pos='v.', **kwargs):
    return WordEntry(text, [Sense(pos, translation)], **kwargs)


def child(text, translation='学习者', *, pos='n.', **kwargs):
    return RelatedWord(text, [Sense(pos, translation)], **kwargs)


def test_merge_adds_phonetics_senses_and_related_without_mutating_inputs():
    local = word(phonetic='', important=False, learned=True, id=7, sort_order=9,
                 related=[child('Learner', phonetic='local'), child('local-only')])
    incoming = word(
        'LEARN', '学', phonetic='main', important=True, learned=False,
        related=[child(' learner ', '学者', phonetic='incoming'),
                 child('learning', '学习过程', phonetic='new')])
    incoming.senses.append(Sense('n.', '学习'))
    incoming.related[0].senses.append(Sense('adj.', '学习的'))
    before = deepcopy((local, incoming))

    decision = plan_auto_merge(local, incoming)

    expected = deepcopy(local)
    expected.phonetic = 'main'
    expected.senses.append(Sense('n.', '学习'))
    expected.related[0].senses.append(Sense('adj.', '学习的'))
    expected.related.append(deepcopy(incoming.related[1]))
    assert decision.entry == expected and not decision.reason
    assert (local, incoming) == before


@pytest.mark.parametrize('local,incoming,expected', [
    ('记录', '记', '记录'), ('记录', '记载', '记录'),
    ('', '光', '光'), ('', '', ''), ('记录', '', '记录'),
    (' 记录 ', ' 记载 ', ' 记录 '),
    ('记录', '记录；记载', None),
    ('记录', '记 录', None), ('记录', '记\n录', None), ('记录', '记录。', None),
    ('记录', ' \n记载\t', '记录'),
])
def test_translation_comparison_counts_all_internal_characters(local, incoming, expected):
    decision = plan_auto_merge(word(translation=local), word(translation=incoming))
    if expected is None:
        assert decision.entry is None and '导入译文更长' in decision.reason
    else:
        assert decision.entry.senses == [Sense('v.', expected)]


def test_longer_related_translation_vetoes_entire_group_and_all_partial_additions():
    local = word(related=[child('careful', '小心的', pos='adj.')])
    incoming = word(translation='学', phonetic='new', related=[
        child('new'), child('CAREFUL', '小心的；仔细的', pos='adj.')])
    incoming.senses.append(Sense('n.', '学习'))
    before = deepcopy((local, incoming))
    decision = plan_auto_merge(local, incoming)
    assert decision.entry is None and 'careful' in decision.reason
    assert (local, incoming) == before


@pytest.mark.parametrize('side,owner', [
    ('local', 'main'), ('incoming', 'main'),
    ('local', 'matched'), ('incoming', 'matched'),
    ('local', 'only'), ('incoming', 'only'),
])
def test_duplicate_pos_anywhere_requires_manual_review(side, owner):
    local = word(related=[child('matched'), child('local-only')])
    incoming = word(related=[child('matched'), child('incoming-only')])
    target = local if side == 'local' else incoming
    target = target if owner == 'main' else target.related[0 if owner == 'matched' else 1]
    target.senses.append(deepcopy(target.senses[0]))
    decision = plan_auto_merge(local, incoming)
    assert decision.entry is None and '重复词性' in decision.reason


@pytest.mark.parametrize('side', ['local', 'incoming'])
def test_duplicate_related_names_casefolded_and_stripped_require_manual_review(side):
    local, incoming = word(), word(phonetic='new')
    target = local if side == 'local' else incoming
    target.related = [child('Help'), child(' help ')]
    decision = plan_auto_merge(local, incoming)
    assert decision.entry is None and '重名关联词' in decision.reason


def test_main_and_related_may_share_name_and_related_sound_is_filled():
    local = word(phonetic='main-local', related=[child('LEARN', '学习')])
    incoming = word(phonetic='main-incoming', related=[child('learn', '学', phonetic='child')])
    merged = plan_auto_merge(local, incoming).entry
    assert merged.phonetic == 'main-local'
    assert merged.related == [child('LEARN', '学习', phonetic='child')]


def test_repeated_candidates_use_latest_local_contents_and_do_not_revisit_skips(repo):
    original = repo.save(word(translation=''))
    candidates = [word(translation='学习', phonetic='/A/'),
                  word(translation='学', phonetic='/B/'),
                  word(translation='学习；研究', phonetic='/C/')]
    candidates[-1].senses.append(Sense('n.', '新增'))
    result = repo.import_entries(candidates)
    batch = repo.auto_merge_imports(result.conflicts)
    assert batch.processed_indices == [0, 1] and set(batch.skipped) == {2}
    assert batch.stats == AutoMergeStats(changed=1, unchanged=1)
    assert repo.get(original.id) == replace(original, senses=[Sense('v.', '学习')], phonetic='A')

    # A later candidate fills an empty noun after the earlier candidate was skipped.
    local = repo.save(word('other'))
    first, second = word('other', '学习；研究'), word('other', '学')
    second.senses.append(Sense('n.', ''))
    third = deepcopy(second)
    third.senses[1].translation = '名词'
    result = repo.import_entries([first, second, third])
    batch = repo.auto_merge_imports(result.conflicts)
    assert batch.processed_indices == [1, 2] and set(batch.skipped) == {0}
    assert batch.stats == AutoMergeStats(changed=2)
    assert repo.get(local.id).senses == [Sense('v.', '学习'), Sense('n.', '名词')]


def test_merge_keeps_metadata_progress_and_persists_after_reopen(repo):
    original = repo.save(word(important=False, related=[child('Learner')]))
    context = StudyContext(date(2026, 11, 22), False)
    repo.prepare_study(context)
    repo.complete_study(original.id, context)
    repo.set_flag(original.id, 'learned', True)
    original = repo.get(original.id)
    progress = [tuple(row) for row in repo.connection.execute('SELECT * FROM memorize_vocabulary_17day')]
    assert progress
    history = repo.study_task_histories()
    assert history
    metadata = tuple(repo.connection.execute(
        'SELECT id,created_at,sort_order,important,learned FROM words WHERE id=?', (original.id,)).fetchone())
    counters = repo.max_pending_sort_order, repo.max_learned_sort_order
    candidate = word('LEARN', '学', important=True, learned=False, phonetic='/sound/',
                     related=[child('learner', phonetic='/child/'), child('new')])
    result = repo.import_entries([candidate])
    batch = repo.auto_merge_imports(result.conflicts)
    saved = repo.get(original.id)
    assert batch.stats == AutoMergeStats(changed=1)
    assert saved.text == original.text and saved.phonetic == 'sound'
    assert saved.related[0].text == 'Learner' and saved.related[0].phonetic == 'child'
    assert metadata == tuple(repo.connection.execute(
        'SELECT id,created_at,sort_order,important,learned FROM words WHERE id=?', (original.id,)).fetchone())
    assert progress == [tuple(row) for row in repo.connection.execute('SELECT * FROM memorize_vocabulary_17day')]
    assert repo.study_task_histories() == history
    assert counters == (repo.max_pending_sort_order, repo.max_learned_sort_order)
    reopened = Repository(repo.path)
    try:
        assert reopened.get(original.id) == saved
    finally:
        reopened.close()


def test_safe_no_change_and_skipped_groups_execute_no_writes(repo):
    original = repo.save(word())
    candidates = [word(translation='学', important=False), word(translation='学习；研究', phonetic='blocked')]
    result = repo.import_entries(candidates)
    before = repo.connection.total_changes
    timestamp = repo.connection.execute('SELECT updated_at FROM words').fetchone()[0]
    batch = repo.auto_merge_imports(result.conflicts)
    assert batch.stats == AutoMergeStats(unchanged=1) and batch.processed_indices == [0]
    assert repo.connection.total_changes == before and repo.get(original.id) == original
    assert repo.connection.execute('SELECT updated_at FROM words').fetchone()[0] == timestamp


def test_partial_database_write_rolls_back_entire_batch_without_mutating_cards(repo):
    originals = [repo.save(word(name)) for name in ('one', 'two')]
    result = repo.import_entries([word('one', phonetic='new'), word('two', phonetic='FAIL')])
    before = deepcopy(result)
    repo.connection.execute("""CREATE TRIGGER fail_merge BEFORE UPDATE OF phonetic ON words
        WHEN NEW.phonetic='FAIL' BEGIN SELECT RAISE(ABORT, 'merge locked'); END""")
    with pytest.raises(sqlite3.IntegrityError, match='merge locked'):
        repo.auto_merge_imports(result.conflicts)
    assert repo.list_entries() == originals and result == before
    repo.connection.execute('DROP TRIGGER fail_merge')
    assert repo.auto_merge_imports(result.conflicts).stats.changed == 2


def test_missing_local_target_rolls_back_preceding_merge(repo):
    first, second = repo.save(word('one')), repo.save(word('two'))
    result = repo.import_entries([word('one', phonetic='new'), word('two', phonetic='new')])
    repo.delete(second.id)
    with pytest.raises(ValueError, match='已不存在'):
        repo.auto_merge_imports(result.conflicts)
    assert repo.get(first.id) == first


def test_dialog_syncs_remaining_same_name_and_updates_stats_without_extra_writes(repo, qtbot, merge_messages):
    original = repo.save(word())
    result = repo.import_entries([word(phonetic='new'), word(translation='学习；研究'),
                                  word(translation='学', important=False)])
    dialog = ImportConflictDialog(repo, result)
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.mouseClick(dialog.auto_merge_button, Qt.MouseButton.LeftButton)
    assert dialog.isVisible() and len(dialog.conflicts) == 1
    assert dialog.cards[0][0].entry == repo.get(original.id)
    assert dialog.cards[0][0].entry.phonetic == 'new'
    assert result.auto_merge_stats == AutoMergeStats(1, 1)
    assert merge_messages == [('自动合并完成', '合并并修改 1 条\n无需修改 1 条\n剩余待人工 1 条', True)]
    assert not hasattr(dialog, 'auto_merge_summary')
    assert not dialog.replaced_names and not dialog.replaced_ids
    qtbot.mouseClick(dialog.auto_merge_button, Qt.MouseButton.LeftButton)
    assert result.auto_merge_stats == AutoMergeStats(1, 1)
    assert merge_messages[-1] == ('自动合并完成', '合并并修改 0 条\n无需修改 0 条\n剩余待人工 1 条', True)
    qtbot.mouseClick(dialog.cards[0][0].word, Qt.MouseButton.LeftButton)
    assert dialog.result() == QDialog.DialogCode.Accepted and not dialog.conflicts
    assert len(merge_messages) == 2


def test_dialog_failure_preserves_cards_and_statistics_and_can_retry(repo, qtbot, monkeypatch, merge_messages):
    originals = [repo.save(word(name)) for name in ('one', 'two')]
    result = repo.import_entries([word('one', phonetic='new'), word('two', phonetic='FAIL')])
    dialog = ImportConflictDialog(repo, result)
    qtbot.addWidget(dialog)
    dialog.show()
    repo.connection.execute("""CREATE TRIGGER fail_merge BEFORE UPDATE OF phonetic ON words
        WHEN NEW.phonetic='FAIL' BEGIN SELECT RAISE(ABORT, 'merge locked'); END""")
    errors = []
    monkeypatch.setattr(QMessageBox, 'critical', lambda *args: errors.append(args[-1]))
    qtbot.mouseClick(dialog.auto_merge_button, Qt.MouseButton.LeftButton)
    assert errors and '全部回滚' in errors[0] and 'merge locked' in errors[0]
    assert repo.list_entries() == originals and len(dialog.conflicts) == 2
    assert result.auto_merge_stats is None and not merge_messages
    assert not dialog.busy_overlay.active
    repo.connection.execute('DROP TRIGGER fail_merge')
    qtbot.mouseClick(dialog.auto_merge_button, Qt.MouseButton.LeftButton)
    assert result.auto_merge_stats == AutoMergeStats(changed=2)
    assert merge_messages == [('自动合并完成', '合并并修改 2 条\n无需修改 0 条\n剩余待人工 0 条', True)]
    assert not dialog.isVisible() and not dialog.conflicts


def test_auto_stats_accumulate_across_manual_adoption_of_a_remaining_candidate(repo, qtbot):
    repo.save(word())
    result = repo.import_entries([
        word(phonetic='first'), word(translation='学习；研究', phonetic='manual'),
        word(translation='学习；研究', phonetic='last'),
    ])
    dialog = ImportConflictDialog(repo, result)
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.auto_merge()
    assert result.auto_merge_stats == AutoMergeStats(changed=1)
    assert len(dialog.conflicts) == 2
    dialog.choose(dialog.conflicts[0], True)
    manual_id = repo.list_entries()[0].id
    dialog.auto_merge()
    assert result.auto_merge_stats == AutoMergeStats(changed=1, unchanged=1)
    assert dialog.replaced_names == {'learn'} and dialog.replaced_ids == {manual_id}
    assert repo.get(manual_id).phonetic == 'manual'
    assert not dialog.conflicts


def test_header_center_tooltip_and_scroll_stability(repo, qtbot, themed_app, tmp_path):
    originals = [repo.save(word(f'word-{index}')) for index in range(25)]
    candidates = [word(entry.text, '学习；研究') for entry in originals]
    candidates[0].senses[0].translation = '学'
    dialog = ImportConflictDialog(repo, repo.import_entries(candidates))
    qtbot.addWidget(dialog)
    dialog.resize(720, 480)
    dialog.show()
    qtbot.wait(20)
    center = dialog.auto_merge_button.mapTo(dialog, dialog.auto_merge_button.rect().center())
    assert abs(center.x() - dialog.rect().center().x()) <= 2
    left, right = [label.parentWidget() for label in dialog.column_titles]
    assert left.mapTo(dialog, left.rect().topRight()).x() < center.x() - dialog.auto_merge_button.width() / 2
    assert right.mapTo(dialog, right.rect().topLeft()).x() > center.x() + dialog.auto_merge_button.width() / 2
    lines = dialog.auto_merge_button.toolTip().splitlines()
    assert lines[0] == '一、不自动合并的标准：' and lines[4] == '二、合并逻辑：'
    for section in (lines[1:4], lines[5:]):
        assert all(line.startswith(f'    {index}、') for index, line in enumerate(section, 1))
    scrollbar = dialog.scroll.verticalScrollBar()
    scrollbar.setValue(scrollbar.maximum() // 2)
    position = scrollbar.value()
    qtbot.mouseClick(dialog.auto_merge_button, Qt.MouseButton.LeftButton)
    qtbot.wait(20)
    assert scrollbar.value() == position
    assert len(dialog.conflicts) == 24
    for lcard, rcard in dialog.cards:
        assert lcard.height() == rcard.height() and abs(lcard.width() - rcard.width()) <= 1
    dialog.grab().save(str(tmp_path / 'auto-merge-header.png'))
    dialog.reject()


def test_auto_merge_tooltip_stays_past_five_seconds_and_hides_with_hover_or_window(repo, qtbot, themed_app):
    repo.save(word())
    dialog = ImportConflictDialog(repo, repo.import_entries([word(phonetic='new')]))
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.wait(20)
    control = dialog.auto_merge_button
    point = control.rect().center()

    def hover():
        QApplication.sendEvent(control, QHelpEvent(QEvent.Type.ToolTip, point, control.mapToGlobal(point)))
        assert dialog._auto_merge_tooltip.isVisible()

    hover()
    qtbot.wait(5300)
    assert dialog._auto_merge_tooltip.isVisible()
    assert dialog.all_left_button.toolTipDuration() == 5000
    QApplication.sendEvent(control, QEvent(QEvent.Type.Leave))
    assert not dialog._auto_merge_tooltip.isVisible()
    hover()
    QApplication.sendEvent(dialog, QEvent(QEvent.Type.WindowDeactivate))
    assert not dialog._auto_merge_tooltip.isVisible()
    hover()
    dialog.reject()
    assert not dialog._auto_merge_tooltip.isVisible()


@pytest.mark.parametrize('route', ['words', 'study_words', 'study_full'])
def test_window_reports_auto_stats_preserves_source_and_refreshes_once(
        window, repo, qtbot, tmp_path, monkeypatch, route):
    original = repo.save(word(important=False, learned=True))
    path = tmp_path / 'import.json'
    candidates = [word('LEARN', '学', phonetic='/sound/'), word('new')]
    if route == 'words':
        write_entries(path, candidates)
    else:
        source_repo = Repository(tmp_path / 'source.sqlite3')
        try:
            for candidate in candidates:
                source_repo.save(candidate)
            source_repo.prepare_study(StudyContext(date(2026, 11, 22), False))
            backup = source_repo.study_backup()
            write_study_backup(path, backup)
        finally:
            source_repo.close()
        monkeypatch.setattr(window, 'choose_study_import_mode',
                            lambda: 'full' if route == 'study_full' else 'words')
    window.refresh()
    before_file = path.read_bytes()
    refreshes, messages = [], []
    refresh = window.refresh_after_transfer

    def record_refresh(*args):
        refreshes.append(True)
        return refresh(*args)

    monkeypatch.setattr(window, 'refresh_after_transfer', record_refresh)
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *args: (str(path), ''))
    monkeypatch.setattr(QMessageBox, 'information', lambda *args: messages.append(args[-1]))
    monkeypatch.setattr(QMessageBox, 'critical', lambda *args: pytest.fail(args[-1]))

    def review(dialog):
        dialog.show()
        assert not refreshes
        qtbot.mouseClick(dialog.auto_merge_button, Qt.MouseButton.LeftButton)
        assert not refreshes and not dialog.isVisible()
        assert not dialog.replaced_names
        return dialog.result()

    monkeypatch.setattr(ImportConflictDialog, 'exec', review)
    window.import_words()
    assert len(refreshes) == 1 and path.read_bytes() == before_file
    saved = repo.get(original.id)
    assert saved.text == original.text and saved.learned and not saved.important
    assert saved.phonetic == 'sound' and saved.senses == original.senses
    message = QTextDocument()
    assert len(messages) == 2
    assert messages[0] == '合并并修改 1 条\n无需修改 0 条\n剩余待人工 0 条'
    message.setHtml(messages[1])
    assert message.toPlainText() == '新增 1 个主词，覆盖 0 个主词。'
    if route == 'study_full':
        restored = repo.study_backup()
        assert restored.records == backup.records and restored.task_history == backup.task_history


def test_auto_merge_does_not_write_back_to_source(repo, qtbot, tmp_path):
    repo.save(word())
    path = tmp_path / 'source.json'
    write_entries(path, [word(phonetic='/sound/')])
    source, entries = ImportSource.read(path, parse_word_or_study_file)
    before = path.read_bytes()
    result = repo.import_entries(entries)
    dialog = ImportConflictDialog(repo, result, source=source)
    qtbot.addWidget(dialog)
    dialog.auto_merge()
    assert result.auto_merge_stats.changed == 1 and path.read_bytes() == before
