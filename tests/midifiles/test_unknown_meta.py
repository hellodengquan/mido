# SPDX-FileCopyrightText: 2026 Ole Martin Bjorndalen <ombdalen@gmail.com>
#
# SPDX-License-Identifier: MIT

"""Opaque handling of unknown manufacturer meta events.

The SMF specification treats every unrecognized meta event as an
opaque block: the ``FF <type> <length> <data>`` bytes must be carried
through merging, copying and file round trips without interpretation.
These tests check that a score editor can copy such events from an
arrangement into the merged output while keeping their timing,
payloads and ordering intact.
"""

import io

import pytest

import mido
from mido.messages import Message
from mido.midifiles.meta import UnknownMetaMessage
from mido.midifiles.tracks import MidiTrack, merge_tracks

# Type byte 0x0d is not defined by the SMF specification, so mido
# represents events carrying it opaquely as UnknownMetaMessage.
UNKNOWN_TYPE = 0x0d

# Distinct manufacturer-style payloads (leading byte is a manufacturer
# ID), used to tell two events of the same unknown type apart.
PAYLOAD_A = (0x41, 0x10, 0x01, 0x7f)
PAYLOAD_B = (0x43, 0x7f, 0x02, 0x00)
PAYLOAD_C = (0x41, 0x10, 0x09, 0x55)


def _absolute_times(messages):
    """Convert a sequence of delta-time messages to absolute tick times."""
    now = 0
    times = []
    for msg in messages:
        now += msg.time
        times.append(now)
    return times


def _build_arrangement():
    """Two source tracks with unknown meta events next to played notes."""
    track1 = MidiTrack([
        Message('note_on', note=60, velocity=64, time=0),
        UnknownMetaMessage(UNKNOWN_TYPE, PAYLOAD_A, time=10),
        Message('note_off', note=60, velocity=64, time=10),
    ])
    track2 = MidiTrack([
        UnknownMetaMessage(UNKNOWN_TYPE, PAYLOAD_B, time=5),
        Message('note_on', note=72, velocity=64, time=15),
        UnknownMetaMessage(UNKNOWN_TYPE, PAYLOAD_C, time=20),
        Message('note_off', note=72, velocity=64, time=10),
    ])
    return track1, track2


def _unknown_messages(messages):
    return [msg for msg in messages
            if isinstance(msg, UnknownMetaMessage)]


def test_merge_keeps_unknown_meta_opaque():
    """Merging copies every unknown meta event into the new track."""
    track1, track2 = _build_arrangement()

    merged = merge_tracks([track1, track2])

    unknowns = _unknown_messages(merged)

    # Each source event survives, represented opaquely with the original
    # type byte and payload, even when several events share the type.
    assert len(unknowns) == 3
    assert [(msg.type_byte, msg.data) for msg in unknowns] == [
        (UNKNOWN_TYPE, PAYLOAD_B),
        (UNKNOWN_TYPE, PAYLOAD_A),
        (UNKNOWN_TYPE, PAYLOAD_C),
    ]

    # Absolute playback positions are preserved by the merge.
    source_positions = {
        msg.data: pos
        for track in (track1, track2)
        for msg, pos in zip(track, _absolute_times(track))
        if isinstance(msg, UnknownMetaMessage)
    }
    merged_positions = {
        msg.data: pos
        for msg, pos in zip(merged, _absolute_times(merged))
        if isinstance(msg, UnknownMetaMessage)
    }
    assert merged_positions == source_positions


def test_merged_unknown_meta_messages_are_independent_copies():
    """Copied events can be retimed without affecting the arrangement."""
    track1, track2 = _build_arrangement()
    source_event = track1[1]

    merged = merge_tracks([track1, track2])
    merged_event = next(
        msg for msg in merged if isinstance(msg, UnknownMetaMessage)
        and msg.data == PAYLOAD_A)

    # merge_tracks produces new message objects, not shared references.
    assert merged_event is not source_event

    # Adjusting the timing of a copied event leaves the source event and
    # the other copied events untouched.
    merged_event.time = 123
    assert source_event.time == 10
    assert all(msg.time != 123 for msg in _unknown_messages(merged)
               if msg is not merged_event)

    # A retimed copy keeps the opaque payload and type byte.
    retimed = merged_event.copy(time=456)
    assert retimed is not merged_event
    assert retimed.time == 456
    assert retimed.type_byte == UNKNOWN_TYPE
    assert retimed.data == PAYLOAD_A
    assert merged_event.time == 123


