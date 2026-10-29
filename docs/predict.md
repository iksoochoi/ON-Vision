# 불량 탐지 (Predict)

저장된 신발 한 켤레(사진 5장)마다 불량 탐지 모델을 돌려서 결과를 **웹 화면의 Predict 메뉴**에서 봅니다. 모델은 `backend/models/defect_detection/best.pt`(YOLO11l, 분사/인쇄 불량 PoC 데이터로 학습, 검증 mAP50 0.31)입니다. 신발 사진용으로 학습한 모델이 아니므로 **개념 검증용**입니다. 박스는 시험 결과로 보고, 판정으로 쓰지 마세요.

## 불량 검출 과정 (신발 배경 제거 → 불량 모델)
```
1. 데이터 수집      카메라 5대에서 사진 1장씩 저장 (카메라 앱)
2. 신발 분할        shoe segmentation 모델이 신발만 윤곽을 땁니다 (신발 틀·홀더·벨트 제외),
                    윤곽 밖은 균일한 회색(114)으로 바꾼 "배경 제거 사진"을 만듭니다
3. 불량 검출        배경 제거 사진에 defect_detection 모델을 적용합니다
```
- 분할 모델: `backend/models/shoe_segmentation/best.pt`(YOLO11n-seg, 클래스 `shoe` 1개, 5대 카메라를 한 모델로 학습: 학습 220장, 손 검수 라벨 44장으로 검증, 마스크 mAP50-95 0.943). 정보는 같은 폴더의 `summary.txt`. (그 폴더의 `yolo11n-seg.pt`는 학습 전 범용 모델이라 쓰지 않습니다.)
- 마스크는 `ON_PREDICT_SEG_GROW`(기본 사진 긴 변의 0.2%, 4000px 사진에서 약 8px)만큼 넓혀서 신발 가장자리를 지키고, 신발을 못 찾은 사진(`seg.found=false`)은 **원래 사진 그대로** 불량 검출합니다.
- 결과 JSON의 카메라별 `seg`에 신발 윤곽(0~1 비율의 다각형), 신뢰도, 개수가 들어 있습니다. `views.<카메라>.shoe`(박스가 신발 위인지 보는 영역)도 이 윤곽의 박스로 바뀌어서, 이전에 카메라 앱의 영역이 없던(BACK/FRONT) 사진도 "신발 위/밖" 판정이 됩니다.
- Predict 화면의 **Show background removed** 를 켜면 사진을 불량 모델이 본 모습(배경 제거)으로 바꿔 보여 줍니다. 사진 파일을 따로 저장하지 않고 저장된 윤곽으로 그때그때 만듭니다(`/api/predict/<id>/cutout`).
- 끄려면 `ON_PREDICT_SEG=0`(저장된 그대로 검사). 모델 파일이 없으면 워커가 경고를 남기고 같은 방식으로 동작합니다.
- 알아둘 점: 불량 모델은 배경이 있는 사진으로 학습했습니다. 배경 제거 사진에서는 배경에서 생기던 박스가 크게 줄지만(시험 3세트: 세트당 22~24개 → 3~7개), 실제 불량을 놓치는지는 재학습 전까지 확인이 필요합니다. 비교는 `run_folder.py`로 `--seg` 유무를 나눠 돌려 볼 수 있습니다.

