# backend/predict: 불량 탐지 (테스트 단계)

`backend/models/defect_detection/best.pt`(YOLO11l, PoC 분사/인쇄 불량 데이터로 학습, mAP50 0.31)로 사진을 검사합니다. **카메라 앱은 이 폴더를 읽지 않으므로** 수집 성능에 영향이 없습니다.

- `worker.py`: 저장된 세트를 자동으로 검사하는 **별도 프로세스**(launchd 서비스). 결과는 `captures/.predict/`에 저장되고 웹의 Predict 메뉴에 나옵니다. 설치와 설정은 [docs/predict.md](../../docs/predict.md).
- `run_folder.py`, `bench.py`: 카메라 없이 PC/Mac에서 시험하는 도구(아래).
- `engine.py`: 위 셋이 같이 쓰는 모델 로드/추론/그리기.

## 설치 (카메라 앱과 별도)
```bash
.venv/bin/python -m pip install -r backend/predict/requirements.txt   # torch도 함께 설치됨(약 1~2GB)
```
Ultralytics는 AGPL-3.0입니다. 상용 사용 전에 검토하세요.

## 폴더 검사 (PC에서 시험)
```bash
python backend/predict/run_folder.py "captures/2026-10-20 Sample" [--device auto|cpu|mps|0] [--imgsz 1280] [--conf 0.25] [--limit N]
```
폴더(하위 폴더 포함)는 읽기만 합니다. 결과는 `exports/predict/<폴더명>_<시각>/`(git 제외)에 박스가 그려진 사진, `results.json`, `results.csv`로 저장됩니다.

## 속도 측정 (Mac Mini에서 실행해 결과를 알려 주세요)
```bash
.venv/bin/python backend/predict/bench.py --src captures --n 5 --runs 3 --devices cpu,mps --imgsz 1280,960,640 --threads 0,2 --out bench.json
```
장치(cpu/mps), 입력 크기, torch 스레드 수별로 사진 1장 시간, 한 세트(5장) 시간, CPU 사용률(%, 코어 1개 = 100), 메모리를 표로 보여 줍니다. 신발은 약 10초마다 지나가므로 세트 시간이 그보다 충분히 짧아야 합니다.
