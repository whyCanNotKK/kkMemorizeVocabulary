from datetime import date
import sqlite3
import threading

import pytest
from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from vocabulary.domain import Sense, WordEntry
from vocabulary.import_dialog import ImportConflictDialog
from vocabulary.learning import StudyContext
from vocabulary.storage import Repository
from vocabulary.study_transfer import write_study_backup
from vocabulary.transfer import read_entries, write_entries
from vocabulary.transfer_jobs import run_repository_task


def word(text):
    return WordEntry(text, [Sense('n.', '1')])


def test_spinner_keeps_animating_and_blocks_input_close_and_reentry(window, qtbot, monkeypatch):
    overlay = window.busy_overlay
    editor = window.blocks[0].word
    editor.setText('draft')
    editor.setFocus()
    qtbot.wait(10)
    focus = QApplication.focusWidget()
    released = threading.Event()
    observations, clicks = [], []
    window.import_button.clicked.connect(lambda: clicks.append(True))
    monkeypatch.setattr(QFileDialog, 'getOpenFileName',
                        lambda *args: pytest.fail('busy task must not reenter import'))
    main_thread = threading.get_ident()

    def inspect():
        try:
            observations.append((overlay.isVisible(), overlay.active, overlay.angle,
                                 QApplication.focusWidget() is focus))
            qtbot.keyClicks(editor, 'blocked')
            qtbot.keyClick(editor, Qt.Key.Key_Escape)
            qtbot.mouseClick(window.import_button, Qt.MouseButton.LeftButton)
            window.import_words()
            window.resize(window.width() + 25, window.height() + 20)
            observations.append(overlay.geometry() == window.rect())
            observations.append(window.close())
        finally:
            released.set()

    QTimer.singleShot(90, inspect)

    def operation():
        if not released.wait(5):
            raise TimeoutError('GUI event loop stopped')
        return threading.get_ident()

    worker_thread = overlay.run('正在导入单词…', operation)
    assert worker_thread != main_thread
    assert observations[0][0:2] == (True, True)
    assert observations[0][2] > 0 and observations[0][3]
    assert observations[1:] == [True, False]
    assert not clicks and editor.text() == 'draft'
    assert window.isVisible() and not window._closing
    assert not overlay.active and not overlay.isVisible() and not overlay.timer.isActive()
    qtbot.keyClicks(editor, 'ok')
    assert editor.text() == 'draftok'


def test_worker_error_removes_overlay_and_preserves_focus_for_retry(window):
    overlay = window.busy_overlay
    editor = window.blocks[0].word
    editor.setFocus()
    focus = QApplication.focusWidget()

    def fail():
        raise OSError('disk full')

    with pytest.raises(OSError, match='disk full'):
        overlay.run('正在导出单词…', fail)
    assert not overlay.isVisible() and not overlay.active and not overlay.timer.isActive()
    assert QApplication.focusWidget() is focus
    assert overlay.run('正在导出单词…', lambda: 'retry') == 'retry'


def test_repository_tasks_keep_high_water_marks_and_use_thread_owned_connection(window, repo, monkeypatch):
    removed = repo.save(word('removed'))
    repo.delete(removed.id)
    thread_ids = []
    original = Repository.import_entries

    def import_entries(worker, entries):
        assert worker is not repo and worker.connection is not repo.connection
        thread_ids.append(threading.get_ident())
        return original(worker, entries)

    monkeypatch.setattr(Repository, 'import_entries', import_entries)
    result = run_repository_task(repo, window.busy_overlay, '正在导入单词…', 'import_entries', [word('new')])
    assert result.inserted == 1 and not result.conflicts
    imported = repo.list_entries()[0]
    assert imported.sort_order == removed.sort_order + 1
    assert repo.save(word('after')).sort_order == imported.sort_order + 1
    assert len(thread_ids) == 1 and thread_ids[0] != threading.get_ident()


