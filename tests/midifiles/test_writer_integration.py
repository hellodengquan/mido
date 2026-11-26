# SPDX-FileCopyrightText: 2026 Mido contributors
#
# SPDX-License-Identifier: MIT

"""Integration tests for the Standard MIDI File (SMF) writer.

The scenario is a recorded track where performances on several MIDI
channels are interleaved with production markers (track name, marker and
cue-marker meta events plus a SysEx message).

Two SMF writer guarantees are covered end to end:

* Running status (the omission of repeated channel status bytes) is only
  used where the file specification allows it, while the raw event stream
  can still be decoded event by event into a message category and channel.
* Reading such a compressed (running-status) track and saving it again
  preserves the performance order and the delta ticks of every message.
"""

import io
import struct
from typing import NamedTuple

import pytest

from mido.messages import Message
from mido.midifiles.meta import MetaMessage
from mido.midifiles.midifiles import MidiFile, MidiTrack, write_track
from mido.midifiles.tracks import fix_end_of_track

TICKS_PER_BEAT = 480

# High status nibble -> (message type, number of data bytes).
CHANNEL_STATUS = {
    0x8: ("note_off", 2),
    0x9: ("note_on", 2),
    0xA: ("polytouch", 2),
    0xB: ("control_change", 2),
    0xC: ("program_change", 1),
    0xD: ("aftertouch", 1),
    0xE: ("pitchwheel", 2),
}


class RawEvent(NamedTuple):
    """An event decoded directly from raw ``MTrk`` bytes."""

    delta: int
    omitted_status: bool
    kind: str  # "channel", "meta" or "sysex"
    # -1 means "not applicable to this kind of event".
    status: int = -1
    message_type: str = ""
    channel: int = -1
    meta_type: int = -1
    payload: bytes = b""


class SmfHeader(NamedTuple):
    file_format: int
    num_tracks: int
    ticks_per_beat: int


def parse_hexdump(hexdump: str) -> bytes:
    """Parse the commented hex-dump format used by the midifiles tests."""
    data = bytearray()
    for line in hexdump.splitlines():
        data += bytearray.fromhex(line.split("#")[0])
    return bytes(data)


def save_to_bytes(mid: MidiFile) -> bytes:
    buffer = io.BytesIO()
    mid.save(file=buffer)
    return buffer.getvalue()


