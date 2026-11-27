from copy import deepcopy
from datetime import date

import pytest
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication

from vocabulary.domain import RelatedWord, Sense, WordEntry, WordContentValidationError
from vocabulary.learning import StudyContext
from vocabulary.related_dialog import LinkQuestion, RelatedConflictDialog
from vocabulary.related_links import changes
from vocabulary.storage import Repository


def child(text, translation='meaning', phonetic=''):
    return RelatedWord(text, [Sense('n.', translation)], phonetic)


def word(text, related=(), translation='meaning', **kwargs):
    return WordEntry(text, [Sense('n.', translation)], list(related), **kwargs)


def save(repo, draft, baseline=None, **kwargs):
    return repo.save_links(repo.prepare_links(draft, baseline, **kwargs))


def test_add_links_only_to_existing_main_and_preserve_metadata(repo):
    target = repo.save(word('B', [child('other')], important=False, learned=True, phonetic='b'))
    original = deepcopy(target)
    result = save(repo, word('A', [child('b', phonetic='b'), child('missing')], phonetic='a'))
    assert [entry.text for entry in repo.list_entries()] == ['A', 'B']
    updated = repo.get(target.id)
    assert updated.related == original.related + [child('A', phonetic='a')]
    assert (updated.id, updated.important, updated.learned, updated.sort_order) == (
        original.id, original.important, original.learned, original.sort_order)
    assert len(result.changes) == 2
    assert result.saved.related[-1].text == 'missing'
    reopened = Repository(repo.path)
    assert reopened.get(target.id) == updated
    reopened.close()


def test_existing_reverse_is_not_compared_or_overwritten(repo):
    b = repo.save(word('B', [child('a', 'independent', 'old')]))
    plan = repo.prepare_links(word('A', [child('B')]), None)
    assert not plan.conflicts
    repo.save_links(plan)
    assert repo.get(b.id) == b


@pytest.mark.parametrize('mutation', ['phonetic', 'pos', 'translation', 'count', 'order', 'internal_space'])
def test_conflicts_include_all_ordered_content(repo, mutation):
    b = repo.save(word('B'))
    b.senses.append(Sense('v.', 'second'))
    b = repo.save(b)
    related = RelatedWord('b', deepcopy(b.senses), b.phonetic)
    if mutation == 'phonetic':
        related.phonetic = 'x'
    elif mutation == 'pos':
        related.senses[0].pos = 'adj.'
    elif mutation == 'translation':
        related.senses[0].translation = 'Meaning'
    elif mutation == 'count':
        related.senses.append(Sense('n.', 'more'))
    elif mutation == 'order':
        related.senses.reverse()
    else:
        related.senses[0].translation = 'mean ing'
    assert len(repo.prepare_links(word('A', [related]), None).conflicts) == 1


def test_strip_and_case_insensitive_names_but_no_content_normalization(repo):
    repo.save(word('B', phonetic='b'))
    assert not repo.prepare_links(word('A', [child(' b ', ' meaning ', ' b ')]), None).conflicts
    baseline = word('A', [child('B')])
    draft = word('A', [child(' b ', 'different', 'changed')])
    assert changes(baseline, draft) == (set(), set())
    assert not repo.prepare_links(draft, baseline).conflicts


@pytest.mark.parametrize('related,message', [
    ([child('B'), child(' b ')], '不可有重复同名关联词'),
    ([child(' a ')], '关联词不可与主词同名'),
])
def test_form_validation_and_import_bypass(repo, related, message):
    with pytest.raises(WordContentValidationError, match=message):
        repo.prepare_links(word('A', related), None)
    assert repo.import_entries([word('A', related)]).inserted == 1


def test_inline_legacy_duplicates_are_not_repaired_or_blocked(repo):
    current = repo.save(word('A', [child('B'), child('B')]))
    draft = deepcopy(current)
    draft.phonetic = 'a'
    assert save(repo, draft, current, inline=True).saved.related == current.related
    draft.related.append(child(' b '))
    with pytest.raises(WordContentValidationError, match='不可有重复同名关联词'):
        repo.prepare_links(draft, current, inline=True)


