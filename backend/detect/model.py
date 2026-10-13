"""Detections from the on-camera YOLO network (ON_DETECTOR=model) as Det objects."""

from ..core import config as C
from .trigger import Det


def msg_ts(msg):
    return msg.getTimestamp().total_seconds()


def to_dets(msg, classes):
    """Convert an ImgDetections message to Det objects (normalized, clamped).

    A detection is a capture target when its class is wanted (ON_TARGET_CLASSES, "*" = any) and
    its bbox covers at least ON_MODEL_MIN_AREA of the frame."""
    dets = []
    for d in msg.detections:
        label = int(d.label)
        name = classes[label] if classes and 0 <= label < len(classes) else str(label)
        det = Det(
            xmin=min(max(d.xmin, 0.0), 1.0), ymin=min(max(d.ymin, 0.0), 1.0),
            xmax=min(max(d.xmax, 0.0), 1.0), ymax=min(max(d.ymax, 0.0), 1.0),
            conf=float(d.confidence), label=name,
        )
        wanted = C.ALL_CLASSES or name in C.TARGET_CLASSES or str(label) in C.TARGET_CLASSES
        det.target = wanted and det.area >= C.MODEL_MIN_AREA
        dets.append(det)
    return dets


def poll_detections(det_queue, classes):
    """Drain the YOLO queue (ON_DETECTOR=model) -> [(timestamp_sec, [Det, ...])], oldest first."""
    out = []
    msg = det_queue.tryGet()
    while msg is not None:
        out.append((msg_ts(msg), to_dets(msg, classes)))
        msg = det_queue.tryGet()
    return out
