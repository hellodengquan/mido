# SPDX-FileCopyrightText: 2026 Mido contributors
#
# SPDX-License-Identifier: MIT

"""Tests for segmented SysEx events in Standard MIDI Files.

A long SysEx dump (for example a device patch dump) can be split over
several events in a track: an ``F0`` packet followed by one or more
``F7`` continuation events, each with its own delta time. A track can
also contain ``F7`` escape events that are not part of any SysEx
message at all.

``SysexEvent`` keeps those events exactly as stored. Only complete
``F0 ... F7`` packets are read as ``Message('sysex')``.
"""

import io

import pytest

from mido import Message, MidiFile, MidiTrack, SysexEvent
from mido.frozen import freeze_message, thaw_message
from mido.midifiles.meta import MetaMessage
from mido.midifiles.tracks import merge_tracks

HEADER = """
4d 54 68 64  # MThd
00 00 00 06  # Chunk size
00 00        # Type 0
00 01        # 1 track
00 78        # 120 ticks per beat
"""


def parse_hexdump(hexdump):
    data = bytearray()
    for line in hexdump.splitlines():
        data += bytearray.fromhex(line.split('#')[0])
    return bytes(data)


def read_bytes(raw, clip=False):
    return MidiFile(file=io.BytesIO(raw), clip=clip)


def read_track(hexdump, clip=False):
    return read_bytes(parse_hexdump(HEADER + hexdump), clip=clip).tracks[0]


def save_bytes(mid):
    out = io.BytesIO()
    mid.save(file=out)
    return out.getvalue()


def chunk_data(mid):
    """Return the raw MTrk payload of a single-track file."""
    data = save_bytes(mid)
    return data[22:]


# The three-packet example from the SMF 1.0 specification: one SysEx
# message sent as an F0 packet plus two F7 continuation packets.
SPEC_SPLIT_SYSEX = """
4d 54 72 6b  # MTrk
00 00 00 1b  # Chunk size
00 f0 03 43 12 00        # delta 0:   F0 packet
81 48 f7 06 43 12 00 43 12 00  # delta 200: F7 continuation
64 f7 04 43 12 00 f7     # delta 100: F7 continuation with EOX
00 ff 2f 00              # end_of_track
"""

SPEC_SPLIT_SYSEX_BYTES = bytes.fromhex(
    '00 f0 03 43 12 00 '
    '81 48 f7 06 43 12 00 43 12 00 '
    '64 f7 04 43 12 00 f7 '
    '00 ff 2f 00')


def test_split_sysex_events_keep_their_framing_and_delta():
    track = read_track(SPEC_SPLIT_SYSEX)

    assert track == [
        SysexEvent(status=0xf0, data=(0x43, 0x12, 0x00), time=0),
        SysexEvent(status=0xf7, data=(0x43, 0x12, 0x00, 0x43, 0x12, 0x00),
                   time=200),
        SysexEvent(status=0xf7, data=(0x43, 0x12, 0x00, 0xf7), time=100),
        MetaMessage('end_of_track', time=0),
    ]


def test_split_sysex_round_trip_is_byte_identical():
    mid = read_bytes(parse_hexdump(HEADER + SPEC_SPLIT_SYSEX))
    assert chunk_data(mid) == SPEC_SPLIT_SYSEX_BYTES


def test_split_sysex_payload_reassembles_to_original_message():
    """The segments, in order, make up the bytes the device received."""
    track = read_track(SPEC_SPLIT_SYSEX)

    payload = bytearray()
    for event in track:
        if isinstance(event, SysexEvent):
            payload.extend(event.data)

    # Concatenating the packets gives the message the device received,
    # with its EOX only at the very end.
    assert bytes(payload) == bytes.fromhex(
        '43 12 00 43 12 00 43 12 00 43 12 00 f7')


def test_split_sysex_preserves_absolute_times():
    track = read_track(SPEC_SPLIT_SYSEX)

    times = []
    now = 0
    for event in track:
        now += event.time
        times.append(now)

    assert times == [0, 200, 300, 300]


def test_unterminated_f0_packet_is_a_sysex_event():
    track = read_track("""
    4d 54 72 6b
    00 00 00 0a
    00 f0 03 43 12 00  # F0 packet without EOX
    00 ff 2f 00
    """)

    assert track[0] == SysexEvent(status=0xf0,
                                  data=(0x43, 0x12, 0x00), time=0)


def test_empty_f0_packet_round_trip():
    raw = parse_hexdump(HEADER + """
    4d 54 72 6b
    00 00 00 07
    00 f0 00
    00 ff 2f 00
    """)
    mid = read_bytes(raw)

    assert mid.tracks[0][0] == SysexEvent(status=0xf0, data=(), time=0)
    assert chunk_data(mid) == bytes.fromhex('00 f0 00 00 ff 2f 00')


