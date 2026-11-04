"""Composites the captured phone footage into a 1920x1080 demo: phone mockup, captions, tap ripples, in-screen zooms.
  python compose.py preview <scene> <frame> [<scene> <frame> ...]   -> preview JPGs
  python compose.py render                                        -> one mp4 per scene in scenes/
"""
import json, math, os, subprocess, sys
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

D = os.path.dirname(os.path.abspath(__file__))
W, H, FPS = 1920, 1080, 30
T = json.load(open(f"{D}/timeline.json"))
VW, VH, SC = T["viewport"][0], T["viewport"][1], T["scale"]

INTER = "/usr/share/fonts/opentype/inter/"
ADV = "/usr/share/texmf/fonts/opentype/public/tex-gyre/texgyreadventor-bold.otf"
f = lambda p, s: ImageFont.truetype(p, s)
F_TITLE, F_SUB = f(ADV, 64), f(INTER + "Inter-Regular.otf", 31)
F_HERO, F_HERO_SUB = f(ADV, 104), f(INTER + "Inter-Regular.otf", 38)
F_BRAND, F_LINE = f(ADV, 48), f(INTER + "Inter-Medium.otf", 34)
F_STATUS = f(INTER + "Inter-SemiBold.otf", 17)
INK, MUTED, COBALT = (255, 255, 255), (169, 182, 207), (111, 135, 255)

# ---------------------------------------------------------------- geometry
SW = 430                                  # screen width on canvas
K = SW / VW                               # CSS px -> canvas px
SB = round(44 * K)                        # status bar
SH = round(VH * K) + SB                   # screen height
BZ = 14                                   # bezel
PX, PY = 1140, (H - SH) // 2              # screen top-left
RAD = 54

def ease(t): t = min(1, max(0, t)); return t * t * (3 - 2 * t)

# background: deep navy with a cobalt glow behind the phone
yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
base = np.stack([11 + 8 * yy / H, 20 + 12 * yy / H, 36 + 20 * yy / H], -1)
g = np.exp(-(((xx - (PX + SW / 2)) / 520) ** 2 + ((yy - H / 2) / 460) ** 2))
base += g[..., None] * np.array([28, 42, 110], np.float32)
BG = Image.fromarray(base.clip(0, 255).astype(np.uint8))
sh = Image.new("L", (W, H), 0); ImageDraw.Draw(sh).rounded_rectangle((PX - BZ + 20, PY - BZ + 40, PX + SW + BZ - 20, PY + SH + BZ + 30), RAD + BZ, fill=170)
sh = sh.filter(ImageFilter.GaussianBlur(40))
BG_PHONE = Image.composite(Image.new("RGB", (W, H), (3, 6, 12)), BG, sh)

def phone_body():
    im = Image.new("RGBA", (SW + 2 * BZ + 8, SH + 2 * BZ), (0, 0, 0, 0)); d = ImageDraw.Draw(im)
    d.rounded_rectangle((4, 0, SW + 2 * BZ + 3, SH + 2 * BZ - 1), RAD + BZ, fill=(8, 12, 20), outline=(58, 72, 102), width=2)
    for y0, y1 in ((150, 210), (240, 330), (350, 440)):            # side buttons
        d.rounded_rectangle((0, y0, 6, y1), 3, fill=(40, 50, 72))
    d.rounded_rectangle((SW + 2 * BZ + 2, 270, SW + 2 * BZ + 7, 380), 3, fill=(40, 50, 72))
    return im
BODY = phone_body()
MASK = Image.new("L", (SW, SH), 0); ImageDraw.Draw(MASK).rounded_rectangle((0, 0, SW - 1, SH - 1), RAD, fill=255)

# ---------------------------------------------------------------- shots
cache = {}
def load(file):
    if file not in cache:
        if len(cache) > 40: cache.clear()
        cache[file] = Image.open(f"{D}/shots/{file}").convert("RGB")
    return cache[file]

def zoom_state(scene, fr):
    z, cx, cy = 1.0, VW / 2, VH / 2
    for zz in scene["zooms"]:
        if fr < zz["at"]: break
        t = ease((fr - zz["at"]) / zz["dur"])
        cap = 1.06 if (scene["id"] == "pay" and zz is scene["zooms"][0]) else 1.18
        z, cx, cy = z + (min(zz["to"], cap) - z) * t, cx + (zz["cx"] - cx) * t, cy + (zz["cy"] - cy) * t
    return z, cx, cy

