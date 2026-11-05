import pytest
from vocabulary.native_shortcuts import WindowHotkeys
from vocabulary.domain import RelatedWord, Sense, WordEntry


class KeyboardStub:
    def __init__(self, window):
        self.window = window
        self.installed = 0
        self.removed = []
        self.foreign = False

    def foreground(self):
        return -1 if self.foreign else int(self.window.winId())

    def modifiers(self):
        return 0

    def install(self, callback):
        self.installed += 1
        return self.installed

    def uninstall(self, handle):
        self.removed.append(handle)


@pytest.fixture
def native(window, monkeypatch):
    api = KeyboardStub(window)
    bridge = WindowHotkeys(window.shortcuts, api=api)
    monkeypatch.setattr(window.shortcuts, "native", bridge)
    yield bridge, api
    bridge.close()


@pytest.mark.parametrize("field_name", ["word", "pos", "translation"])
@pytest.mark.parametrize("action", ["add_sense", "add_related"])
def test_native_chord_reaches_form_before_ime_and_fires_once(window, qtbot, native, field_name, action):
    bridge, api = native
    block = window.blocks[0]
    field = block.word if field_name == "word" else getattr(block.rows[0], field_name)
    field.setFocus()
    bridge.refresh()
    assert bridge.handle and bridge.active()
    key = ord("J") if action == "add_sense" else ord("N")
    assert not bridge.dispatch_key(0xA2, True)
    assert bridge.dispatch_key(key, True)
    assert bridge.dispatch_key(key, True)  # Held keys must not repeat structural changes.
    assert bridge.dispatch_key(key, False)
    assert not bridge.dispatch_key(0xA2, False)
    qtbot.wait(20)
    assert len(block.rows) == (2 if action == "add_sense" else 1)
    assert len(window.blocks) == (2 if action == "add_related" else 1)


def test_native_chords_release_for_foreign_window_and_suspended_recorder(window, native):
    bridge, api = native
    bridge.refresh()
    assert bridge.handle
    api.foreign = True
    assert not bridge.dispatch_key(0xA2, True)
    assert not bridge.dispatch_key(ord("J"), True)
    bridge.refresh()
    assert bridge.handle is None and api.removed
    api.foreign = False
    bridge.refresh()
    assert bridge.handle
    window.shortcuts.set_suspended(True)
    assert bridge.handle is None
    assert not bridge.dispatch_key(ord("J"), True)
    window.shortcuts.set_suspended(False)
    assert bridge.handle


def test_native_chords_follow_saved_remapping_and_leave_editing_keys_alone(window, qtbot, native):
    bridge, api = native
    window.shortcuts.update(dict(window.shortcuts.values, add_sense="Alt+J"))
    bridge.refresh()
    bridge.dispatch_key(0xA2, True)
    assert not bridge.dispatch_key(ord("J"), True)
    assert not bridge.dispatch_key(ord("A"), True)
    bridge.dispatch_key(0xA2, False)
    bridge.dispatch_key(0xA4, True)
    assert bridge.dispatch_key(ord("J"), True)
    bridge.dispatch_key(ord("J"), False)
    bridge.dispatch_key(0xA4, False)
    qtbot.wait(20)
    assert len(window.blocks[0].rows) == 2


def test_queued_native_chord_is_discarded_after_configuration_changes(window, qtbot, native):
    bridge, api = native
    bridge.dispatch_key(0xA2, True)
    assert bridge.dispatch_key(ord("J"), True)
    window.shortcuts.update(dict(window.shortcuts.values, add_sense="Alt+J"))
    qtbot.wait(20)
    assert len(window.blocks[0].rows) == 1