def test_complete_f0_packet_still_reads_as_message():
    track = read_track("""
    4d 54 72 6b
    00 00 00 0c
    00 f0 05 7e 7f 09 01 f7  # GM system on
    00 ff 2f 00
    """)

    assert track[0] == Message('sysex', data=(0x7e, 0x7f, 0x09, 0x01),
                               time=0)
    assert not isinstance(track[0], SysexEvent)


def test_complete_f0_packet_round_trip():
    raw = parse_hexdump(HEADER + """
    4d 54 72 6b
    00 00 00 0c
    00 f0 05 7e 7f 09 01 f7
    00 ff 2f 00
    """)
    assert chunk_data(read_bytes(raw)) == raw[22:]


def test_f7_escape_with_realtime_byte():
    """An F7 escape may carry bytes above 127, such as a clock byte."""
    track = read_track("""
    4d 54 72 6b
    00 00 00 08
    00 f7 01 f8
    00 ff 2f 00
    """)

    assert track[0] == SysexEvent(status=0xf7, data=(0xf8,), time=0)


def test_f7_escape_round_trip():
    raw = parse_hexdump(HEADER + """
    4d 54 72 6b
    00 00 00 08
    00 f7 01 f8
    00 ff 2f 00
    """)
    assert chunk_data(read_bytes(raw)) == bytes.fromhex(
        '00 f7 01 f8 00 ff 2f 00')


def test_f7_escape_carrying_a_complete_message_is_kept_verbatim():
    """A full channel message sent as an escape stays a file event."""
    track = read_track("""
    4d 54 72 6b
    00 00 00 0a
    00 f7 03 90 40 40
    00 ff 2f 00
    """)

    assert track[0] == SysexEvent(status=0xf7, data=(0x90, 0x40, 0x40), time=0)


def test_performance_events_keep_their_position_after_escape():
    """Playing events after an F7 escape stay at their original ticks."""
    raw = parse_hexdump(HEADER + """
    4d 54 72 6b
    00 00 00 10
    00 90 40 40   # note on
    10 f7 01 f8   # escape at tick 16
    20 90 40 00   # note off at tick 48
    00 ff 2f 00
    """)
    track = read_bytes(raw).tracks[0]

    assert track == [
        Message('note_on', note=0x40, velocity=0x40, time=0),
        SysexEvent(status=0xf7, data=(0xf8,), time=16),
        Message('note_on', note=0x40, velocity=0, time=32),
        MetaMessage('end_of_track', time=0),
    ]
    assert chunk_data(read_bytes(raw)) == raw[22:]


def test_running_status_survives_a_sysex_event():
    """A track may keep using the channel running status after SysEx.

    SysEx has no running status of its own. Like a meta message, it
    leaves the channel status from before in place, so a following
    data byte is still read as the previous channel message.
    """
    raw = parse_hexdump(HEADER + """
    4d 54 72 6b
    00 00 00 0b
    00 90 40 40  # note on, sets running status
    00 f7 01 f8  # escape
    00 41 40     # running status: another note on
    00 ff 2f 00
    """)
    track = read_bytes(raw).tracks[0]

    assert track[1] == SysexEvent(status=0xf7, data=(0xf8,), time=0)
    assert track[2] == Message('note_on', note=0x41, velocity=0x40, time=0)


def test_running_status_without_channel_status_fails():
    raw = parse_hexdump(HEADER + """
    4d 54 72 6b
    00 00 00 07
    00 f7 01 f8
    00 41 40
    00 ff 2f 00
    """)
    with pytest.raises(OSError):
        read_bytes(raw)


def test_sysex_event_with_status_byte_in_complete_payload():
    """A complete F0 packet with a stray status byte stays a file event."""
    track = read_track("""
    4d 54 72 6b
    00 00 00 0a
    00 f0 03 43 f8 f7
    00 ff 2f 00
    """)

    assert track[0] == SysexEvent(status=0xf0, data=(0x43, 0xf8, 0xf7),
                                  time=0)


def test_complete_sysex_is_clipped_when_requested():
    track = read_track("""
    4d 54 72 6b
    00 00 00 0a
    00 f0 03 43 f8 f7
    00 ff 2f 00
    """, clip=True)

    assert track[0] == Message('sysex', data=(0x43, 0x7f), time=0)


def test_unterminated_f0_packet_is_clipped_when_requested():
    track = read_track("""
    4d 54 72 6b
    00 00 00 09
    00 f0 02 43 f8
    00 ff 2f 00
    """, clip=True)

    assert track[0] == SysexEvent(status=0xf0, data=(0x43, 0x7f), time=0)