def screen(scene, fr, file):
    im = load(file)
    z, cx, cy = zoom_state(scene, fr)
    cw, ch = VW / z, VH / z
    x0 = min(max(cx - cw / 2, 0), VW - cw); y0 = min(max(cy - ch / 2, 0), VH - ch)
    crop = im.crop((round(x0 * SC), round(y0 * SC), round((x0 + cw) * SC), round((y0 + ch) * SC)))
    content = crop.resize((SW, SH - SB), Image.LANCZOS if z > 1.01 else Image.BILINEAR)
    # status bar in the page's own top colour
    top = im.getpixel((6, 6)); dark = sum(top) / 3 < 110
    scr = Image.new("RGB", (SW, SH), top); scr.paste(content, (0, SB))
    d = ImageDraw.Draw(scr); fg = (240, 244, 250) if dark else (19, 32, 52)
    d.text((34, SB / 2 + 2), "9:41", font=F_STATUS, fill=fg, anchor="lm")
    d.rounded_rectangle((SW / 2 - 52, 9, SW / 2 + 52, 37), 14, fill=(0, 0, 0))                     # dynamic island
    bx = SW - 36                                                                                    # battery, wifi, signal
    d.rounded_rectangle((bx - 24, SB / 2 - 6, bx, SB / 2 + 6), 3, outline=fg, width=1); d.rectangle((bx - 22, SB / 2 - 4, bx - 5, SB / 2 + 4), fill=fg)
    d.rectangle((bx + 1, SB / 2 - 2, bx + 3, SB / 2 + 2), fill=fg)
    for i, r in enumerate((4, 8, 12)):
        d.arc((bx - 46 - r, SB / 2 + 5 - r, bx - 46 + r, SB / 2 + 5 + r), 225, 315, fill=fg, width=2)
    for i in range(4):
        d.rectangle((bx - 84 + i * 5, SB / 2 + 5 - (i + 1) * 3, bx - 81 + i * 5, SB / 2 + 5), fill=fg)
    # tap ripples (positions follow the zoom)
    ov = Image.new("RGBA", (SW, SH), (0, 0, 0, 0)); od = ImageDraw.Draw(ov)
    for tp in scene["taps"]:
        age = fr - tp["at"]
        if 0 <= age < 18:
            sx = (tp["x"] - x0) * z * K; sy = (tp["y"] - y0) * z * K + SB
            r = 14 + age * 1.8; a = int(150 * (1 - age / 18))
            od.ellipse((sx - r, sy - r, sx + r, sy + r), outline=(255, 255, 255, a), width=3)
            if age < 10:
                od.ellipse((sx - 15, sy - 15, sx + 15, sy + 15), fill=(120, 140, 170, int(120 * (1 - age / 10))))
    scr = Image.alpha_composite(scr.convert("RGBA"), ov).convert("RGB")
    return scr

def shot_at(scene, fr):
    acc = 0
    for s in scene["shots"]:
        if fr < acc + s["dur"]: return s["file"]
        acc += s["dur"]
    return scene["shots"][-1]["file"]

def scene_len(scene): return sum(s["dur"] for s in scene["shots"])

# ---------------------------------------------------------------- text
def wrap(d, text, font, width):
    lines, cur = [], ""
    for w in text.split():
        t = (cur + " " + w).strip()
        if d.textlength(t, font=font) <= width: cur = t
        else: lines.append(cur); cur = w
    return lines + [cur] if cur else lines

def text_block(canvas, x, y, title, sub, alpha, rise, width=760, center=False, tf=F_TITLE, sf=F_SUB):
    if alpha <= 0: return
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0)); d = ImageDraw.Draw(layer)
    tl = wrap(d, title, tf, width); sl = wrap(d, sub, sf, width) if sub else []
    th, sh_ = tf.size * 1.12, sf.size * 1.45
    total = len(tl) * th + (24 + len(sl) * sh_ if sl else 0)
    cy = y - total / 2 + rise
    for line in tl:
        d.text((x if not center else W / 2, cy), line, font=tf, fill=INK + (int(255 * alpha),), anchor="la" if not center else "ma"); cy += th
    cy += 24
    for line in sl:
        d.text((x if not center else W / 2, cy), line, font=sf, fill=MUTED + (int(255 * alpha),), anchor="la" if not center else "ma"); cy += sh_
    canvas.alpha_composite(layer)