def build_recording() -> MidiTrack:
    """Return one recorded track interleaving channels and markers.

    Consecutive identical status bytes are deliberately included so that
    running status omissions actually occur.  Meta/SysEx events are placed
    between events that share a status byte to prove that they cancel
    running status.
    """
    return MidiTrack([
        MetaMessage("track_name", name="Live Take 236", time=0),

        # Patch changes on three different channels (all explicit status).
        Message("program_change", channel=0, program=0, time=0),
        Message("program_change", channel=1, program=40, time=0),
        Message("program_change", channel=2, program=48, time=0),

        # Channel 0 mixer run: B0 repeats and may use running status.
        Message("control_change", channel=0, control=0, value=0, time=0),
        Message("control_change", channel=0, control=32, value=1, time=0),
        Message("control_change", channel=0, control=7, value=110, time=0),
        Message("control_change", channel=0, control=10, value=64, time=0),
        # Different low nibble (channel) means the status must be written.
        Message("control_change", channel=1, control=7, value=100, time=0),
        Message("control_change", channel=2, control=7, value=90, time=0),

        # Repeated pitch-bend status.
        Message("pitchwheel", channel=0, pitch=0, time=0),
        Message("pitchwheel", channel=0, pitch=2048, time=0),

        # Channel 0 chord (90 run) answered by channel 2.
        Message("note_on", channel=0, note=60, velocity=100, time=120),
        Message("note_on", channel=0, note=64, velocity=96, time=0),
        Message("note_on", channel=0, note=67, velocity=92, time=0),
        Message("note_on", channel=2, note=48, velocity=80, time=0),

        # Polyphonic aftertouch run.
        Message("polytouch", channel=0, note=60, value=40, time=60),
        Message("polytouch", channel=0, note=64, value=35, time=0),

        # Sustain pedal, channel aftertouch and another B0 right before
        # the marker below.
        Message("control_change", channel=0, control=64, value=127, time=180),
        Message("aftertouch", channel=0, value=55, time=0),
        Message("aftertouch", channel=0, value=60, time=0),
        Message("control_change", channel=0, control=1, value=40, time=0),

        # A meta event cancels running status, so the following B0 must be
        # explicit even though it matches the B0 just above the marker.
        MetaMessage("marker", text="Chorus", time=0),
        Message("control_change", channel=0, control=64, value=0, time=0),

        # Note-off run; one omitted-status event carries a 2-byte VLQ delta.
        Message("note_off", channel=0, note=67, velocity=64, time=0),
        Message("note_off", channel=0, note=64, velocity=64, time=0),
        Message("note_off", channel=0, note=60, velocity=64, time=240),
        Message("note_off", channel=2, note=48, velocity=64, time=0),

        # Percussion channel (channel index 9 / MIDI channel 10).
        Message("note_on", channel=9, note=36, velocity=110, time=240),
        Message("note_on", channel=9, note=42, velocity=90, time=0),
        Message("note_off", channel=9, note=36, velocity=64, time=120),
        Message("note_off", channel=9, note=42, velocity=64, time=0),

        # SysEx also cancels running status: the B0 after it is explicit.
        Message("control_change", channel=0, control=5, value=8, time=0),
        Message("sysex", data=(0x7E, 0x7F, 0x09, 0x01), time=0),
        Message("control_change", channel=0, control=5, value=0, time=0),

        # Closing phrase with a long, multi-byte delta.
        Message("note_on", channel=0, note=72, velocity=100, time=1920),
        Message("note_off", channel=0, note=72, velocity=64, time=240),

        MetaMessage("cue_marker", text="Outro cue", time=0),
        MetaMessage("end_of_track", time=480),
    ])


def emitted_messages() -> list:
    """Messages in the exact order/value the writer serializes them."""
    return list(fix_end_of_track(build_recording()))


def spec_omission_pattern(messages) -> tuple:
    """Determine from the SMF spec which status bytes may be omitted.

    A channel-voice event may omit its status byte exactly when its full
    status byte (command nibble *and* channel nibble) equals the currently
    active running status.  Meta and SysEx events cancel running status;
    any other non-channel status replaces it with nothing reusable.
    """
    may_omit = []
    running_status = None

    for msg in messages:
        if msg.is_meta or msg.type == "sysex":
            may_omit.append(False)
            running_status = None
            continue

        status_byte = msg.bytes()[0]
        if status_byte < 0xF0:
            may_omit.append(status_byte == running_status)
            running_status = status_byte
        else:
            may_omit.append(False)
            running_status = None

    return tuple(may_omit)


def read_variable_length(data: bytes, pos: int) -> tuple:
    value = 0
    while True:
        byte = data[pos]
        pos += 1
        value = (value << 7) | (byte & 0x7F)
        if byte < 0x80:
            return value, pos


