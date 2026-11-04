# Demo video tooling

Rebuilds the 1080p phone demo video from the live app (no screen recorder needed).

1. `python camfeed.py cam.y4m`: a fake camera feed showing a chai-stall UPI QR code.
2. `date +%s > base.txt; echo 0 > filmclock.txt`, then `OMNIPAY_DB=film.db python filmserver.py filmclock.txt $(cat base.txt)` (demo server on :8124 whose clock follows the film).
3. `node capture.js`: films the app one screenshot per frame (Playwright, phone viewport), writing `shots/` and `timeline.json`.
4. `python compose.py render`: phone mockup, captions, tap ripples and zooms, one mp4 per scene in `scenes/`.
5. `python music.py`: generated music bed, tap ticks and payment chimes in `soundtrack.wav`.
6. Join the scenes with ffmpeg's concat demuxer (`-c copy`) and add the soundtrack (`-c:v copy`).

Captions live in `CAPS` in compose.py. To change one scene, run `python compose.py render <scene>` and join again.