def captions_at(caps, fr, length):
    """caps: [(start, title, sub)]. Returns [(title, sub, alpha, rise)] with crossfades."""
    out = []
    for i, (st, ti, su) in enumerate(caps):
        end = caps[i + 1][0] if i + 1 < len(caps) else length + 30
        if st - 2 <= fr < end + 10:
            a_in = ease((fr - st) / 12); a_out = 1 - ease((fr - (end - 6)) / 10) if fr >= end - 6 else 1
            if i + 1 == len(caps) and end >= length: a_out = 1
            out.append((ti, su, a_in * a_out, (1 - a_in) * 26))
    return out

def brand(canvas, cx, y, alpha, size=56):
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0)); d = ImageDraw.Draw(layer); a = int(255 * alpha)
    tw = d.textlength("OmniPay", font=F_BRAND); x = cx - (size + 18 + tw) / 2
    d.rounded_rectangle((x, y, x + size, y + size), size * 0.28, fill=(255, 255, 255, a))
    s = size / 32
    d.ellipse((x + (13 - 6.5) * s, y + (16 - 6.5) * s, x + (13 + 6.5) * s, y + (16 + 6.5) * s), outline=(19, 32, 52, a), width=max(2, round(2.6 * s)))
    d.ellipse((x + (20.5 - 6.5) * s, y + (16 - 6.5) * s, x + (20.5 + 6.5) * s, y + (16 + 6.5) * s), outline=(39, 71, 232, a), width=max(2, round(2.6 * s)))
    d.text((x + size + 18, y + size / 2), "OmniPay", font=F_BRAND, fill=(255, 255, 255, a), anchor="lm")
    canvas.alpha_composite(layer)

# ---------------------------------------------------------------- scenes
CAPS = {
    "signin": [(0, "One request. Every way to pay it.", "Dana asks for $120. You could send euros, USDC, bitcoin or Apple shares."),
               (88, "Try it with a demo wallet", "One tap. No sign-up.")],
    "home": [(0, "Everything you hold, in one balance", "Cash, crypto and shares. Every one of them can pay anyone.")],
    "scan": [(0, "Scan any QR code", "UPI codes and OmniPay codes, straight from the camera."),
             ("opened", "Ravi's chai stall wants 250 rupees", "Payee, amount and note fill themselves in.")],
    "choose": [(0, "You choose what leaves your wallet", "Pay in USDC, euros or dollars. Ravi still gets rupees."),
               (112, "No fee. No spread.", "Converted at mid-market. Sponsored options are labelled and never picked for you.")],
    "pay": [(0, "See exactly where the money goes", "3 USDC in, ₹250 out, at the mid-market rate."),
            (150, "Your PIN, and it's done", "Every payment needs your PIN. Prices are held while you decide."),
            ("paid", "Paid in seconds", "Ravi gets rupees. You spent stablecoins.")],
    "shares": [(0, "Or pay with shares", "Dana takes Apple stock, so it reaches her as it is."),
               (150, "Every asset, side by side", "The exact amount in each one, all at the same value."),
               ("paid", "0.43 Apple shares, sent", "Shares only go to people who opt in, and are never sold for you.")],
    "qr": [(0, "Your own QR code", "Unique to you. Anyone can pay you in whatever they hold."),
           (96, "Ask for an amount", "Share it, save it as an image, or print it for your stall.")],
    "assistant": [(0, "This request hides an attack", "Its note tells AI assistants to send $5,000 to someone else."),
                  (100, "The assistant isn't fooled", "It pays the real $120 bill, inside your limits, and logs every step.")],
    "dark": [(0, "Made for your phone", "Light and dark themes. Add it to your home screen like an app.")],
}
PAD = {"signin": 20, "home": 0, "scan": 10, "choose": 20, "pay": 20, "shares": 10, "qr": 20, "assistant": 10, "dark": 40}

def phone_frame(scene, fr, length, enter=False):
    can = BG_PHONE.copy().convert("RGBA")
    dy = 0
    if enter: dy = (1 - ease(fr / 26)) * 1100
    can.alpha_composite(BODY, (PX - BZ - 4, PY - BZ + int(dy)))
    scr = screen(scene, min(fr, scene_len(scene) - 1), shot_at(scene, min(fr, scene_len(scene) - 1)))
    can.paste(scr, (PX, PY + int(dy)), MASK)
    caps = [(scene["marks"].get(s, s) if isinstance(s, str) else s, a, b) for s, a, b in CAPS[scene["id"]]]
    for ti, su, al, ri in captions_at(caps, fr, length):
        text_block(can, 150, H / 2, ti, su, al, ri)
    return can.convert("RGB")

