# logs 폴더 안내

앱과 실행 스크립트가 남기는 로그 파일 모음입니다(위치는 `.env`의 `ON_LOG_DIR`, 기본은 프로젝트의 `logs/`). 이 파일만 git에 올라가고 로그 자체는 올라가지 않습니다. 파일마다 **남기는 내용이 달라서**, 무슨 일이 있었는지 찾을 때는 아래 표로 어느 파일을 볼지 정하세요.

| 알고 싶은 것 | 볼 파일 |
|---|---|
| 카메라가 끊겼는지, 멈췄는지, 언제 돌아왔는지 | `collect.log` (자세한 배경은 `app.log`) |
| 어떤 캡처에서 사진이 빠졌는지, QR이 읽혔는지 | `capture.log` (문제만 모아 보려면 `collect.log`) |
| 알림이 갔는지, Google Chat 전송이 실패했는지 | `collect.log` (`ALERT_SENT` / `ALERT_FAILED`) |
| 앱이 왜 죽었는지, 파이썬 오류, depthai 오류 | `app.log` (네이티브 충돌은 `fault.log`) |
| 앱이 시작은 했는지, 포트 충돌, 실행 스크립트 문제 | `run.log` |
| 서비스(launchd)가 받은 원본 출력 | `labeler.log`, `ui.log` |
| 밤에 서비스를 멈추고 시작한 기록 | `quiet.log` |
| 불탐지 워커가 한 일(세트별 결과, 오류) | `predict.log` |
| 백업 / 삭제 스크립트가 한 일 | `backup.log` |

## 파일별 내용

### `collect.log`: 데이터 수집 문제 (가장 먼저 볼 파일)
카메라 연결, 캡처 실패, 디스크, 알림처럼 **데이터 수집에 관한 사건만** 한 줄씩 남습니다. 항상 켜져 있고 끌 수 없습니다. 같은 줄이 화면과 `app.log`에도 찍혀서 시간 순서를 함께 볼 수 있습니다.
```
2026-10-15 09:17:54.123 ERROR   CAMERA_STALLED camera=left camera is online but sent no picture for 12.0 s; ...
```
줄 형식: `시각 수준 종류 [camera=이름] 설명 [| 항목=값 ...]`. 수준은 `INFO` / `WARNING` / `ERROR`입니다.

| 종류 | 뜻 | Chat 알림 |
|---|---|---|
| `CAMERA_STALLED` | 카메라는 ONLINE인데 5초 넘게 사진이 안 옴. 같은 시각에 조용해진 다른 카메라, 모든 카메라의 fps / USB 속도 / 마지막 사진 시각 / Mac 부하가 같이 적힘 | O |
| `CAMERA_RESTART` | 카메라가 60초(`ON_HEALTH_RESTART_SEC`) 넘게 멎어 그 카메라만 재시작함(몇 번째 시도인지) | O |
| `CAMERA_SETTINGS_LOST` | 카메라가 설정한 노출을 따르지 않아(예: 수동 8ms인데 사진은 30ms) 설정을 다시 보냄(최대 3번, 처음 한 번만 알림) | O |
| `CAMERA_SETTINGS_IGNORED` | 3번 다시 보내도 따르지 않아 그 카메라를 재시작함 | O |
| `CAMERA_SETTINGS_RESTORED` | 카메라가 설정을 다시 따름 | 알린 문제만 |
| `CAMERA_SETTINGS_REAPPLIED` | 사진이 3초 이상 끊겼다 돌아와 설정을 바로 다시 보냄 | 알리지 않음 |
| `APP_RESTART` | 카메라 재시작이 통하지 않아(2번 해도 멎음, 또는 재시작한 카메라가 60초 안에 ONLINE이 안 됨) 앱 전체를 종료함(launchd가 다시 시작) | O |
| `CAMERA_DOWN` | 카메라가 60초 넘게 OFFLINE | O |
| `CAMERA_FLAPPING` | 10분 안에 3번 OFFLINE이 됨 | O |
| `CAMERA_RECOVERED` | 영상이 다시 옴 / 다시 ONLINE | 알린 문제만 |
| `CAMERA_ONLINE` / `CAMERA_OFFLINE` | 상태가 바뀔 때마다(이유 포함) | 아니오 |
| `CAPTURE_PARTIAL` | 신발 한 번의 캡처에서 일부 카메라 사진이 빠짐(어느 카메라가 왜) | O |
| `CAPTURE_FAILED` | 한 장도 저장하지 못함 | O |
| `CAPTURE_DROPPED` / `CAPTURE_SKIPPED` | 저장이 밀려 버려짐 / 기준 카메라 사진이 없어 건너뜀 | O |
| `QR_CONFLICT` | 한 신발 캡처에서 **서로 다른 QR 값**이 읽힘. 이웃 신발의 QR이 화면에 걸렸을 가능성. 어느 값을 골랐는지, 어떤 값을 뺐는지 적힘 | 아니오 |
| `DISK_STOP` | 디스크가 가득 차서 자동 캡처가 꺼짐 | O |
| `AUTO_CAPTURE` | 시작할 때 자동 캡처가 켜졌는지 | 아니오 |
| `ABNORMAL_RESTART` | 앱이 지난번에 정상 종료 없이 끝났음(충돌, `kill -9`, 정전) | O |
| `APP_ERROR` | 스레드나 프로그램이 예외로 죽음 | O |
| `APP_START` / `APP_STOP` | 시작 / 종료 | 아니오 |
| `STATUS` | 60초마다 카메라별 fps, 마지막 사진 시각, Mac 부하 한 줄. 문제 직전 몇 분의 추이를 보는 용도 | 아니오 |
| `ALERT_SENT` / `ALERT_FAILED` | Google Chat 전송 결과 | 아니오 |

