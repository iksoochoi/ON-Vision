# 프로젝트 설명

컨베이어 위를 지나가는 신발(러닝화)를 자동으로 촬영하여 데이터 수집 및 불량 검사를 수행합니다.

## 1. 프로젝트 구조

```
bin/        setup_mac.sh (처음 한 번)  run.sh (수동 실행)  install_launchd.sh (자동 시작 서비스)  capture_backup.sh (하루치 통계+zip 백업 후 삭제)
docs/       운영·사용 문서 (아래 목록)
frontend/   page (HTML), css, js
backend/
  main.py     실행 진입점 (python -m backend.main)
  core/       config (.env 읽기), state, events, rotation (회전·줌 크롭), zoom
  detect/     trigger (중앙 통과 판정), background (배경차분), model (YOLO 결과)
  qr/         decode, enhance (못 읽은 QR 다시 읽기), hub (최근 결과), scanner (카메라별 실시간 스캔)
  storage/    paths, meta, shoe_id, shoes (세트 색인: 메모리에서 개수·목록·통계), store (세트 저장·조회), capture_log, export
  capture/    sharpness, disk_guard, session (세트 만들기 + 트리거 세션)
  camera/     slot, registry, device, controls, overlay, pipeline, worker  (depthai는 여기서만 import)
  api/        server (라우팅) + responses / state / sample / export / stream routes, feeds (SSE로 화면에 밀어 주는 것), state_payload
  health/     app.log (앱이 찍는 모든 것), collect.log (수집 문제), 카메라 감시(monitor), Google Chat 웹훅 알림(alerts)
  cli/        list_cameras, export_dataset, test_alert, fps_probe, qr_bench, capture_backup  (python -m backend.cli.<이름>)
  models/     모델 파일 (shoe_detection/: 신발 감지 모델, OAK 카메라 안에서 실행)
.env        설정 (항목마다 설명과 가능한 값이 적혀 있음)
captures/ logs/ exports/   실행 중 생기는 데이터
```

## 2. 아키텍처 (Mac mini)

```
OAK-1 Lite x5 (USB3) ──► camera/worker (카메라마다 스레드 1개, depthai)
   top = 기준 카메라                │ 전체 크기 프레임 → 카메라별 링 버퍼 (최근 2.5초)
                                   │ 프리뷰 프레임   → MJPEG 화면
        기준 카메라의 프리뷰/NN ──► detect (배경차분 또는 YOLO) ──► trigger (중앙 통과 판정)
                                                                  │ 발동
                                         capture/session ◄────────┘
                      모든 카메라에서 같은 순간의 프레임을 고름 (가장 선명한 프레임)
                                                │ 전·중·후 컷 묶음
                                      storage/store (쓰기 스레드 3개)
                      QR 값 찾기 (사진 → 실시간 스캔 → 확대 → 대기) → 파일명 앞에 붙임
                                                │
                       captures/<카메라>/<id>.jpg|json, captures/.sets/<id>.json, logs/capture.log
                                                │
                              api/server ──► 브라우저 (실시간 / 이미지 조회 / QR / 라벨링)

qr/scanner : 옆 카메라(left, right)의 최신 프레임을 계속 스캔 (신발이 지나가기 전에도 읽음)
capture/disk_guard : 디스크 사용률 90%에 도달하면 자동 캡처 중지
```

- **기준 카메라(`top`)**가 신발을 감지하고 캡처를 시작합니다. 발동하면 5대 모두 같은 순간의 사진을 한 장씩 저장해 한 세트가 됩니다.
- 신발 한 번이 지나가면 **카메라마다 가장 좋은 프레임 한 장**(5대 = 사진 5장)이 한 묶음으로 저장됩니다(기본 `ON_PICK_MODE=best`: 신발이 명확히 있고 가장 선명한 프레임, QR 카메라는 QR이 읽히는 프레임 우선). `ON_PICK_MODE=classic`이면 전·중·후 컷 3개(사진 15장)입니다. 자세히는 [capture-guide.md](docs/capture-guide.md).
- Mac에서는 두 개의 LaunchAgent가 돕니다: 앱(`com.on.labeling`, 죽으면 10초 뒤 재시작)과 로그인 시 브라우저로 화면을 한 번 여는 서비스(`com.on.labeling.ui`). 로그인된 세션에서만 실행되므로 자동 로그인을 켜 두세요.
- Docker는 쓰지 않습니다. macOS의 Docker는 컨테이너에 USB 장치를 연결하지 못합니다.

