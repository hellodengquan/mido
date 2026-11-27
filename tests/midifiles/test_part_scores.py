# SPDX-FileCopyrightText: 2026
#
# SPDX-License-Identifier: MIT

"""Integration tests for split delivery of a score.

The score editor can merge a few adjacent SMF tracks into a part score
(a "part"), save it, and later combine the delivered parts back into the
full score.  Two export routes are exercised end to end, including
serialization to bytes and reloading:

* direct export: the whole source score is written in one go;
* staged export: adjacent groups of source tracks are written as part
  files, handed off, reloaded and concatenated into the full score.

Two independent notions of "where the music ends" are checked separately:

* the end position recorded in the file, measured in MIDI ticks;
* the length a listener experiences, measured in seconds and driven by
  the tempo map that is in effect during playback.

Keeping the two assertions in separate tests means a failure can still be
localized even if both export routes happen to produce the same wrong
duration.
"""

import io

import pytest

from mido import Message, MetaMessage, MidiFile, MidiTrack
from mido.midifiles.units import tick2second

TICKS_PER_BEAT = 480

# Tempo map of the source score: 120 BPM until beat 1, then 240 BPM.
INITIAL_TEMPO = 500000
FAST_TEMPO = 250000
TEMPO_CHANGE_TICK = TICKS_PER_BEAT

# Voice lengths, in ticks, for the three source tracks.
CONDUCTOR_END_TICK = 2 * TICKS_PER_BEAT
NOTE_PART_END_TICK = 2 * TICKS_PER_BEAT
LONG_PART_END_TICK = 4 * TICKS_PER_BEAT


def build_source_score():
    """Build a type 1 score with three tracks sharing events on one beat.

    The tracks deliberately place a tempo change and ordinary performance
    messages on the same beat so that playback ordering and timing can be
    verified:

    * track 0 ("conductor") holds the tempo map and a control message at
      the same tick as the tempo change;
    * track 1 ("notes") attacks a note on that same beat;
    * track 2 ("long voice") starts immediately and lasts until beat 4.
    """
    conductor = MidiTrack([
        MetaMessage('track_name', name='conductor', time=0),
        MetaMessage('set_tempo', tempo=FAST_TEMPO, time=TEMPO_CHANGE_TICK),
        Message('control_change', channel=0, control=64, value=100, time=0),
        MetaMessage('end_of_track',
                    time=CONDUCTOR_END_TICK - TEMPO_CHANGE_TICK),
    ])
    notes = MidiTrack([
        Message('note_on', channel=1, note=60, velocity=64,
                time=TEMPO_CHANGE_TICK),
        Message('note_off', channel=1, note=60, velocity=64,
                time=TICKS_PER_BEAT),
        MetaMessage('end_of_track', time=0),
    ])
    long_voice = MidiTrack([
        Message('note_on', channel=2, note=72, velocity=64, time=0),
        Message('note_off', channel=2, note=72, velocity=64,
                time=LONG_PART_END_TICK),
        MetaMessage('end_of_track', time=0),
    ])
    return MidiFile(type=1, ticks_per_beat=TICKS_PER_BEAT,
                    tracks=[conductor, notes, long_voice])


def round_trip(midi_file):
    """Serialize to SMF bytes and load the file back again."""
    buffer = io.BytesIO()
    midi_file.save(file=buffer)
    buffer.seek(0)
    return MidiFile(file=buffer)


def make_part(tracks):
    """Deliver one part score: a standalone, reloaded SMF file."""
    return round_trip(MidiFile(type=1,
                               ticks_per_beat=TICKS_PER_BEAT,
                               tracks=tracks))


def staged_export():
    """Export the score through part-score handoff and reassembly."""
    source = build_source_score()
    first_part = make_part(source.tracks[0:2])
    second_part = make_part(source.tracks[2:3])
    return round_trip(MidiFile(type=1,
                               ticks_per_beat=TICKS_PER_BEAT,
                               tracks=first_part.tracks + second_part.tracks))


def direct_export():
    """Export the whole score in one step."""
    return round_trip(build_source_score())


def end_tick(midi_file):
    """End position recorded in the file, in MIDI ticks."""
    return max(sum(message.time for message in track)
               for track in midi_file.tracks)


def playback_stream(midi_file):
    """What a listener receives: ordered messages and their absolute time.

    Meta messages are included so the tempo map is part of the comparison.
    Iterating a ``MidiFile`` yields seconds since the previous message,
    which are accumulated into the absolute time of each event.
    """
    stream = []
    elapsed = 0.0
    for message in midi_file:
        elapsed += message.time
        stream.append((type(message).__name__,
                       message.type,
                       elapsed,
                       tuple(sorted((name, value)
                                    for name, value in vars(message).items()
                                    if name not in ('time', 'type')))))
    return stream


def merged_stream(midi_file):
    """The merged track as recorded, with delta times still in ticks."""
    return [(type(message).__name__,
             message.type,
             message.time,
             tuple(sorted((name, value)
                          for name, value in vars(message).items()
                          if name != 'time')))
            for message in midi_file.merged_track]


def expected_playback_length(end_tick_value):
    """Expected listener length, computed directly from the tempo map.

    Everything up to the tempo change uses the default 120 BPM tempo; the
    remainder plays at the faster 240 BPM tempo.
    """
    return (tick2second(min(end_tick_value, TEMPO_CHANGE_TICK),
                        TICKS_PER_BEAT, INITIAL_TEMPO)
            + tick2second(max(0, end_tick_value - TEMPO_CHANGE_TICK),
                          TICKS_PER_BEAT, FAST_TEMPO))