def test_escape_is_not_clipped():
    """Escape payloads legitimately carry status bytes; keep them."""
    track = read_track("""
    4d 54 72 6b
    00 00 00 08
    00 f7 01 f8
    00 ff 2f 00
    """, clip=True)

    assert track[0] == SysexEvent(status=0xf7, data=(0xf8,), time=0)


def test_long_sysex_event_uses_variable_length():
    raw = parse_hexdump(HEADER + """
    4d 54 72 6b
    00 00 00 88
    00 f0 81 00 {payload}
    00 ff 2f 00
    """.format(payload=' '.join(['40'] * 128)))
    mid = read_bytes(raw)

    assert mid.tracks[0][0] == SysexEvent(status=0xf0,
                                          data=(0x40,) * 128, time=0)
    assert chunk_data(mid) == raw[22:]


def test_channel_message_after_sysex_is_written_with_its_status():
    """A SysEx event breaks the run, so the next channel message
    repeats its status byte."""
    mid = MidiFile(type=0, tracks=[MidiTrack([
        Message('note_on', note=60, time=0),
        SysexEvent(status=0xf7, data=(0xf8,), time=0),
        Message('note_on', note=62, time=0),
    ])])

    assert chunk_data(mid) == bytes.fromhex(
        '00 90 3c 40 '
        '00 f7 01 f8 '
        '00 90 3e 40 '
        '00 ff 2f 00')


def test_merge_tracks_keeps_sysex_events():
    track0 = MidiTrack([
        SysexEvent(status=0xf0, data=(0x43, 0x12), time=0),
        Message('note_on', note=60, time=10),
    ])
    track1 = MidiTrack([
        SysexEvent(status=0xf7, data=(0x43, 0x12, 0xf7), time=5),
        Message('note_on', note=62, time=10),
    ])

    merged = merge_tracks([track0, track1])

    assert merged[0] == SysexEvent(status=0xf0, data=(0x43, 0x12), time=0)
    assert merged[1] == SysexEvent(status=0xf7,
                                   data=(0x43, 0x12, 0xf7), time=5)
    # Notes come at absolute ticks 10 and 15, so the merged deltas are
    # 5 and 5: the escape does not pull them earlier.
    assert merged[2] == Message('note_on', note=60, time=5)
    assert merged[3] == Message('note_on', note=62, time=5)


def test_sysex_event_is_not_sendable():
    from mido.ports import BaseOutput

    class DummyOutput(BaseOutput):
        def _open(self, **kwargs):
            pass

        def _send(self, msg):
            pass

    port = DummyOutput()
    with pytest.raises(TypeError):
        port.send(SysexEvent(status=0xf7, data=(0x43,)))


def test_sysex_event_copy_and_dict():
    event = SysexEvent(status=0xf7, data=(0x43, 0x12, 0xf7), time=3)

    assert event.copy() == event
    assert event.copy(time=9) == SysexEvent(status=0xf7,
                                           data=(0x43, 0x12, 0xf7), time=9)
    assert event.dict() == {
        'type': 'sysex_event',
        'status': 0xf7,
        'data': [0x43, 0x12, 0xf7],
        'time': 3,
    }
    assert SysexEvent.from_dict(event.dict()) == event


def test_sysex_event_from_dict_ignores_type():
    assert SysexEvent.from_dict({
        'type': 'sysex_event', 'status': 0xf0, 'data': [1, 2]}) == \
        SysexEvent(status=0xf0, data=(1, 2))


def test_sysex_event_bytes():
    event = SysexEvent(status=0xf7, data=(0x43, 0xf7), time=5)
    assert event.bytes() == [0xf7, 0x02, 0x43, 0xf7]
    assert event.hex() == 'F7 02 43 F7'


def test_sysex_event_validation():
    with pytest.raises(ValueError):
        SysexEvent(status=0xf1)
    with pytest.raises(TypeError):
        SysexEvent(status='f7')
    with pytest.raises(ValueError):
        SysexEvent(status=0xf7, data=(256,))
    with pytest.raises(ValueError):
        SysexEvent(status=0xf7, data=(-1,))
    with pytest.raises(TypeError):
        SysexEvent(status=0xf7, data=('a',))


def test_sysex_event_attribute_rules():
    event = SysexEvent(status=0xf7, data=(0x43,))

    with pytest.raises(AttributeError):
        event.type = 'sysex'
    with pytest.raises(ValueError):
        event.status = 0x90
    with pytest.raises(AttributeError):
        event.bogus = 1

    event.status = 0xf0
    event.data = (0x43, 0xf7)
    assert event == SysexEvent(status=0xf0, data=(0x43, 0xf7), time=0)