- 크기: 10MB(`ON_COLLECT_LOG_MAX_MB`)마다 `collect.log.1`로 넘어가고 5개 보관.

### `capture.log`: 캡처 한 번마다 한 줄
신발이 한 번 지나갈 때(전·중·후)나 수동 캡처마다 **무엇이 저장됐는지**를 남깁니다. 성공도 포함되므로 문제가 없는 날에도 계속 쌓입니다. 문제가 생긴 줄은 `PARTIAL` / `FAILED` / `DROPPED` / `SKIPPED`로 표시되고 `collect.log`에도 따로 남습니다.
```
2026-10-12 10:40:16.098 PARTIAL auto id=... pass=... cuts=3 cameras=4/5 images=12/15 shoe=- qr_px=right:58(not-read) took=0.5s | top 3/3 front 3/3 back 0/3 [offline] ... | run: ... | total: ...
```
카메라별 저장 개수와 빠진 이유, QR 값과 읽은 곳(`capture` / `live` / `capture_zoom` / `live_late`), QR 크기(px), 다른 신발 값(`other_codes=`), 이번 실행 누계(`run:`), 디스크 전체 현황(`total:`). 읽는 법은 [docs/capture-log.md](../docs/capture-log.md). `ON_CAPTURE_LOG=0`으로 끌 수 있고(문제 기록은 `collect.log`에 계속 남음), 10MB(`ON_CAPTURE_LOG_MAX_MB`)마다 넘어가 5개 보관.

### `app.log`: 앱이 화면에 찍는 모든 것
앱이 내는 모든 출력에 **시각을 붙여** 남깁니다: 카메라 연결, 트리거(신발 감지·캡처 판단), 저장 알림, QR, 오류의 traceback, **depthai 같은 라이브러리가 직접 쓰는 메시지**(`X_LINK_ERROR`, `Device ... has crashed`, 재연결). 카메라가 왜 끊겼는지 원인을 찾을 때 `collect.log`의 시각 주변을 여기서 봅니다. 양이 많고, 20MB(`ON_APP_LOG_MAX_MB`)마다 `app.log.1`로 넘어가 5개 보관. `ON_APP_LOG=0`으로 끌 수 있습니다.

### `run.log`: 실행 스크립트가 한 일
`bin/run.sh`가 남깁니다(수동 실행과 서비스 시작 모두). 시작, 이전 실행 중인 앱 정리(포트), 파이썬이나 `.venv`가 없을 때 같은 **시작 단계의 문제**입니다. 앱이 뜬 뒤의 일은 여기 없습니다. 작습니다.

### `fault.log`: 네이티브 충돌 스택
파이썬 밖(depthai 등 C 코드)에서 앱이 치명적으로 죽었을 때(segfault)의 스택만 남깁니다. 평소에는 비어 있거나 없습니다. 이 파일이 생겼다면 `app.log`의 마지막 줄들과 함께 보세요.

### `quiet.log`, `backup.log`: 밤 정지와 백업 기록
- `quiet.log`: `install_launchd.sh quiet-install`을 쓸 때, 조용한 시간(기본 23:00~06:00)에 앱을 멈추고 시작한 기록(시각 포함).
- `backup.log`: `bin/capture_backup.sh`의 출력(백업한 날짜, 세트와 이미지 수, 용량, zip 이름과 검증, 삭제한 파일 수). `--delete`로 지운 양도 여기에 남습니다.

### `predict.log`: 불량 탐지 워커
`install_launchd.sh predict-install`로 설치한 워커(`backend/predict/worker.py`)의 출력입니다. 시작할 때 모델·장치, 세트마다 한 줄(걸린 시간, 검출 개수, DEFECT/ok/ERROR). 시각이 붙어 있고 크기 제한은 없습니다(`logs predict`로 봅니다). 자세히는 [docs/predict.md](../docs/predict.md).

### `labeler.log`, `ui.log`: 서비스(launchd) 원본 출력
`bin/install_launchd.sh`로 설치한 서비스가 받은 **원본 출력**입니다. `labeler.log`는 앱 서비스(시각이 없음, 크기 제한 없음), `ui.log`는 로그인 때 브라우저를 여는 서비스입니다. 시각이 붙은 `app.log`가 있으므로 보통은 볼 필요가 없고, 앱이 `app.log`를 만들기도 전에 죽었을 때 확인합니다.

### `.app_running`: 정상 종료 표시
앱이 실행되는 동안만 있는 빈 표시 파일입니다. 앱이 정상 종료하면 지워지고, 시작할 때 남아 있으면 지난 실행이 비정상 종료였다는 뜻으로 `ABNORMAL_RESTART`를 남깁니다. 직접 지우지 마세요.

## 보는 법
서비스로 돌릴 때:
```bash
./bin/install_launchd.sh logs            # app.log (기본)
./bin/install_launchd.sh logs collect    # collect.log
./bin/install_launchd.sh logs capture    # capture.log
./bin/install_launchd.sh logs run        # run.log
./bin/install_launchd.sh logs service    # labeler.log
```
직접 보기:
```bash
tail -f logs/collect.log                 # 실시간
grep "13:2[0-9]" logs/collect.log        # 특정 시간대
grep -E "WARNING|ERROR" logs/collect.log # 문제만
```
넘어간 파일은 `.1`(가장 최근)부터 `.5`(가장 오래됨)까지 번호가 붙습니다. 알림 설정과 감시 기준은 [docs/logs-and-alerts.md](../docs/logs-and-alerts.md)를 보세요.
