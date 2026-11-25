# 불량 탐지 (Predict)

저장된 신발 한 켤레(사진 5장)마다 불량 탐지 모델을 돌려서 결과를 **웹 화면의 Predict 메뉴**에서 봅니다. 모델은 **한 번에 하나**이고 `.env`의 `ON_PREDICT_MODEL`로 고릅니다(아래 "모델 고르기"). 아직 사진이 적은 시험 모델이라, 박스는 시험 결과로 보고 판정으로 쓰지 마세요.

## 모델 고르기 (`ON_PREDICT_MODEL`)
| 값 | 모델 | 내용 |
|---|---|---|
| `overspray` (기본) | `backend/models/defect_detection/overspray/best.pt` | YOLO11s, 클래스 `Overspray` 1개. 이 현장 신발 사진(배경 제거)으로 2026-11-20 학습. 검증: 불량 신발 14켤레 중 10켤레 찾음, 정상 12켤레 중 2켤레 오검출(점수 0.25) |
| `poc` | `backend/models/defect_detection/poc/best.pt` | YOLO11l, 분사/인쇄 불량 12클래스. 다른 제품의 PoC 데이터로 학습(mAP50 0.31) |
| 경로 | `.pt` 파일 또는 `best.pt`가 있는 폴더 | 프로젝트 폴더 기준 상대 경로 또는 절대 경로 |

```bash
# Mac의 .env
ON_PREDICT_MODEL=overspray # overspray | poc | path
./bin/install_launchd.sh predict-restart      # 워커가 새 모델을 읽음 (앱은 재시작 필요 없음)
```
- 모델 폴더마다 `best.pt`, `class_metrics.json`(화면의 Class mAP50), `summary.txt`(학습 정보)가 있습니다. 결과 JSON의 `model`은 `overspray/best.pt`처럼 폴더 이름까지 적힙니다(이전 결과는 `best.pt`).
- **주의: 모델을 바꿔도 이미 만든 결과(`captures/.predict/`)는 그대로 남습니다.** 이전 모델 결과를 새 모델로 바꾸려면 Predict 화면의 **Predict a period**에서 기간을 고르고 **again**을 켜서 예측하세요(아래 "기간 예측").
- **Overspray 모델 주의**: 학습 사진에 top 뷰가 없고(front/left/right/back만), 불량 예시는 ON1WH뿐이며 back 불량은 7장뿐입니다. top 사진과 back 사진의 박스는 특히 조심해서 보세요. 점수 0.5를 넘는 박스가 거의 없으므로 `ON_PREDICT_CONF`는 0.25 근처로 두세요(0.4면 불량을 대부분 놓침).

## 불량 검출 과정 (신발 배경 제거 → 불량 모델 → Bonding gap)
```
1. 데이터 수집      카메라 5대에서 사진 1장씩 저장 (카메라 앱)
2. 신발 분할        shoe segmentation 모델이 신발만 윤곽을 땁니다 (신발 틀·홀더·벨트 제외),
                    윤곽 밖은 균일한 회색(114)으로 바꾼 "배경 제거 사진"을 만듭니다
3. 불량 검출        배경 제거 사진에 불량 모델(ON_PREDICT_MODEL)을 적용합니다
4. Bonding gap      left·right 원본 사진에서 스프레이 끝 ~ 미드솔 턱 간격을 잽니다 (QC viewer와 같은 측정)
5. 판정             모델 또는 bonding gap 중 하나라도 불량이면 DEFECT
```
- 분할 모델: `backend/models/shoe_segmentation/best.pt`(YOLO11n-seg, 클래스 `shoe` 1개, 5대 카메라를 한 모델로 학습. 버전 3(2026-11-09): 암막 환경 2026-11-02 사진 1285장 + 이전 2026-10-24 사진 1292장, 두 환경 검증 IoU 모두 0.980). 정보는 같은 폴더의 `summary.txt`. (그 폴더의 `yolo11n-seg.pt`는 학습 전 범용 모델이라 쓰지 않습니다.)
- 마스크는 `ON_PREDICT_SEG_GROW`(기본 사진 긴 변의 0.2%, 4000px 사진에서 약 8px)만큼 넓혀서 신발 가장자리를 지키고, 신발을 못 찾은 사진(`seg.found=false`)은 **원래 사진 그대로** 불량 검출합니다.
- 결과 JSON의 카메라별 `seg`에 신발 윤곽(0~1 비율의 다각형), 신뢰도, 개수가 들어 있습니다. `views.<카메라>.shoe`(박스가 신발 위인지 보는 영역)도 이 윤곽의 박스로 바뀌어서, 이전에 카메라 앱의 영역이 없던(BACK/FRONT) 사진도 "신발 위/밖" 판정이 됩니다.
- Predict 화면의 **Show background removed** 를 켜면 사진을 불량 모델이 본 모습(배경 제거)으로 바꿔 보여 줍니다. 사진 파일을 따로 저장하지 않고 저장된 윤곽으로 그때그때 만듭니다(`/api/predict/<id>/cutout`).
- 끄려면 `ON_PREDICT_SEG=0`(저장된 그대로 검사). 모델 파일이 없으면 워커가 경고를 남기고 같은 방식으로 동작합니다.
- 알아둘 점: `overspray` 모델은 이 배경 제거 사진과 같은 방식으로 만든 사진으로 학습했습니다(`train/defect_detection/make_cutout.py`). `poc` 모델은 배경이 있는 사진으로 학습했습니다. 배경 제거 사진에서는 배경에서 생기던 박스가 크게 줄지만(시험 3세트: 세트당 22~24개 → 3~7개), 실제 불량을 놓치는지는 재학습 전까지 확인이 필요합니다. 비교는 `run_folder.py`로 `--seg` 유무를 나눠 돌려 볼 수 있습니다.

