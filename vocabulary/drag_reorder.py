"""Shared geometry and visual rules for entry, related-word and definition dragging."""
ANIMATION_MS = 150


def insertion_index(groups, source, top, base=0):
    target, offset = 0, base
    for i, group in enumerate(groups):
        if i == source:
            continue
        if top < offset + group[3] / 2:
            break
        target += 1
        offset += group[3]
    return target


def group_positions(groups, base=0):
    positions = {}
    for key, start, end, height in groups:
        positions[key] = float(base)
        base += height
    return positions


def edge_scroll_delta(y, height):
    return (-max(2, (32 - y) // 2) if y < 32 else
            max(2, (y - height + 32) // 2) if y > height - 32 else 0)