def test_direct_and_staged_export_play_identically():
    direct = direct_export()
    staged = staged_export()

    # Same recorded playback order, including messages sharing one tick.
    assert merged_stream(direct) == merged_stream(staged)

    # Same listener experience: identical messages at identical seconds.
    assert playback_stream(direct) == playback_stream(staged)
    assert direct.length == staged.length


def test_same_tick_events_keep_source_order():
    # At the tempo-change beat the conductor's control message and the
    # note part's note_on share a tick with the set_tempo meta message.
    # Within the tick, source track order (conductor before notes) and
    # in-track order (set_tempo before the control message) must win.
    expected = [
        ('MetaMessage', 'set_tempo'),
        ('Message', 'control_change'),
        ('Message', 'note_on'),
    ]
    for midi_file in (direct_export(), staged_export()):
        kinds = [(type(message).__name__, message.type)
                 for message in midi_file.merged_track]
        index = kinds.index(('MetaMessage', 'set_tempo'))
        # The first event carries the inter-tick delta; the other two
        # events share its tick and therefore have a delta time of zero.
        assert kinds[index:index + len(expected)] == expected
        assert [message.time
                for message in midi_file.merged_track][index + 1:index + 3] \
            == [0, 0]


def test_playback_seconds_follow_the_active_tempo():
    direct = direct_export()

    # The note is attacked on the tempo-change beat.  That beat is reached
    # at the initial tempo; everything after it is played twice as fast.
    first_beat_seconds = tick2second(TEMPO_CHANGE_TICK,
                                     TICKS_PER_BEAT, INITIAL_TEMPO)
    fast_beat_seconds = tick2second(TICKS_PER_BEAT,
                                    TICKS_PER_BEAT, FAST_TEMPO)

    stream = playback_stream(direct)

    def first_time(kind, name, **params):
        for event_kind, event_name, time, event_params in stream:
            if event_kind == kind and event_name == name and all(
                    dict(event_params).get(key) == value
                    for key, value in params.items()):
                return time
        raise AssertionError(f'{kind} {name} {params} not found in stream')

    assert first_time('MetaMessage', 'set_tempo') == \
        pytest.approx(first_beat_seconds)
    assert first_time('Message', 'note_on', channel=1, note=60,
                      velocity=64) == pytest.approx(first_beat_seconds)
    # The next beat, now under the new tempo, only takes half as long, so
    # the note ends one fast beat after the tempo change.
    assert first_time('Message', 'note_off', channel=1, note=60,
                      velocity=64) == pytest.approx(
                          first_beat_seconds + fast_beat_seconds)


def test_part_scores_record_their_own_complete_end_tick():
    source = build_source_score()
    first_part = make_part(source.tracks[0:2])
    second_part = make_part(source.tracks[2:3])

    # Each delivery ends where the longest voice contained in it ends,
    # regardless of how long the other (not yet delivered) voices are.
    assert end_tick(first_part) == NOTE_PART_END_TICK
    assert end_tick(second_part) == LONG_PART_END_TICK

    # Reassembling the parts restores the full score's recorded end.
    assert end_tick(direct_export()) == LONG_PART_END_TICK
    assert end_tick(staged_export()) == LONG_PART_END_TICK


def test_part_scores_have_their_own_listener_length():
    source = build_source_score()
    first_part = make_part(source.tracks[0:2])
    second_part = make_part(source.tracks[2:3])

    # The first part contains the tempo map, so its two beats are split
    # across both tempos; the second part has no tempo event of its own
    # and plays entirely at the default tempo, even though it lasts the
    # same number of ticks as the full score.
    assert first_part.length == pytest.approx(
        expected_playback_length(NOTE_PART_END_TICK))
    assert second_part.length == pytest.approx(
        tick2second(LONG_PART_END_TICK, TICKS_PER_BEAT, INITIAL_TEMPO))

    # The full score, from either route, has the tempo-aware length.
    expected_full_length = expected_playback_length(LONG_PART_END_TICK)
    assert direct_export().length == pytest.approx(expected_full_length)
    assert staged_export().length == pytest.approx(expected_full_length)


def test_part_scores_play_standalone_and_as_merge_input():
    source = build_source_score()
    first_part = make_part(source.tracks[0:2])
    second_part = make_part(source.tracks[2:3])

    # Every delivered file is a playable SMF on its own.
    standalone_events = {
        'first': playback_stream(first_part),
        'second': playback_stream(second_part),
    }
    assert standalone_events['first']
    assert standalone_events['second']
    for name, events in standalone_events.items():
        assert all(time >= 0 for _kind, _name, time, _params in events), name

    # And each part is a valid input to the later merge: the reassembled
    # full score, after another save/load cycle, still matches the direct
    # export.
    reassembled = round_trip(MidiFile(
        type=1,
        ticks_per_beat=TICKS_PER_BEAT,
        tracks=first_part.tracks + second_part.tracks))
    assert merged_stream(reassembled) == merged_stream(direct_export())
    assert playback_stream(reassembled) == playback_stream(direct_export())


def test_three_way_delivery_reassembles_into_the_same_performance():
    source = build_source_score()
    parts = [make_part(source.tracks[index:index + 1])
             for index in range(len(source.tracks))]
    reassembled = round_trip(MidiFile(
        type=1,
        ticks_per_beat=TICKS_PER_BEAT,
        tracks=[track for part in parts for track in part.tracks]))

    assert end_tick(reassembled) == LONG_PART_END_TICK
    assert reassembled.length == pytest.approx(
        expected_playback_length(LONG_PART_END_TICK))
    assert merged_stream(reassembled) == merged_stream(direct_export())
    assert playback_stream(reassembled) == playback_stream(direct_export())