def test_remove_rename_and_new_main_name_policy(repo):
    b = repo.save(word('B', [child('A', 'old'), child('A2', 'different'), child('keep')]))
    c = repo.save(word('C'))
    a = repo.save(word('A', [child('B')]))
    draft = deepcopy(a)
    draft.text = 'A2'
    draft.related = [child('C')]
    result = save(repo, draft, a)
    assert [item.text for item in repo.get(b.id).related] == ['A', 'keep']
    assert repo.get(c.id).related == [child('A2')]
    assert len(result.changes) == 3


def test_deleted_and_readded_name_does_not_repair_reverse(repo):
    b = repo.save(word('B'))
    a = repo.save(word('A', [child('B')]))
    save(repo, deepcopy(a), a)
    assert repo.get(b.id).related == []


def test_plan_uses_loaded_baseline_not_database_at_save(repo):
    b = repo.save(word('B', [child('A')]))
    baseline = repo.save(word('A', [child('B')]))
    changed = deepcopy(baseline)
    changed.related = []
    repo.save(changed)
    plan = repo.prepare_links(changed, baseline)
    repo.save_links(plan)
    assert repo.get(b.id).related == []


def test_atomic_failure_rolls_back_all_targets_and_revision(repo, monkeypatch):
    b = repo.save(word('B'))
    c = repo.save(word('C'))
    plan = repo.prepare_links(word('A', [child('B'), child('C')]), None)
    revision, maximum = repo.content_revision, repo.max_pending_sort_order
    original = repo._save_entry

    def fail(entry, counters):
        original(entry, counters)
        if entry.id == c.id:
            raise RuntimeError('disk failure')

    monkeypatch.setattr(repo, '_save_entry', fail)
    with pytest.raises(RuntimeError, match='disk failure'):
        repo.save_links(plan)
    assert repo.list_entries() == [b, c]
    assert repo.content_revision == revision
    assert repo.max_pending_sort_order == maximum
    assert plan.draft.id is None
    monkeypatch.setattr(repo, '_save_entry', original)
    repo.save_links(plan)
    assert repo.get(b.id).related == [child('A')]


@pytest.mark.parametrize('change', ['target', 'source', 'appeared', 'disappeared'])
def test_stale_plan_refuses_all_writes(repo, change):
    b = repo.save(word('B'))
    a = repo.save(word('A'))
    draft = deepcopy(a)
    draft.related = [child('B'), child('missing')]
    plan = repo.prepare_links(draft, a)
    if change == 'target':
        b.phonetic = 'external'
        repo.save(b)
    elif change == 'source':
        a.phonetic = 'external'
        repo.save(a)
    elif change == 'appeared':
        repo.save(word('missing'))
    else:
        repo.delete(b.id)
    entries = repo.list_entries()
    with pytest.raises(ValueError):
        repo.save_links(plan)
    assert repo.list_entries() == entries


def test_target_flag_change_is_preserved(repo):
    b = repo.save(word('B'))
    plan = repo.prepare_links(word('A', [child('B')]), None)
    repo.set_flag(b.id, 'learned', True)
    repo.set_flag(b.id, 'important', False)
    repo.save_links(plan)
    assert repo.get(b.id).learned and not repo.get(b.id).important


def test_question_defaults_and_cancel(qtbot, themed_app):
    question = LinkQuestion()
    qtbot.addWidget(question)
    question.show()
    assert question.yes.isDefault()
    qtbot.keyClick(question.yes, Qt.Key.Key_Return)
    assert question.choice is True
    question = LinkQuestion()
    qtbot.addWidget(question)
    question.show()
    qtbot.keyClick(question, Qt.Key.Key_Escape)
    assert question.choice is None