## Bonding gap (Sidewall bonding margin)
- 정의: 스프레이가 칠해진 끝과 미드솔 턱(ledge) 사이에 접착되지 않은 흰 띠의 폭. QC 기준서 "Sidewall Bonding Margin"(최대 4 mm)과 같은 것이고, QC viewer에서 만든 측정을 **그대로** 씁니다(`backend/predict/bonding_gap.py`, 2026-11-24에 `train/qc_sheet`에서 옮김. 측정 내용은 바꾸지 않았고, 같은 세트에서 QC viewer와 값이 같은 것을 확인함).
- 순서: 불량 모델 **다음**에 잽니다. 두 검사는 서로의 결과를 쓰지 않지만, 모델 결과가 먼저 저장되므로 gap 측정이 느리거나 실패해도 Overspray 판정은 남습니다.
- 측정: left·right 카메라의 **원본 사진**(배경 제거 전). 자는 신발 갑피의 QR 라벨(13 mm)이 그 카메라에 보이면 QR, 아니면 신발 틀 위 캠플레이트 크기(`backend/models/bonding_gap/`의 템플릿과 보정값 `plate_calib.json`). QR 옆(또는 신발 박스 안)에서 두 선을 찾아 신발 옆면 전체를 따라갑니다.
- 판정(카메라별): 간격이 `ON_PREDICT_GAP_LIMIT_MM`(5 mm)를 넘는 구간이 신발 길이 방향으로 `ON_PREDICT_GAP_OVER_MM`(30 mm) 이상이면 **BONDING GAP**. 세트는 left·right 중 하나라도 그러면 불량입니다. 기준서는 4 mm이지만, 4 mm로 하면 정상 신발도 불량이 많이 나와서(QC 시트 OK 42세트 중 7개) QC viewer와 같은 5 mm / 30 mm를 기본으로 했습니다(2026-11-19, 631세트 분석).
- 화면:
  - left·right 사진에 스프레이 끝(빨강), 미드솔 턱(초록), 기준 초과 구간(빨간 반투명), 최대 위치(노랑) 선이 그려집니다. 카메라 칸 제목에 `gap 6.8 mm, over 199 mm`.
  - 세트 화면의 **Bonding gap** 표: 카메라별 판정, 측정 상태(`ok` / `unsure` 경계를 찾은 길이가 짧음 / `fail`·`none` 못 잼, 이유), 최대·중앙값, 기준 초과 길이, 따라간 길이, 자(QR / cam plate).
  - 목록에서는 클래스 `Bonding_gap`(세트당 1개)으로 보이고 클래스 필터로 거를 수 있습니다. 보정(corrections)은 모델 박스에만 적용되고 gap에는 적용되지 않습니다.
  - Save: 저장 사진에 같은 선이 그려지고, `result.json`에 `bonding_gap`(세트)과 카메라별 `views.<카메라>.bonding_gap`이 들어갑니다.
