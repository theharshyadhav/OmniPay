"""Generates the soundtrack: a warm, light music bed (pad, pluck arpeggio, soft beat, bass) + a tick on every on-screen tap
and a chime when a payment succeeds. Timing comes from scenes/plan.json. Writes soundtrack.wav (48 kHz stereo)."""
import json, os, wave
import numpy as np

D = os.path.dirname(os.path.abspath(__file__))
P = json.load(open(f"{D}/scenes/plan.json"))
FPS, SR = 30, 48000
total = sum(L for _, L in P["scenes"]) / FPS
N = int(total * SR) + SR
t = np.arange(N) / SR
L = np.zeros(N); R = np.zeros(N)
BPM = 104; beat = 60 / BPM; bar = 4 * beat
hz = lambda m: 440 * 2 ** ((m - 69) / 12)

# progression (MIDI notes): Cmaj9, Am9, Fmaj9, G6sus
CH = [[48, 55, 59, 62, 64], [45, 52, 55, 59, 60], [41, 48, 52, 55, 57], [43, 50, 55, 57, 62]]
intro_end = P["scenes"][0][1] / FPS
outro_start = total - P["scenes"][-1][1] / FPS

def lowpass(x, a):
    y = np.empty_like(x); acc = 0.0
    for i in range(0, len(x), 1):
        acc += a * (x[i] - acc); y[i] = acc
    return y

def add(sig, start, gain=1.0, pan=0.0):
    i = int(start * SR); j = min(N, i + len(sig))
    if i >= N or j <= i: return
    L[i:j] += sig[:j - i] * gain * (1 - pan) ; R[i:j] += sig[:j - i] * gain * (1 + pan)

# pad: two bars per chord, slow swell, gentle detune
nbars = int(total / bar) + 1
for b in range(0, nbars, 2):
    chord = CH[(b // 2) % 4]; st = b * bar; dur = 2 * bar + 0.6
    tt = np.arange(int(dur * SR)) / SR
    env = np.minimum(1, tt / 0.9) * np.minimum(1, (dur - tt) / 0.8)
    sig = np.zeros_like(tt)
    for k, m in enumerate(chord[1:]):
        f0 = hz(m + 12)
        sig += (np.sin(2 * np.pi * f0 * tt) + 0.6 * np.sin(2 * np.pi * f0 * 1.003 * tt + k) + 0.18 * np.sin(4 * np.pi * f0 * tt)) / 4
    add(sig * env, st, 0.075, -0.15); add(sig * env, st + 0.012, 0.075, 0.15)
    # bass on each bar
    for k in range(2):
        bt = np.arange(int(bar * SR)) / SR
        f0 = hz(chord[0] - 12 + 12)
        bs = np.sin(2 * np.pi * f0 * bt) * np.exp(-bt * 1.6) * np.minimum(1, bt / 0.01)
        add(bs, st + k * bar, 0.16)

# pluck arpeggio in 8ths, from the end of the intro until the outro fades
step = beat / 2; pattern = [1, 2, 3, 4, 3, 2, 4, 3]
k = 0; x = intro_end - 0.2
while x < total - 1.5:
    b = int(x / bar); chord = CH[(b // 2) % 4]
    m = chord[pattern[k % 8]] + 24
    pt = np.arange(int(0.6 * SR)) / SR
    sig = (np.sin(2 * np.pi * hz(m) * pt) + 0.3 * np.sin(4 * np.pi * hz(m) * pt)) * np.exp(-pt * 7) * np.minimum(1, pt / 0.003)
    add(sig, x, 0.05 if k % 2 == 0 else 0.035, 0.35 if k % 2 else -0.35)
    x += step; k += 1

# soft kick on 1 and 3, closed hats on the off-beats (only between intro and outro)
rng = np.random.default_rng(7)
x = intro_end
while x < outro_start + 2 * bar:
    for q in range(4):
        tb = x + q * beat
        if q in (0, 2):
            kt = np.arange(int(0.3 * SR)) / SR
            ph = 2 * np.pi * np.cumsum(50 + 80 * np.exp(-kt * 30)) / SR
            add(np.sin(ph) * np.exp(-kt * 9), tb, 0.22)
        ht = np.arange(int(0.05 * SR)) / SR
        nz = rng.standard_normal(len(ht)); nz = nz - lowpass(nz, 0.35)
        add(nz * np.exp(-ht * 70), tb + beat / 2, 0.02, 0.4)
    x += bar

# UI sounds
for fr in P["taps"]:
    tt = np.arange(int(0.06 * SR)) / SR
    add(np.sin(2 * np.pi * 1800 * tt) * np.exp(-tt * 90), fr / FPS, 0.10)
for fr in P["paid"]:
    for i, m in enumerate((84, 88, 91)):                       # C6 E6 G6, rolled
        tt = np.arange(int(1.4 * SR)) / SR
        f0 = hz(m)
        bell = (np.sin(2 * np.pi * f0 * tt) + 0.25 * np.sin(2 * np.pi * f0 * 2.01 * tt)) * np.exp(-tt * 3.2) * np.minimum(1, tt / 0.004)
        add(bell, fr / FPS + i * 0.07, 0.09, (i - 1) * 0.3)

# master: fades, gentle saturation, normalise
fade = np.ones(N); fi = int(0.8 * SR); fade[:fi] = np.linspace(0, 1, fi)
fo0 = int((total - 2.2) * SR); fade[fo0:] = np.linspace(1, 0, N - fo0).clip(0)
L *= fade; R *= fade
mix = np.tanh(np.stack([L, R], 1) * 1.4) / 1.4
mix = mix[: int(total * SR)]
mix *= 0.85 / max(1e-6, np.abs(mix).max())
with wave.open(f"{D}/soundtrack.wav", "wb") as w:
    w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR)
    w.writeframes((mix * 32767).astype(np.int16).tobytes())
print(f"soundtrack {total:.1f}s, {len(P['taps'])} taps, {len(P['paid'])} chimes")