def parse_track_events(chunk: bytes) -> list:
    """Decode an MTrk body while maintaining running status ourselves.

    This intentionally does not use mido's reader, so that the writer's
    output is checked against the wire format rather than against itself.
    """
    pos = 0
    running_status = None
    events = []

    while pos < len(chunk):
        delta, pos = read_variable_length(chunk, pos)
        first_byte = chunk[pos]

        if first_byte < 0x80:
            # A leading data byte means the status byte was omitted.
            assert running_status is not None, (
                "data byte with no running status"
            )
            status = running_status
            omitted = True
        else:
            status = first_byte
            pos += 1
            omitted = False
            # Only channel voice statuses establish running status.
            running_status = status if status < 0xF0 else None

        if status == 0xFF:
            meta_type = chunk[pos]
            pos += 1
            length, pos = read_variable_length(chunk, pos)
            payload = chunk[pos:pos + length]
            pos += length
            events.append(RawEvent(
                delta, omitted, "meta", status=status,
                meta_type=meta_type, payload=payload,
            ))
        elif status in (0xF0, 0xF7):
            length, pos = read_variable_length(chunk, pos)
            payload = chunk[pos:pos + length]
            pos += length
            events.append(RawEvent(
                delta, omitted, "sysex", status=status, payload=payload,
            ))
        else:
            assert 0x80 <= status < 0xF0, f"undefined status 0x{status:02x}"
            message_type, data_length = CHANNEL_STATUS[status >> 4]
            payload = chunk[pos:pos + data_length]
            pos += data_length
            events.append(RawEvent(
                delta, omitted, "channel", status=status,
                message_type=message_type, channel=status & 0x0F,
                payload=payload,
            ))

    assert pos == len(chunk), "MTrk chunk length does not match event data"
    return events


def parse_smf(raw: bytes) -> tuple:
    """Parse an entire SMF byte string into raw per-track event lists."""
    assert raw[:4] == b"MThd", "missing MThd header"
    header_size = struct.unpack(">I", raw[4:8])[0]
    file_format, num_tracks, ticks_per_beat = struct.unpack(
        ">HHH", raw[8:14]
    )
    header = SmfHeader(file_format, num_tracks, ticks_per_beat)

    pos = 8 + header_size
    tracks = []
    for _ in range(num_tracks):
        assert raw[pos:pos + 4] == b"MTrk", "missing MTrk chunk"
        chunk_size = struct.unpack(">I", raw[pos + 4:pos + 8])[0]
        body_start = pos + 8
        body_end = body_start + chunk_size
        tracks.append(parse_track_events(raw[body_start:body_end]))
        pos = body_end

    assert pos == len(raw), "trailing bytes after last track"
    return header, tracks


def performance_signature(messages) -> list:
    """Order, category, channel and occupied delta ticks of messages."""
    return [
        (msg.type, getattr(msg, "channel", None), msg.time)
        for msg in messages
    ]


def cumulative_ticks(messages) -> list:
    total = 0
    positions = []
    for msg in messages:
        total += msg.time
        positions.append(total)
    return positions


# 0-based positions of the events whose status byte is omitted in the
# fixed recording above (kept explicit to pin the writer's behaviour).
EXPECTED_OMITTED_INDEXES = frozenset({
    5, 6, 7,        # repeated control_change channel 0
    11,             # repeated pitchwheel channel 0
    13, 14,         # repeated note_on channel 0
    17,             # repeated polytouch channel 0
    20,             # repeated aftertouch channel 0
    25, 26,         # repeated note_off channel 0
    29,             # repeated note_on channel 9
    31,             # repeated note_off channel 9
})


def test_recording_fixture_covers_channels_categories_and_markers():
    messages = build_recording()
    channels = {msg.channel for msg in messages if hasattr(msg, "channel")}
    types_ = {msg.type for msg in messages}

    assert {0, 1, 2, 9}.issubset(channels)
    assert {
        "note_on", "note_off", "polytouch", "control_change",
        "program_change", "aftertouch", "pitchwheel",
    }.issubset(types_)
    assert {"track_name", "marker", "cue_marker"}.issubset(types_)
    assert "sysex" in types_


@pytest.mark.parametrize("file_type", [0, 1])
def test_writer_omits_status_bytes_exactly_when_spec_allows(file_type):
    mid = MidiFile(
        type=file_type,
        ticks_per_beat=TICKS_PER_BEAT,
        tracks=[build_recording()],
    )
    header, parsed_tracks = parse_smf(save_to_bytes(mid))

    assert header.file_format == file_type
    assert header.num_tracks == 1
    assert header.ticks_per_beat == TICKS_PER_BEAT
    assert len(parsed_tracks) == 1

    events = parsed_tracks[0]
    expected = emitted_messages()
    assert len(events) == len(expected)

    expected_pattern = spec_omission_pattern(expected)
    actual_pattern = tuple(event.omitted_status for event in events)

    # The writer omits exactly the status bytes that the spec permits.
    assert actual_pattern == expected_pattern
    omitted_indexes = {
        index for index, omitted in enumerate(actual_pattern) if omitted
    }
    assert omitted_indexes == EXPECTED_OMITTED_INDEXES
    # The fixture must genuinely exercise running status.
    assert len(omitted_indexes) >= 10