## 3. 실행 방법

### Mac mini (운영)
```bash
brew install python@3.12             # 이미 있으면 생략 (3.9~3.14 지원)
bash bin/setup_mac.sh                # 처음 한 번: .venv 생성 + 패키지 설치 + OAK 확인 + 실행 권한 부여
./bin/run.sh                         # 수동 실행 (caffeinate로 절전 방지)
./bin/install_launchd.sh install     # 자동 시작 서비스 설치 (로그인 시 시작, 죽으면 재시작)
```
서비스 명령: `./bin/install_launchd.sh install | uninstall | restart | status | logs | ui`

- 프로젝트 폴더는 `~/Desktop`, `~/Documents`, `~/Downloads` **밖**에 두세요. 안에 있으면 서비스가 시작되지 못합니다.
- 서비스가 켜져 있을 때 `run.sh`를 직접 실행하면 포트를 서로 뺏습니다. 먼저 `uninstall` 하세요.

### 접속
브라우저에서 `http://localhost:8090` (실시간), `/browse` (이미지 조회), `/qr` (QR). 라벨링 화면(`/label`)은 메뉴에서 숨겼습니다(주소로는 열립니다). 설정은 `.env`이고 바꾼 뒤에는 앱을 다시 시작하세요.

### 보조 명령
```bash
.venv/bin/python -m backend.cli.list_cameras     # 연결된 OAK의 장치 id와 .env에 붙일 ON_CAMERAS 줄
.venv/bin/python -m backend.cli.export_dataset   # YOLO 데이터셋 내보내기 (옵션: --reviewed-only --val-ratio --no-negatives --max-side)
.venv/bin/python -m backend.cli.qr_bench <사진 폴더>   # 저장된 사진으로 QR 읽기 방식(기본/추가 시도/zxing) 비교
.venv/bin/python -m backend.cli.fps_probe --sweep   # 카메라 1대가 요청한 fps대로 나오는지 측정 (앱/서비스를 먼저 멈출 것)
```

### 신발 감지 모델 (기준 카메라에서 신발을 찾는 모델)
`backend/models/shoe_detection/shoe_detection.rvc2.tar.xz`는 이 현장의 신발 사진으로 학습한 OAK(RVC2)용 모델입니다(클래스 `shoe`, 입력 512x384). 저장소에 들어 있고, 쓰려면 `.env`(저장소에 없음, 장비마다 따로 둠)에 아래를 넣고 앱을 다시 시작하세요.
```
ON_DETECTOR=model
ON_MODEL_ARCHIVE=backend/models/shoe_detection/shoe_detection.rvc2.tar.xz
ON_TARGET_CLASSES=shoe
```
`ON_MODEL_ARCHIVE`를 비우면 예전 방식(zoo의 COCO 모델 `ON_MODEL_ZOO`)으로 돌아갑니다. 신발이 있는데 잡히지 않으면 `ON_DETECT_CONF`를 낮춰 보세요.

## 문서 (`docs/`)
| 파일 | 내용 |
|---|---|
| [operations.md](docs/operations.md) | 맥으로 옮길 때, 자동 시작 서비스, 운영 팁 |
| [capture-guide.md](docs/capture-guide.md) | 시험 순서, 감지 모드, 튜닝, 선명한 사진, 디스크 보호 |
| [ui-guide.md](docs/ui-guide.md) | 실시간 화면, 이미지 조회 |
| [storage-and-qr.md](docs/storage-and-qr.md) | 저장 구조, 신발 ID(QR) 찾는 순서 |
| [capture-log.md](docs/capture-log.md) | `logs/capture.log` 형식과 읽는 법 |
| [logs/README.md](logs/README.md) | 로그 파일마다 어떤 정보가 남는지 |
| [capture-backup.md](docs/capture-backup.md) | 하루치 캡처를 통계와 함께 zip으로 백업하고 지우는 스크립트, readme.md 내용 |
| [web-performance.md](docs/web-performance.md) | 세트가 많이 쌓여도 화면이 느려지지 않게 한 방법(메모리 색인, SSE), 측정 결과 |
| [logs-and-alerts.md](docs/logs-and-alerts.md) | 로그 파일(app/collect/run), 카메라 감시, 수집 문제 Google Chat 알림 설정 |
