# SPDX-FileCopyrightText: 2016 Ole Martin Bjorndalen <ombdalen@gmail.com>
#
# SPDX-License-Identifier: MIT

from ..messages import _changes
from .meta import MetaMessage


class MidiTrack(list):
    """A list of MIDI messages.

    The track keeps an internal revision counter that is incremented by
    every in-place edit (including edits made directly to the messages it
    contains).  This allows derived data, such as
    :attr:`MidiFile.merged_track`, to be cached until the arrangement
    actually changes -- callers never need to invalidate the cache
    themselves.
    """

    def __init__(self, messages=()):
        list.__init__(self, messages)
        self._version = 0
        for message in self:
            _changes.connect(message, self)

    @property
    def version(self):
        """Monotonic revision number; incremented on every edit."""
        return getattr(self, '_version', 0)

    @version.setter
    def version(self, value):
        self._version = value

    # Alias for the same concept under the "revision" naming.
    revision = version

    @property
    def _revision(self):
        return getattr(self, '_version', 0)

    @_revision.setter
    def _revision(self, value):
        self._version = value

    def _mido_bump_revision(self):
        """Called by the change bus when a contained message is edited."""
        self._version = getattr(self, '_version', 0) + 1

    # The message notification bus uses the name above; keep a plainly
    # named alias for internal/manual use.
    _bump_version = _mido_bump_revision

    @property
    def name(self):
        """Name of the track.

        This will return the name from the first track_name meta
        message in the track, or '' if there is no such message.

        Setting this property will update the name field of the first
        track_name message in the track. If no such message is found,
        one will be added to the beginning of the track with a delta
        time of 0."""
        for message in self:
            if message.type == 'track_name':
                return message.name
        else:
            return ''

    @name.setter
    def name(self, name):
        # Find the first track_name message and modify it.  Editing the
        # message in place notifies us through the change bus.
        for message in self:
            if message.type == 'track_name':
                message.name = name
                return
        else:
            # No track name found, add one.
            self.insert(0, MetaMessage('track_name', name=name, time=0))

    def copy(self):
        return self.__class__(self)

    def append(self, message):
        list.append(self, message)
        _changes.connect(message, self)
        self._mido_bump_revision()

    def extend(self, messages):
        messages = list(messages)
        list.extend(self, messages)
        for message in messages:
            _changes.connect(message, self)
        self._mido_bump_revision()

    def insert(self, index, message):
        list.insert(self, index, message)
        _changes.connect(message, self)
        self._mido_bump_revision()

    def __iadd__(self, messages):
        # list's in-place concat slot bypasses an overridden extend(), so
        # route through our versioned implementation explicitly.
        self.extend(messages)
        return self

    def __imul__(self, count):
        list.__imul__(self, count)
        # Repeated messages are the same objects and already registered.
        self._mido_bump_revision()
        return self

    def __setitem__(self, index_or_slice, value):
        if isinstance(index_or_slice, slice):
            new_messages = list(value)
            old_messages = list.__getitem__(self, index_or_slice)
            list.__setitem__(self, index_or_slice, new_messages)
            for message in old_messages:
                _changes.disconnect(message, self)
            for message in new_messages:
                _changes.connect(message, self)
        else:
            old_message = list.__getitem__(self, index_or_slice)
            list.__setitem__(self, index_or_slice, value)
            _changes.disconnect(old_message, self)
            _changes.connect(value, self)
        self._mido_bump_revision()

    def __delitem__(self, index_or_slice):
        removed = list.__getitem__(self, index_or_slice)
        list.__delitem__(self, index_or_slice)
        if isinstance(index_or_slice, slice):
            for message in removed:
                _changes.disconnect(message, self)
        else:
            _changes.disconnect(removed, self)
        self._mido_bump_revision()

    def pop(self, index=-1):
        message = list.pop(self, index)
        _changes.disconnect(message, self)
        self._mido_bump_revision()
        return message

    def remove(self, message):
        # list.remove() compares by value, so locate the actual object that
        # will be removed in order to detach it from the change bus.
        index = list.index(self, message)
        removed = list.__getitem__(self, index)
        list.remove(self, message)
        _changes.disconnect(removed, self)
        self._mido_bump_revision()

    def clear(self):
        for message in list(self):
            _changes.disconnect(message, self)
        list.clear(self)
        self._mido_bump_revision()

    def sort(self, *args, **kwargs):
        list.sort(self, *args, **kwargs)
        self._mido_bump_revision()

    def reverse(self):
        list.reverse(self)
        self._mido_bump_revision()

    def __getitem__(self, index_or_slice):
        # Retrieve item from the MidiTrack
        lst = list.__getitem__(self, index_or_slice)
        if isinstance(index_or_slice, int):
            # If an index was provided, return the list element
            return lst
        else:
            # Otherwise, construct a MidiTrack to return.
            # TODO: this make a copy of the list. Is there a better way?
            return self.__class__(lst)

    def __add__(self, other):
        return self.__class__(list.__add__(self, other))

    def __mul__(self, other):
        return self.__class__(list.__mul__(self, other))

    def __repr__(self):
        if len(self) == 0:
            messages = ''
        elif len(self) == 1:
            messages = f'[{self[0]}]'
        else:
            messages = '[\n  {}]'.format(',\n  '.join(repr(m) for m in self))
        return f'{self.__class__.__name__}({messages})'