@pytest.mark.parametrize("modifier_key", [0xA4, 0xA2], ids=["alt", "ctrl"])
def test_native_digits_switch_tabs_before_ime(window, qtbot, native, modifier_key):
    bridge, api = native
    for number, page in [(2, "all"), (3, "learned"), (4, "study17"), (1, "pending")]:
        window.setFocus()
        bridge.refresh()
        bridge.dispatch_key(modifier_key, True)
        assert bridge.dispatch_key(ord(str(number)), True)
        assert bridge.dispatch_key(ord(str(number)), False)
        bridge.dispatch_key(modifier_key, False)
        qtbot.wait(20)
        assert window.filter_name == page


def test_native_visibility_shortcut_fires_once_and_follows_remapping(window, qtbot, native):
    bridge, api = native
    window.blocks[0].word.setFocus()
    bridge.refresh()
    bridge.dispatch_key(0xA2, True)
    assert bridge.dispatch_key(ord("S"), True)
    assert bridge.dispatch_key(ord("S"), True)
    bridge.dispatch_key(ord("S"), False)
    bridge.dispatch_key(0xA2, False)
    qtbot.wait(20)
    assert not window.global_hidden
    window.shortcuts.update(dict(window.shortcuts.values, toggle_visibility="Alt+E"))
    bridge.dispatch_key(0xA2, True)
    assert not bridge.dispatch_key(ord("S"), True)
    bridge.dispatch_key(0xA2, False)
    bridge.dispatch_key(0xA4, True)
    assert bridge.dispatch_key(ord("E"), True)
    bridge.dispatch_key(ord("E"), False)
    bridge.dispatch_key(0xA4, False)
    qtbot.wait(20)
    assert window.global_hidden


def test_native_edit_shortcut_loads_selected_word_once(window, repo, qtbot, native, monkeypatch):
    entry = repo.save(WordEntry('edit', [Sense('n.', '编辑')]))
    window.refresh()
    window.table.setCurrentIndex(window.model.index(0, 1))
    window.table.setFocus()
    calls = []
    original = window.list_action
    monkeypatch.setattr(window, 'list_action', lambda *args: (calls.append(args), original(*args)))
    bridge, api = native
    bridge.refresh()
    bridge.dispatch_key(0xA2, True)
    assert bridge.dispatch_key(ord('E'), True)
    assert bridge.dispatch_key(ord('E'), True)
    bridge.dispatch_key(ord('E'), False)
    bridge.dispatch_key(0xA2, False)
    qtbot.wait(20)
    assert calls == [(entry.id, 'edit')]
    assert window.edit_id == entry.id and window.blocks[0].word.hasFocus()


def test_native_delete_shortcut_targets_focused_definition(window, qtbot, native, monkeypatch):
    block = window.blocks[0]
    block.add_sense()
    block.add_sense()
    qtbot.wait(20)
    rows = list(block.rows)
    rows[0].translation.setFocus()
    bridge, api = native
    bridge.refresh()
    bridge.dispatch_key(0xA2, True)
    assert bridge.dispatch_key(ord("K"), True)
    bridge.dispatch_key(ord("K"), False)
    bridge.dispatch_key(0xA2, False)
    qtbot.wait(20)
    assert block.rows == rows[1:]


@pytest.mark.parametrize("action", ["add", "remove"])
def test_native_related_actions_follow_focused_middle_word(window, qtbot, native, monkeypatch, action):
    window.reset_form(WordEntry("main", [Sense("n.", "main")],
                               [RelatedWord(name, [Sense("n.", name)]) for name in ("a", "b", "c")]))
    qtbot.wait(10)
    source = window.blocks[2].rows[0].translation
    source.setFocus()
    monkeypatch.setattr("vocabulary.window.confirm_remove", lambda *args: True)
    bridge, api = native
    bridge.refresh()
    key = ord("N" if action == "add" else "M")
    bridge.dispatch_key(0xA2, True)
    assert bridge.dispatch_key(key, True)
    assert bridge.dispatch_key(key, True)
    bridge.dispatch_key(key, False)
    bridge.dispatch_key(0xA2, False)
    qtbot.wait(10)
    assert [block.word.text() for block in window.blocks] == (
        ["main", "a", "b", "", "c"] if action == "add" else ["main", "a", "c"])
