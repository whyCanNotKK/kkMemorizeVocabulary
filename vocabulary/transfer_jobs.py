"""Run transfer database work on a connection owned entirely by its task thread."""
from .storage import Repository


def run_repository_task(repository, overlay, message, method, *args):
    # Snapshot only plain values on the GUI thread, never pass its connection to a worker.
    path = repository.path
    limits = repository.max_pending_sort_order, repository.max_learned_sort_order

    def operation():
        worker = Repository(path)
        try:
            worker.max_pending_sort_order = max(worker.max_pending_sort_order, limits[0])
            worker.max_learned_sort_order = max(worker.max_learned_sort_order, limits[1])
            value = getattr(worker, method)(*args)
            return value, (worker.max_pending_sort_order, worker.max_learned_sort_order)
        finally:
            worker.close()

    value, updated = overlay.run(message, operation)
    repository.max_pending_sort_order = max(repository.max_pending_sort_order, updated[0])
    repository.max_learned_sort_order = max(repository.max_learned_sort_order, updated[1])
    return value