@pytest.mark.parametrize("file_type", [0, 1])
def test_compressed_stream_still_decodes_type_and_channel_per_event(file_type):
    mid = MidiFile(
        type=file_type,
        ticks_per_beat=TICKS_PER_BEAT,
        tracks=[build_recording()],
    )
    _, tracks = parse_smf(save_to_bytes(mid))
    events = tracks[0]
    expected = emitted_messages()

    # Every event, including every status-less one, resolves to the same
    # category/channel and delta ticks as the original score.
    for index, (event, msg) in enumerate(zip(events, expected)):
        assert event.delta == msg.time, index

        if msg.is_meta:
            assert event.kind == "meta", index
        elif msg.type == "sysex":
            assert event.kind == "sysex", index
        else:
            assert event.kind == "channel", index
            assert event.message_type == msg.type, index
            assert event.channel == msg.channel, index
            assert event.status == (msg.bytes()[0]), index

    # Re-walk the stream enforcing the running-status chain: an omitted
    # status must equal the previous effective channel status, and running
    # status is cleared by every meta/SysEx event.
    active_status = None
    for event in events:
        if event.kind == "channel":
            if event.omitted_status:
                assert event.status == active_status
            active_status = event.status
        else:
            assert event.omitted_status is False
            active_status = None


def test_repeated_channel_status_emits_known_running_status_bytes():
    track = MidiTrack([
        Message("note_on", channel=0, note=60, velocity=64, time=0),
        Message("note_on", channel=0, note=62, velocity=64, time=240),
        MetaMessage("end_of_track", time=0),
    ])
    buffer = io.BytesIO()
    write_track(buffer, track)

    # Delta 240 is the two-byte VLQ 81 70; the second 9n status is absent.
    assert buffer.getvalue() == parse_hexdump("""
        4d 54 72 6b              # MTrk
        00 00 00 0c              # chunk length = 12 bytes
        00 90 3c 40              # delta 0:   90 3c 40
        81 70 3e 40              # delta 240: [90 omitted] 3e 40
        00 ff 2f 00              # delta 0:   end of track
    """)


@pytest.mark.parametrize("file_type", [0, 1])
def test_read_compressed_track_then_resave_preserves_score(file_type):
    original = MidiFile(
        type=file_type,
        ticks_per_beat=TICKS_PER_BEAT,
        tracks=[build_recording()],
    )
    compressed = save_to_bytes(original)

    loaded = MidiFile(file=io.BytesIO(compressed))
    assert len(loaded.tracks) == 1
    loaded_track = loaded.tracks[0]
    expected = emitted_messages()

    # Full semantic equality (type, channel and every value, incl. time).
    assert loaded_track == expected

    # Explicit performance-order and per-message tick assertions.
    assert performance_signature(loaded_track) == performance_signature(expected)
    assert [msg.time for msg in loaded_track] == [
        msg.time for msg in expected
    ]
    assert cumulative_ticks(loaded_track) == cumulative_ticks(expected)

    # Saving again must reproduce the exact compressed byte stream, and a
    # second read must yield the same performance.
    resaved = save_to_bytes(loaded)
    assert resaved == compressed

    reloaded = MidiFile(file=io.BytesIO(resaved))
    assert reloaded.tracks[0] == expected
    assert performance_signature(reloaded.tracks[0]) == (
        performance_signature(expected)
    )


