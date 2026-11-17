# Floorshow UI design

Design canvas for the control window, configuration dialog and calibration window
(built from the app design brief).

- Live canvas: https://claude.ai/artifact/HLSTFefkgNHM5JkQKb9TH5
- `*.dc.html`: one artboard per file (self-contained; they render inside the Design canvas runtime, not a plain browser)
- `canvas.json`: canvas index (board positions, titles)
- `generator/`: Python that produced the artboards. Edit tokens/components there and run
  `python3 generator/build.py` to regenerate all files.

Rules: red is only the beat; amber is warning and error; teal is the single accent.
Tokens are in `generator/gen_lib.py` (`DARK`, `LIGHT`) and on the `Tokens` board.
