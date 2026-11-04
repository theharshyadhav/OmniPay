"""Writes a .y4m video of a QR code for Chromium's fake camera (--use-file-for-fake-video-capture), so the real
camera scanning path can be tested headless. Usage: python tests/make_fake_camera.py out.y4m 'text in the code'"""
import sys

import cv2
import numpy as np

out, text = sys.argv[1], sys.argv[2]
W, H = 640, 480
frame = np.full((H, W), 235, np.uint8)                        # a light grey "table", the code in the middle
if text != "--blank":
    qr = cv2.QRCodeEncoder.create().encode(text)              # 1 px per module
    s = 420
    qr = cv2.resize(qr, (s, s), interpolation=cv2.INTER_NEAREST)
    y, x = (H - s) // 2, (W - s) // 2
    frame[y:y + s, x:x + s] = qr
yuv = cv2.cvtColor(cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR), cv2.COLOR_BGR2YUV_I420)
with open(out, "wb") as f:
    f.write(f"YUV4MPEG2 W{W} H{H} F10:1 Ip A1:1 C420jpeg\n".encode())
    for _ in range(20):
        f.write(b"FRAME\n" + yuv.tobytes())