def test_sysex_event_repr_round_trip():
    event = SysexEvent(status=0xf7, data=(0x43, 0x12, 0xf7), time=10)
    assert eval(repr(event)) == event  # noqa: S307


def test_sysex_event_string_format():
    from mido import format_as_string

    event = SysexEvent(status=0xf0, data=(0x43, 0x12), time=4)

    assert str(event) == format_as_string(event)
    assert format_as_string(event, include_time=False) == (
        'sysex_event status=0xf0 data=(67,18)')


def test_sysex_event_freeze_and_thaw():
    event = SysexEvent(status=0xf7, data=(0x43, 0x12, 0xf7), time=2)
    frozen = freeze_message(event)

    assert freeze_message(frozen) is frozen
    assert frozen == event
    assert hash(frozen) == hash(freeze_message(event))
    assert thaw_message(frozen) == event
    assert isinstance(thaw_message(frozen), SysexEvent)


def test_track_with_sysex_events_and_meta():
    track = MidiTrack([
        MetaMessage('track_name', name='Dump'),
        SysexEvent(status=0xf0, data=(0x41, 0x10), time=2),
        SysexEvent(status=0xf7, data=(0x42, 0xf7), time=30),
        Message('note_on', note=60, time=1),
    ])
    mid = MidiFile(type=0, tracks=[track])

    reread = read_bytes(save_bytes(mid))
    assert reread.tracks[0] == [
        MetaMessage('track_name', name='Dump'),
        SysexEvent(status=0xf0, data=(0x41, 0x10), time=2),
        SysexEvent(status=0xf7, data=(0x42, 0xf7), time=30),
        Message('note_on', note=60, time=1),
        MetaMessage('end_of_track', time=0),
    ]


def test_midifile_iteration_yields_sysex_events_in_seconds():
    mid = read_bytes(parse_hexdump(HEADER + SPEC_SPLIT_SYSEX))

    messages = list(mid)

    assert [type(msg) for msg in messages[:3]] == [SysexEvent] * 3

    # Default tempo is 0.5 s per beat and the header has 120 ticks
    # per beat, so one tick is 1/240 s.
    assert messages[1].time == pytest.approx(200 / 240)
    assert messages[2].time == pytest.approx(100 / 240)


def test_play_hides_file_events_unless_meta_is_requested():
    mid = read_bytes(parse_hexdump(HEADER + """
    4d 54 72 6b
    00 00 00 0d
    00 f0 02 41 10  # unterminated segment
    00 90 3c 40
    00 ff 2f 00
    """))

    assert list(mid.play(meta_messages=False)) == [
        Message('note_on', note=0x3c, velocity=0x40, time=0),
    ]
    assert [type(msg) for msg in mid.play(meta_messages=True)] == [
        SysexEvent, Message, MetaMessage]


def test_one_shot_patch_message_and_escape_keep_their_own_semantics():
    """A complete patch message and an escape coexist in one track."""
    raw = parse_hexdump(HEADER + """
    4d 54 72 6b
    00 00 00 14
    00 f0 05 7e 7f 09 01 f7  # GM system on, a complete message
    60 f7 01 f8              # escape at tick 96
    60 90 3c 40              # note on at tick 192
    00 ff 2f 00
    """)
    track = read_bytes(raw).tracks[0]

    assert track[0] == Message('sysex', data=(0x7e, 0x7f, 0x09, 0x01),
                               time=0)
    assert track[1] == SysexEvent(status=0xf7, data=(0xf8,), time=96)
    assert track[2] == Message('note_on', note=0x3c, velocity=0x40, time=96)

    assert chunk_data(read_bytes(raw)) == raw[22:]


def test_segments_and_performance_events_interleave():
    """Reading a file with segments between notes and writing it back
    leaves every event at its original tick."""
    raw = parse_hexdump(HEADER + """
    4d 54 72 6b
    00 00 00 1e
    00 90 3c 40   # tick 0
    81 70 f0 02 41 10  # tick 240: start segment
    81 70 90 3e 40  # tick 480: next note
    81 70 f7 02 42 f7  # tick 720: continuation
    81 70 80 3c 00  # tick 960: note off
    00 ff 2f 00
    """)
    mid = read_bytes(raw)

    absolute = []
    now = 0
    for msg in mid.tracks[0]:
        now += msg.time
        absolute.append((type(msg).__name__, now))

    assert absolute == [
        ('Message', 0),
        ('SysexEvent', 240),
        ('Message', 480),
        ('SysexEvent', 720),
        ('Message', 960),
        ('MetaMessage', 960),
    ]
    assert chunk_data(mid) == raw[22:]
