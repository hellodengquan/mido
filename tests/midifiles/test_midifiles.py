# SPDX-FileCopyrightText: 2017 Ole Martin Bjorndalen <ombdalen@gmail.com>
#
# SPDX-License-Identifier: MIT

import io

from pytest import raises

from mido.messages import Message
from mido.midifiles.meta import KeySignatureError, MetaMessage
from mido.midifiles.midifiles import MidiFile, MidiTrack

HEADER_ONE_TRACK = """
4d 54 68 64  # MThd
00 00 00 06  # Chunk size
      00 01  # Type 1
      00 01  # 1 track
      00 78  # 120 ticks per beat
"""


def parse_hexdump(hexdump):
    data = bytearray()
    for line in hexdump.splitlines():
        data += bytearray.fromhex(line.split('#')[0])
    return data


def read_file(hexdump, clip=False):
    return MidiFile(file=io.BytesIO(parse_hexdump(hexdump)), clip=clip)


def test_no_tracks():
    assert read_file("""
    4d 54 68 64  # MThd
    00 00 00 06  # Chunk size
    00 01  # Type 1
    00 00  # 0 tracks
    00 78  # 120 ticks per beat
    """).tracks == []


def test_single_message():
    assert read_file(HEADER_ONE_TRACK + """
    4d 54 72 6b  # MTrk
    00 00 00 04
    20 90 40 40  # note_on
    """).tracks[0] == [Message('note_on', note=64, velocity=64, time=32)]


def test_too_long_message():
    with raises(IOError):
        read_file(HEADER_ONE_TRACK + """
        4d 54 72 6b  # MTrk
        00 00 00 04
        00 ff 03 ff ff 7f # extremely long track name message
        """)


def test_two_tracks():
    mid = read_file("""
    4d54 6864 0000 0006 0001 0002 0040        # Header
    4d54 726b 0000 0008 00 90 40 10  40 80 40 10   # Track 0
    4d54 726b 0000 0008 00 90 47 10  40 80 47 10   # Track 1
    """)
    assert len(mid.tracks) == 2
    # TODO: add some more tests here.


def test_empty_file():
    with raises(EOFError):
        read_file("")


def test_eof_in_track():
    with raises(EOFError):
        read_file(HEADER_ONE_TRACK + """
        4d 54 72 6b  # MTrk
        00 00 00 01  # Chunk size
        # Oops, no data here.
        """)


def test_invalid_data_byte_no_clipping():
    # TODO: should this raise IOError?
    with raises(IOError):
        read_file(HEADER_ONE_TRACK + """
        4d 54 72 6b  # MTrk
        00 00 00 04  # Chunk size
        00 90 ff 40  # note_on note=255 velocity=64
        """)


def test_invalid_data_byte_with_clipping_high():
    midi_file = read_file(HEADER_ONE_TRACK + """
                          4d 54 72 6b  # MTrk
                          00 00 00 04  # Chunk size
                          00 90 ff 40  # note_on note=255 velocity=64
                          """, clip=True)
    assert midi_file.tracks[0][0].note == 127


def test_meta_messages():
    # TODO: should this raise IOError?
    mid = read_file(HEADER_ONE_TRACK + """
    4d 54 72 6b  # MTrk
    00 00 00 0c  # Chunk size
    00 ff 03 04 54 65 73 74  # track_name name='Test'
    00 ff 2f 00  # end_of_track
    """)

    track = mid.tracks[0]

    assert track[0] == MetaMessage('track_name', name='Test')
    assert track[1] == MetaMessage('end_of_track')


def test_meta_message_bad_key_sig_throws_key_signature_error_sharps():
    with raises(KeySignatureError):
        read_file(HEADER_ONE_TRACK + """
            4d 54 72 6b  # MTrk
            00 00 00 09  # Chunk size
            00 ff 59 02 08 # Key Signature with 8 sharps
            00 ff 2f 00  # end_of_track
            """)


def test_meta_message_bad_key_sig_throws_key_signature_error_flats():
    with raises(KeySignatureError):
        read_file(HEADER_ONE_TRACK + """
            4d 54 72 6b  # MTrk
            00 00 00 09  # Chunk size
            00 ff 59 02 f8 # Key Signature with 8 flats
            00 ff 2f 00  # end_of_track
            """)


def test_meta_messages_with_length_0():
    """sequence_number and midi_port with no data bytes should be accepted.

    In rare cases these messages have length 0 and thus no data
    bytes. (See issues 42 and 93.) It is unclear why these messages
    are missing their data. It could be cased by a bug in the software
    that created the files.

    So far this has been fixed by adding a test to each of these two
    meta message classes. If the problem appears with other message
    types it may be worth finding a more general solution.
    """
    mid = read_file(HEADER_ONE_TRACK + """
    4d 54 72 6b  # MTrk
    00 00 00 17

    00 ff 00 00  # sequence_number with no data bytes (should be 2).
    00 ff 21 00  # midi_port with no data bytes (should be 1).

    00 ff 00 02 00 01  # sequence_number with correct number of data bytes (2).
    00 ff 21 01 01     # midi_port with correct number of data bytes (1).

    00 ff 2f 00
    """)

    assert mid.tracks[0] == [
        MetaMessage('sequence_number', number=0),
        MetaMessage('midi_port', port=0),

        MetaMessage('sequence_number', number=1),
        MetaMessage('midi_port', port=1),

        MetaMessage('end_of_track'),
    ]