def test_unknown_meta_round_trip_through_file():
    """Saving and reading back keeps the opaque events and their order."""
    track1, track2 = _build_arrangement()
    merged = merge_tracks([track1, track2])

    midifile = mido.MidiFile(type=1, ticks_per_beat=480, tracks=[merged])
    buffer = io.BytesIO()
    midifile.save(file=buffer)
    buffer.seek(0)
    loaded = mido.MidiFile(file=buffer)

    assert len(loaded.tracks) == 1
    loaded_track = loaded.tracks[0]
    loaded_unknowns = _unknown_messages(loaded_track)

    # Every extension event is read back as an opaque unknown meta
    # message with its original type byte and payload.
    assert [(msg.type_byte, msg.data) for msg in loaded_unknowns] == [
        (UNKNOWN_TYPE, PAYLOAD_B),
        (UNKNOWN_TYPE, PAYLOAD_A),
        (UNKNOWN_TYPE, PAYLOAD_C),
    ]

    # The ordering relative to ordinary performance events is unchanged.
    assert [type(msg) for msg in loaded_track] == [
        Message,                       # note_on (track 1)
        UnknownMetaMessage,            # PAYLOAD_B
        UnknownMetaMessage,            # PAYLOAD_A
        Message,                       # note_off (track 1)
        Message,                       # note_on (track 2)
        UnknownMetaMessage,            # PAYLOAD_C
        Message,                       # note_off (track 2)
        mido.MetaMessage,              # end_of_track
    ]


@pytest.mark.xfail(
    reason=('build_meta_message() does not forward the delta time to '
            'UnknownMetaMessage, so unknown meta events are read back '
            'with time=0 instead of their original delta time'),
    strict=True,
)
def test_unknown_meta_round_trip_keeps_beat_positions():
    """Read-back extension info must stay attached to its beat positions.

    The SMF opaque-meta-event convention requires the delta time written
    before each ``FF <type> <length> <data>`` block to be restored on
    read so the event keeps its place relative to ordinary events.
    """
    track1, track2 = _build_arrangement()
    merged = merge_tracks([track1, track2])

    midifile = mido.MidiFile(type=1, ticks_per_beat=480, tracks=[merged])
    buffer = io.BytesIO()
    midifile.save(file=buffer)
    buffer.seek(0)
    loaded = mido.MidiFile(file=buffer)

    assert _absolute_times(loaded.tracks[0]) == _absolute_times(merged)

    # The ordinary performance events around the opaque ones must keep
    # the same relative sequence and spacing.
    assert [msg.time for msg in loaded.tracks[0]
            if not isinstance(msg, mido.MetaMessage)] == [
        msg.time for msg in merged
        if not isinstance(msg, mido.MetaMessage)]


def test_unknown_meta_bytes_are_preserved_bit_for_bit():
    """The raw SMF bytes of an opaque event survive a full round trip."""
    payload = (0x00, 0x21, 0x7f, 0x42)
    event = UnknownMetaMessage(UNKNOWN_TYPE, payload, time=7)

    midifile = mido.MidiFile(type=1, ticks_per_beat=480, tracks=[
        MidiTrack([
            Message('note_on', note=60, velocity=64, time=3),
            event,
            Message('note_off', note=60, velocity=64, time=2),
        ])])
    buffer = io.BytesIO()
    midifile.save(file=buffer)
    buffer.seek(0)
    loaded = mido.MidiFile(file=buffer)

    loaded_event = _unknown_messages(loaded.tracks[0])[0]
    assert loaded_event.bytes() == event.bytes()
