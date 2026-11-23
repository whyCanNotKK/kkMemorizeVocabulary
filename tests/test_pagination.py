from PySide6.QtCore import Qt

from vocabulary.pagination import PaginationBar


def test_navigation_is_requested_without_committing_new_page(qtbot):
    bar = PaginationBar()
    qtbot.addWidget(bar)
    bar.show()
    bar.set_page(2, 1100)
    requested = []
    bar.pageRequested.connect(requested.append)
    qtbot.mouseClick(bar.next_button, Qt.MouseButton.LeftButton)
    assert requested == [3]
    assert bar.page_number == 2
    assert bar.summary_label.text() == "共 1100 个主词 · 当前 501–1000"
    bar.set_page(3, 1100)
    assert not bar.next_button.isEnabled()
    assert bar.previous_button.isEnabled()
    bar.set_loading(True)
    assert not bar.page_input.isEnabled()
    assert not bar.first_button.isEnabled()
    bar.set_loading(False)
    assert bar.first_button.isEnabled()


def test_page_input_filters_paste_clamps_and_only_enter_requests(qtbot):
    bar = PaginationBar()
    qtbot.addWidget(bar)
    bar.show()
    bar.set_page(2, 4100)
    requested = []
    bar.pageRequested.connect(requested.append)
    bar.page_input.setFocus()
    bar.page_input.setText("-1.a５5")
    assert bar.page_input.text() == "15"
    assert not requested
    qtbot.keyClick(bar.page_input, Qt.Key.Key_Return)
    assert requested == [9]
    bar.page_input.setText("0")
    qtbot.keyClick(bar.page_input, Qt.Key.Key_Return)
    assert requested == [9, 1]
    bar.page_input.clear()
    qtbot.keyClick(bar.page_input, Qt.Key.Key_Return)
    assert requested == [9, 1]
    assert bar.page_input.text() == "2"
    bar.page_input.setText("7")
    qtbot.keyClick(bar.page_input, Qt.Key.Key_Escape)
    assert bar.page_input.text() == "2"
    bar.page_input.setText("7")
    bar.activateWindow()
    bar.page_input.setFocus()
    qtbot.waitUntil(bar.page_input.hasFocus)
    bar.next_button.setFocus()
    qtbot.waitUntil(lambda: bar.page_input.text() == "2")
    assert requested == [9, 1]


def test_empty_result_has_no_fake_page_and_single_page_keeps_navigation_bar(qtbot):
    bar = PaginationBar()
    qtbot.addWidget(bar)
    bar.show()
    assert bar.page_number is None and bar.page_count == 0
    assert bar.summary_label.text() == "共 0 个主词"
    assert not bar.page_input.isVisible()
    assert not bar.page_label.isVisible()
    assert not bar.pages_label.isVisible()
    assert not bar.next_button.isEnabled()
    bar.set_page(1, 1)
    assert bar.page_input.isVisible()
    assert bar.page_input.text() == "1"
    assert not bar.next_button.isEnabled()
    assert bar.summary_label.text() == "共 1 个主词 · 当前 1–1"


def test_long_numeric_input_clamps_and_loading_hides_old_count(qtbot):
    bar = PaginationBar()
    qtbot.addWidget(bar)
    bar.show()
    bar.set_page(2, 1250)
    requested = []
    bar.pageRequested.connect(requested.append)
    bar.page_input.setText("0" * 100 + "3")
    qtbot.keyClick(bar.page_input, Qt.Key.Key_Return)
    assert requested == [3]
    bar.set_page(2, 1250)
    bar.page_input.setText("9" * 5000)
    qtbot.keyClick(bar.page_input, Qt.Key.Key_Return)
    assert requested == [3, 3]
    bar.set_loading(True)
    assert "正在更新" in bar.summary_label.text()
    assert not bar.page_input.isEnabled()
    bar.set_loading(False)
    assert "1250" in bar.summary_label.text()
    assert "501" in bar.summary_label.text()
