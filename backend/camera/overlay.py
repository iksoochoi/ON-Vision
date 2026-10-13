"""Drawing on the preview frames: labels, the detection overlay and the center cross."""

import cv2


def draw_label(frame, text):
    cv2.putText(frame, text, (12, frame.shape[0] - 14), cv2.FONT_HERSHEY_SIMPLEX, 0.8,
                (255, 255, 255), 2)


def draw_overlay(frame, dets, auto_enabled, note=None):
    h, w = frame.shape[:2]
    cv2.line(frame, (w // 2, 0), (w // 2, h), (255, 255, 0), 1)
    cv2.line(frame, (0, h // 2), (w, h // 2), (255, 255, 0), 1)
    for d in dets:
        color = (0, 255, 0) if d.target else (160, 160, 160)
        p1, p2 = (int(d.xmin * w), int(d.ymin * h)), (int(d.xmax * w), int(d.ymax * h))
        cv2.rectangle(frame, p1, p2, color, 3 if d.target else 1)
        cv2.circle(frame, (int(d.cx * w), int(d.cy * h)), 5, color, -1)
        cv2.putText(frame, f"{d.label} {d.conf:.2f} {d.area:.0%}", (p1[0], max(18, p1[1] - 6)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)
    badge = "AUTO ON" if auto_enabled else "AUTO OFF"
    cv2.putText(frame, badge, (12, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.8,
                (0, 255, 0) if auto_enabled else (0, 0, 255), 2)
    if note:
        cv2.putText(frame, note, (12, 62), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 165, 255), 2)