## 수집에 미치는 영향이 없게 한 구조
```
카메라 앱 (backend.main) ── 사진 5장 + captures/.sets/<id>.json 저장
                                   │ (디스크만 주고받음)
예측 워커 (backend.predict.worker, 별도 프로세스, 낮은 우선순위)
   └─ captures/.predict/<id>.json 에 결과 저장
카메라 앱의 웹 서버 ── .predict/ 를 읽어 Predict 메뉴에 보여 줌
```
- 워커는 **별도 launchd 서비스**입니다. 카메라 앱은 워커를 import하지 않고 기다리지도 않습니다. 워커가 느리거나 죽어도 수집은 그대로입니다.
- **QR 값이 읽힌 세트만** 예측합니다(세트 ID가 QR 값으로 시작하는 것). QR이 안 읽힌 세트(ID가 촬영 시각뿐)는 데이터 수집만 하고 예측하지 않으며, Predict 목록에도 나오지 않습니다(예전에 예측된 결과가 남아 있어도 표시하지 않습니다).
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
- **판단에 도움이 되는 정보**(세트를 열 때 저장된 자료로 계산합니다. **다시 예측하지 않으므로** 이미 쌓인 결과에도 바로 보입니다):
  - **On shoe**: 박스가 신발 위에 있는지(`on shoe` / `off shoe` / `unknown`). 신발 분할 모델의 윤곽에서 구한 신발 영역(`views.<카메라>.shoe`, 모든 카메라; 없으면 세트를 저장할 때 구해 둔 `views.<카메라>.pick.shoe.box`)과 겹치는 비율이 `ON_PREDICT_ON_SHOE_MIN`(0.5) 이상이면 신발 위입니다. 신발 밖 박스는 점선으로 그려집니다. 신발 영역이 없는 세트(`ON_PICK_MODE=classic`으로 저장)나, 영역이 사진의 `ON_PREDICT_SHOE_MAX_AREA`(80%)를 넘어서 믿기 어려운 경우(left 카메라에서 자주 그렇습니다)는 `unknown`입니다.
  - **신발 구역(shoe zone)은 기본으로 꺼져 있습니다**(`ON_PREDICT_ZONE=0`). 신발 분할 모델이 모든 카메라의 사진에서 자기 신발 윤곽을 찾아 결과에 저장하므로 구역이 필요 없어졌고, 카메라를 옮긴 뒤 오래된 구역이 남으면 분할이 신발을 못 찾은 사진에서 진짜 불량을 `off shoe`로 빼 버릴 수 있기 때문입니다. 구역이 없으면 그런 사진의 박스는 `unknown`이어서 **판정에 그대로 들어갑니다**. 화면에서 `Show shoe zone`, `Rebuild`, 구역 상태 줄은 없앴습니다. `ON_PREDICT_ZONE=1`로 켜면 백엔드는 예전처럼 최근 `ON_PREDICT_ZONE_SETS`(60)개 세트로 카메라별 구역을 학습해 신발 영역이 없는 사진의 대체 판정에 쓰지만(표에 `on shoe (zone)`), 다시 만드는 버튼이 없으므로 카메라를 옮기면 새 사진이 60세트 쌓일 때까지 부정확합니다. 저장: `captures/.predict/zones/`.
  - **Class mAP50**: 모델이 그 클래스를 자기 검증 사진에서 얼마나 잘 찾았는지(`backend/models/defect_detection/class_metrics.json`, 학습 로그에서 옮긴 값). 0.5 이상 초록, 0.2 미만 노랑. 검출 하나의 정확도가 아니라 **모델의 그 클래스에 대한 평소 성능**입니다.
  - **Sharp**: 그 카메라 사진의 선명도(세트 JSON의 `sharpness`). 같은 카메라끼리 비교하세요. 카메라 칸 제목에도 나옵니다.
  - **검출 개수**: 카메라 칸 제목의 `N found`.
- **보정(corrections, 기본 켬)**: 화면 왼쪽 위 `Apply corrections` 체크로 켜고 끕니다. 아래에 해당하는 검출은 **판정에서 뺍니다**(사진에서는 점선과 흐리게, 이유가 붙고, 검출 표 `Verdict` 열에 이유가 나옵니다).
  - **recurring(반복)**: 같은 카메라에서 **같은 클래스가 같은 자리**(IoU `ON_PREDICT_RECUR_IOU` 0.5 이상)에 **다른 세트 `ON_PREDICT_RECUR_MIN`(10)개 이상**에서도 나온 것. 한 신발의 불량이 대부분 신발의 같은 픽셀에 있을 수는 없으므로, 배경이나 모델이 모든 신발에서 읽어 내는 것(로고, 글씨)으로 봅니다. `Recurs` 열이 그 개수입니다.
  - **off shoe**: 박스가 신발 영역 밖(`unknown`은 남김).
  - **fine**: `ON_PREDICT_OK_CLASSES`의 클래스.
  - 남은 검출이 하나라도 있으면 DEFECT, 없으면 OK입니다. 목록에는 모델이 혼자 낸 판정이 다르면 `raw: DEFECT`처럼 같이 보이고, 위쪽에 `173 → 144 defect sets`처럼 보정 전후 개수가 나옵니다. 체크를 끄면 모델의 원래 판정입니다.
  - 결과를 새로 예측하지 않고 쌓인 결과 전체에서 그때그때 계산합니다(결과 수천 개까지 1초 안팎). **세트가 10개 미만이면 반복 판정이 아직 없습니다**(처음 몇십 켤레는 보정 효과가 작음).
  - **주의**: 모든 신발에 똑같이 있는 진짜 결함(예: 지그나 공정 때문에 매번 같은 곳)도 반복으로 빠집니다. 라벨링을 위한 후보를 줄이는 용도로 쓰고, 반복으로 빠진 박스도 처음에 한 번은 훑어보세요. 시험(Mac 결과 173세트): 검출 1,322개 중 957개(72%)가 빠지고 365개만 남았으며 DEFECT 세트는 173 → 144개였습니다. 남은 검출도 대부분 신발의 글씨나 무늬라서, 판정으로 쓰기에는 여전히 부족하고 **라벨링 후보 정리용**입니다.
