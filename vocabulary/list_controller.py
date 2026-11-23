"""Window orchestration for bounded list snapshots and asynchronous reads.

The input query is a draft. Only a successfully committed snapshot determines
which words the table, toolbar and export actions operate on.
"""
from copy import deepcopy

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication

from .list_state import ListState
from .list_jobs import ListJobs
from .list_query import normalize_keyword
from .theme import icon
from .learning import StudyContext


class ListControllerMixin:
    def init_lists(self):
        self.list_state = ListState()
        self.list_jobs = ListJobs(self.repository.path, self)
        self.list_jobs.success.connect(self.list_loaded)
        self.list_jobs.failure.connect(self.list_failed)
        self._display_cache = None
        self._list_loading = False
        self._list_request = None
        self._pending_search_anchor = None
        self._enter_search_intent = False
        self._pending_entries = {}
        self._pending_edit_target = None
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(150)
        self._search_timer.timeout.connect(self.run_search)
        self._known_content_revision = getattr(self.repository, 'content_revision', 0)
        self.revealed = self.list_state.normal['pending'].revealed_ids

    def applied_keyword(self):
        return self._display_cache.keyword if self._display_cache is not None else ''

    def save_list_view(self):
        cache = self._display_cache
        if cache is None or self.filter_name == 'study17':
            return
        cache.view_state = self.table.view_state()
        cache.scroll = self.table.verticalScrollBar().value()
        self._filter_scroll[cache.category] = cache.scroll
        self._filter_view[cache.category] = cache.view_state
        if cache.keyword:
            self.list_state.remember_search_position()

    def list_layout(self):
        studying = self.filter_name == 'study17'
        self.search_box.setVisible(not studying)
        self.editor_panel.setVisible(not studying)
        self.editor_panel.setEnabled(not studying)
        self.history_panel.setVisible(studying)
        self.table.setVisible(not studying)
        self.study_page.setVisible(studying)
        self.pagination.setVisible(not studying)
        self.list_loading_label.setVisible(not studying and self._list_loading)
        searching = bool(self.applied_keyword()) and not studying
        import_label = '导入学习进度' if studying else '导入单词'
        self.import_button.setToolTip(import_label)
        self.import_button.setAccessibleName(import_label)
        export_label = ('导出学习进度' if studying else
                        '导出全部搜索结果' if searching else '导出当前分类全部单词')
        self.export_button.setToolTip(export_label)
        self.export_button.setAccessibleName(export_label)
        for key, control in self.filter_buttons.items():
            active = key == self.filter_name
            if control.property('active') != active:
                control.setProperty('active', active)
                control.style().unpolish(control)
                control.style().polish(control)
        self.update_tab_tooltips()
        self.update_visibility_button()

    def set_list_loading(self, loading):
        self._list_loading = loading
        self.table.setEnabled(not loading)
        self.pagination.set_loading(loading)
        self.list_loading_label.setText('正在搜索…' if normalize_keyword(self.search_box.text()) else '正在加载…')
        self.list_loading_label.setVisible(loading and self.filter_name != 'study17')
        self.export_button.setEnabled(not loading)
        if loading:
            self.search_box.set_result_count(None)
        self.global_hide_button.setEnabled(not loading)

    def request_list(self, category, keyword='', page_number=1, anchor=None,
                     *, intent='restore', force=False, reveal_id=None):
        if self._closing:
            return
        if self._pending_entries and self.current_table().delegate.editor is None:
            self.refresh_after_inline(requery=False)
        keyword = normalize_keyword(keyword)
        revision = getattr(self.repository, 'content_revision', 0)
        if revision != self._known_content_revision:
            self._known_content_revision = revision
            self.list_state.bump_revision()
            self.list_state.invalidate()
            self.study_page.invalidate_contents()
        self.save_list_view()
        cache = self.list_state.cache_for(category, keyword, None if anchor else page_number)
        if cache is not None and anchor and anchor[0] not in cache.ids:
            cache = None
        action = {'kind': intent, 'anchor': anchor, 'reveal_id': reveal_id}
        if cache is not None and cache.valid and not force:
            self.list_jobs.cancel()
            self._list_request = None
            self.set_list_loading(False)
            self.show_list_cache(cache, action)
            return
        action['restore_focus'] = self.table.hasFocus()
        self.set_list_loading(True)
        action['loading_focus_id'] = id(QApplication.focusWidget())
        requested = self.list_jobs.request(
            category, keyword, page_number or 1,
            anchor_id=anchor[0] if anchor else None,
            revision=self.list_state.revision, intent=action)
        self._list_request = requested if self._list_loading else None

    def list_loaded(self, request, result):
        if (self._closing or request is not self.list_jobs.latest_request
                or request.revision != self.list_state.revision):
            return
        if getattr(self.repository, 'content_revision', 0) != self._known_content_revision:
            self._list_request = None
            self.request_list(request.category, request.keyword, request.page_number,
                              request.intent.get('anchor'), intent=request.intent['kind'], force=True,
                              reveal_id=request.intent.get('reveal_id'))
            return
        self.save_list_view()
        cache = self.list_state.apply(result)
        self._list_request = None
        self.set_list_loading(False)
        self.show_list_cache(cache, request.intent)

    def cancel_list_intents(self):
        self._pending_edit_target = None
        self._enter_search_intent = False
        if hasattr(self, 'table'):
            self.table.cancel_deferred_edit()

    def list_failed(self, request, error):
        if self._closing or request is not self.list_jobs.latest_request:
            return
        self._list_request = None
        self.cancel_list_intents()
        self.set_list_loading(False)
        # No UI error or retry action: retain the last committed snapshot.
        if self._display_cache is not None:
            self.search_box.set_result_count(
                self._display_cache.total if self.applied_keyword() else None)
        else:
            self.pagination.summary_label.clear()
        self.list_layout()
        self.restore_loading_focus(request.intent)

    def restore_loading_focus(self, action):
        if (action and action.get('restore_focus')
                and id(QApplication.focusWidget()) == action.get('loading_focus_id')):
            with self.table.preserve_view():
                self.table.setFocus(Qt.FocusReason.OtherFocusReason)

    def show_list_cache(self, cache, action=None):
        action = action or {'kind': 'restore'}
        if cache.keyword != self.applied_keyword():
            self.list_state.reset_search_positions(cache.keyword)
        self.filter_name = cache.category
        self._view_filter = cache.category
        self._display_cache = cache
        self.revealed = self.list_state.normal[cache.category].revealed_ids
        self.list_layout()
        labels = {'pending': '待记', 'all': '全部', 'learned': '已记'}
        for key, label in labels.items():
            self.filter_buttons[key].setText(f'{label}  {cache.counts.get(key, 0)}')
        self.search_box.set_result_count(cache.total if cache.keyword else None)
        self.pagination.set_page(cache.page_number, cache.total)
        shuffle_count = (max(cache.counts.get('pending', 0), cache.counts.get('learned', 0))
                         if cache.category == 'all' else cache.total)
        self.table.set_page_state(cache.category, bool(cache.keyword), shuffle_count)
        revealed = ({entry.id for entry in cache.entries} if cache.keyword else cache.revealed_ids)
        reveal_id = action.get('reveal_id')
        if reveal_id is not None and not cache.keyword:
            if action['kind'] == 'manual_hide':
                cache.revealed_ids.discard(reveal_id)
            else:
                cache.revealed_ids.add(reveal_id)
            if action['kind'] in {'manual_reveal', 'manual_hide'}:
                self.list_state.update_eye_from_visible(cache.category, cache.ids, cache.revealed_ids)
            revealed = cache.revealed_ids
        top = action['kind'] in {'top', 'enter'}
        state = (None, False, 0) if top or action['kind'] == 'search' else cache.view_state
        reset_search_scroll = action['kind'] == 'search' and bool(cache.keyword) and not action.get('anchor')
        position = 0 if top or reset_search_scroll else cache.scroll
        with self.table.preserve_view(scroll_position=position, view_state=state):
            self.model.populate(cache.entries, revealed)
            self.table.update_spans()
        anchor = action.get('anchor')
        if anchor:
            index = self.table._entry_index(*anchor)
            if index is not None and index.isValid():
                self.table.setCurrentIndex(index)
                self.table.ensure_entry_visible(anchor[0])
            elif action['kind'] == 'search':
                self.table.clearSelection()
                self.table.setCurrentIndex(self.model.index(-1, -1))
                self.table.scrollToTop()
        elif top:
            self.table.clearSelection()
            self.table.setCurrentIndex(self.model.index(-1, -1))
            self.table.scrollToTop()
        if action['kind'] == 'enter' and not cache.entries:
            self.search_box.setFocus(Qt.FocusReason.OtherFocusReason)
        elif action['kind'] in {'top', 'enter', 'activate', 'locate', 'marked'}:
            self.focus_list_result(first=top)
        if action['kind'] == 'search':
            self.search_box.setFocus(Qt.FocusReason.OtherFocusReason)
        if action['kind'] == 'focus_search':
            self.search_box.setFocus(Qt.FocusReason.ShortcutFocusReason)
        if action['kind'] == 'restore':
            self.restore_loading_focus(action)
        self.update_visibility_button()
        self.save_list_view()
        if self._pending_edit_target is not None:
            target, self._pending_edit_target = self._pending_edit_target, None
            if target[0] in cache.ids:
                self.table.edit_field(target)

    def focus_list_result(self, first=False):
        table = self.current_table()
        if first:
            table.navigate_entries(1, first=True)
        else:
            current = table.currentIndex()
            row = current.data(Qt.ItemDataRole.UserRole)
            if row is not None and table.selectionModel().isSelected(current):
                table.setFocus(Qt.FocusReason.OtherFocusReason)
                table.ensure_entry_visible(row.entry.id)
            else:
                table.navigate_entries(1)
        if not table.model().rows:
            table.setFocus(Qt.FocusReason.OtherFocusReason)

    def current_anchor(self):
        current = self.table.currentIndex()
        row = current.data(Qt.ItemDataRole.UserRole)
        if row is not None and self.table.selectionModel().isSelected(current):
            return (row.entry.id, row.owner, row.sense, current.column())
        return None

    def refresh(self, entries=None):
        if self.filter_name == 'study17':
            self.list_layout()
            return
        self.list_state.bump_revision()
        self.list_state.invalidate()
        cache = self._display_cache
        page = cache.page_number if cache is not None else 1
        self.request_list(self.filter_name, self.applied_keyword(), page or 1,
                          self.current_anchor(), force=True)

    def search_changed(self):
        if not self.current_table().delegate.submit():
            self.search_box.blockSignals(True)
            self.search_box.setText(getattr(self, '_applied_search', ''))
            self.search_box.blockSignals(False)
            return
        self.refresh_after_inline(requery=False)
        self._applied_search = self.search_box.text()
        self.cancel_list_intents()
        self._pending_search_anchor = self.current_anchor()
        self.save_list_view()
        self.list_jobs.cancel()
        self._list_request = None
        if normalize_keyword(self.search_box.text()):
            self.set_list_loading(True)
            self._search_timer.start()
        else:
            self._search_timer.stop()
            self.run_search()

    def run_search(self):
        if self._closing or self.filter_name == 'study17':
            return
        keyword = normalize_keyword(self.search_box.text())
        anchor = self._pending_search_anchor
        normal = self.list_state.normal[self.filter_name]
        page = 1 if keyword or anchor else (normal.page_number or 1)
        self.request_list(self.filter_name, keyword, page, anchor,
                          intent='search')

    def enter_search_results(self):
        if self._closing or self.filter_name == 'study17':
            return
        self._search_timer.stop()
        self._enter_search_intent = True
        self.request_list(self.filter_name, normalize_keyword(self.search_box.text()), 1,
                          intent='enter')
        return True

    def change_page(self, page_number):
        cache = self._display_cache
        if cache is None or self._list_loading or page_number == cache.page_number:
            return
        if not self.current_table().delegate.submit():
            self.pagination.set_page(cache.page_number, cache.total)
            return
        self.cancel_list_intents()
        self.request_list(cache.category, cache.keyword, page_number, intent='top')

    def set_filter(self, key, *, focus=False):
        if not self.current_table().delegate.submit():
            return False
        self.refresh_after_inline(requery=False)
        self._search_timer.stop()
        self.cancel_list_intents()
        self.save_list_view()
        if key == 'study17':
            if not self.study_page.reload():
                return False
            self.list_jobs.cancel()
            self._list_request = None
            self.set_list_loading(False)
            self.filter_name = key
            self.history_panel.set_collapsed(bool(self.study_page.tasks))
            self.list_layout()
            if focus:
                self.focus_list_result()
            return True
        keyword = normalize_keyword(self.search_box.text())
        cache = self.list_state.normal[key]
        page = cache.page_number or 1
        anchor = None
        if keyword:
            position = self.list_state.search_positions.get(key)
            page = position.page_number if position and position.keyword == keyword else 1
            if position and position.keyword == keyword and position.view_state[1]:
                anchor = position.view_state[0]
        self.request_list(key, keyword, page, anchor, intent='activate' if focus else 'restore')
        return True

    def activate_filter(self, key):
        self.set_filter(key, focus=True)

    def render_normal_cache(self):
        cache = self._display_cache
        if cache is None:
            return
        self.save_list_view()
        revealed = {entry.id for entry in cache.entries} if cache.keyword else cache.revealed_ids
        with self.table.preserve_view():
            self.model.populate(cache.entries, revealed)
            self.table.update_spans()
        self.update_visibility_button()

    def reveal(self, entry_id, expanded):
        if self.applied_keyword() or self._display_cache is None:
            return
        if entry_id not in self._display_cache.ids:
            if getattr(self.repository, 'content_revision', 0) != self._known_content_revision:
                self.request_list(self.filter_name, '', self._display_cache.page_number or 1,
                                  (entry_id, -1, 0, 1), force=True, reveal_id=entry_id,
                                  intent='manual_reveal' if expanded else 'manual_hide')
            return
        self.list_state.set_revealed(self.filter_name, entry_id, expanded, manual=True)
        entry = next((entry for entry in self._display_cache.entries if entry.id == entry_id), None)
        if entry is not None:
            self.table.replace_entry(entry, expanded)
        self.update_visibility_button()
        if expanded:
            self.table.ensure_entry_visible(entry_id)

    @property
    def global_hidden(self):
        if self.filter_name == 'study17':
            return self.study_page.global_hidden
        return not self.list_state.eye_visible[self.filter_name]

    def next_global_hidden(self):
        return not self.global_hidden

    def set_global_hidden(self, hidden=None):
        if self._list_loading or (self.filter_name != 'study17' and self.applied_keyword()):
            return
        if not self.current_table().delegate.submit():
            self.update_visibility_button()
            return
        self.refresh_after_inline(requery=False)
        hidden = self.next_global_hidden() if hidden is None else hidden
        if self.filter_name == 'study17':
            self.study_page.set_global_hidden(hidden)
            self.list_state.eye_visible['study17'] = not hidden
        else:
            self.list_state.eye_visible[self.filter_name] = not hidden
            cache = self.list_state.normal[self.filter_name]
            cache.revealed_ids.clear()
            if not hidden:
                cache.revealed_ids.update(entry.id for entry in cache.entries)
            self.render_normal_cache()
        self.update_visibility_button()

    def update_visibility_button(self):
        studying = self.filter_name == 'study17'
        searching = not studying and bool(self.applied_keyword())
        self.global_hide_button.setVisible(not searching)
        visible = not self.global_hidden
        self.global_hide_button.setIcon(icon('eye' if visible else 'eye_closed'))
        self.global_hide_button.setChecked(visible)
        self.global_hide_button.setAccessibleName('显示释义' if visible else '隐藏释义')
        if hasattr(self, 'shortcuts'):
            self.global_hide_button.setToolTip(
                f"显示/隐藏 本页释义 · {self.shortcuts.labels()['toggle_visibility']}")

    def note_list_mutation(self):
        self._known_content_revision = getattr(self.repository, 'content_revision', 0)
        self.list_state.bump_revision()
        self.list_jobs.cancel()
        self._list_request = None
        self.set_list_loading(False)

    def patch_entry_caches(self, entry):
        self.list_state.patch_entry(entry)
        self.study_page.patch_entry(entry)


    def study_visibility_changed(self, visible):
        self.list_state.eye_visible['study17'] = visible
        if hasattr(self, 'shortcuts'):
            self.update_visibility_button()

    def cached_entry(self, entry_id):
        if self.filter_name == 'study17':
            for task in self.study_page.tasks + self.study_page.completed_tasks:
                if task.entry.id == entry_id:
                    return deepcopy(task.entry)
        for cache in [self._display_cache, *self.list_state.normal.values(), self.list_state.search]:
            if cache is not None and (cache.valid or cache is self._display_cache):
                for entry in cache.entries:
                    if entry.id == entry_id:
                        return deepcopy(entry)
        for task in self.study_page.tasks + self.study_page.completed_tasks:
            if task.entry.id == entry_id:
                return deepcopy(task.entry)
        return self.repository.get(entry_id)

    def move_word(self, entry_id, target_index):
        cache = self._display_cache
        if cache is None or cache.keyword or cache.category == 'all':
            return
        try:
            source = next(i for i, entry in enumerate(cache.entries) if entry.id == entry_id)
            if not 0 <= target_index < len(cache.entries):
                return
            orders = self.repository.move_entry(entry_id, (cache.page_number - 1) * 500 + target_index)
        except Exception as error:
            self.show_notice(str(error), True)
            return
        self.note_list_mutation()
        cache.entries.insert(target_index, cache.entries.pop(source))
        self.list_state.patch_sort_orders(orders)
        self.study_page.patch_sort_orders(orders)
        self.list_state.invalidate([key for key in self.list_state.normal if key != cache.category])
        cache.valid = True
        self.render_normal_cache()

    def move_related_word(self, entry_id, source, target):
        self.reorder_word_part(entry_id, source, target)

    def move_word_sense(self, entry_id, owner, source, target):
        self.reorder_word_part(entry_id, source, target, owner=owner)

    def reorder_word_part(self, entry_id, source, target, owner=None):
        self.save_list_view()
        study_part, study_selected, study_scroll = self.study_page.table.view_state()
        entry = self.cached_entry(entry_id)
        items = entry.related if owner is None else (entry if owner == -1 else entry.related[owner]).senses
        permutation = list(range(len(items)))
        if not (0 <= source < len(items) and 0 <= target < len(items)):
            return
        anchor = self.current_anchor() if self.filter_name != 'study17' else self.study_page.table.view_state()[0]
        try:
            if owner is None:
                self.repository.move_related(entry_id, source, target)
            else:
                self.repository.move_sense(entry_id, owner, source, target)
        except Exception as error:
            self.show_notice(str(error), True)
            return
        items.insert(target, items.pop(source))
        permutation.insert(target, permutation.pop(source))
        self.note_list_mutation()
        self.patch_entry_caches(entry)
        self.list_state.remap_locations(
            entry_id, related_permutation=permutation if owner is None else None,
            sense_owner=owner, sense_permutation=permutation if owner is not None else None)
        if study_part and study_part[0] == entry_id:
            key, study_owner, study_sense, study_column = study_part
            if owner is None and study_owner >= 0:
                study_owner = permutation.index(study_owner)
            elif owner == study_owner:
                study_sense = permutation.index(study_sense)
            with self.study_page.table.preserve_view(
                    view_state=((key, study_owner, study_sense, study_column), study_selected, study_scroll)):
                pass
        table = self.current_table()
        table.replace_entry(entry)
        if anchor and anchor[0] == entry_id:
            entry_key, old_owner, sense, column = anchor
            if owner is None and old_owner >= 0:
                old_owner = permutation.index(old_owner)
            elif owner == old_owner:
                sense = permutation.index(sense)
            table.setCurrentIndex(table._entry_index(entry_key, old_owner, sense, column))
        self.save_list_view()

    def shuffle_words(self):
        cache = self._display_cache
        if cache is None or cache.keyword or not self.table.delegate.submit():
            return
        try:
            orders = self.repository.shuffle_entries(cache.category)
        except Exception as error:
            self.show_notice(str(error), True)
            return
        self.note_list_mutation()
        self.list_state.patch_sort_orders(orders)
        self.study_page.patch_sort_orders(orders)
        self.list_state.invalidate()
        self.request_list(cache.category, '', 1, intent='top', force=True)

    def defer_list_edit(self, target):
        self._pending_edit_target = target

    def refresh_after_inline(self, *, requery=True):
        if self._closing or self.current_table().delegate.editor is not None:
            return
        if self.current_table().dragging:
            QTimer.singleShot(50, self.refresh_after_inline)
            return
        changes, self._pending_entries = self._pending_entries, {}
        if not changes:
            return
        cache = self._display_cache
        anchor = self.current_anchor() if self.filter_name != 'study17' else None
        if cache is not None and anchor and anchor[0] in changes:
            changed = changes[anchor[0]]
            if changed is None or (cache.keyword and not self.entry_matches(changed, cache.keyword)):
                next_id = self.adjacent_entry_id(cache, anchor[0])
                anchor = (next_id, -1, 0, 1) if next_id != anchor[0] else None
        self.note_list_mutation()
        deleted = False
        for entry_id, entry in changes.items():
            if entry is None:
                self.list_state.remove_entry(entry_id)
                if self.duplicate_id == entry_id:
                    self.duplicate_id = None
                self.study_page.invalidate_contents()
                deleted = True
            else:
                self.patch_entry_caches(entry)
                self.current_table().replace_entry(entry)
        if self.filter_name == 'study17':
            if deleted:
                self.study_page.load(self.study_page.context)
            return
        cache = self._display_cache
        if cache is None:
            return
        if cache.keyword or deleted:
            self.list_state.invalidate()
            if requery:
                self.request_list(cache.category, cache.keyword, cache.page_number or 1,
                                  anchor, force=True)
        elif self.list_state.search is not None:
            self.list_state.search.valid = False

    def prepare_list_edit(self, entry_id, column):
        self.refresh_after_inline()
        if self._list_loading or column == 1:
            return
        row = next((row for row in self.current_table().model().rows if row.entry.id == entry_id), None)
        if row is not None and row.hidden:
            if self.filter_name == 'study17':
                self.study_page.reveal(entry_id, True)
            else:
                self.reveal(entry_id, True)

    def queue_saved_entry(self, entry_id):
        try:
            self._pending_entries[entry_id] = self.repository.get(entry_id)
        except ValueError:
            self._pending_entries[entry_id] = None
        if self.current_table().delegate.editor is None:
            self.refresh_after_inline()

    @staticmethod
    def entry_matches(entry, keyword):
        texts = [entry.text, *(word.text for word in entry.related),
                 *(sense.translation for sense in entry.senses),
                 *(sense.translation for word in entry.related for sense in word.senses)]
        return any(keyword in text.casefold() for text in texts)

    def form_saved(self, entry, is_new):
        pending = self._list_request
        pending_keyword = (normalize_keyword(self.search_box.text())
                           if self._list_loading or self._search_timer.isActive() else self.applied_keyword())
        pending_category = pending.category if pending is not None else self.filter_name
        self._search_timer.stop()
        self._pending_search_anchor = None
        self.cancel_list_intents()
        self.note_list_mutation()
        self.patch_entry_caches(entry)
        cache = self._display_cache
        category = 'pending' if is_new and pending_category == 'learned' else pending_category
        keyword = pending_keyword
        if not is_new and cache is not None and not keyword and not cache.keyword and category == cache.category:
            self.table.replace_entry(entry)
            if self.list_state.search is not None:
                self.list_state.search.valid = False
            return
        self.list_state.invalidate()
        anchor = (entry.id, -1, 0, 1)
        if is_new and keyword and not self.entry_matches(entry, keyword):
            if cache is not None and category == cache.category and keyword == cache.keyword:
                if is_new:
                    cache.counts['pending'] += 1
                    cache.counts['all'] += 1
                cache.valid = True
                self.show_list_cache(cache)
            else:
                self.request_list(category, keyword, 1, force=True)
            self.toast.show_message(f'已保存 {entry.text}，不在当前搜索结果中')
        else:
            if not is_new and keyword and cache is not None and not self.entry_matches(entry, keyword):
                next_id = self.adjacent_entry_id(cache, entry.id)
                anchor = (next_id, -1, 0, 1) if next_id != entry.id else None
            self.request_list(category, keyword, (cache.page_number or 1) if cache else 1, anchor,
                              intent='saved', force=True, reveal_id=entry.id if is_new else None)

    def list_action(self, entry_id, action):
        table = self.current_table()
        if self._list_loading or not table.delegate.submit():
            return
        self.refresh_after_inline(requery=False)
        if table.delegate.target_after_removal((entry_id, -1, 0, 1)) is None:
            return
        if action == 'edit':
            if self.edit_id == entry_id:
                self.set_form_collapsed(False)
                self.blocks[0].word.setFocus()
                return
            if not self.resolve_dirty():
                return
            self.reset_form(self.cached_entry(entry_id))
            self.blocks[0].word.setFocus()
            return
        if self.edit_id == entry_id and action == 'trash':
            if not self.resolve_dirty():
                return
            self.reset_form()
        cache = self._display_cache
        ids = [entry.id for entry in cache.entries] if cache is not None else []
        next_id = entry_id
        if action in ('learned', 'trash') and entry_id in ids:
            position = ids.index(entry_id)
            if position + 1 < len(ids):
                next_id = ids[position + 1]
            elif cache.after_id is not None:
                next_id = cache.after_id
            elif position:
                next_id = ids[position - 1]
            elif cache.before_id is not None:
                next_id = cache.before_id
        try:
            entry = self.cached_entry(entry_id)
            if action == 'important':
                self.repository.set_flag(entry_id, 'important', not entry.important)
                entry.important = not entry.important
                self.note_list_mutation()
                self.patch_entry_caches(entry)
                table.replace_entry(entry)
                return
            if action == 'learned':
                self.repository.set_flag(entry_id, 'learned', not entry.learned)
            elif action == 'trash':
                if not self.confirm_word_removal(entry_id):
                    return
                self.repository.delete(entry_id, self.study_page.context or StudyContext.now())
                self.list_state.remove_entry(entry_id)
                self.study_page.invalidate_contents()
                if self.study_page.context is not None:
                    self.study_page.load(self.study_page.context)
                self.history_panel.refresh()
            else:
                return
        except Exception as error:
            self.show_notice(str(error), True)
            return
        self.toast.dismiss()
        self.note_list_mutation()
        self.list_state.invalidate()
        self.request_list(cache.category, cache.keyword, cache.page_number or 1,
                          (next_id, -1, 0, 1), intent='marked', force=True)

    def locate_duplicate(self):
        if self.duplicate_id is None or not self.current_table().delegate.submit():
            return
        self._search_timer.stop()
        self.search_box.blockSignals(True)
        self.search_box.clear()
        self.search_box.blockSignals(False)
        self._applied_search = ''
        self.request_list('all', '', 1, (self.duplicate_id, -1, 0, 1), intent='locate')

    @staticmethod
    def adjacent_entry_id(cache, entry_id):
        ids = [entry.id for entry in cache.entries]
        if entry_id not in ids:
            return entry_id
        position = ids.index(entry_id)
        if position + 1 < len(ids):
            return ids[position + 1]
        if cache.after_id is not None:
            return cache.after_id
        if position:
            return ids[position - 1]
        return cache.before_id if cache.before_id is not None else entry_id
