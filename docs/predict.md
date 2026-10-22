# 불량 탐지 (Predict)

저장된 신발 한 켤레(사진 5장)마다 불량 탐지 모델을 돌려서 결과를 **웹 화면의 Predict 메뉴**에서 봅니다. 모델은 `backend/models/defect_detection/best.pt`(YOLO11l, 분사/인쇄 불량 PoC 데이터로 학습, 검증 mAP50 0.31)입니다. 신발 사진용으로 학습한 모델이 아니므로 **개념 검증용**입니다. 박스는 시험 결과로 보고, 판정으로 쓰지 마세요.

## 수집에 미치는 영향이 없게 한 구조
```
카메라 앱 (backend.main) ── 사진 5장 + captures/.sets/<id>.json 저장
                                   │ (디스크만 주고받음)
예측 워커 (backend.predict.worker, 별도 프로세스, 낮은 우선순위)
   └─ captures/.predict/<id>.json 에 결과 저장
카메라 앱의 웹 서버 ── .predict/ 를 읽어 Predict 메뉴에 보여 줌
```
- 워커는 **별도 launchd 서비스**입니다. 카메라 앱은 워커를 import하지 않고 기다리지도 않습니다. 워커가 느리거나 죽어도 수집은 그대로입니다.
- 새 세트가 저장되면(파일이 1.5초 이상 지난 뒤) 워커가 **최신 세트부터** 5장을 검사합니다. 밀리면 `ON_PREDICT_MAX_BACKLOG`(100)개만 보고 **오래된 것은 건너뜁니다**. `ON_PREDICT_MAX_AGE_HOURS`(24시간)보다 오래된 세트도 하지 않습니다. 모두 하려면 `python -m backend.predict.worker --backfill`.
- Mac Mini에서 측정한 값(Apple GPU=mps, 1280px): 사진 1장 약 98ms, **세트 약 0.5초**, CPU 코어 0.07개분, 메모리 약 0.8GB. 신발 간격(약 10초)의 5%입니다. CPU로 돌리면 세트당 2~3초에 코어 2~3개분이라 `auto`가 mps를 고릅니다.

## 설치 (Mac)
```bash
git pull
.venv/bin/python -m pip install -r backend/predict/requirements.txt   # torch 포함 약 1~2GB, 카메라 앱과 별도
./bin/install_launchd.sh predict-install      # 워커 서비스 등록 + 시작 (로그인 때 자동 시작)
./bin/install_launchd.sh status               # 워커 상태
./bin/install_launchd.sh logs predict         # 워커 로그 (logs/predict.log)
./bin/install_launchd.sh predict-restart      # .env를 바꾼 뒤
./bin/install_launchd.sh predict-uninstall    # 서비스 제거 (이미 만든 결과는 남음)
```
- `.env`에 `ON_PREDICT_*`를 넣지 않아도 기본값으로 돌아갑니다(키 목록은 아래).
- 웹 화면(앱)은 재시작할 필요가 없습니다. 단, **새 메뉴(Predict) 자체는 앱을 한 번 재시작해야** 나타납니다(`./bin/install_launchd.sh restart`).
- 워커는 밤 정지(quiet hours)에도 계속 돕니다. 일이 없으면 CPU를 쓰지 않습니다. 모델은 메모리에 약 1GB 올라가 있습니다.
- `.venv`에 패키지가 없거나 모델 파일이 없으면 `predict-install`이 이유를 말하고 멈춥니다.

## Predict 메뉴
- 왼쪽 목록: 세트별 시각, QR 값, 검출된 클래스, **DEFECT / OK / ERROR** 표시. 위쪽에서 All / Defect / OK / Error, 날짜, 클래스로 거릅니다(각 개수도 보임).
- 오른쪽: 선택한 세트의 카메라 5장에 박스와 `클래스 점수`를 겹쳐 보여 줍니다. 사진을 누르면 원본 크기로 보입니다. 아래에 검출 표, 맨 아래에 전체 클래스별 개수가 있습니다. 새 결과는 서버가 알려 주어서(SSE) 목록이 저절로 갱신됩니다.
- 위쪽 `worker:` 칩: 워커가 살아 있는지(`not running`이면 꺼져 있음), 장치, 세트당 평균 시간, 대기 개수.
- **DEFECT 기준**: `ON_PREDICT_OK_CLASSES`에 없는 클래스가 하나라도 검출되면 불량입니다. 모델에 "좋은 인쇄" 클래스(`Print4_poor_print_good`)가 있어서 기본으로 OK 클래스에 넣었습니다.
- ERROR: 사진을 하나도 읽지 못했거나 추론이 실패한 세트(다시 시도하지 않음). 일부 사진만 빠진 세트는 나머지로 판정하고 카메라 칸에 이유가 나옵니다.

## 결과 파일
`captures/.predict/<세트 id>.json` (워커가 쓰고 앱은 읽기만): `defect`(true/false/null), `count`, `classes`, 카메라별 `detections`(상자는 사진 크기에 대한 0~1 비율), `ms`, 모델·장치·입력 크기·점수 기준. `.status.json`은 워커 상태입니다. 세트를 지우면(웹 삭제, `capture_backup.sh`의 백업/삭제) **결과도 함께** 지워지고, 백업 zip에도 같이 들어갑니다.

## 설정 (`.env`, 워커가 읽음)
| 키 | 기본 | 뜻 |
|---|---|---|
| `ON_PREDICT_MODEL` | `backend/models/defect_detection/best.pt` | 모델 파일 |
| `ON_PREDICT_DEVICE` | `auto` | `auto`(mps → cuda → cpu) / `cpu` / `mps` / `0` |
| `ON_PREDICT_IMGSZ` | `1280` | 입력 크기(학습 때와 같음). 줄이면 빨라지고 작은 결함은 놓칠 수 있음 |
| `ON_PREDICT_CONF` | `0.25` | 검출 최소 점수 |
| `ON_PREDICT_THREADS` | `2` | torch CPU 스레드 수 |
| `ON_PREDICT_MAX_AGE_HOURS` | `24` | 이보다 오래된 세트는 안 함(0 = 제한 없음) |
| `ON_PREDICT_MAX_BACKLOG` | `100` | 밀리면 최신 이 개수만, 오래된 것은 건너뜀 |
| `ON_PREDICT_OK_CLASSES` | `Print4_poor_print_good` | 이 클래스만 검출되면 불량이 아님(쉼표로 구분) |

## PC에서 시험 / 속도 측정
카메라 없이 폴더만 검사하거나 속도를 잴 때는 [backend/predict/README.md](../backend/predict/README.md)의 `run_folder.py`, `bench.py`를 씁니다.