- 결과 JSON: `gap` = `{"algo", "limit_mm", "over_min_mm", "ms", "defect", "views": {"left", "right"}}`, 모델만의 판정은 `model_defect`. 이 기능 전에 만든 결과에는 `gap`이 없습니다. gap까지 보려면 **Predict a period**에서 again을 켜고 다시 예측하세요.
- 속도: 세트(양쪽)당 약 1~2.5초 추가(이 PC 측정; CPU 사용, OpenCV 스레드는 `ON_PREDICT_THREADS`로 제한). 신발 간격 약 10초 안에 들어갑니다.
- 주의:
  - 캠플레이트 보정값은 **지금 카메라 위치** 기준입니다. 현장에서 left/right 카메라를 옮기면 아래 "캠플레이트 보정"으로 다시 보정하세요.
  - 앞코·뒤꿈치는 카메라에서 멀어지고 휘어서 값이 덜 정확합니다(판정은 최대값이 아니라 초과 길이로).
  - 측정은 측면(left/right) 사진에 보이는 곳만입니다. 다른 위치의 gap 불량은 놓칠 수 있습니다(QC 시트 Bonding_gap 중 일부는 3.5~4.9 mm로 측정됨).
  - 새 색의 갑피가 나오면 경계가 잘 보이는지 확인이 필요합니다(회색 ON1WH, 연두 ON1GR까지 확인).
  - 제품(회색 ON1WH ↔ 연두 ON1GR)이 바뀌어도 설정을 바꿀 필요가 없습니다. 사진마다 갑피의 채도를 보고 경계를 찾을 채널을 스스로 고릅니다(회색: 밝기, 색 있는 갑피: V−2S). 기준(5 mm / 30 mm)과 캠플레이트 보정은 두 제품 공통입니다.
  - 2026-11-25(ALGO 9): ON RUNNING 글자가 밑창 가까이 있는 곳(연두 ON1GR 오른쪽 카메라)에서 시작 구간이 글자 아래 끝을 경계로 잡아 상자 모양 가짜 gap(약 38 mm)이 생기던 것을 고침. qc_1105~1121 1,075세트: 새로 불량이 된 것 0, 오판정 22개 줄어듦(ON1GR OK 2→0), 알려진 Bonding_gap 검출은 그대로. 그 전 결과는 again으로 다시 예측해야 바뀝니다.
  - 끄려면 `ON_PREDICT_GAP=0`(워커 재시작).

### 캠플레이트 보정 (Predict 화면 왼쪽 "Cam plate", 수동)
- 왜: QR 라벨이 신발에 없으면(2026-11-14부터 대부분) gap의 자는 신발 틀 위 캠플레이트입니다. 카메라별 템플릿(판 모양)과 K(판 크기 → 신발 옆면 mm/px, `mm/px = K / s`)가 필요합니다. 기본값은 저장소의 `backend/models/bonding_gap/`(PC에서 갑피에 QR이 붙은 세트로 만든 정확한 K).
- 언제: left/right 카메라를 옮긴 뒤. 칸을 열면 최근 100개 결과에서 **판을 찾은 비율과 매칭 점수**가 보입니다. 찾은 비율이 80% 아래로 떨어지면(노란색) 또는 점수가 예전보다 낮아지면 카메라가 움직인 것입니다.
- 방법: 카메라를 옮긴 **뒤의 날짜**를 고르고 **Calibrate** → 워커가 새 세트와 기간 예측이 없을 때 그 기간 세트 최대 30개(QR 값이 있는 세트)의 left/right 사진으로 계산합니다(이 PC 약 95초). 결과(후보)가 칸에 나오면 확인하고 **Apply**(또는 **Discard**). Apply 전에는 아무것도 바뀌지 않습니다.
  1. 지금 쓰는 템플릿으로 사진마다 판을 찾음
  2. 판을 가장 잘 찾은 사진(기울기가 작은 것)에서 새 템플릿을 자름(판 위 QR 라벨은 지움)
  3. **추정 K**: 새 템플릿으로 다시 찾아서, 사진마다 예전 방식과 같은 mm/px가 되는 K의 중앙값 → 스케일은 예전 값을 이어감(판과 신발 옆면의 거리 비율이 그대로라고 가정)
  4. 후보 화면: 카메라별 K 변화, 판을 찾은 수(전/후), 예전 스케일과의 차이(중앙값, p90), 갑피 QR 사진이 있으면 QR과의 차이, 템플릿 비교 그림
