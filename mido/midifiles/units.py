# SPDX-FileCopyrightText: 2017 Ole Martin Bjorndalen <ombdalen@gmail.com>
#
# SPDX-License-Identifier: MIT

# Real playback rate for each SMPTE frame-format code stored in the high
# byte of a negative MIDI division word. Code 29 denotes 30 drop-frame.
SMPTE_FRAMES_PER_SECOND = {
    24: 24.0,
    25: 25.0,
    29: 29.97,
    30: 30.0,
}


def tick2second(tick, ticks_per_beat, tempo):
    """Convert absolute time in ticks to seconds.

    Returns absolute time in seconds for a chosen MIDI file time resolution
    (ticks/pulses per quarter note, also called PPQN) and tempo (microseconds
    per quarter note).

    If `ticks_per_beat` is given the raw *negative* SMPTE division word from
    a file header, the frame-based conversion is used instead and `tempo` is
    ignored: such ticks subdivide SMPTE frames and measure wall-clock time
    directly.
    """
    if isinstance(ticks_per_beat, int) and ticks_per_beat < 0:
        frame_rate_code = -(ticks_per_beat >> 8)
        ticks_per_frame = ticks_per_beat & 0xFF
        return smpte_tick2second(tick, frame_rate_code, ticks_per_frame)

    scale = tempo * 1e-6 / ticks_per_beat
    return tick * scale


def smpte_tick2second(tick, frames_per_second, ticks_per_frame):
    """Convert absolute time in SMPTE timecode ticks to seconds.

    This is used for MIDI files whose division is a negative SMPTE time
    division instead of ticks per beat. `frames_per_second` is the frame rate
    declared by the file (24, 25, 29.97 or 30) and `ticks_per_frame` is the
    number of delta-time ticks subdividing each frame.

    Unlike :func:`tick2second`, no tempo is involved: in a timecode division
    file the ticks measure wall-clock time directly, so tempo meta messages
    are still stored on the track for editors but do not change playback
    speed.
    """
    return tick / (float(frames_per_second) * ticks_per_frame)


def second2smpte_tick(second, frames_per_second, ticks_per_frame):
    """Convert absolute time in seconds to SMPTE timecode ticks.

    Inverse of :func:`smpte_tick2second`. Normal rounding applies.
    """
    return int(round(second * frames_per_second * ticks_per_frame))


def second2tick(second, ticks_per_beat, tempo):
    """Convert absolute time in seconds to ticks.

    Returns absolute time in ticks for a chosen MIDI file time resolution
    (ticks/pulses per quarter note, also called PPQN) and tempo (microseconds
    per quarter note). Normal rounding applies.
    """
    scale = tempo * 1e-6 / ticks_per_beat
    return int(round(second / scale))


def bpm2tempo(bpm, time_signature=(4, 4)):
    """Convert BPM (beats per minute) to MIDI file tempo (microseconds per
    quarter note).

    Depending on the chosen time signature a bar contains a different number of
    beats. These beats are multiples/fractions of a quarter note, thus the
    returned BPM depend on the time signature. Normal rounding applies.
    """
    return int(round(60 * 1e6 / bpm * time_signature[1] / 4.))


def tempo2bpm(tempo, time_signature=(4, 4)):
    """Convert MIDI file tempo (microseconds per quarter note) to BPM (beats
    per minute).

    Depending on the chosen time signature a bar contains a different number of
    beats. The beats are multiples/fractions of a quarter note, thus the
    returned tempo depends on the time signature denominator.
    """
    return 60 * 1e6 / tempo * time_signature[1] / 4.