def test_question_tab_no_then_enter(qtbot, themed_app):
    question = LinkQuestion()
    qtbot.addWidget(question)
    question.show()
    question.activateWindow()
    qtbot.wait(10)
    question.yes.setFocus()
    qtbot.keyClick(question.yes, Qt.Key.Key_Tab)
    assert question.no.hasFocus()
    qtbot.keyClick(question.no, Qt.Key.Key_Return)
    assert question.choice is False


def test_compare_edit_adopt_reorder_and_atomic_save(repo, qtbot, themed_app):
    b = repo.save(word('B', translation='local'))
    c = repo.save(word('C', translation='local C'))
    plan = repo.prepare_links(word('A', [child('B', 'incoming'), child('C', 'incoming C')]), None)
    dialog = RelatedConflictDialog(plan, repo.save_links)
    qtbot.addWidget(dialog)
    dialog.show()
    _, left, right = dialog.cards[0]
    assert left.spelling.isReadOnly() and right.spelling.isReadOnly()
    right.phonetic.setText('new')
    right.add_row(Sense('v.', 'second'))
    right.move_row(right.rows[1], -1)
    dialog.adopt(right, left)
    assert left.value().senses == [Sense('v.', 'second'), Sense('n.', 'incoming')]
    assert repo.get(b.id) == b
    dialog.cards[1][1].rows[0].translation.setPlainText('edited C')
    dialog.save_all()
    assert dialog.result_value is not None
    assert repo.get(b.id).phonetic == 'new'
    assert repo.get(b.id).senses == left.value().senses
    assert repo.get(c.id).senses == [Sense('n.', 'edited C')]
    assert dialog.result_value.saved.related[1].senses == [Sense('n.', 'incoming C')]


def test_compare_failure_retry_and_cancel(repo, qtbot, themed_app):
    b = repo.save(word('B'))
    plan = repo.prepare_links(word('A', [child('B', 'conflict')]), None)
    attempts = []

    def commit(*args):
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError('retry')
        return repo.save_links(*args)

    dialog = RelatedConflictDialog(plan, commit)
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.cards[0][1].phonetic.setText('keep edit')
    dialog.save_all()
    assert dialog.isVisible() and dialog.result_value is None
    assert repo.get(b.id) == b
    assert dialog.cards[0][1].phonetic.text() == 'keep edit'
    dialog.save_all()
    assert repo.get(b.id).phonetic == 'keep edit'
    cancel = RelatedConflictDialog(plan, commit)
    qtbot.addWidget(cancel)
    cancel.show()
    cancel.cards[0][1].phonetic.setText('discard')
    qtbot.keyClick(cancel, Qt.Key.Key_Escape)
    assert cancel.result_value is None
    assert repo.get(b.id).phonetic == 'keep edit'


@pytest.mark.parametrize('action', ['adopt_left', 'adopt_right', 'remove_sense', 'close'])
def test_comparison_disposes_populated_editors_without_deleted_viewport_callbacks(
        window, repo, qtbot, action):
    from PySide6.QtCore import QEvent
    import gc
    import weakref
    import shiboken6

    repo.save(word('ballpoint', translation='圆珠笔1'))
    draft = word('football', [child('ballpoint', '圆珠笔')])
    dialog = RelatedConflictDialog(repo.prepare_links(draft, None), repo.save_links, window)
    dialog.show()
    qtbot.wait(10)
    _, left, right = dialog.cards[0]
    disposed = right.rows[0] if action == 'adopt_left' else left.rows[0]
    old_row = weakref.ref(disposed)
    disposed.translation.setFocus()
    qtbot.keyClicks(disposed.translation, '1')
    # Replay the late textChanged observed in application.log while Qt tears
    # down the viewport, before all of the editor's children are disconnected.
    placeholder = disposed.translation.enter_placeholder
    disposed.translation.viewport().destroyed.connect(lambda: placeholder.refresh())
    with qtbot.captureExceptions() as errors:
        if action.startswith('adopt'):
            source = left if action == 'adopt_left' else right
            qtbot.mouseClick(source.adopt_button, Qt.MouseButton.LeftButton)
        elif action == 'remove_sense':
            left.add_row(Sense('v.', 'second'))
            qtbot.mouseClick(disposed.remove_button, Qt.MouseButton.LeftButton)
        else:
            dialog.close()
            dialog.deleteLater()
        del disposed
        QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        gc.collect()
        qtbot.wait(10)
    assert old_row() is None or not shiboken6.isValid(old_row())
    assert not errors, [(kind.__name__, str(error)) for kind, error, _ in errors]
    if action != 'close':
        dialog.deleteLater()
        QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_form_decline_saves_and_refreshes_target(window, repo):
    b = repo.save(word('B', translation='local'))
    window.refresh()
    window.reset_form(word('A', [child('B', 'incoming')]))
    QTimer.singleShot(0, lambda: QApplication.activeModalWidget().no.click())
    assert window._save_form()
    assert repo.get(b.id).related == [child('A')]
    assert window.cached_entry(b.id) == repo.get(b.id)


