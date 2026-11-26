# SPDX-FileCopyrightText: 2024 Mido contributors
#
# SPDX-License-Identifier: MIT

"""Internal change tracking used to keep derived caches coherent.

This module is private.  It lets objects such as :class:`~mido.MidiTrack`
(and through them :attr:`~mido.MidiFile.merged_track`) notice in-place edits
to messages, so callers can edit tracks and messages directly without having
to invalidate or notify anything explicitly.

Messages define ``__eq__`` (and are therefore unhashable) and their
``vars()`` dictionary is part of their public value/equality semantics, so
bookkeeping must neither add instance attributes nor rely on a
``WeakKeyDictionary``.  Instead the registry is keyed by ``id(message)`` and
only holds weak references, which keeps message equality intact and avoids
extending object lifetimes.
"""

import weakref

# id(message) -> [weakref.ref(message), list[weakref.ref(owner)], revision]
_registry = {}


def _cleanup_when_collected(key, ref):
    entry = _registry.get(key)
    # Only remove the entry if it still belongs to the collected message.
    # (Guards against id reuse.)
    if entry is not None and entry[0] is ref:
        _registry.pop(key, None)


def _entry(message, create=False):
    key = id(message)
    entry = _registry.get(key)

    # Discard a stale entry whose referent has already been collected but
    # whose cleanup callback has not run yet (guards against id reuse).
    if entry is not None and entry[0]() is None:
        _registry.pop(key, None)
        entry = None

    if entry is None and create:
        ref = weakref.ref(
            message, lambda ref, key=key: _cleanup_when_collected(key, ref)
        )
        entry = [ref, [], 0]
        _registry[key] = entry
    return entry


def _prune_owners(entry):
    owners = entry[1]
    alive = []
    for ref in owners:
        if ref() is not None:
            alive.append(ref)
    entry[1] = alive


def connect(message, owner):
    """Register ``owner`` as an object to bump when ``message`` changes.

    ``owner`` is typically the :class:`~mido.MidiTrack` that contains the
    message.  Registering the same owner more than once is harmless.
    """
    entry = _entry(message, create=True)
    _prune_owners(entry)

    for ref in entry[1]:
        if ref() is owner:
            return

    entry[1].append(weakref.ref(owner))


def disconnect(message, owner):
    """Stop notifying ``owner`` about changes to ``message``."""
    entry = _entry(message)
    if entry is None:
        return

    for index, ref in enumerate(entry[1]):
        if ref() is owner:
            del entry[1][index]
            return


def notify(message):
    """Declare that ``message`` may have changed.

    Bumps the per-message revision and notifies every live registered owner.
    """
    entry = _entry(message, create=True)

    entry[2] += 1

    for ref in list(entry[1]):
        owner = ref()
        if owner is None:
            continue
        bump = getattr(owner, '_mido_bump_revision', None)
        if bump is not None:
            bump()


def message_revision(message):
    """Return the number of change notifications for ``message``.

    Returns ``0`` for messages that have never reported a change.
    """
    entry = _entry(message)
    return 0 if entry is None else entry[2]