- **DEFECT 기준**: `ON_PREDICT_OK_CLASSES`에 없는 클래스가 하나라도 검출되면 불량입니다. 모델에 "좋은 인쇄" 클래스(`Print4_poor_print_good`)가 있어서 기본으로 OK 클래스에 넣었습니다.
- ERROR: 사진을 하나도 읽지 못했거나 추론이 실패한 세트(다시 시도하지 않음). 일부 사진만 빠진 세트는 나머지로 판정하고 카메라 칸에 이유가 나옵니다.

## 결과 저장 (Save 버튼)
화면에 보이는 결과를 **박스가 그려진 사진 + result.json**으로 파일에 저장합니다. 불량 후보 정리, 재학습 라벨링 준비, 다른 곳으로 옮겨 보기 위한 용도입니다.
- **Save this set**(오른쪽 위, 세트 제목 아래): 지금 열어 둔 세트 하나.
- **Save shown (N)**(왼쪽 위): 목록에 **지금 보이는 세트 전부**(All/Defect/OK/Error, 날짜, 클래스 필터가 적용된 것). 50세트가 넘으면 한 번 물어봅니다. 한 번에 최대 `ON_PREDICT_EXPORT_MAX_SETS`(200)세트이고 넘으면 나머지는 빠졌다고 알려 줍니다.
- **with originals**: 박스 없는 원본 사진도 같이 저장합니다(용량이 커집니다).
- **Apply corrections** 체크 상태가 그대로 반영됩니다(켜면 판정에 들어가는 박스는 빨강, 빠진 박스는 얇은 회색과 이유, 끄면 모델의 원래 결과로 전부 빨강).
- 저장 위치는 **앱이 도는 컴퓨터**의 `exports/predict_<날짜_시각>/`(`ON_EXPORTS_DIR`)이고, 같은 이름의 zip도 만들어서 **Download zip** 링크가 나옵니다(다른 컴퓨터의 브라우저에서도 받을 수 있음).
```
exports/predict_20261023_110500/
  manifest.json            저장한 세트 목록, 필터, 보정 설정(recur_min 등)
  <세트 id>/result.json    판정(모델 원래 / 보정), 카메라별 검출: class, conf, box(비율과 원본 사진 픽셀),
                           신발 위 여부와 겹침 비율, 반복 개수, 판정에 들어가는지(counts)와 빠진 이유, 클래스 mAP50, 선명도
  <세트 id>/<카메라>.jpg    박스가 그려진 사진 (긴 변 `ON_PREDICT_EXPORT_MAX_SIDE`(2000)px로 줄임, 0이면 원본 크기)
  <세트 id>/original/<카메라>.jpg   with originals를 켰을 때만
exports/predict_20261023_110500.zip
```
- 박스 좌표 `box_px`는 **원본 사진의 픽셀**이라, 줄인 사진이 아니라 `original`이나 원래 저장된 사진에 그대로 맞습니다.
- 세트마다 사진 읽기와 그리기에 1초쯤 걸립니다(사진 5장). 저장 중에는 버튼이 `Saving…`으로 바뀝니다. 예측 결과가 없는 세트나 사진이 없는 카메라는 건너뛰고 이유를 `manifest.json`에 남깁니다.

## 결과 지우기 (Delete)
Predict 화면에서 **불량 탐지 결과만** 지웁니다. 세트와 사진은 그대로 남습니다(`captures/.predict/<id>.json`만 삭제).
- **선택 삭제**: 목록 왼쪽 체크박스로 세트를 고르고(**select shown**: 보이는 것 모두) **Delete selected**. 체크박스를 눌러도 세트가 열리지는 않습니다.
- **한 세트**: 세트를 열었을 때 위쪽의 **Delete result**.
- **전체 삭제**: **Delete all results**. 확인 창이 한 번 뜹니다.
- 지우면 워커가 **최근 세트(`ON_PREDICT_MAX_AGE_HOURS`, 24시간 이내)는 다시 예측**합니다. 그래서 설정이나 모델을 바꾼 뒤 "다시 예측"하는 용도로도 씁니다. 더 오래된 세트는 결과 없이 남고, 모두 다시 하려면 `python -m backend.predict.worker --backfill`을 씁니다.
- API: `POST /api/predict/delete` 본문 `{"ids": [세트 id, ...]}` 또는 `{"all": true}` → `{"deleted": n}`.
- 세트 자체를 지우면(Gallery 삭제) 결과도 함께 지워집니다(원래 동작).

