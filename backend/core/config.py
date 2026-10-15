"""Static configuration: paths, ports, detection/trigger tuning, label categories, camera parameter specs.

Values come from ON_* settings in <project>/.env (or real environment variables, which win).
Backend reads all of them; the frontend only receives the ON_UI_* ones via /api/state."""

import os
import re
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[2]


def _load_dotenv(path):
    """Minimal .env parser: KEY=VALUE lines, '#' comments, optional quotes / 'export '.
    Never overrides variables already set in the real environment."""
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        key, _, value = line.partition("=")
        value = value.strip()
        if value[:1] in ("'", '"') and value.count(value[0]) >= 2:
            value = value[1:value.index(value[0], 1)]
        else:
            value = value.split(" #", 1)[0].strip()
        os.environ.setdefault(key.strip(), value)


_load_dotenv(PROJECT_DIR / ".env")

def _env(name, default, cast=str):
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return cast(raw)
    except ValueError:
        print(f"[config] invalid {name}={raw!r} - using default {default!r}")
        return default


def _path(name, default):
    """Path setting: '~' is expanded, relative paths are relative to the project folder."""
    p = Path(_env(name, default)).expanduser()
    return p if p.is_absolute() else PROJECT_DIR / p


FRONTEND_DIR = PROJECT_DIR / "frontend"
# ON_CAPTURES_DIR: where images/labels are stored.
CAPTURES_DIR = _path("ON_CAPTURES_DIR", "captures")
THUMBS_DIR = CAPTURES_DIR / ".thumbs"
THUMBS_DIR.mkdir(parents=True, exist_ok=True)
SETS_DIR = CAPTURES_DIR / ".sets"   # one JSON per capture set (what the app lists and labels)
SETS_DIR.mkdir(parents=True, exist_ok=True)

# ON_HOST: 0.0.0.0 = reachable from other devices on the network, 127.0.0.1 = this machine only.
HOST = _env("ON_HOST", "0.0.0.0")
CONTROL_PORT = _env("ON_PORT", 8090, int)


# ---- detector selection ----------------------------------------------------
# "background": foreground vs. an empty-belt reference image, no AI model needed.
# "model": YOLO on the camera (see the detection model section below).
DETECTOR = _env("ON_DETECTOR", "background")
if DETECTOR not in ("background", "model"):
    print(f"[config] invalid ON_DETECTOR={DETECTOR!r} - using 'background'")
    DETECTOR = "background"

# Background-subtraction tuning. Works on a BG_SIZE copy of the frame (same 4:3 field of view).
BG_SIZE = (640, 480)
BG_THRESH = _env("ON_BG_THRESH", 30, int)          # pixel difference (0..255) that counts as foreground
BG_MIN_AREA = _env("ON_BG_MIN_AREA", 0.02, float)  # smallest object, as a fraction of the frame
BG_CLOSE = _env("ON_BG_CLOSE", 0.04, float)        # gap-closing kernel, as a fraction of frame width
BG_FRAMES = _env("ON_BG_FRAMES", 8, int)           # frames averaged when capturing the background
BACKGROUND_PATH = CAPTURES_DIR / ".background.jpg"

# ---- detection model -------------------------------------------------------
# ON_MODEL_ARCHIVE: path to a custom NNArchive (.tar.xz). If empty, the model is
# downloaded from the Luxonis model zoo by name (needs internet on first run).
MODEL_ARCHIVE = str(_path("ON_MODEL_ARCHIVE", "")) if _env("ON_MODEL_ARCHIVE", "") else ""
MODEL_ZOO_NAME = _env("ON_MODEL_ZOO", "yolov6-nano") 
# Class names (or numeric ids) that start a capture. The COCO zoo model has no
# "shoe" class, so "person" is a stand-in for desk tests; set it to your shoe
# class once a custom model is in place.
TARGET_CLASSES = [c.strip() for c in _env("ON_TARGET_CLASSES", "person").split(",") if c.strip()]
# "*" (or "all") in ON_TARGET_CLASSES: every class the model detects counts.
ALL_CLASSES = any(c.lower() in ("*", "all") for c in TARGET_CLASSES)
DETECT_CONF = _env("ON_DETECT_CONF", 0.5, float)
# A detection only counts when its bbox covers at least this part of the reference camera's frame
# (0..1, e.g. 0.6 = 60%). 0 = any size.
MODEL_MIN_AREA = max(0.0, min(1.0, _env("ON_MODEL_MIN_AREA", 0.6, float)))