- 적용하면 워커가 다음 라운드에 바로 씁니다(재시작 불필요). 결과마다 어떤 보정으로 쟀는지 `gap.calib`(`site`/`default`, 시각)에 남고, 세트 화면 Bonding gap 표 아래에 보입니다. 이미 만든 결과는 그대로라, 다시 재려면 Predict a period(again).
- **Use default**: 현장 보정을 그만 쓰고 저장소 기본값으로 돌아갑니다(현장 보정은 history에 보관).
- 저장 위치: `captures/.predict/bonding_gap/` — `active/`(사용 중), `candidate/`(후보), `history/<시각>/`(교체된 것). git이 관리하지 않아 `git pull`과 충돌하지 않습니다. 요청 파일 `captures/.predict/.calib.json`, 진행 상황은 `.status.json`의 `calib`.
- 시험(2026-11-14 현장 40세트, 이 PC): 판 30/30 찾음, 새 템플릿의 스케일이 예전과 중앙값 0.5%(p90 1.0~1.6%) 차이. 사이트 보정으로 다시 잰 gap 최대값은 기본 보정 대비 중앙값 0.0%, p90 1.8%, 78장 중 판정 바뀜 1(기준 근처).
- 한계(주의):
  - **추정 K는 스케일을 새로 재는 것이 아니라 이어 가는 것**입니다. 카메라 각도·거리가 바뀌어 판과 신발 옆면의 비율이 달라졌다면 그 차이는 잡지 못합니다. 정확한 K는 갑피 옆면에 QR(같은 값의 라벨 사본)을 붙인 세트가 필요합니다(나중에 추가 예정, 지금은 PC의 `train/qc_sheet/plate_calib.py`).
  - 카메라를 너무 많이 옮겨 **지금 템플릿으로 판을 5장 이상 못 찾으면** 그 카메라는 보정이 안 되고(이유가 표시됨) 예전 값을 유지합니다. 이 경우 PC에서 기준 사진과 판 위치(`REF`)를 정해 `plate_calib.py`로 보정해야 합니다.
  - API: `GET /api/predict/calib`, `GET /api/predict/calib/template?which=in_use|candidate|default&view=left`, `POST /api/predict/calib` `{"from","to"}`, `POST /api/predict/calib/cancel|apply|discard|default`.

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
- **새 세트 예측은 기본으로 꺼져 있습니다.** Predict 화면 왼쪽의 **Predict new sets**를 체크해야 라인에서 저장되는 세트를 예측합니다(아래 "새 세트 예측 켜고 끄기"). 꺼져 있으면 워커는 **Predict a period**로 요청한 기간만 예측합니다.
- 켜져 있을 때, 새 세트가 저장되면(파일이 1.5초 이상 지난 뒤) 워커가 **최신 세트부터** 5장을 검사합니다. 밀리면 `ON_PREDICT_MAX_BACKLOG`(100)개만 보고 **오래된 것은 건너뜁니다**. `ON_PREDICT_MAX_AGE_HOURS`(24시간)보다 오래된 세트도 하지 않습니다. 모두 하려면 `python -m backend.predict.worker --backfill`.
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
- **`predict-install` 직후에는 새 세트를 예측하지 않습니다**(Predict new sets 기본 꺼짐). 라인 세트를 예측하려면 Predict 화면에서 체크하세요. 체크 상태는 파일에 남아서 워커나 Mac을 재시작해도 유지됩니다.