def test_repeated_adoption_in_modal_form_comparison_has_no_late_errors(window, repo, qtbot):
    import gc

    repo.save(word('ballpoint', translation='圆珠笔1'))
    window.reset_form(word('football', [child('ballpoint', '圆珠笔')]))

    def answer():
        QApplication.activeModalWidget().yes.click()
        QTimer.singleShot(0, adopt)

    def adopt():
        dialog = QApplication.activeModalWidget()
        for _ in range(20):
            _, left, right = dialog.cards[0]
            qtbot.mouseClick(left.adopt_button, Qt.MouseButton.LeftButton)
            qtbot.wait(5)
            qtbot.mouseClick(right.adopt_button, Qt.MouseButton.LeftButton)
            gc.collect()
            qtbot.wait(5)
        dialog.reject()

    with qtbot.captureExceptions() as errors:
        QTimer.singleShot(0, answer)
        assert not window._save_form()
        qtbot.wait(10)
    assert not errors, [(kind.__name__, str(error)) for kind, error, _ in errors]


def test_form_compare_cancel_preserves_original_draft(window, repo):
    b = repo.save(word('B'))
    window.reset_form(word('A', [child('B', 'incoming')]))
    original = window.form_value()

    def answer():
        QApplication.activeModalWidget().yes.click()
        QTimer.singleShot(0, cancel)

    def cancel():
        dialog = QApplication.activeModalWidget()
        dialog.cards[0][2].phonetic.setText('discard')
        dialog.reject()

    QTimer.singleShot(0, answer)
    assert not window._save_form()
    assert window.form_value() == original
    assert repo.list_entries() == [b]


def test_form_duplicate_toast_anchor_and_main_rename_self(window, repo, monkeypatch):
    messages = []
    monkeypatch.setattr(window.toast, 'show_message', lambda text, anchor=None, **kw: messages.append((text, anchor)))
    window.reset_form(word('A', [child('B'), child(' b ')]))
    assert not window._save_form()
    assert messages[-1] == ('不可有重复同名关联词', window.save_button)
    window.reset_form(word('A', [child('B')]))
    window.blocks[0].word.setText('b')
    assert not window._save_form()
    assert messages[-1][0] == '关联词不可与主词同名'
    assert not repo.list_entries()