def test_midifile_invalid_type_error_message():
    # https://github.com/mido/mido/issues/673
    # the error used to interpolate the builtin format function instead of
    # the value that was rejected
    with raises(ValueError) as e:
        MidiFile(type=3)
    assert 'invalid format 3 (must be 0, 1 or 2)' in str(e.value)


def test_midifile_repr():
    midifile = MidiFile(type=1, ticks_per_beat=123, tracks=[
        MidiTrack([
            Message('note_on', channel=1, note=2, time=3),
            Message('note_off', channel=1, note=2, time=3)]),
        MidiTrack([
            MetaMessage('sequence_number', number=5),
            Message('note_on', channel=2, note=6, time=9),
            Message('note_off', channel=2, note=6, time=9)]),
    ])
    midifile_eval = eval(repr(midifile))  # noqa: S307
    for track, track_eval in zip(midifile.tracks, midifile_eval.tracks):
        for m1, m2 in zip(track, track_eval):
            assert m1 == m2


def make_type2_songs():
    # Two independent songs (type 2). The second song starts with a rest
    # and changes tempo before its first note, so it must be timed with
    # its own tempo map from its own time zero.
    song_a = MidiTrack([
        MetaMessage('track_name', name='Song A'),
        Message('note_on', note=60, time=480),
        Message('note_off', note=60, time=480),
        MetaMessage('end_of_track', time=960),
    ])
    song_b = MidiTrack([
        MetaMessage('track_name', name='Song B'),
        # A rest of one beat at the default tempo (0.5 s).
        MetaMessage('set_tempo', tempo=1000000, time=480),
        # One beat at 60 BPM (1.0 s).
        Message('note_on', note=72, time=480),
        Message('note_off', note=72, time=480),
        MetaMessage('end_of_track', time=480),
    ])
    return MidiFile(type=2, ticks_per_beat=480,
                    tracks=[song_a, song_b])


def test_type2_play_selected_track_uses_its_own_tempo_map():
    mid = make_type2_songs()

    messages = list(mid.play(track=1, meta_messages=True))
    assert [msg.type for msg in messages] == [
        'track_name', 'set_tempo', 'note_on', 'note_off', 'end_of_track']

    # The first beat is a rest at the default tempo, then the track's
    # own 60 BPM takes over. The song starts at its own time zero.
    assert messages[0].time == 0
    assert messages[1].time == 0.5
    assert messages[2].time == 1.0
    assert messages[3].time == 1.0
    assert messages[4].time == 1.0


def test_type2_track_length_includes_whole_arrangement():
    mid = make_type2_songs()
    assert mid.track_length(0) == 480 / 960 + 480 / 960 + 960 / 960
    assert mid.track_length(1) == 0.5 + 1.0 + 1.0 + 1.0


def test_type2_play_selected_track_is_a_single_song():
    mid = make_type2_songs()
    messages = list(mid.play(track=0))
    assert [msg.note for msg in messages] == [60, 60]
    messages = list(mid.play(track=1))
    assert [msg.note for msg in messages] == [72, 72]


def test_type2_selection_does_not_persist():
    mid = make_type2_songs()
    list(mid.play(track=1))

    # The file is untouched: both songs are still there and the
    # original behaviour for an unselected type 2 file is intact.
    assert len(mid.tracks) == 2
    with raises(TypeError):
        list(mid.play())
    with raises(TypeError):
        list(mid)
    with raises(ValueError):
        mid.length


def test_type2_track_index_out_of_range():
    mid = make_type2_songs()
    with raises(IndexError):
        list(mid.play(track=2))
    with raises(IndexError):
        mid.track_length(2)


def test_type2_track_index_must_be_an_integer():
    mid = make_type2_songs()
    with raises(TypeError):
        list(mid.play(track='1'))


def test_track_selection_rejected_for_synchronous_files():
    mid = MidiFile(type=1, ticks_per_beat=480, tracks=[MidiTrack([
        Message('note_on', note=64, time=0),
    ])])
    with raises(ValueError):
        list(mid.play(track=0))
    with raises(ValueError):
        mid.iter_track(0)
    with raises(ValueError):
        mid.track_length(0)

    # Synchronous files keep playing as an ensemble.
    assert [msg.note for msg in mid.play()] == [64]
    assert [msg.note for msg in mid if not msg.is_meta] == [64]


def test_type2_alternating_songs_keep_their_own_tempo():
    # Interleaving the iterators of two songs must not leak one song's
    # tempo map or position into the other.
    mid = make_type2_songs()
    song_a = mid.iter_track(0)
    song_b = mid.iter_track(1)

    first_a = next(song_a)
    first_b = next(song_b)
    assert first_a.time == 0
    assert first_b.time == 0

    tempo_a = next(song_a)  # Song A has no tempo change before its notes.
    tempo_b = next(song_b)  # Song B switches to 60 BPM here.
    assert tempo_a.time == 0.5
    assert tempo_b.time == 0.5

    # Song B now plays at its own 60 BPM, not Song A's default tempo.
    assert next(song_b).time == 1.0
    # Song A keeps its own default tempo.
    assert next(song_a).time == 0.5