## Predict 메뉴
- 왼쪽 목록: 세트별 시각, QR 값, 검출된 클래스, **DEFECT / OK / ERROR** 표시. 위쪽에서 All / Defect / OK / Error, 날짜, 클래스로 거릅니다(각 개수도 보임).
- 오른쪽: 선택한 세트의 카메라 5장에 박스와 `클래스 점수`를 겹쳐 보여 줍니다. 사진을 누르면 원본 크기로 보입니다. 아래에 검출 표, 맨 아래에 전체 클래스별 개수가 있습니다. 새 결과는 서버가 알려 주어서(SSE) 목록이 저절로 갱신됩니다.
- 위쪽 `worker:` 칩: 워커가 살아 있는지(`not running`이면 꺼져 있음), 장치, 세트당 평균 시간, 대기 개수.
- **판단에 도움이 되는 정보**(세트를 열 때 저장된 자료로 계산합니다. **다시 예측하지 않으므로** 이미 쌓인 결과에도 바로 보입니다):
  - **On shoe**: 박스가 신발 위에 있는지(`on shoe` / `off shoe` / `unknown`). 신발 분할 모델의 윤곽에서 구한 신발 영역(`views.<카메라>.shoe`, 모든 카메라; 없으면 세트를 저장할 때 구해 둔 `views.<카메라>.pick.shoe.box`)과 겹치는 비율이 `ON_PREDICT_ON_SHOE_MIN`(0.5) 이상이면 신발 위입니다. 신발 밖 박스는 점선으로 그려집니다. 신발 영역이 없는 세트(`ON_PICK_MODE=classic`으로 저장)나, 영역이 사진의 `ON_PREDICT_SHOE_MAX_AREA`(80%)를 넘어서 믿기 어려운 경우(left 카메라에서 자주 그렇습니다)는 `unknown`입니다.
  - **신발 구역(shoe zone)은 기본으로 꺼져 있습니다**(`ON_PREDICT_ZONE=0`). 신발 분할 모델이 모든 카메라의 사진에서 자기 신발 윤곽을 찾아 결과에 저장하므로 구역이 필요 없어졌고, 카메라를 옮긴 뒤 오래된 구역이 남으면 분할이 신발을 못 찾은 사진에서 진짜 불량을 `off shoe`로 빼 버릴 수 있기 때문입니다. 구역이 없으면 그런 사진의 박스는 `unknown`이어서 **판정에 그대로 들어갑니다**. 화면에서 `Show shoe zone`, `Rebuild`, 구역 상태 줄은 없앴습니다. `ON_PREDICT_ZONE=1`로 켜면 백엔드는 예전처럼 최근 `ON_PREDICT_ZONE_SETS`(60)개 세트로 카메라별 구역을 학습해 신발 영역이 없는 사진의 대체 판정에 쓰지만(표에 `on shoe (zone)`), 다시 만드는 버튼이 없으므로 카메라를 옮기면 새 사진이 60세트 쌓일 때까지 부정확합니다. 저장: `captures/.predict/zones/`.
  - **Class mAP50**: 모델이 그 클래스를 자기 검증 사진에서 얼마나 잘 찾았는지(쓰는 모델 폴더의 `class_metrics.json`, 학습 로그에서 옮긴 값). 0.5 이상 초록, 0.2 미만 노랑. 검출 하나의 정확도가 아니라 **모델의 그 클래스에 대한 평소 성능**입니다.
  - **Sharp**: 그 카메라 사진의 선명도(세트 JSON의 `sharpness`). 같은 카메라끼리 비교하세요. 카메라 칸 제목에도 나옵니다.
  - **검출 개수**: 카메라 칸 제목의 `N found`.
- **보정(corrections, 기본 꺼짐)**: 화면 왼쪽 위 `Apply corrections` 체크로 켜고 끕니다(시작 상태는 `ON_PREDICT_CORRECT`). 아래에 해당하는 검출은 **판정에서 뺍니다**(사진에서는 점선과 흐리게, 이유가 붙고, 검출 표 `Verdict` 열에 이유가 나옵니다).
  - **recurring(반복)**: 같은 카메라에서 **같은 클래스가 같은 자리**(IoU `ON_PREDICT_RECUR_IOU` 0.5 이상)에 **다른 세트 `ON_PREDICT_RECUR_MIN`(10)개 이상**에서도 나온 것. 한 신발의 불량이 대부분 신발의 같은 픽셀에 있을 수는 없으므로, 배경이나 모델이 모든 신발에서 읽어 내는 것(로고, 글씨)으로 봅니다. `Recurs` 열이 그 개수입니다.
  - **off shoe**: 박스가 신발 영역 밖(`unknown`은 남김).
  - **fine**: `ON_PREDICT_OK_CLASSES`의 클래스.
  - 남은 검출이 하나라도 있으면 DEFECT, 없으면 OK입니다. 목록에는 모델이 혼자 낸 판정이 다르면 `raw: DEFECT`처럼 같이 보이고, 위쪽에 `173 → 144 defect sets`처럼 보정 전후 개수가 나옵니다. 체크를 끄면 모델의 원래 판정입니다.
  - 결과를 새로 예측하지 않고 쌓인 결과 전체에서 그때그때 계산합니다(결과 수천 개까지 1초 안팎). **세트가 10개 미만이면 반복 판정이 아직 없습니다**(처음 몇십 켤레는 보정 효과가 작음).
  - **주의**: 모든 신발에 똑같이 있는 진짜 결함(예: 지그나 공정 때문에 매번 같은 곳)도 반복으로 빠집니다. 라벨링을 위한 후보를 줄이는 용도로 쓰고, 반복으로 빠진 박스도 처음에 한 번은 훑어보세요. 시험(Mac 결과 173세트): 검출 1,322개 중 957개(72%)가 빠지고 365개만 남았으며 DEFECT 세트는 173 → 144개였습니다. 남은 검출도 대부분 신발의 글씨나 무늬라서, 판정으로 쓰기에는 여전히 부족하고 **라벨링 후보 정리용**입니다.