NN_FPS = _env("ON_NN_FPS", 15, float)
FULL_FPS = _env("ON_FULL_FPS", 10, float)          # capture-size frames per second, trigger camera
PREVIEW_SIZE = (1280, 960)  # 4:3, same field of view as the full-res and NN streams

# ---- cameras ---------------------------------------------------------------
# ON_CAMERAS="top:<deviceId>,front:<deviceId>,..." assigns a role to each OAK (see backend/cli/list_cameras.py).
# Empty = one camera, role "top", first device found. The trigger camera detects the shoe; when
# it fires, every camera contributes the frame closest in time to that moment (one "set").
AUTO_ROLES = ("top", "front", "back", "left", "right")


def _parse_cameras(raw):
    if raw.strip().lower() == "auto":   # up to 5 cameras: every free OAK takes the next role
        return [(role, None) for role in AUTO_ROLES]
    cams, seen = [], set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        role, _, device = part.partition(":")
        role, device = role.strip().lower(), device.strip()
        if not re.fullmatch(r"[a-z0-9_]{1,16}", role) or role in seen:
            print(f"[config] ignoring camera entry {part!r} (bad or duplicate role)")
            continue
        seen.add(role)
        cams.append((role, device or None))
    return cams


def _parse_size(raw):
    m = re.fullmatch(r"\s*(\d{3,5})\s*[xX]\s*(\d{3,5})\s*", raw or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


CAMERAS = _parse_cameras(_env("ON_CAMERAS", "")) or [("top", None)]
_roles = [r for r, _ in CAMERAS]
TRIGGER_CAMERA = _env("ON_TRIGGER_CAMERA", "top")
if TRIGGER_CAMERA not in _roles:
    TRIGGER_CAMERA = _roles[0]
CAMERAS.sort(key=lambda c: c[0] != TRIGGER_CAMERA)  # trigger camera first
MULTI_CAMERA = len(CAMERAS) > 1
AUTO_CAMERAS = _env("ON_CAMERAS", "").strip().lower() == "auto"
if MULTI_CAMERA and not AUTO_CAMERAS and any(device is None for _, device in CAMERAS):
    print("[config] ON_CAMERAS: some roles have no device id - they take whichever OAK is free first")

SIDE_FPS = _env("ON_SIDE_FPS", 5, float)            # capture-size fps of the non-trigger cameras
SIDE_PREVIEW_SIZE = (640, 480)
SIDE_PREVIEW_FPS = _env("ON_SIDE_PREVIEW_FPS", 8, float)
RING_SEC = _env("ON_RING_SEC", 2.5, float)          # how long each camera keeps frames for matching
# Pick the sharpest frame within +-this many seconds of each shot instead of the one nearest in time.
# About one frame period (0.12 s at 9 fps). 0 = off: always the nearest frame.
PICK_WINDOW_SEC = max(0.0, _env("ON_PICK_WINDOW_SEC", 0.12, float))
MAX_SKEW_SEC = _env("ON_MAX_SKEW_SEC", 0.5, float)  # a view further from the trigger moment is dropped
# Saved image size. Empty = sensor full resolution (single camera) or half of it (several cameras,
# to stay inside the USB bandwidth). Example: 4056x3040
CAPTURE_SIZE = _parse_size(_env("ON_CAPTURE_SIZE", ""))
if CAPTURE_SIZE is None and MULTI_CAMERA:
    CAPTURE_SIZE = (2028, 1520)

# Resolutions offered in the UI. Changing one restarts that camera. "full" = sensor size.
# Each camera's choices are remembered in <captures>/.settings.json and override the defaults above.
SETTINGS_PATH = CAPTURES_DIR / ".settings.json"


def _fmt_size(size):
    return "full" if size is None else f"{size[0]}x{size[1]}"


PREVIEW_CHOICES = ["640x480", "960x720", "1280x960", "1920x1440"]
CAPTURE_CHOICES = ["full", "3040x2280", "2028x1520", "1520x1140", "1014x760"]
for _choices, _value in ((PREVIEW_CHOICES, _fmt_size(PREVIEW_SIZE)),
                         (PREVIEW_CHOICES, _fmt_size(SIDE_PREVIEW_SIZE)),
                         (CAPTURE_CHOICES, _fmt_size(CAPTURE_SIZE))):
    if _value not in _choices:
        _choices.append(_value)

# ---- center trigger --------------------------------------------------------
# Distances are in normalized frame units (0..1).
TRIGGER_AXIS = _env("ON_TRIGGER_AXIS", "xy")     # "x", "y" or "xy": which offset from center counts
CENTER_TOL = _env("ON_CENTER_TOL", 0.30, float)  # best sample must be at least this close to center
AWAY_HYST = _env("ON_AWAY_HYST", 0.03, float)    # moved this much past the minimum => crossed center
LOST_SEC = _env("ON_LOST_SEC", 0.4, float)       # track lost after this long without a match
HOLD_SEC = _env("ON_HOLD_SEC", 0.8, float)       # fire if the best sample hasn't improved for this long
MIN_OBS =_env("ON_MIN_OBS", 3, int)             # ignore flickering detections seen fewer times
EDGE_MARGIN = _env("ON_EDGE_MARGIN", 0, float)  # 0.01 # bbox touching the border => shoe is cut off
MATCH_GATE = _env("ON_MATCH_GATE", 0.35, float)  # max center jump between frames for the same track
# Shots per shoe: 3 = before the center, at the center, after the center (the "before"/"after" shots
# are taken when the shoe is ON_SHOT_SPREAD away from the center); 1 = only the center shot.
SHOTS = 3 if _env("ON_SHOTS", 3, int) >= 3 else 1
SHOT_SPREAD = _env("ON_SHOT_SPREAD", 0.15, float)
# How the "before" / "after" shots are chosen (ON_SHOTS=3):
#   adjacent: the reference camera's frames right before / after the center frame. A frame whose
#             sharpness is below ON_ADJACENT_SHARP_RATIO x the center frame's is passed over for the
#             next one further out, up to ON_ADJACENT_MAX_STEP frames (none sharp enough: the sharpest).
#   spread:   the moments the shoe was ON_SHOT_SPREAD away from the center (detection based).
SHOT_MODE = _env("ON_SHOT_MODE", "adjacent")
if SHOT_MODE not in ("adjacent", "spread"):
    print(f"[config] invalid ON_SHOT_MODE={SHOT_MODE!r} - using 'adjacent'")
    SHOT_MODE = "adjacent"
ADJACENT = SHOTS >= 3 and SHOT_MODE == "adjacent"
ADJACENT_SHARP_RATIO = max(0.0, _env("ON_ADJACENT_SHARP_RATIO", 0.8, float))
ADJACENT_MAX_STEP = max(1, _env("ON_ADJACENT_MAX_STEP", 3, int))

# ---- QR codes (on the side of the shoe) ------------------------------------
QR_ENABLED = _env("ON_QR", 1, int) != 0
QR_SCAN_HZ = _env("ON_QR_SCAN_HZ", 5.0, float)  # live scans per second of EACH scanned camera
# Cameras searched for the QR code (live and on the saved pictures), e.g. "left,right". Empty = all.
QR_CAMERAS = [r.strip().lower() for r in _env("ON_QR_CAMERAS", "").split(",") if r.strip()]
# The shoe's QR value goes in front of the names of its saved files. Where it is looked for, in this order:
# 1. the saved pictures of the shoe pass   2. what the live scanner read while the shoe passed
# 3. the saved pictures again, searched in enlarged tiles (finds small codes)   4. what the live scanner
# reads in the next seconds (the writer waits for it). Saving takes that much longer, never more.
QR_LIVE_WINDOW_SEC = max(0.0, _env("ON_QR_LIVE_WINDOW_SEC", 3.0, float))  # live reads count from this long before the first shot
QR_WAIT_SEC = max(0.0, _env("ON_QR_WAIT_SEC", 4.0, float))                # ... until this long after the last shot (auto captures)
QR_ZOOM = _env("ON_QR_ZOOM", 1, int) != 0                                 # step 3
# Which shoe a code belongs to when shoes pass close together (automatic captures only). At the moment of the
# center shot the shoe being saved is in the middle of the picture, its neighbours are further out. A code counts
# for this shoe with a weight that is 1 in the middle of the picture and falls to 0 at this distance from it
# (fraction of the picture width); a code further out is a neighbour's and is ignored. 0 = off (every code counts).
QR_CENTER_BAND = min(0.5, max(0.0, _env("ON_QR_CENTER_BAND", 0.4, float)))
# With the band on, live reads count only from frames this close (seconds) to the center shot
QR_MATCH_SEC = max(0.1, _env("ON_QR_MATCH_SEC", 1.0, float))


def _parse_roi(text):
    """'x,y,w,h' as fractions of the picture (the QR search area) -> (x, y, w, h); ValueError if not valid."""
    x, y, w, h = (float(v) for v in text.split(","))
    if not (0 <= x < 1 and 0 <= y < 1 and w > 0 and h > 0 and x + w <= 1.0001 and y + h <= 1.0001):
        raise ValueError(text)
    return (x, y, min(w, 1 - x), min(h, 1 - y))


# Where in the picture the QR code is looked for, as x,y,w,h fractions of the (zoomed) view, e.g. 0,0.45,1,0.4 =
# the band across the picture at 45%..85% of its height. A small area is searched faster and enlarged,
# which finds small codes. Live scans look only there; the saved pictures are searched there first and
# then as a whole. Empty = the whole picture.
QR_ROI = _env("ON_QR_ROI", None, _parse_roi)

# ---- capture log -----------------------------------------------------------
# One line per capture (automatic or manual) in <ON_LOG_DIR>/capture.log: see backend/storage/capture_log.py.
LOG_DIR = _path("ON_LOG_DIR", "logs")
CAPTURE_LOG = _env("ON_CAPTURE_LOG", 1, int) != 0
CAPTURE_LOG_MAX_MB = max(1, _env("ON_CAPTURE_LOG_MAX_MB", 10, int))   # per file, 5 older ones are kept

# ---- logs of the app and of the data collection (backend/health/) ------------
# Everything the app prints (also what native code such as depthai writes) goes to <ON_LOG_DIR>/app.log with a
# time in front of every line; problems of the data collection (camera offline / stalled, captures with a
# missing picture or lost, disk full, crashes) go to <ON_LOG_DIR>/collect.log, one line each.
APP_LOG = _env("ON_APP_LOG", 1, int) != 0
APP_LOG_MAX_MB = max(1, _env("ON_APP_LOG_MAX_MB", 20, int))           # per file, 5 older ones are kept
COLLECT_LOG_MAX_MB = max(1, _env("ON_COLLECT_LOG_MAX_MB", 10, int))   # per file, 5 older ones are kept
# The monitor looks at the cameras every HEALTH_INTERVAL_SEC seconds. An ONLINE camera that has sent no new
# full-size picture for HEALTH_STALL_SEC seconds is "stalled"; a camera that stays offline for
# HEALTH_OFFLINE_SEC seconds is "down". HEALTH_STATUS_SEC: one status line per camera set in collect.log
# every so often (fps, age of the last picture, USB speed), so the time before a problem can be read (0 = off).
HEALTH_INTERVAL_SEC = max(0.5, _env("ON_HEALTH_INTERVAL_SEC", 2.0, float))
HEALTH_STALL_SEC = max(1.0, _env("ON_HEALTH_STALL_SEC", 5.0, float))
HEALTH_OFFLINE_SEC = max(5.0, _env("ON_HEALTH_OFFLINE_SEC", 60.0, float))
HEALTH_STATUS_SEC = max(0.0, _env("ON_HEALTH_STATUS_SEC", 60.0, float))

# ---- alerts for failures of the data collection: a Google Chat webhook (backend/health/alerts.py) ------------
# The webhook URL is a secret (it carries a key and a token): it lives in .env, which is not in git, and is
# never written to a log. How to get one: docs/logs-and-alerts.md.
ALERT = _env("ON_ALERT", 0, int) != 0
ALERT_WEBHOOK_URL = _env("ON_ALERT_WEBHOOK_URL", "").strip()
ALERT_PREFIX = _env("ON_ALERT_PREFIX", "[ON-Vision]")                       # in front of the first line of a message
ALERT_COOLDOWN_SEC = max(0.0, _env("ON_ALERT_COOLDOWN_SEC", 1800.0, float))   # the same problem is sent once per this time
ALERT_DIGEST_SEC = max(0.0, _env("ON_ALERT_DIGEST_SEC", 60.0, float))         # problems within this time share one message

# ---- saved samples ---------------------------------------------------------
# Automatic capture stops when this part (%) of the disk's capacity is used (see backend/capture/disk_guard.py).
# 0 = never stop.
DISK_STOP_PERCENT = max(0.0, min(100.0, _env("ON_DISK_STOP_PERCENT", 90.0, float)))
# The automatic capture is ON when the app starts (1), or OFF until it is switched on on the live page (0).
# It stays OFF when the disk is already past ON_DISK_STOP_PERCENT.
AUTO_CAPTURE = _env("ON_AUTO_CAPTURE", 1, int) != 0
JPEG_QUALITY = _env("ON_JPEG_QUALITY", 95, int)
THUMB_WIDTH = _env("ON_THUMB_WIDTH", 480, int)

# ---- dataset export (YOLO format) ------------------------------------------
EXPORTS_DIR = _path("ON_EXPORTS_DIR", "exports")
EXPORT_MAX_SIDE = _env("ON_EXPORT_MAX_SIDE", 1280, int)  # downscale exported images; 0 = keep original

# ---- frontend (sent to the browser via /api/state; keep secrets out of ON_UI_*) ----
_FILTERS = ("unlabeled", "ok", "ng", "reject", "all")
UI = {
    "title": _env("ON_UI_TITLE", "OAK"),
    "recent_count": max(1, _env("ON_UI_RECENT_COUNT", 6, int)),
    "default_filter": _env("ON_UI_DEFAULT_FILTER", "unlabeled"),
}
if UI["default_filter"] not in _FILTERS:
    UI["default_filter"] = "unlabeled"

# Defect categories offered when labeling a sample as NG.
CATEGORIES = {
    "Spray": [
        "Toe&Heel bonding margin_1",
        "Medial&lateral&side bonding margin_2",
        "Inner medial_lateral bonding margin_3",
        "Overspray_4",
        "tail_5",
        "hole_mask_6",
        "helix_7",
        "thin_thick spray_8",
        "contam_9",
        "bubble_10",
        "bur_11",
    ],
    "Print": [
        "over printing_1",
        "smudging_3",
        "poor printing_4",
    ],
    "BU": [
        "contam_01",
        "combi_side_02_01",
        "combi_inner_02_02",
        "combi_bottom_02_03",
        "lug_03",
    ],
}

# exposure_ms / iso / ae_limit_ms / lens_position: 0 means auto (no limit, continuous autofocus).
# ae_limit_ms caps the automatic exposure (the camera raises ISO instead); lens_position 1..255
# locks the focus. ON_AE_LIMIT_MS is only the starting value: the live page remembers its own.
CAM_PARAMS = {
    "exposure_ms":    {"type": "int",  "min": 0,    "max": 33,    "default": 0},
    "iso":            {"type": "int",  "min": 0,    "max": 1600,  "default": 0},
    "ae_limit_ms":    {"type": "int",  "min": 0,    "max": 33,
                       "default": max(0, min(33, _env("ON_AE_LIMIT_MS", 0, int)))},
    "wb":             {"type": "int",  "min": 0,    "max": 12000, "default": 0},
    "lens_position":  {"type": "int",  "min": 0,    "max": 255,   "default": 0},
    "sharpness":      {"type": "int",  "min": 0,    "max": 4,     "default": 1},
    "luma_denoise":   {"type": "int",  "min": 0,    "max": 4,     "default": 1},
    "chroma_denoise": {"type": "int",  "min": 0,    "max": 4,     "default": 1},
    "ae_comp":        {"type": "int",  "min": -9,   "max": 9,     "default": 0},
    "brightness":     {"type": "int",  "min": -10,  "max": 10,    "default": 0},
    "contrast":       {"type": "int",  "min": -10,  "max": 10,    "default": 0},
    "hw_saturation":  {"type": "int",  "min": -10,  "max": 10,    "default": 0},
    "antibanding":    {"type": "enum", "values": ["OFF", "MAINS_50_HZ", "MAINS_60_HZ", "AUTO"],
                       "default": "AUTO"},
}

DEFAULT_EXPOSURE_US = 20000
DEFAULT_ISO = 400
