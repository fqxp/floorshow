# 6. The audio clock takes the tracker's phase and tempo but keeps its own bar; the downbeat stays manual

- Status: accepted
- Date: 2026-11-16
- Code: `Clock.use_audio` and `Clock.on_audio_estimate` in `src/floorshow/common/clock.py`, tests in
  `tests/test_clock.py`

## Context

The renderer's clock is one model for every source: `beat(t) = anchor_beat + (t - anchor_time) * bpm / 60`, with the
bar position `beat / beats_per_bar`. Scenes switch on bars and draw on bar positions, so the bar matters. The beat
tracker has a model of the same form, but its beat numbers start wherever it first locked, and a beat tracker
cannot tell reliably which beat starts a bar.

## Options considered

- **Detect the downbeat from the audio.** Possible in principle (accents, bass patterns), unreliable on the music
  this is for, and wrong bars are worse than none: every scene change would land off the phrase. The project owner
  decided against it.
- **Let the tracker's beat numbers drive the clock.** Every re-lock (a new track, a phase jump) would move the bar
  to wherever the tracker counts from.
- **Take only the fraction of the tracker's beat**, so the clock keeps counting its own beats and the bar stays
  where the Downbeat button put it, as with MIDI beat notes, which do not carry the bar either.

## Decision

About ten times a second the listener hands the tracker's model to the renderer. The clock computes the tracker's
beat at that moment, takes the difference to its own beat modulo one (−0.5 to 0.5), and:

- slews half of a small difference per estimate (the tracker's model is already smoothed);
- jumps at once when the difference is more than a quarter beat (a re-lock, a new track);
- takes the tracker's tempo, applied from that moment on (its own position is read at the old tempo first; the
  tests caught the order once);
- ignores estimates the tracker is not sure of, and reports "waiting for beat" when no sure estimate came for
  1 s (or 2 beats), while the beat keeps running at the held tempo, as with a lost MIDI clock.

The bar is set by hand with Downbeat (key D).

## Consequences

- A bar set with Downbeat survives every correction, re-lock and tempo change (`test_audio_keeps_the_downbeat`).
- One press of Downbeat per track (or after a jump the tracker did not see as one) is part of running the show.
- End to end with the real tracker on synthetic audio, the clock is within 15 ms of the true beats from 12 s on;
  between 8 and 12 s it is still settling (up to about 30 ms).
- `offset_ms` and `audio_offset_ms` both shift what the clock reports, so the projector's and the sound path's
  delays are calibrated separately.