- **DEFECT 기준**: `ON_PREDICT_OK_CLASSES`에 없는 클래스가 하나라도 검출되면 불량입니다. `poc` 모델에 "좋은 인쇄" 클래스(`Print4_poor_print_good`)가 있어서 기본으로 OK 클래스에 넣었습니다. `overspray` 모델에는 그 클래스가 없으므로 영향이 없습니다(Overspray 박스가 하나라도 있으면 DEFECT).
- ERROR: 사진을 하나도 읽지 못했거나 추론이 실패한 세트(다시 시도하지 않음). 일부 사진만 빠진 세트는 나머지로 판정하고 카메라 칸에 이유가 나옵니다.

## 새 세트 예측 켜고 끄기 (Predict new sets)
- Predict 화면 왼쪽의 **Predict new sets** 체크박스. **기본은 꺼짐**입니다.
- 켜면: 라인에서 저장되는 세트(QR 값이 있는 것)를 워커가 바로 예측합니다(위 "수집에 미치는 영향이 없게 한 구조"의 규칙대로).
- 끄면: 새 세트는 예측하지 않고, **Predict a period**로 요청한 기간만 예측합니다. 위쪽 `worker:` 칩에 `new sets off`가 나옵니다.
- 워커가 매 라운드(약 1초) 읽으므로 **재시작 없이** 바로 바뀝니다. 꺼져 있던 동안 저장된 세트는, 켤 때 최근 24시간(`ON_PREDICT_MAX_AGE_HOURS`) 안의 것이면 그때 예측합니다. 더 오래된 것은 Predict a period로 하세요.
- 명령줄의 `--once`, `--backfill`은 체크와 상관없이 대기 세트를 예측합니다.
- 설정 파일: `captures/.predict/.live.json`(`{"on": true}`; 없으면 꺼짐). API: `POST /api/predict/live` 본문 `{"on": true | false}`. `GET /api/predict` 응답의 `live`, 워커 상태(`.status.json`)의 `live`.

## 기간 예측 (Predict a period)
워커는 평소 최근 24시간(`ON_PREDICT_MAX_AGE_HOURS`) 세트만 예측합니다. 그보다 오래된 세트나, 모델을 바꾼 뒤 이전 결과를 다시 예측하려면 화면 왼쪽의 **Predict a period**를 씁니다.
1. 첫날과 마지막 날을 고릅니다(두 날 모두 포함, 기본은 오늘).
2. **again (with a result too)**: 켜면 결과가 이미 있는 세트도 다시 예측해서 결과를 바꿉니다(모델을 바꾼 뒤). 끄면 결과가 없는 세트만 합니다.
3. **Predict**를 누르면 세트 수, 다시 예측되는 수, 예상 시간을 묻고 시작합니다.
- **현장 라인이 먼저**: 워커는 새로 들어온 세트가 없을 때만 기간 세트를 예측합니다(새 세트를 예측한 라운드에는 기간 세트를 하지 않음). 수집 중에 돌려도 새 신발 결과가 늦어지지 않습니다.
- 진행 상황이 버튼 아래에 나옵니다(`Predicting … 120 / 300 · about 5 min left`, 약 5초마다 갱신). **Cancel**로 멈추면 그때까지 만든 결과는 남습니다(워커가 하던 라운드를 마치고 몇 초 뒤 멈춤). 끝나면 `Last period … done, N predicted`.
- 한 번에 한 기간만 됩니다. 진행 중에는 Predict 버튼이 꺼집니다(먼저 Cancel).
- QR 값이 읽힌 세트만 대상입니다(평소 예측과 같음). 시간 기준은 세트 ID의 촬영 시각입니다.
- 워커가 꺼져 있으면 요청은 `waiting: the worker is not running`으로 남아 있다가 워커가 켜지면 시작합니다. 워커를 재시작하면 같은 요청을 이어서 합니다(again이면 요청 뒤에 만든 결과는 다시 하지 않음).
- 속도: Mac에서 세트당 약 3초 안팎(배경 제거 포함)이면 1,000세트에 약 50분입니다. 확인 창의 예상 시간은 워커의 평균 시간으로 계산합니다.
- 요청 파일: `captures/.predict/.job.json`(앱이 쓰고, 워커가 끝나면 지움, Cancel이면 앱이 지움). 진행 상황: `.status.json`의 `job`.
- API: `GET /api/predict/job?from=2026-11-05&to=2026-11-09` → `{"sets", "with_result", "job", "progress"}`, `POST /api/predict/job` 본문 `{"from", "to", "redo"}`(진행 중이면 409), `POST /api/predict/job/cancel`.

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
- 지우면 워커가 **최근 세트(`ON_PREDICT_MAX_AGE_HOURS`, 24시간 이내)는 다시 예측**합니다. 그래서 설정이나 모델을 바꾼 뒤 "다시 예측"하는 용도로도 씁니다. 더 오래된 세트는 결과 없이 남습니다. 다시 하려면 **Predict a period**(위)를 씁니다.
- API: `POST /api/predict/delete` 본문 `{"ids": [세트 id, ...]}` 또는 `{"all": true}` → `{"deleted": n}`.
- 세트 자체를 지우면(Gallery 삭제) 결과도 함께 지워집니다(원래 동작).