def intro_frame(fr):
    can = BG.copy().convert("RGBA")
    brand(can, W / 2, 300, ease((fr - 4) / 14))
    a1 = ease((fr - 14) / 16); a2 = ease((fr - 48) / 16)
    text_block(can, 0, 520, "Pay with whatever you hold.", "", a1, (1 - a1) * 30, width=1500, center=True, tf=F_HERO)
    text_block(can, 0, 650, "Cash, crypto, even shares. Paid by a single scan.", "", a2, (1 - a2) * 20, width=1500, center=True, tf=F_HERO_SUB)
    out = 1 - ease((fr - 132) / 18)
    if out < 1: can = Image.blend(BG.convert("RGBA"), can, out)
    return can.convert("RGB")

OUTRO_LINES = ["No fees and no spread, ever", "Scan UPI and OmniPay QR codes", "PayPal for money in and out", "A double-entry ledger, 164 tests"]
def outro_frame(fr):
    can = BG.copy().convert("RGBA")
    brand(can, W / 2, 210, ease(fr / 14), size=64)
    a = ease((fr - 8) / 16)
    text_block(can, 0, 400, "Pay with whatever you hold.", "", a, (1 - a) * 26, width=1600, center=True, tf=F_HERO)
    layer = Image.new("RGBA", can.size, (0, 0, 0, 0)); d = ImageDraw.Draw(layer)
    for i, line in enumerate(OUTRO_LINES):
        al = ease((fr - 30 - i * 10) / 14)
        y = 540 + i * 62 + (1 - al) * 16
        d.ellipse((W / 2 - 300, y + 13, W / 2 - 288, y + 25), fill=COBALT + (int(255 * al),))
        d.text((W / 2 - 266, y), line, font=F_LINE, fill=(230, 236, 246, int(255 * al)))
    al = ease((fr - 80) / 16)
    d.text((W / 2, 870), "Built for the PayPal Hackathon", font=F_SUB, fill=MUTED + (int(255 * al),), anchor="ma")
    can.alpha_composite(layer)
    end = ease((fr - (OUTRO_LEN - 24)) / 22)
    if end > 0: can = Image.blend(can, Image.new("RGBA", can.size, (0, 0, 0, 255)), end)
    return can.convert("RGB")

INTRO_LEN, OUTRO_LEN = 150, 210
def plan():
    out = [("intro", INTRO_LEN, intro_frame)]
    for i, sc in enumerate(T["scenes"]):
        L = scene_len(sc) + PAD[sc["id"]]
        out.append((sc["id"], L, (lambda sc, L, first: lambda fr: phone_frame(sc, fr, L, enter=first))(sc, L, i == 0)))
    out.append(("outro", OUTRO_LEN, outro_frame))
    return out

def render():
    os.makedirs(f"{D}/scenes", exist_ok=True)
    total = 0
    only = sys.argv[2:]
    for name, L, fn in plan():
        if only and name not in only: total += L; continue
        tmp, final = f"{D}/scenes/{name}.tmp.mp4", f"{D}/scenes/{name}.mp4"
        p = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
                              "-c:v", "libx264", "-crf", "18", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-r", str(FPS), tmp], stdin=subprocess.PIPE)
        for fr in range(L):
            p.stdin.write(fn(fr).tobytes())
        p.stdin.close()
        if p.wait() == 0: os.replace(tmp, final)
        else: sys.exit(f"ffmpeg failed on {name}")
        total += L
        print(name, L, f"{total / FPS:.1f}s", flush=True)
    if not only: json.dump({"scenes": [(n, L) for n, L, _ in plan()], "taps": taps_global(), "paid": paid_global()}, open(f"{D}/scenes/plan.json", "w"))

def offsets():
    o, acc = {}, 0
    for name, L, _ in plan(): o[name] = acc; acc += L
    return o, acc
def taps_global():
    o, _ = offsets(); return [o[sc["id"]] + t["at"] + 7 for sc in T["scenes"] for t in sc["taps"]]
def paid_global():
    o, _ = offsets(); return [o[sc["id"]] + sc["marks"]["paid"] for sc in T["scenes"] if "paid" in sc["marks"]]

if __name__ == "__main__":
    if sys.argv[1] == "preview":
        fns = {n: fn for n, L, fn in plan()}
        a = sys.argv[2:]
        for i in range(0, len(a), 2):
            fns[a[i]](int(a[i + 1])).save(f"{D}/prev_{a[i]}_{a[i + 1]}.jpg", quality=90)
    else:
        render()