def test_save_and_reload_on_disk_preserves_recording(tmp_path):
    path = tmp_path / "recorded_take.mid"
    MidiFile(
        type=1,
        ticks_per_beat=TICKS_PER_BEAT,
        tracks=[build_recording()],
    ).save(filename=str(path))

    loaded = MidiFile(filename=str(path))
    expected = emitted_messages()

    assert performance_signature(loaded.tracks[0]) == performance_signature(
        expected
    )
    assert cumulative_ticks(loaded.tracks[0]) == cumulative_ticks(expected)


# A hand-encoded format-1 file whose track deliberately omits status bytes
# via running status.  Chunk length 0x36 matches the body below.
HAND_COMPRESSED_SMF = """
4d 54 68 64                    # MThd
00 00 00 06                    # header length
00 01                          # format 1
00 01                          # one track
01 e0                          # 480 ticks per beat

4d 54 72 6b                    # MTrk
00 00 00 36                    # chunk length = 54 bytes

00 ff 03 04 54 61 6b 65        # track_name "Take"
00 c0 05                       # program_change channel 0, program 5
00 c1 28                       # program_change channel 1, program 40
00 90 3c 60                    # note_on  channel 0, note 60, vel 96
00 40 50                       # [90 omitted] note_on ch0 note64 vel80
60 b0 07 64                    # delta 96: control_change ch0 ctrl7=100
00 0a 40                       # [b0 omitted] control_change ch0 ctrl10=64
00 ff 06 03 56 31 32           # marker "V12" (cancels running status)
00 b0 0b 7f                    # control_change ch0 ctrl11=127 (explicit)
78 80 3c 40                    # delta 120: note_off ch0 note60 vel64
00 40 40                       # [80 omitted] note_off ch0 note64 vel64
00 81 43 40                    # note_off channel 1, note 67, vel 64
00 ff 2f 00                    # end_of_track
"""


def hand_encoded_score() -> MidiTrack:
    return MidiTrack([
        MetaMessage("track_name", name="Take", time=0),
        Message("program_change", channel=0, program=5, time=0),
        Message("program_change", channel=1, program=40, time=0),
        Message("note_on", channel=0, note=60, velocity=96, time=0),
        Message("note_on", channel=0, note=64, velocity=80, time=0),
        Message("control_change", channel=0, control=7, value=100, time=96),
        Message("control_change", channel=0, control=10, value=64, time=0),
        MetaMessage("marker", text="V12", time=0),
        Message("control_change", channel=0, control=11, value=127, time=0),
        Message("note_off", channel=0, note=60, velocity=64, time=120),
        Message("note_off", channel=0, note=64, velocity=64, time=0),
        Message("note_off", channel=1, note=67, velocity=64, time=0),
        MetaMessage("end_of_track", time=0),
    ])


def test_hand_encoded_compressed_track_decodes_running_status():
    raw = parse_hexdump(HAND_COMPRESSED_SMF)
    _, tracks = parse_smf(raw)
    events = tracks[0]
    score = hand_encoded_score()

    assert tuple(event.omitted_status for event in events) == (
        False, False, False, False, True,
        False, True,
        False,
        False, False, True, False,
        False,
    )

    # The status-less events still identify category and channel.
    for event, msg in zip(events, score):
        assert event.delta == msg.time
        if event.kind == "channel":
            assert event.message_type == msg.type
            assert event.channel == msg.channel


def test_read_hand_encoded_compressed_track_then_save_matches_score():
    raw = parse_hexdump(HAND_COMPRESSED_SMF)

    loaded = MidiFile(file=io.BytesIO(raw))
    score = hand_encoded_score()

    # Reading the compressed track reconstructs the original score.
    assert loaded.tracks[0] == score
    assert performance_signature(loaded.tracks[0]) == (
        performance_signature(score)
    )
    assert cumulative_ticks(loaded.tracks[0]) == cumulative_ticks(score)

    # Saving again keeps order/ticks and, because the hand encoding is
    # canonical running status, reproduces the exact compressed bytes.
    resaved = save_to_bytes(loaded)
    assert performance_signature(
        MidiFile(file=io.BytesIO(resaved)).tracks[0]
    ) == performance_signature(score)
    assert resaved == raw
