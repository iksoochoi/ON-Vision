"""Defect detection with the YOLO model of backend/models/defect_detection (a proof-of-concept model, see the README of
this folder). Shared by bench.py (timing) and run_folder.py (predict a folder of pictures).

Needs the packages of backend/predict/requirements.txt (torch + ultralytics), which the camera app itself does not
need: nothing in the camera app imports this folder, so it never slows the collection down."""

import time
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MODEL = ROOT / "backend" / "models" / "defect_detection" / "best.pt"
IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".bmp")
IMGSZ = 1280      # the size the model was trained at
CONF = 0.25

_models = {}


def pick_device(name="auto"):
    """'auto' -> Apple GPU (mps), else CUDA, else cpu. Anything else is passed on as it is ('cpu', 'mps', '0')."""
    if name != "auto":
        return name
    import torch
    if torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "0"
    return "cpu"


def load(model_path=None, device="auto"):
    """The model, loaded once per (path, device). Returns (model, device)."""
    from ultralytics import YOLO
    path = Path(model_path or DEFAULT_MODEL)
    if not path.exists():
        raise FileNotFoundError(f"model not found: {path}")
    device = pick_device(device)
    key = (str(path), device)
    if key not in _models:
        model = YOLO(str(path))
        model.to("cuda:0" if device == "0" else device)
        _models[key] = model
    return _models[key], device


def find_images(folder, limit=0):
    """The pictures under a folder (sub-folders too), sorted by path; limit 0 = all."""
    folder = Path(folder)
    if folder.is_file():
        return [folder]
    files = sorted(p for p in folder.rglob("*") if p.suffix.lower() in IMAGE_SUFFIXES)
    return files[:limit] if limit else files


def predict(model, device, image, imgsz=IMGSZ, conf=CONF):
    """One picture (a path or a BGR array). Returns {"detections": [...], "ms": ...}; the detections are
    {"class", "conf", "box": [x0, y0, x1, y1] in pixels of the picture}."""
    started = time.perf_counter()
    result = model.predict(image, imgsz=imgsz, conf=conf, device="cuda:0" if device == "0" else device, verbose=False)[0]
    ms = (time.perf_counter() - started) * 1000
    names = result.names
    dets = [{"class": names[int(c)], "conf": round(float(p), 3), "box": [round(float(v), 1) for v in b]}
            for b, c, p in zip(result.boxes.xyxy.cpu().numpy(), result.boxes.cls.cpu().numpy(), result.boxes.conf.cpu().numpy())]
    return {"detections": dets, "ms": round(ms, 1)}


def draw(image, detections, max_width=1600):
    """The picture with the boxes and 'class score' labels, scaled down to max_width (a 4000 px picture is not needed)."""
    out = image.copy()
    h, w = out.shape[:2]
    thick = max(2, round(w / 800))
    scale = max(0.6, w / 2000)
    for d in detections:
        x0, y0, x1, y1 = (int(v) for v in d["box"])
        cv2.rectangle(out, (x0, y0), (x1, y1), (0, 0, 255), thick)
        label = f"{d['class']} {d['conf']:.2f}"
        cv2.putText(out, label, (x0, max(y0 - 6, 20)), cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 255), max(1, thick - 1), cv2.LINE_AA)
    if w > max_width:
        out = cv2.resize(out, (max_width, round(h * max_width / w)), interpolation=cv2.INTER_AREA)
    return out