@pytest.mark.parametrize('page', ['pending', 'all', 'learned', 'study17'])
def test_inline_rename_delete_and_duplicate_keep_editor(window, repo, qtbot, monkeypatch, page):
    monkeypatch.setattr(StudyContext, 'now', classmethod(lambda cls: StudyContext(date(2026, 11, 2), False)))
    b = repo.save(word('B', [child('A', 'independent')]))
    c = repo.save(word('C'))
    a = repo.save(word('A', [child('B'), child('other')], learned=page == 'learned'))
    window.set_filter(page)
    window.refresh()
    table = window.current_table()
    table.revealRequested.emit(a.id, True)
    index = table._entry_index(a.id, 0, 0, 1)
    table.edit(index)
    editor = table.delegate.editor
    editor.setText(' OTHER ')
    assert not table.delegate.submit()
    assert table.delegate.editor is editor
    assert window.toast.label.text() == '不可有重复同名关联词'
    editor.setText('C')
    assert table.delegate.submit()
    qtbot.wait(10)
    assert repo.get(b.id).related == []
    assert repo.get(c.id).related == [child('A')]
    assert repo.get(a.id).related[0].text == 'C'
    table.edit(table._entry_index(a.id, 0, 0, 1))
    table.delegate.editor.setText('')
    assert table.delegate.submit()
    qtbot.wait(10)
    assert repo.get(c.id).related == []
    assert [item.text for item in repo.get(a.id).related] == ['other']


@pytest.mark.parametrize('accept', [False, True])
def test_inline_whole_paste_comparison_preserves_draft_and_atomicity(window, repo, qtbot, accept):
    b = repo.save(word('B', [child('A')]))
    c = repo.save(word('C', translation='local'))
    a = repo.save(word('A', [child('B')]))
    window.refresh()
    window.reset_form(word('unsaved', translation='independent'))
    original_form = window.form_value()
    window.reveal(a.id, True)
    table = window.table
    table.edit(table._entry_index(a.id, 0, 0, 1))
    editor = table.delegate.editor
    table.delegate._paste_word(child('C', 'pasted', 'sound'))

    def answer():
        QApplication.activeModalWidget().yes.click()
        QTimer.singleShot(0, finish)

    def finish():
        dialog = QApplication.activeModalWidget()
        assert repo.get(b.id).related == [child('A')]
        if accept:
            dialog.adopt(dialog.cards[0][2], dialog.cards[0][1])
            dialog.save_all()
        else:
            dialog.reject()

    QTimer.singleShot(0, answer)
    assert table.delegate.submit() is accept
    qtbot.wait(10)
    assert window.form_value() == original_form
    if accept:
        assert repo.get(b.id).related == []
        assert repo.get(c.id).senses == [Sense('n.', 'pasted')]
        assert repo.get(c.id).related == [child('A')]
        assert window.cached_entry(c.id) == repo.get(c.id)
    else:
        assert table.delegate.editor is editor
        assert repo.get(a.id) == a and repo.get(b.id) == b and repo.get(c.id) == c


def test_search_requeries_once_after_form_reverse_deletion(window, repo, monkeypatch):
    b = repo.save(word('B', [child('A', 'needle')]))
    a = repo.save(word('A', [child('B')], translation='needle'))
    window.refresh()
    window.search_box.setText('needle')
    window.reset_form(a)
    # Use a blank draft list; the deletion confirmation itself is covered by form tests.
    draft = deepcopy(a)
    draft.related = []
    monkeypatch.setattr(window, 'form_value', lambda: deepcopy(draft))
    requests = []
    original = window.list_jobs.request
    monkeypatch.setattr(window.list_jobs, 'request', lambda *args, **kw: (requests.append(1), original(*args, **kw))[1])
    assert window._save_form()
    assert len(requests) == 1
    assert window._display_cache.ids == {a.id}
    assert repo.get(b.id).related == []


def test_linked_edits_preserve_study_records(repo):
    b = repo.save(word('B', [child('A')]))
    c = repo.save(word('C'))
    a = repo.save(word('A', [child('B')]))
    context = StudyContext(date(2026, 11, 2), False)
    repo.prepare_study(context)
    repo.complete_study(b.id, context)
    progress = [tuple(row) for row in repo.connection.execute('SELECT * FROM memorize_vocabulary_17day')]
    histories = repo.study_task_histories()
    draft = deepcopy(a)
    draft.related = [child('C')]
    save(repo, draft, a)
    assert [tuple(row) for row in repo.connection.execute('SELECT * FROM memorize_vocabulary_17day')] == progress
    assert repo.study_task_histories() == histories
    assert repo.get(c.id).related == [child('A')]
