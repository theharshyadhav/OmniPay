import sys, math, cv2, numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter
out = sys.argv[1]
W, H = 960, 720
F = "/usr/share/fonts/opentype/inter/"
AD = "/usr/share/texmf/fonts/opentype/public/tex-gyre/texgyreadventor-bold.otf"
text = "upi://pay?pa=ravi@omni&pn=Ravi%27s%20Chai%20Stall&am=250&cu=INR&tn=Masala%20chai%20x5"
# background: warm wooden counter, out of focus
yy, xx = np.mgrid[0:H*2, 0:W*2]
wood = 120 + 18*np.sin(xx/37.0 + 3*np.sin(yy/90.0)) + 10*np.sin(yy/11.0 + xx/300.0)
bg = np.stack([wood*1.05+20, wood*0.72+5, wood*0.45], -1).clip(0, 255).astype(np.uint8)
bg = Image.fromarray(bg).filter(ImageFilter.GaussianBlur(6))
# a warm light glow
glow = Image.new("L", bg.size, 0); ImageDraw.Draw(glow).ellipse((300, 100, 1700, 1200), fill=90); glow = glow.filter(ImageFilter.GaussianBlur(160))
bg = Image.composite(Image.new("RGB", bg.size, (255, 214, 160)), bg, glow)
# the standee card
cw, ch = 560, 720
card = Image.new("RGBA", (cw, ch), (0, 0, 0, 0)); d = ImageDraw.Draw(card)
d.rounded_rectangle((0, 0, cw-1, ch-1), 36, fill=(255, 251, 243))
d.rounded_rectangle((0, 0, cw-1, 130), 36, fill=(196, 74, 40)); d.rectangle((0, 90, cw-1, 130), fill=(196, 74, 40))
d.text((cw/2, 66), "Ravi's Chai Stall", font=ImageFont.truetype(AD, 44), fill="white", anchor="mm")
q = cv2.QRCodeEncoder.create().encode(text); n = q.shape[0]
qs = 400; qimg = Image.fromarray(q).resize((qs, qs), Image.NEAREST).convert("RGB")
card.paste(qimg, ((cw-qs)//2, 160))
d.text((cw/2, 612), "Scan & pay with any UPI app", font=ImageFont.truetype(F+"Inter-SemiBold.otf", 28), fill=(60, 40, 30), anchor="mm")
d.text((cw/2, 656), "Masala chai  ₹50", font=ImageFont.truetype(F+"Inter-Regular.otf", 26), fill=(120, 90, 70), anchor="mm")
card = card.resize((int(cw*0.78), int(ch*0.78)), Image.LANCZOS); cw, ch = card.size
shadow = Image.new("RGBA", (cw+120, ch+120), (0, 0, 0, 0)); ImageDraw.Draw(shadow).rounded_rectangle((60, 75, cw+60, ch+75), 40, fill=(30, 15, 5, 150))
shadow = shadow.filter(ImageFilter.GaussianBlur(22))
frames = []
N = 60
for i in range(N):
    t = i / N * 2 * math.pi
    dx, dy, rot = 10*math.sin(t), 7*math.sin(2*t+1), -4 + 1.2*math.sin(t+0.5)   # gentle hand shake
    canvas = bg.crop((480+int(dx*0.6), 360+int(dy*0.6), 480+W+int(dx*0.6), 360+H+int(dy*0.6))).copy()
    c = card.rotate(rot, resample=Image.BICUBIC, expand=True); s = shadow.rotate(rot, resample=Image.BICUBIC, expand=True)
    cx, cy = W//2 + int(dx), H//2 + int(dy) + 10
    canvas.paste(s, (cx - s.width//2, cy - s.height//2 + 8), s)
    canvas.paste(c, (cx - c.width//2, cy - c.height//2), c)
    # slight scale: card is ~ the square crop
    frames.append(np.array(canvas))
with open(out, "wb") as f:
    f.write(f"YUV4MPEG2 W{W} H{H} F30:1 Ip A1:1 C420jpeg\n".encode())
    for fr in frames:
        f.write(b"FRAME\n" + cv2.cvtColor(fr, cv2.COLOR_RGB2YUV_I420).tobytes())
Image.fromarray(frames[0]).save(out.replace(".y4m", ".jpg"), quality=90)
