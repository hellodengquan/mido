# SPDX-FileCopyrightText: 2026 Mido contributors
#
# SPDX-License-Identifier: MIT

"""File level SysEx events for Standard MIDI Files.

A Standard MIDI File stores a SysEx message as one or more events:

* an ``F0`` event carries the bytes that follow a SysEx start byte.
  If its payload ends with ``F7`` the message is complete and can be
  represented as ``Message('sysex')``. If it does not, the message
  continues in later events.
* an ``F7`` event (an "escape" or continuation event) carries bytes
  that are transmitted as-is. That is either the next segment of a
  SysEx message that was started by an earlier ``F0`` event, or an
  arbitrary byte sequence such as a System Realtime clock message.

``Message('sysex')`` can only represent a complete message: it cannot
hold a segment without a terminator and its ``data`` cannot contain
status bytes. ``SysexEvent`` keeps such events exactly as they are
stored in the file, including their status byte and delta time, so a
track can be read, inspected and written back unchanged.
"""

from numbers import Integral

from ..messages import BaseMessage, check_time
from ..messages.specs import SYSEX_END, SYSEX_START
from ..messages.strings import msg2str
from .meta import encode_variable_int


def _check_status(status):
    if not isinstance(status, Integral):
        raise TypeError('sysex event status must be int')
    elif status not in (SYSEX_START, SYSEX_END):
        raise ValueError('sysex event status must be 0xf0 or 0xf7')


def _check_data_byte(value):
    if not isinstance(value, Integral):
        raise TypeError('sysex event byte must be int')
    elif not 0 <= value <= 255:
        raise ValueError('sysex event byte must be in range 0..255')


def _check_data(data):
    for byte in data:
        _check_data_byte(byte)


class SysexEvent(BaseMessage):
    """A SysEx packet or escape event stored in a MIDI file track.

    This is a file event, not a sendable MIDI message. It represents
    one of the two event kinds that a Standard MIDI File uses to store
    SysEx data:

    ``status=0xf0``
        The start of a SysEx message. ``data`` holds the bytes that
        follow the ``F0`` status byte. When ``data`` ends with ``0xf7``
        the message is complete and it is normally returned as
        ``Message('sysex')`` instead. When it does not, the message is
        continued by one or more ``F7`` events.

    ``status=0xf7``
        An escape or continuation event. ``data`` is transmitted
        as-is. It may contain any byte, including status bytes.

    The ``data`` attribute holds raw bytes and may include values
    above 127, unlike the payload of ``Message('sysex')``.

    The ``time`` attribute is the delta time in ticks when the event
    is stored in a track.
    """

    is_meta = False

    def __init__(self, status, data=(), time=0, skip_checks=False):
        if not skip_checks:
            _check_status(status)
            _check_data(data)
            check_time(time)

        vars(self).update({
            'type': 'sysex_event',
            'status': status,
            'data': tuple(data),
            'time': time,
        })

    def _setattr(self, name, value):
        if name == 'type':
            raise AttributeError('type attribute is read only')
        elif name == 'status':
            _check_status(value)
            vars(self)['status'] = value
        elif name == 'data':
            _check_data(value)
            vars(self)['data'] = tuple(value)
        elif name == 'time':
            check_time(value)
            vars(self)['time'] = value
        else:
            raise AttributeError(
                f'sysex_event has no attribute {name}')

    __setattr__ = _setattr

    def copy(self, skip_checks=False, **overrides):
        """Return a copy of the event.

        Attributes can be overridden with keyword arguments. The event
        type can not be changed.

        The skip_checks arg can be used to bypass validation and
        should be used cautiously.
        """
        if 'type' in overrides and overrides['type'] != self.type:
            raise ValueError('copy must be same message type')
        overrides.pop('type', None)

        if not overrides and not skip_checks:
            # Bypass all checks.
            event = self.__class__.__new__(self.__class__)
            vars(event).update(vars(self))
            return event

        attrs = vars(self).copy()
        del attrs['type']
        attrs.update(overrides)

        return self.__class__(skip_checks=skip_checks, **attrs)

    def dict(self):
        """Return the attributes of the event as a dictionary."""
        data = vars(self).copy()
        data['data'] = list(data['data'])
        return data

    @classmethod
    def from_dict(cls, data):
        """Create an event from a dictionary."""
        data = dict(data)
        data.pop('type', None)
        return cls(**data)

    def bytes(self):
        """Encode the event (without delta time) as a list of integers."""
        return ([self.status] +
                encode_variable_int(len(self.data)) +
                list(self.data))

    def __len__(self):
        return len(self.bytes())

    def __repr__(self):
        return '{}(status=0x{:02x}, data={!r}, time={!r})'.format(
            self.__class__.__name__, self.status, self.data, self.time)

    def __str__(self):
        return msg2str(vars(self))
