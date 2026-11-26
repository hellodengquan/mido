# SPDX-FileCopyrightText: 2026 Mido contributors
#
# SPDX-License-Identifier: MIT

"""Playback timing for SMPTE/timecode divisions.

The MIDI header division word is either a positive ticks-per-beat
resolution or a negative SMPTE time division. These tests cover the
timecode case: tracks must play at the frame rate declared by the file,
while tempo meta messages stay available to editors and beat-division
files keep using their tempo map.
"""

import io
import struct

from pytest import raises

from mido import Message, MetaMessage
from mido.midifiles.midifiles import MidiFile

# 25 frames/s, 40 ticks/frame -> division bytes e7 28 -> signed -6360.
SMPTE_DIVISION = 0xE728 - 0x10000
PPQN_DIVISION = 480

# note_on @0, note_off(480) @480, end_of_track @480.
TRACK_EVENTS = bytes.fromhex(
    '00 90 40 40'
    '83 60 80 40 40'
    '00 ff 2f 00'
)

# Same, plus a set_tempo (1000000 us/beat) at time 0 before the note_off.
TRACK_EVENTS_WITH_TEMPO = bytes.fromhex(
    '00 90 40 40'
    '00 ff 51 03 0f 42 40'
    '83 60 80 40 40'
    '00 ff 2f 00'
)


def make_smf(division, track_data, file_type=1):
    header = struct.pack('>hhh', file_type, 1, division)
    return (b'MThd' + struct.pack('>L', len(header)) + header
            + b'MTrk' + struct.pack('>L', len(track_data)) + track_data)


def read_smf(data):
    return MidiFile(file=io.BytesIO(data))


def save_bytes(midifile):
    out = io.BytesIO()
    midifile.save(file=out)
    return out.getvalue()


def test_smpte_time_conversion():
    mid = read_smf(make_smf(SMPTE_DIVISION, TRACK_EVENTS))
    # 25 fps * 40 ticks/frame = 1000 ticks/s, so 480 ticks is 0.48 s.
    assert [msg.time for msg in mid] == [0, 0.48, 0]
    assert mid.length == 0.48


def test_smpte_length_matches_last_event():
    mid = read_smf(make_smf(SMPTE_DIVISION, TRACK_EVENTS))
    total = 0.0
    for msg in mid:
        total += msg.time
    assert mid.length == total == 0.48


def test_smpte_tempo_does_not_change_timing_but_is_preserved():
    mid = read_smf(make_smf(SMPTE_DIVISION, TRACK_EVENTS_WITH_TEMPO))
    messages = list(mid)

    # The 1000000 us/beat tempo would stretch 480 PPQ ticks to 1 s in a
    # metrical file. In a timecode file the same 480 ticks stay 0.48 s.
    assert [msg.time for msg in messages] == [0, 0, 0.48, 0]
    assert mid.length == 0.48

    tempo_messages = [msg for msg in messages if msg.type == 'set_tempo']
    assert len(tempo_messages) == 1
    assert tempo_messages[0].tempo == 1_000_000

    # The editor can rewrite the tempo and it round-trips through save.
    mid.tracks[0][1].tempo = 250_000
    reread = read_smf(save_bytes(mid))
    assert [m.tempo for m in reread if m.type == 'set_tempo'] == [250_000]
    assert reread.length == 0.48


def test_smpte_attributes():
    mid = read_smf(make_smf(SMPTE_DIVISION, TRACK_EVENTS))
    assert mid.is_smpte
    assert mid.smpte_fps == 25
    assert mid.smpte_ticks_per_frame == 40
    assert mid.ticks_per_frame == 40
    assert mid.ticks_per_beat is None
    assert mid.division == SMPTE_DIVISION
    assert mid.frames_per_second == 25.0


def test_smpte_frame_rate_codes():
    for high_byte, code in [(0xE8, 24), (0xE7, 25), (0xE3, 29), (0xE2, 30)]:
        division = (high_byte << 8 | 40) - 0x10000
        mid = read_smf(make_smf(division, TRACK_EVENTS))
        assert mid.smpte_fps == code
        assert mid.fps == code
        assert mid.smpte_ticks_per_frame == 40
        assert mid.length == 480 / (code * 40)

    # Code 29 is 30 drop-frame, i.e. 29.97 fps wall clock.
    division_2997 = (0xE3 << 8 | 40) - 0x10000
    mid = read_smf(make_smf(division_2997, TRACK_EVENTS))
    assert mid.frames_per_second == 29.97
    assert mid.frame_rate == 29.97


def test_smpte_save_preserves_division_for_picture_sync():
    smf = make_smf(SMPTE_DIVISION, TRACK_EVENTS)
    saved = save_bytes(read_smf(smf))

    # The division bytes in the header must be preserved exactly (e7 28).
    assert saved[12:14] == smf[12:14] == b'\xe7\x28'

    reread = read_smf(saved)
    assert reread.smpte_fps == 25
    assert reread.smpte_ticks_per_frame == 40
    assert reread.ticks_per_beat is None
    assert reread.length == 0.48


def test_smpte_constructor_from_raw_division_word():
    mid = MidiFile(ticks_per_beat=SMPTE_DIVISION)
    assert mid.ticks_per_beat is None
    assert mid.smpte_fps == 25
    assert mid.smpte_ticks_per_frame == 40

    track = mid.add_track()
    track.append(Message('note_on', time=0))
    track.append(Message('note_off', time=1000))  # 1 s at 25*40 ticks/s
    track.append(MetaMessage('end_of_track', time=0))
    assert mid.length == 1.0

    saved = save_bytes(mid)
    assert saved[12:14] == b'\xe7\x28'


def test_smpte_direct_raw_word_assignment():
    mid = MidiFile()
    mid.ticks_per_beat = SMPTE_DIVISION
    assert mid.is_smpte
    assert mid.smpte_fps == 25
    assert mid.length == 0  # no events yet


def test_invalid_smpte_frame_rate_rejected():
    # e4 is -28, not an allowed SMPTE frame format.
    division = (0xE4 << 8 | 40) - 0x10000
    with raises((OSError, ValueError), match='invalid SMPTE frame rate'):
        read_smf(make_smf(division, TRACK_EVENTS))


def test_zero_division_rejected():
    with raises(ValueError, match='ticks_per_beat must be positive'):
        MidiFile(ticks_per_beat=0)


def test_ppqn_attributes_and_tempo_map_unchanged():
    mid = read_smf(make_smf(PPQN_DIVISION, TRACK_EVENTS))
    assert mid.ticks_per_beat == PPQN_DIVISION
    assert not mid.is_smpte
    assert mid.smpte_fps is None
    assert mid.smpte_ticks_per_frame is None
    assert mid.fps is None
    assert [msg.time for msg in mid] == [0, 0.5, 0]
    assert mid.length == 0.5

    with_tempo = read_smf(make_smf(PPQN_DIVISION, TRACK_EVENTS_WITH_TEMPO))
    # 480 ticks at 480 ppq and 1000000 us/beat is one second.
    assert [msg.time for msg in with_tempo] == [0, 0, 1.0, 0]
    assert with_tempo.length == 1.0
