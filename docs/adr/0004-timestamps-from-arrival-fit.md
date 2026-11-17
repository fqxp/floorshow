# 4. Capture times from a fit of arrival times, not from PipeWire's timestamps

- Status: accepted
- Date: 2026-11-16
- Code: `src/mirrorballs/listen/timing.py`, tests `tests/test_listen_timing.py`

## Context

The renderer draws the beat on its monotonic clock. The tracker sees samples. Every block of samples therefore needs
the monotonic time at which it was captured, and that time must stay right for a whole night: an error that grows by
a millisecond a minute is a minute-long show that looks fine and a three-hour one that does not.

## Options considered

- **The buffers' own timestamps (PTS)** from GStreamer, mapped to the monotonic clock once.
- **Counting samples** from the first block's arrival.
- **A fit of arrival times against the sample count**: buffers reach Python late by a varying amount but never early,
  and the shortest delays are nearly constant, so the earliest arrivals trace a straight line through
  (sample count, time). Per second of audio the earliest arrival is kept, and a line through the last 30 of them
  gives the time of any sample. Its slope follows the sound card's drift.

## How it was tested

With `tools/audio_spike.py` on the built-in card, 60 s, three sources:

- PTS against sample count: very smooth (jitter 0.01 ms) at 47 999.0 to 47 999.2 samples per second. Arrival times
  against sample count: 47 998.2. The PTS time base drifts about 20 ppm against the monotonic clock, about 1.2 ms
  a minute.
- `audiobuffersplit` (ADR 3) rewrites the PTS from the sample count (exactly 48 000.0), hiding the drift entirely.
- Arrival times scatter by 0.3 ms (5.3 ms buffers) to 1.5 ms (20 ms buffers) around their fit, but the earliest
  ones are stable.

Tests of the fit (`tests/test_listen_timing.py`): simulated cards running −35, 0, +20 and +200 ppm, buffers arriving
4 ms plus an exponentially distributed extra delay (mean 3 ms): after two minutes every capture time is within
0.5 ms and the measured drift within 3 ppm.

The tracker originally took only its first block's time and counted samples after that. A test with a card running
0.2 % fast (`test_block_timestamps_win_over_counting_samples`) puts the beat 53 ms off (median) after 30 s that way,
and 0.4 ms off with every block's own time. The tracker now uses every block's time.

## Decision

Time the samples with the arrival fit (`SampleClock`), hand every block's capture time to the tracker, and use the PTS
only through `audiorate`, to keep the sample count continuous across lost audio.

## Consequences

- The beat stays on the monotonic clock however long the show runs, whatever the card's drift.
- One constant is not observable this way: the shortest delay from capture to Python. It is taken as one PipeWire
  quantum (`CAPTURE_DELAY_S`, 5 ms); the rest is calibrated with the clock's audio offset in Preferences.
- The first second has only a rough fit (the earliest arrival so far); the tracker needs several seconds to lock
  anyway.
