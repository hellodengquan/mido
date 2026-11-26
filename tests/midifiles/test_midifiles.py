# SPDX-FileCopyrightText: 2017 Ole Martin Bjorndalen <ombdalen@gmail.com>
#
# SPDX-License-Identifier: MIT

import io

import pytest
from pytest import raises

from mido.messages import Message
from mido.midifiles.meta import (
    KeySignatureError,
    MetaMessage,
    encode_string,
    meta_charset,
)
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


def _text_file(charset, text):
    """Save a one-track file with a lyrics and a marker message."""
    mid = MidiFile(type=0, charset=charset)
    mid.tracks.append(MidiTrack([
        MetaMessage('lyrics', text=text),
        MetaMessage('marker', text=text),
        MetaMessage('end_of_track'),
    ]))
    file = io.BytesIO()
    mid.save(file=file)
    return file.getvalue()


def _texts(data, charset):
    mid = MidiFile(type=0, charset=charset, file=io.BytesIO(data))
    return (mid.tracks[0][0].text, mid.tracks[0][1].text)


def test_charset_is_used_for_read_and_write():
    latin1_data = _text_file('latin1', 'café')
    utf8_data = _text_file('utf-8', '日本')

    assert _texts(latin1_data, 'latin1') == ('café', 'café')
    assert _texts(utf8_data, 'utf-8') == ('日本', '日本')

    # The bytes are written with the file's own charset.
    assert b'caf\xe9' in latin1_data
    assert '日本'.encode('utf-8') in utf8_data


def test_charset_is_restored_after_load_error():
    latin1_data = _text_file('latin1', 'café')

    with raises(EOFError):
        MidiFile(type=0, charset='utf-8', file=io.BytesIO(b''))

    # The failed utf-8 load must not change the charset used for the
    # next file.
    mid = MidiFile(type=0, charset='latin1', file=io.BytesIO(latin1_data))
    assert mid.tracks[0][0].text == 'café'

    file = io.BytesIO()
    mid.save(file=file)
    assert b'caf\xe9' in file.getvalue()


def test_saving_another_file_inside_a_session_keeps_charset():
    """Entering another file's read/write flow must not change the
    charset used for the file being processed."""
    latin1_mid = MidiFile(type=0, charset='latin1', file=io.BytesIO(
        _text_file('latin1', 'café')))
    utf8_mid = MidiFile(type=0, charset='utf-8', file=io.BytesIO(
        _text_file('utf-8', '日本')))
    utf8_data = _text_file('utf-8', '日本語')

    with meta_charset('latin1'):
        # Another song's edit session, which runs and returns on its own.
        utf8_file = io.BytesIO()
        utf8_mid.save(file=utf8_file)
        MidiFile(type=0, charset='utf-8', file=io.BytesIO(utf8_data))

        # The enclosing session still encodes strings as latin1.
        assert encode_string('café') == list('café'.encode('latin1'))

        latin1_file = io.BytesIO()
        latin1_mid.save(file=latin1_file)

    assert _texts(utf8_file.getvalue(), 'utf-8') == ('日本', '日本')
    assert _texts(latin1_file.getvalue(), 'latin1') == ('café', 'café')


def test_failed_other_session_keeps_charset_of_the_first_file():
    latin1_mid = MidiFile(type=0, charset='latin1', file=io.BytesIO(
        _text_file('latin1', 'café')))

    with meta_charset('latin1'):
        with raises(EOFError):
            MidiFile(type=0, charset='utf-8', file=io.BytesIO(b''))

        assert encode_string('café') == list('café'.encode('latin1'))

        latin1_file = io.BytesIO()
        latin1_mid.save(file=latin1_file)

    assert b'caf\xe9' in latin1_file.getvalue()


def test_reopening_a_saved_file_preserves_text():
    """Independent edit sessions produce the same result as when run
    separately, and reopening a saved file restores the text."""
    utf8_mid = MidiFile(type=0, charset='utf-8', file=io.BytesIO(
        _text_file('utf-8', '日本')))
    utf8_mid.tracks[0][0].text = '日本語'

    latin1_mid = MidiFile(type=0, charset='latin1', file=io.BytesIO(
        _text_file('latin1', 'café')))
    latin1_mid.tracks[0][1].text = 'café olé'

    # Both sessions are kept open and saved one after the other.
    utf8_file = io.BytesIO()
    latin1_file = io.BytesIO()
    latin1_mid.save(file=latin1_file)
    utf8_mid.save(file=utf8_file)

    utf8_data = utf8_file.getvalue()
    latin1_data = latin1_file.getvalue()

    # Reopening the saved files restores the edited text.
    assert _texts(utf8_data, 'utf-8') == ('日本語', '日本')
    assert _texts(latin1_data, 'latin1') == ('café', 'café olé')

    # Each file was written with its own charset.
    assert '日本語'.encode('utf-8') in utf8_data
    assert 'café olé'.encode('latin1') in latin1_data


def test_charset_does_not_leak_between_threads():
    """A session in one thread must not change another thread's charset."""
    import threading

    from mido.midifiles import meta

    utf8_data = _text_file('utf-8', '日本')
    errors = []
    worker_ready = threading.Event()
    main_in_session = threading.Event()
    worker_done = threading.Event()

    real_encode = meta.encode_string
    in_worker = threading.local()

    def encode_string(string):
        # Pause the worker right before it encodes the song text, so
        # that its save overlaps with the main thread's latin1 session.
        if getattr(in_worker, 'active', False):
            worker_ready.set()
            main_in_session.wait(10)
            result = real_encode(string)
            worker_done.set()
            return result
        return real_encode(string)

    def edit_utf8_song():
        in_worker.active = True
        try:
            mid = MidiFile(type=0, charset='utf-8', file=io.BytesIO(
                utf8_data))
            file = io.BytesIO()
            mid.save(file=file)
            assert _texts(file.getvalue(), 'utf-8') == ('日本', '日本')
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            worker_done.set()

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(meta, 'encode_string', encode_string)

        thread = threading.Thread(target=edit_utf8_song)
        thread.start()
        try:
            assert worker_ready.wait(10)
            # The worker is inside its utf-8 save. While it waits here,
            # this thread enters its own latin1 session.
            with meta_charset('latin1'):
                main_in_session.set()
                assert worker_done.wait(10)

                # The worker encoded its text as utf-8, and our session
                # still encodes as latin1.
                assert encode_string('café') == list('café'.encode('latin1'))
        finally:
            main_in_session.set()
            thread.join(10)

    assert not errors
