"""Share Ctrl+V recognition between the form and inline text editors."""
from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import QApplication

from .domain import ValidationError
from .word_clipboard import decode


def handle_word_paste(event, apply, reject):
    if (event.type() != QEvent.Type.KeyPress or event.key() != Qt.Key.Key_V
            or event.modifiers() != Qt.KeyboardModifier.ControlModifier):
        return False
    try:
        word = decode(QApplication.clipboard().text())
    except ValidationError as error:
        if not event.isAutoRepeat():
            reject(str(error))
        return True
    if word is None:
        return False
    if not event.isAutoRepeat():
        apply(word)
    return True


def set_word_field(field, text):
    # A previously imported word can be longer than QLineEdit's default limit.
    length = len(text.encode("utf-16-le", errors="surrogatepass")) // 2
    if length > field.maxLength():
        field.setMaxLength(length)
    field.setText(text)