## 결과 파일
`captures/.predict/<세트 id>.json` (워커가 쓰고 앱은 읽기만): `defect`(true/false/null, **모델의 원래 판정**. 보정 판정은 화면이 계산), `count`, `classes`, 카메라별 `detections`(상자는 사진 크기에 대한 0~1 비율), `ms`, 모델·장치·입력 크기·점수 기준. 카메라별 `seg`(신발 윤곽 다각형·신뢰도·개수), 세트 `seg`(분할 모델 정보와 배경이 제거된 카메라 목록). `.status.json`은 워커 상태입니다. 세트를 지우면(웹 삭제, `capture_backup.sh`의 백업/삭제) **결과도 함께** 지워지고, 백업 zip에도 같이 들어갑니다.

## 설정 (`.env`, 워커가 읽음)
| 키 | 기본 | 뜻 |
|---|---|---|
| `ON_PREDICT_MODEL` | `overspray` | 불량 모델: `overspray` / `poc`(`backend/models/defect_detection/<이름>/best.pt`) 또는 `.pt` 파일 경로 |
| `ON_PREDICT_DEVICE` | `auto` | `auto`(mps → cuda → cpu) / `cpu` / `mps` / `0` |
| `ON_PREDICT_IMGSZ` | `1280` | 입력 크기(학습 때와 같음). 줄이면 빨라지고 작은 결함은 놓칠 수 있음 |
| `ON_PREDICT_CONF` | `0.25` | 검출 최소 점수 |
| `ON_PREDICT_SEG` | `1` | 신발 배경 제거 후 불량 검출 (0 = 저장된 사진 그대로) |
| `ON_PREDICT_SEG_MODEL` | `backend/models/shoe_segmentation/best.pt` | 신발 분할 모델 파일 |
| `ON_PREDICT_SEG_IMGSZ` | `960` | 분할 모델 입력 크기(학습 때와 같음) |
| `ON_PREDICT_SEG_CONF` | `0.5` | 신발로 인정하는 최소 점수 |
| `ON_PREDICT_SEG_GROW` | `0.002` | 윤곽을 사진 긴 변의 이 비율만큼 넓힘(0.002 = 4000px에서 8px) |
| `ON_PREDICT_GAP` | `1` | 모델 다음에 bonding gap 측정 (0 = 안 함) |
| `ON_PREDICT_GAP_LIMIT_MM` | `5` | 이 간격(mm)을 넘으면 기준 초과 |
| `ON_PREDICT_GAP_OVER_MM` | `30` | 기준 초과가 신발 길이 방향으로 이만큼(mm) 이상이면 BONDING GAP |
| `ON_PREDICT_THREADS` | `2` | torch CPU 스레드 수 |
| `ON_PREDICT_MAX_AGE_HOURS` | `24` | 이보다 오래된 세트는 안 함(0 = 제한 없음) |
| `ON_PREDICT_MAX_BACKLOG` | `100` | 밀리면 최신 이 개수만, 오래된 것은 건너뜀 |
| `ON_PREDICT_ON_SHOE_MIN` | `0.5` | 박스의 이만큼이 신발 영역 안에 있으면 `on shoe` (앱이 읽음, 화면 표시용) |
| `ON_PREDICT_SHOE_MAX_AREA` | `0.8` | 신발 영역이 사진의 이 비율을 넘으면 믿지 않고 `unknown` |
| `ON_PREDICT_CORRECT` | `0` | 1이면 화면이 보정(Apply corrections)을 켠 채로 시작. 기본은 꺼짐(모델 원래 판정). 화면에서 바꿀 수 있음 |
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

