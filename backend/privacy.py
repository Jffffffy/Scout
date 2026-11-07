"""Privacy pipeline: runs BEFORE any photo leaves our server.

1. Decode and downscale the photo (smaller = cheaper AI call).
2. Re-encode it, which strips EXIF metadata (GPS, device info).
3. Detect faces and blur them heavily.

If the face detector cannot load, we refuse the upload instead of sending an
unblurred photo to the AI model. Fail closed, never fail open.
"""

import os
from typing import Tuple

import cv2
import numpy as np

MAX_SIDE = 1280


class PrivacyError(Exception):
    pass


def _load_cascade(name: str):
    base = getattr(getattr(cv2, "data", None), "haarcascades", "")
    path = os.path.join(base, name)
    if not os.path.exists(path):
        return None
    cascade = cv2.CascadeClassifier(path)
    return None if cascade.empty() else cascade


_FRONTAL = _load_cascade("haarcascade_frontalface_default.xml")
_PROFILE = _load_cascade("haarcascade_profileface.xml")


def detector_ready() -> bool:
    return _FRONTAL is not None


def _decode(image_bytes: bytes) -> np.ndarray:
    arr = np.frombuffer(image_bytes, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise PrivacyError("Could not read this image. Please upload a JPG or PNG photo.")
    return img


def _downscale(img: np.ndarray) -> np.ndarray:
    h, w = img.shape[:2]
    scale = MAX_SIDE / max(h, w)
    if scale < 1:
        img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    return img


def _detect_faces(img: np.ndarray):
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    gray = cv2.equalizeHist(gray)
    boxes = []
    for cascade in (_FRONTAL, _PROFILE):
        if cascade is None:
            continue
        found = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(24, 24))
        boxes.extend([tuple(b) for b in found])
    # profile faces looking the other way: run on the mirrored image too
    if _PROFILE is not None:
        flipped = cv2.flip(gray, 1)
        w_img = gray.shape[1]
        for (x, y, w, h) in _PROFILE.detectMultiScale(flipped, 1.1, 5, minSize=(24, 24)):
            boxes.append((w_img - x - w, y, w, h))
    return boxes


def blur_faces(image_bytes: bytes) -> Tuple[bytes, int]:
    """Return (clean JPEG bytes with faces blurred and EXIF removed, number of faces)."""
    if not detector_ready():
        raise PrivacyError("Face blurring is unavailable on this server, so the upload was refused.")

    img = _downscale(_decode(image_bytes))
    boxes = _detect_faces(img)
    h_img, w_img = img.shape[:2]
    for (x, y, w, h) in boxes:
        # pad the box so hair and ears are covered too
        pad = int(0.25 * max(w, h))
        x0, y0 = max(0, x - pad), max(0, y - pad)
        x1, y1 = min(w_img, x + w + pad), min(h_img, y + h + pad)
        roi = img[y0:y1, x0:x1]
        if roi.size == 0:
            continue
        k = max(31, (max(x1 - x0, y1 - y0) // 2) | 1)  # odd kernel size
        img[y0:y1, x0:x1] = cv2.GaussianBlur(roi, (k, k), 0)

    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])
    if not ok:
        raise PrivacyError("Could not process this image.")
    return buf.tobytes(), len(boxes)
