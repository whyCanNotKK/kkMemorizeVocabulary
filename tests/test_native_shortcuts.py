import pytest
from vocabulary.native_shortcuts import WindowHotkeys


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
