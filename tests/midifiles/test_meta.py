# SPDX-FileCopyrightText: 2017 Ole Martin Bjorndalen <ombdalen@gmail.com>
#
# SPDX-License-Identifier: MIT

import pytest

from mido.midifiles.meta import (
    KeySignatureError,
    MetaMessage,
    MetaSpec_key_signature,
    UnknownMetaMessage,
    decode_string,
    encode_string,
    get_charset,
    meta_charset,
)


def test_copy_invalid_argument():
    with pytest.raises(ValueError):
        MetaMessage('track_name').copy(a=1)


def test_copy_cant_override_type():
    with pytest.raises(ValueError):
        MetaMessage('track_name').copy(type='end_of_track')


class TestKeySignature:
    @pytest.mark.parametrize('bad_key_sig', [[8, 0], [8, 1], [0, 2],
                                             [9, 1], [255 - 7, 0]])
    def test_bad_key_sig_throws_key_signature_error(self, bad_key_sig):
        with pytest.raises(KeySignatureError):
            MetaSpec_key_signature().decode(MetaMessage('key_signature'),
                                            bad_key_sig)

    @pytest.mark.parametrize('input_bytes,expect_sig', [([0, 0], 'C'),
                                                        ([0, 1], 'Am'),
                                                        ([255 - 6, 0], 'Cb'),
                                                        ([255 - 6, 1], 'Abm'),
                                                        ([7, 1], 'A#m')
                                                        ])
    def test_key_signature(self, input_bytes, expect_sig):
        msg = MetaMessage('key_signature')
        MetaSpec_key_signature().decode(msg, input_bytes)
        assert msg.key == expect_sig


def test_meta_message_repr():
    msg = MetaMessage('end_of_track', time=10)
    msg_eval = eval(repr(msg))  # noqa: S307
    assert msg == msg_eval


def test_unknown_meta_message_repr():
    msg = UnknownMetaMessage(type_byte=99, data=[1, 2], time=10)
    msg_eval = eval(repr(msg))  # noqa: S307
    assert msg == msg_eval


def test_meta_from_bytes_invalid():
    test_bytes = [
        0xC0,  # Not a meta event (Program Change channel 1)
        0x05   # Program #5
    ]
    with pytest.raises(ValueError):
        MetaMessage.from_bytes(test_bytes)


def test_meta_from_bytes_data_too_short():
    test_bytes = [
        0xFF,  # Meta event
        0x01,  # Event Type: Text
        0x04,  # Length
        ord('T'), ord('E'), ord('S'),  # Text: TES
    ]
    with pytest.raises(ValueError):
        MetaMessage.from_bytes(test_bytes)


def test_meta_from_bytes_data_too_long():
    test_bytes = [
        0xFF,  # Meta event
        0x01,  # Event Type: Text
        0x04,  # Length
        ord('T'), ord('E'), ord('S'), ord('T'), ord('S')  # Text: TESTS
    ]
    with pytest.raises(ValueError):
        MetaMessage.from_bytes(test_bytes)


def test_meta_from_bytes_text():
    test_bytes = [
        0xFF,  # Meta event
        0x01,  # Event Type: Text
        0x04,  # Length
        ord('T'), ord('E'), ord('S'), ord('T')  # Text: TEST
    ]
    msg = MetaMessage.from_bytes(test_bytes)
    assert msg.type == 'text'
    assert msg.text == 'TEST'


class TestMetaCharset:
    def test_default_charset_is_latin1(self):
        assert get_charset() == 'latin1'

    def test_encode_decode_use_active_charset(self):
        with meta_charset('utf-8'):
            assert encode_string('日本') == list('日本'.encode('utf-8'))
            assert decode_string('日本'.encode('utf-8')) == '日本'

        assert encode_string('café') == list('café'.encode('latin1'))

    def test_charset_restored_after_exception(self):
        with pytest.raises(RuntimeError):
            with meta_charset('utf-8'):
                assert get_charset() == 'utf-8'
                raise RuntimeError

        assert get_charset() == 'latin1'
        assert encode_string('café') == list('café'.encode('latin1'))

    def test_nested_sessions_restore_each_other(self):
        with meta_charset('latin1'):
            with meta_charset('utf-8'):
                assert get_charset() == 'utf-8'
            assert get_charset() == 'latin1'
            assert encode_string('café') == list('café'.encode('latin1'))

        assert get_charset() == 'latin1'

    def test_nested_session_exception_does_not_leak(self):
        with meta_charset('latin1'):
            with pytest.raises(RuntimeError):
                with meta_charset('utf-8'):
                    raise RuntimeError
            assert get_charset() == 'latin1'

        assert get_charset() == 'latin1'

    def test_threads_use_their_own_charset(self):
        """An active session in another thread must not change this
        thread's charset."""
        import threading

        main_session_active = threading.Event()
        other_checked = threading.Event()
        result = {}

        def other_thread():
            with meta_charset('utf-8'):
                assert main_session_active.wait(5)
                # The main thread now has its own latin1 session
                # active. Ours must still be utf-8.
                result['other'] = (get_charset(), encode_string('日本'))
                other_checked.set()

        thread = threading.Thread(target=other_thread)
        thread.start()
        try:
            with meta_charset('latin1'):
                main_session_active.set()
                assert other_checked.wait(5)
                assert get_charset() == 'latin1'
        finally:
            thread.join(5)

        assert result['other'] == ('utf-8', list('日本'.encode('utf-8')))
        assert get_charset() == 'latin1'
