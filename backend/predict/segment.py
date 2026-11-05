"""Shoe segmentation with the YOLO model of backend/models/shoe_segmentation (one class, "shoe"; trained on the five cameras'
pictures, see train/shoe_segmentation on the PC where it was made). Used by the worker before the defect model: the picture the
defect model gets is the shoe on a flat grey background (backend/storage/cutout.py).

Needs the packages of backend/predict/requirements.txt, like engine.py."""

import time

import numpy as np

from ..storage import cutout as cut
from . import engine


def predict(model, device, image, imgsz, conf):
    """The shoe of one picture (BGR array): {"found": bool, "polygon": [[x, y] ...] as fractions (the most certain shoe),
    "conf", "count" (shoes found: another shoe far in the background counts too), "area", "box", "ms"}."""
    started = time.perf_counter()
    h, w = image.shape[:2]
    result = model.predict(image, imgsz=imgsz, conf=conf, device="cuda:0" if device == "0" else device, verbose=False)[0]
    out = {"found": False, "count": 0, "ms": 0.0}
    if result.masks is not None and len(result.masks.xy):
        confs = result.boxes.conf.cpu().numpy()
        best = int(confs.argmax())
        polygon = cut.polygon_from_pixels(result.masks.xy[best], w, h)
        out["count"] = int(len(confs))
        if polygon:
            out.update(found=True, polygon=polygon, conf=round(float(confs[best]), 3), area=round(cut.area_of(polygon), 4),
                       box=[round(v, 4) for v in cut.box_of(polygon)])
    out["ms"] = round((time.perf_counter() - started) * 1000, 1)
    return out


def load(model_path, device="auto"):
    """The segmentation model, loaded once (engine.load does it). Returns (model, device)."""
    return engine.load(model_path, device)


def load_warm(model_path, device, imgsz, conf, log):
    """load(), with a first prediction (it is slow). A device that cannot do this model: the processor does it."""
    model, used = load(model_path, device)
    blank = np.zeros((64, 64, 3), np.uint8)
    try:
        predict(model, used, blank, imgsz, conf)
    except Exception as e:
        log(f"segmentation on {used} failed ({type(e).__name__}: {e}): using the cpu for it")
        model, used = load(model_path, "cpu")
        predict(model, used, blank, imgsz, conf)
    return model, used