## 고객 전달용: 불량 모델만 쓴 결과 저장 (기간 지정)
특정 기간에 저장된 세트를 **배경 제거 없이, 보정(corrections) 없이 불량 모델만** 돌려 사진과 결과를 파일로 저장합니다. 워커의 결과(`captures/.predict`)와 captures 폴더는 건드리지 않고 읽기만 합니다.
```
python -m backend.cli.predict_report --from 2026-11-02 --to 2026-11-04 --zip
```
- 세트가 있는 컴퓨터(Mac)에서 실행합니다. 다른 폴더를 가리키려면 `--captures <경로>`.
- 기본은 **QR을 못 읽은 세트도 포함**합니다. QR을 읽은 세트만 하려면 `--qr-only`.
- 결과는 `exports/predict_raw_20261102_20261104/`: `summary.csv`(세트별 판정 DEFECT/OK/ERROR, 박스 수, 클래스), `detections.csv`(박스마다 카메라·클래스·점수·좌표), `<세트 id>/<카메라>.jpg`(박스를 그린 사진, 긴 변 2000 px), `<세트 id>/result.json`, `manifest.json`(사용한 모델·conf·imgsz), `README.txt`. `--zip`이면 같은 이름의 .zip도 만듭니다.
- 판정: 사진 중 하나라도 ok 클래스(`ON_PREDICT_OK_CLASSES`)가 아닌 박스가 있으면 DEFECT입니다. 반복 박스·신발 밖 박스를 거르지 않은 **모델 원본 결과**라서 DEFECT 비율이 높게 나옵니다.
- **`--dry-run`**: 예측도 저장도 하지 않고 기간별 세트 수, 사진 장수, 저장된 사진 용량과 **결과 폴더 예상 용량**(박스 그린 사진은 12장을 실제로 줄여서 재서 추정)·남은 디스크를 보여 줍니다. 실행 전에 먼저 해 보세요.
- 박스 그린 사진의 크기: `--max-side`(기본 **2000**)보다 긴 사진은 긴 변을 2000 px로 줄이고(JPEG 품질 92), 짧으면 그대로 저장합니다. 저장된 원본(4208x3120, 품질 100)은 장당 약 8 MB인데 2000 px로 줄이면 장당 약 0.8 MB(원본의 약 10%)입니다. `--max-side 0`이면 줄이지 않고 원본 크기로 그리며 원본의 약 40%입니다.
- **`--seg`**: 워커처럼 신발 분할 모델로 배경을 회색으로 지운 뒤 불량 모델을 돌립니다(보정은 계속 꺼짐). 결과는 `exports/predict_seg_<기간>/`이라 `--seg` 없는 결과와 섞이지 않습니다. 박스는 모델이 본 회색 배경 사진에 그립니다(저장된 원본도 필요하면 `--originals`). 설정은 `ON_PREDICT_SEG_MODEL/_IMGSZ/_CONF/_GROW`를 따르고, `ON_PREDICT_SEG=0`이어도 `--seg`를 주면 켜집니다. 신발을 못 찾은 사진은 워커와 같이 저장된 그대로 예측하고 `summary.csv`의 `no_shoe_found` 칸에 카메라를 적습니다. 끝날 때 카메라별 배경 제거 수를 로그, `manifest.json`, `README.txt`에 남깁니다. 분할 모델 파일이 없으면 실행하지 않습니다.
- 옵션: `--originals`(저장된 원본 사진도 복사), `--max-side 0`(박스 그린 사진도 원본 크기), `--conf`, `--imgsz`, `--device`, `--limit N`(시험용).
- 중간에 멈춰도 **같은 명령을 다시** 실행하면 끝난 세트는 건너뜁니다(모델·conf·imgsz가 같을 때). 처음부터 다시 하려면 `--redo`.
- 속도: 이 PC(RTX 4060)에서 세트당 약 1초, Mac(mps)은 세트당 약 3초 안팎으로 예상.