## 결과 파일
`captures/.predict/<세트 id>.json` (워커가 쓰고 앱은 읽기만): `defect`(true/false/null, **모델의 원래 판정**. 보정 판정은 화면이 계산), `count`, `classes`, 카메라별 `detections`(상자는 사진 크기에 대한 0~1 비율), `ms`, 모델·장치·입력 크기·점수 기준. 카메라별 `seg`(신발 윤곽 다각형·신뢰도·개수), 세트 `seg`(분할 모델 정보와 배경이 제거된 카메라 목록). `.status.json`은 워커 상태입니다. 세트를 지우면(웹 삭제, `capture_backup.sh`의 백업/삭제) **결과도 함께** 지워지고, 백업 zip에도 같이 들어갑니다.

## 설정 (`.env`, 워커가 읽음)
| 키 | 기본 | 뜻 |
|---|---|---|
| `ON_PREDICT_MODEL` | `backend/models/defect_detection/best.pt` | 모델 파일 |
| `ON_PREDICT_DEVICE` | `auto` | `auto`(mps → cuda → cpu) / `cpu` / `mps` / `0` |
| `ON_PREDICT_IMGSZ` | `1280` | 입력 크기(학습 때와 같음). 줄이면 빨라지고 작은 결함은 놓칠 수 있음 |
| `ON_PREDICT_CONF` | `0.25` | 검출 최소 점수 |
| `ON_PREDICT_SEG` | `1` | 신발 배경 제거 후 불량 검출 (0 = 저장된 사진 그대로) |
| `ON_PREDICT_SEG_MODEL` | `backend/models/shoe_segmentation/best.pt` | 신발 분할 모델 파일 |
| `ON_PREDICT_SEG_IMGSZ` | `960` | 분할 모델 입력 크기(학습 때와 같음) |
| `ON_PREDICT_SEG_CONF` | `0.5` | 신발로 인정하는 최소 점수 |
| `ON_PREDICT_SEG_GROW` | `0.002` | 윤곽을 사진 긴 변의 이 비율만큼 넓힘(0.002 = 4000px에서 8px) |
| `ON_PREDICT_THREADS` | `2` | torch CPU 스레드 수 |
| `ON_PREDICT_MAX_AGE_HOURS` | `24` | 이보다 오래된 세트는 안 함(0 = 제한 없음) |
| `ON_PREDICT_MAX_BACKLOG` | `100` | 밀리면 최신 이 개수만, 오래된 것은 건너뜀 |
| `ON_PREDICT_ON_SHOE_MIN` | `0.5` | 박스의 이만큼이 신발 영역 안에 있으면 `on shoe` (앱이 읽음, 화면 표시용) |
| `ON_PREDICT_SHOE_MAX_AREA` | `0.8` | 신발 영역이 사진의 이 비율을 넘으면 믿지 않고 `unknown` |
| `ON_PREDICT_CORRECT` | `1` | 화면이 보정을 켠 채로 시작(화면에서 바꿀 수 있음) |
| `ON_PREDICT_RECUR_MIN` | `10` | 같은 자리·같은 클래스가 이 개수 이상의 다른 세트에 있으면 반복 |
| `ON_PREDICT_RECUR_IOU` | `0.5` | 같은 자리로 보는 박스 겹침(IoU) 기준 |
| `ON_PREDICT_EXPORT_MAX_SIDE` | `2000` | 저장하는 박스 사진의 긴 변(0 = 원본 크기) |
| `ON_PREDICT_EXPORT_MAX_SETS` | `200` | 한 번에 저장하는 최대 세트 수 |
| `ON_PREDICT_ZONE` | `0` | 배운 신발 구역 사용 (1 = 켬, 화면에는 버튼이 없음) |
| `ON_PREDICT_ZONE_SETS` | `60` | 구역을 만드는 데 쓰는 최근 세트 수 |
| `ON_PREDICT_ZONE_MIN_SETS` | `20` | 구역을 만들기 위한 최소 결과 수 |
| `ON_PREDICT_ZONE_REFRESH_SETS` | `30` | 새 결과가 이만큼 쌓이면 다시 만듦 |
| `ON_PREDICT_ZONE_THR` | `0.30` | 핵에서 이 비율 이상 달라 보이는 곳까지 몇 픽셀 넓힘 (낮을수록 구역이 커짐) |
| `ON_PREDICT_ZONE_CORE` | `0.50` | 구역의 핵: 중앙값과 이 비율 넘게 다른 곳 (신발이 서로 비슷하면 스스로 낮춤) |
| `ON_PREDICT_OK_CLASSES` | `Print4_poor_print_good` | 이 클래스만 검출되면 불량이 아님(쉼표로 구분) |

## PC에서 시험 / 속도 측정
카메라 없이 폴더만 검사하거나 속도를 잴 때는 [backend/predict/README.md](../backend/predict/README.md)의 `run_folder.py`, `bench.py`를 씁니다.
