"""Guard against unnecessary layout work and whole-document JSON reformatting."""
import json

import pytest
from PySide6.QtCore import Qt

from vocabulary.domain import RelatedWord, Sense, WordEntry
from vocabulary.import_dialog import ElidedLabel, ImportConflictDialog
from vocabulary.study_transfer import parse_word_or_study_file
from vocabulary.transfer import ImportSource, serialize_entries


@pytest.mark.parametrize('action', ['phonetic', 'translation', 'related', 'choose_left', 'choose_right'])
def test_click_does_not_resize_unrelated_text_columns(repo, qtbot, monkeypatch, themed_app, action):
    for index in range(25):
        repo.save(WordEntry(f'word-{index}', [Sense('n.', '本地释义')], phonetic='local'))
    candidates = [WordEntry(f'word-{index}', [Sense('v.', '导入释义')],
                           [RelatedWord('new-child', [Sense('n.', '关联释义')], 'child')], phonetic='incoming')
                  for index in range(25)]
    dialog = ImportConflictDialog(repo, repo.import_entries(candidates))
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.wait(20)
    unrelated = dialog.cards[-1][1].word
    width = unrelated.width()
    widths = []
    resize = ElidedLabel.resizeEvent

    def record(label, event):
        if label is unrelated and event.size().width() != width:
            widths.append(event.size().width())
        return resize(label, event)

    monkeypatch.setattr(ElidedLabel, 'resizeEvent', record)
    left, right = dialog.cards[0]
    control = {'phonetic': right.description.phonetic_labels[-1],
               'translation': right.description.sense_labels[-1, 0][1],
               'related': right.description.word_labels[1],
               'choose_left': left.word, 'choose_right': right.word}[action]
    button = Qt.MouseButton.LeftButton if action.startswith('choose_') else Qt.MouseButton.RightButton
    qtbot.mouseClick(control, button)
    qtbot.wait(10)
    assert not widths, 'an unchanged card must not temporarily shrink and expand'
    assert unrelated.width() == width
    dialog.reject()


def test_flag_write_preserves_original_json_layout_and_escaped_keys(tmp_path):
    entry = WordEntry('one', [Sense('n.', '正文包含 "important": false 以及中文')], important=False)
    content = serialize_entries([entry, entry]).replace('  ', '\t')
    content = content.replace('"words"', '"\\u0077ords"')
    content = content.replace('"important": false', '"important" : false')
    path = tmp_path / 'layout.json'
    path.write_text(content, encoding='utf-8')
    source, _ = ImportSource.read(path, parse_word_or_study_file)
    source.set_flag(0, 'important', True)
    expected = content.replace('"important" : false', '"important" : true', 1)
    assert path.read_text(encoding='utf-8') == expected
    source.set_flag(1, 'learned', True)
    source.set_flag(0, 'important', False)
    source.set_flag(1, 'learned', False)
    assert path.read_text(encoding='utf-8') == content
    assert json.loads(source.content)['words'][0]['senses'][0]['translation'] == entry.senses[0].translation


def test_failed_flag_write_keeps_prior_edits_and_retry_positions(tmp_path, monkeypatch):
    entry = WordEntry('one', [Sense('n.', '释义')], important=False)
    path = tmp_path / 'retry.json'
    path.write_text(serialize_entries([entry, entry, entry]), encoding='utf-8')
    source, _ = ImportSource.read(path, parse_word_or_study_file)
    source.set_flag(0, 'important', True)
    previous = path.read_text(encoding='utf-8')

    def fail(*args):
        raise OSError('locked')

    with monkeypatch.context() as patch:
        patch.setattr('vocabulary.transfer.os.replace', fail)
        with pytest.raises(OSError, match='locked'):
            source.set_flag(1, 'important', True)
    assert path.read_text(encoding='utf-8') == previous == source.content
    source.set_flag(2, 'learned', True)
    words = json.loads(path.read_text(encoding='utf-8'))['words']
    assert [word['important'] for word in words] == [True, False, False]
    assert [word['learned'] for word in words] == [False, False, True]
    assert not list(tmp_path.glob('*.tmp'))


def test_flag_index_uses_last_duplicate_words_key_like_the_word_parser(tmp_path):
    entry = WordEntry('one', [Sense('n.', '释义')], important=False)
    content = serialize_entries([entry]).replace('{', '{"words": ["ignored"],', 1)
    path = tmp_path / 'duplicate-key.json'
    path.write_text(content, encoding='utf-8')
    source, _ = ImportSource.read(path, parse_word_or_study_file)
    source.set_flag(0, 'important', True)
    assert path.read_text(encoding='utf-8') == content.replace('"important": false', '"important": true')