def test_repository_failure_rolls_back_and_restores_busy_state(window, repo):
    repo.connection.execute("""CREATE TRIGGER fail_import BEFORE INSERT ON words
        WHEN NEW.text='bad' BEGIN SELECT RAISE(ABORT,'import failed'); END""")
    limits = repo.max_pending_sort_order, repo.max_learned_sort_order
    with pytest.raises(sqlite3.IntegrityError, match='import failed'):
        run_repository_task(repo, window.busy_overlay, '正在导入单词…', 'import_entries',
                            [word('first'), word('bad')])
    assert repo.list_entries() == []
    assert (repo.max_pending_sort_order, repo.max_learned_sort_order) == limits
    assert not window.busy_overlay.active
    assert repo.save(word('retry')).sort_order == 1


@pytest.mark.parametrize('study', [False, True])
@pytest.mark.parametrize('direction', ['import', 'export'])
def test_word_and_study_flows_show_feedback_but_release_it_for_dialogs(
        window, repo, monkeypatch, tmp_path, study, direction):
    path = tmp_path / 'transfer.json'
    expected = word('alpha')
    if study:
        context = StudyContext(date(2026, 11, 2), False)
        repo.save(expected)
        repo.prepare_study(context)
        backup = repo.study_backup()
        if direction == 'import':
            write_study_backup(path, backup)
        window.set_filter('study17')
    elif direction == 'import':
        write_entries(path, [expected])
    else:
        repo.save(expected)
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *args: (str(path), ''))
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *args: (str(path), ''))
    stages, messages = [], []
    original_run = window.busy_overlay.run

    def run(message, operation):
        def work():
            stages.append((message, window.busy_overlay.active))
            return operation()
        return original_run(message, work)

    monkeypatch.setattr(window.busy_overlay, 'run', run)

    def choose():
        assert not window.busy_overlay.active
        return 'full'

    monkeypatch.setattr(window, 'choose_study_import_mode', choose)

    def information(*args):
        assert not window.busy_overlay.active
        messages.append(args[-1])

    monkeypatch.setattr(QMessageBox, 'information', information)
    monkeypatch.setattr(QMessageBox, 'critical', lambda *args: pytest.fail(args[-1]))
    getattr(window, direction + '_words')()
    assert stages and all(active for _, active in stages)
    assert all(message.startswith('正在') and '\n' not in message for message, _ in stages)
    assert messages and not window.busy_overlay.isVisible() and not window.shortcuts.suspended
    if not study and direction == 'export':
        assert read_entries(path)[0].text == 'alpha'


def test_cancel_and_invalid_file_leave_overlay_hidden_and_draft_untouched(window, monkeypatch, tmp_path):
    editor = window.blocks[0].word
    editor.setText('draft')
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *args: ('', ''))
    window.import_words()
    assert not window.busy_overlay.active and editor.text() == 'draft'
    path = tmp_path / 'invalid.json'
    path.write_text('{', encoding='utf-8')
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *args: (str(path), ''))
    errors = []

    def critical(*args):
        assert not window.busy_overlay.active
        errors.append(args[-1])

    monkeypatch.setattr(QMessageBox, 'critical', critical)
    window.import_words()
    assert errors == ['数据结构不合法，导入失败']
    assert editor.text() == 'draft' and not window.repository.list_entries()
    assert not window.busy_overlay.active and not window.shortcuts.suspended


def test_conflict_adoption_uses_its_own_overlay_and_releases_it_afterward(repo, qtbot, monkeypatch):
    original = repo.save(word('alpha'))
    incoming = word('alpha')
    incoming.senses[0].translation = 'changed'
    dialog = ImportConflictDialog(repo, repo.import_entries([incoming]))
    qtbot.addWidget(dialog)
    dialog.show()
    states = []
    replace = Repository.replace_imports

    def adopt(worker, choices):
        states.append(dialog.busy_overlay.active)
        return replace(worker, choices)

    monkeypatch.setattr(Repository, 'replace_imports', adopt)
    dialog.choose_all(True)
    assert states == [True]
    assert not dialog.busy_overlay.active and not dialog.isVisible()
    adopted = repo.list_entries()[0]
    assert adopted.id != original.id and adopted.senses[0].translation == 'changed'
