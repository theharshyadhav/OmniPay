"""Decode the QR images written by tests/qr_check.js and compare with the payloads. Needs opencv-python."""
import sys, glob, cv2
d = sys.argv[1]; bad = 0
for f in sorted(glob.glob(f"{d}/q*.pgm")):
    got, _, _ = cv2.QRCodeDetector().detectAndDecode(cv2.imread(f, 0))
    want = open(f[:-4] + ".txt").read()
    print("ok  " if got == want else "FAIL", f, len(want)); bad += got != want
sys.exit(bad)
