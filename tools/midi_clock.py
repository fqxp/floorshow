"""Virtual MIDI clock generator for testing (no hardware needed).

    uv run python tools/midi_clock.py --bpm 128            # runs until Ctrl-C
    uv run python tools/midi_clock.py --bpm 128 --measure 10   # also receives its own clock, prints jitter
    uv run python tools/midi_clock.py --bpm 128 --beat-notes   # the messages of Mixxx's "MIDI for light" script

Creates an ALSA sequencer output port named "floorshow test clock", sends Start, then 24 ticks
per beat. Pick that port as the clock source in the app.
"""

import argparse
import statistics
import time

import rtmidi

PORT_NAME = "floorshow test clock"
CLOCK, START, STOP = 0xF8, 0xFA, 0xFC


def beat_notes(out: rtmidi.MidiOut, bpm: float) -> None:
    """The messages of Mixxx's Midi_for_light script (channel 1): on every beat note-on D (50, velocity 100) and
    note-on E (52, value BPM - 50), half a beat later the beat's note-off in both forms the script sends."""
    period = 60.0 / bpm
    tempo = min(127, max(0, round(bpm) - 50))
    print(f"sending Midi_for_light beats every {period * 1000:.1f} ms ({bpm} BPM) on '{PORT_NAME}', Ctrl-C to stop")
    out.send_message([0x90, 0x30, 0x64])  # deck change: deck 1
    start, k = time.monotonic(), 0
    try:
        while True:
            for offset, messages in ((0.0, ([0x90, 50, 100], [0x90, 52, tempo])), (period / 2, ([0x90, 50, 0], [0x80, 50, 0]))):
                while (dt := start + k * period + offset - time.monotonic()) > 0:
                    time.sleep(dt - 0.0005 if dt > 0.001 else 0)
                for m in messages:
                    out.send_message(m)
            k += 1
    except KeyboardInterrupt:
        pass


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bpm", type=float, default=120.0)
    ap.add_argument("--measure", type=float, metavar="SECONDS", help="receive our own clock and report jitter")
    ap.add_argument("--beat-notes", action="store_true",
                    help="send note D (50, velocity 100) on every beat and the tempo on note E (52, value BPM - 50)")
    args = ap.parse_args()

    out = rtmidi.MidiOut()
    out.open_virtual_port(PORT_NAME)

    stamps: list[int] = []
    inp = None
    if args.measure:
        inp = rtmidi.MidiIn()
        inp.ignore_types(timing=False)
        inp.set_callback(lambda msg, _: stamps.append(time.monotonic_ns()) if msg[0][0] == CLOCK else None)
        ports = inp.get_ports()
        idx = next(i for i, p in enumerate(ports) if PORT_NAME in p)
        inp.open_port(idx)

    if args.beat_notes:
        beat_notes(out, args.bpm)
        return
    period = 60.0 / (args.bpm * 24)
    print(f"sending {args.bpm} BPM on '{PORT_NAME}' (period {period * 1000:.3f} ms), Ctrl-C to stop")
    out.send_message([START])
    start = time.monotonic()
    k = 0
    try:
        while True:
            target = start + k * period
            while (dt := target - time.monotonic()) > 0:
                time.sleep(dt - 0.0005 if dt > 0.001 else 0)  # sleep, then busy-wait the last ~1 ms
            out.send_message([CLOCK])
            k += 1
            if args.measure and k * period >= args.measure:
                break
    except KeyboardInterrupt:
        pass
    out.send_message([STOP])

    if inp is not None:
        time.sleep(0.1)
        iv = [(b - a) / 1e6 for a, b in zip(stamps, stamps[1:])]
        print(f"received {len(stamps)}/{k} ticks; interval ms: mean {statistics.mean(iv):.3f} "
              f"(ideal {period * 1000:.3f}), stdev {statistics.stdev(iv):.3f}, "
              f"min {min(iv):.3f}, max {max(iv):.3f}")
        print(f"implied BPM {60000 / (statistics.mean(iv) * 24):.3f}")
        # drift-free phase error: deviation of each tick from a straight line
        t0 = stamps[0]
        err = [(s - t0) / 1e6 - i * period * 1000 for i, s in enumerate(stamps)]
        print(f"phase error vs ideal grid ms: stdev {statistics.stdev(err):.3f}, "
              f"max |err| {max(abs(e) for e in err):.3f}")


if __name__ == "__main__":
    main()