def tracks_cache_token(tracks):
    """Return a cheap, comparable token describing the arrangement.

    The token changes whenever any track is added, removed, reordered or
    replaced, when a :class:`MidiTrack` is edited, or when a message inside
    a plain list-like track is modified.  It is used to decide whether a
    cached merged track can be reused.

    For normal :class:`MidiTrack` objects the cost is O(number of tracks),
    which makes repeatedly viewing a long unchanged score cheap.
    """
    parts = []
    for track in tracks:
        version = getattr(track, '_version', None)
        if version is not None:
            # The id detects replacement/reordering of whole tracks; the
            # version detects every in-place edit.
            parts.append((id(track), version))
        else:
            # Fallback for plain list-like tracks: track the identity and
            # per-message revision of every message.  This is O(messages)
            # but stays correct for tracks that are not MidiTrack.
            parts.append((
                id(track),
                tuple(
                    (id(message), _changes.message_revision(message))
                    for message in track
                ),
            ))
    return tuple(parts)


def _to_abstime(messages, skip_checks=False):
    """Convert messages to absolute time."""
    now = 0
    for msg in messages:
        now += msg.time
        yield msg.copy(skip_checks=skip_checks, time=now)


def _to_reltime(messages, skip_checks=False):
    """Convert messages to relative time."""
    now = 0
    for msg in messages:
        delta = msg.time - now
        yield msg.copy(skip_checks=skip_checks, time=delta)
        now = msg.time


def fix_end_of_track(messages, skip_checks=False):
    """Remove all end_of_track messages and add one at the end.

    This is used by merge_tracks() and MidiFile.save()."""
    # Accumulated delta time from removed end of track messages.
    # This is added to the next message.
    accum = 0

    for msg in messages:
        if msg.type == 'end_of_track':
            accum += msg.time
        else:
            if accum:
                delta = accum + msg.time
                yield msg.copy(skip_checks=skip_checks, time=delta)
                accum = 0
            else:
                yield msg

    yield MetaMessage('end_of_track', time=accum)


def merge_tracks(tracks, skip_checks=False):
    """Returns a MidiTrack object with all messages from all tracks.

    The messages are returned in playback order with delta times
    as if they were all in one track.

    Pass skip_checks=True to skip validation of messages before merging.
    This should ONLY be used when the messages in tracks have already
    been validated by mido.checks.
    """
    messages = []
    for track in tracks:
        messages.extend(_to_abstime(track, skip_checks=skip_checks))

    messages.sort(key=lambda msg: msg.time)

    return MidiTrack(
        fix_end_of_track(
            _to_reltime(messages, skip_checks=skip_checks),
            skip_checks=skip_checks,
        )
    )
