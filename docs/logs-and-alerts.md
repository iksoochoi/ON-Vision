# 로그와 알림 (데이터 수집 문제 추적)

카메라가 끊기거나 영상이 멈추는 것, 사진이 빠진 채 저장되는 것 같은 **데이터 수집 문제가 생겼을 때 그 순간의 상태를 남기고 Google Chat으로 알려 주는** 기능입니다. 문제를 알리는 것에 더해, **영상이 멈춘 카메라는 그 카메라만 자동으로 재시작**합니다(`ON_HEALTH_RESTART_SEC`, 아래 "자동 재시작"). `ON_HEALTH_RESTART_SEC=0`이면 예전처럼 감시만 하고 아무것도 고치지 않습니다.

## 로그 파일 (`logs/` 폴더)
`bin/run.sh`로 실행하든 `install_launchd.sh` 서비스로 실행하든 똑같이 남습니다. 폴더는 `.env`의 `ON_LOG_DIR`로 바꿉니다.

| 파일 | 내용 | 크기 관리 |
|---|---|---|
| `app.log` | 앱이 화면에 찍는 **모든 것**(시각 포함). depthai 같은 라이브러리가 직접 쓰는 오류와 예외 기록(traceback)도 들어갑니다 | 20MB씩, 5개 보관 (`ON_APP_LOG_MAX_MB`) |
| `collect.log` | **데이터 수집 문제만** 한 줄씩(아래 표). 별도 파일 | 10MB씩, 5개 보관 (`ON_COLLECT_LOG_MAX_MB`) |
| `capture.log` | 캡처 한 번마다 한 줄 ([capture-log.md](capture-log.md)) | 10MB씩, 5개 보관 |
| `run.log` | `run.sh`가 한 일(시작, 포트 정리, 오류) | 작음 |
| `fault.log` | 앱이 네이티브 코드에서 치명적으로 죽었을 때(segfault) 남는 스택 | 작음 |
| `labeler.log` | 서비스(launchd)가 받은 원본 출력. 예전과 같고, 시각이 없음 | 계속 커짐 |

각 파일에 **무엇이 남는지**는 [`logs/README.md`](../logs/README.md)에 정리되어 있습니다.

보는 법(서비스로 돌릴 때):
```bash
./bin/install_launchd.sh logs            # app.log (기본)
./bin/install_launchd.sh logs collect    # 수집 문제만
./bin/install_launchd.sh logs capture    # 캡처 기록
./bin/install_launchd.sh logs run        # 시작 스크립트
```
`app.log`를 끄려면 `ON_APP_LOG=0`입니다.

## collect.log의 줄
```
2026-10-15 09:17:54.123 ERROR   CAMERA_STALLED camera=left camera is online but sent no picture for 12.0 s; silent at the same time: front | usb=SUPER fps=0.0/0.0
```

| 종류 | 뜻 | 알림 |
|---|---|---|
| `CAMERA_STALLED` | 카메라는 ONLINE인데 `ON_HEALTH_STALL_SEC`(5초) 넘게 새 사진이 없음. **같은 시각에 조용해진 다른 카메라**, 모든 카메라의 fps / USB 속도 / 마지막 사진 시각 / Mac 부하가 같이 적힘 | O |
| `CAMERA_RESTART` | 카메라가 `ON_HEALTH_RESTART_SEC`(60초) 넘게 멎어 **그 카메라만 재시작**함. 몇 번째 시도인지 적힘 | O |
| `APP_RESTART` | 재시작을 `ON_HEALTH_RESTART_MAX`(3)번 해도 돌아오지 않아 **앱 전체를 종료**함(launchd가 다시 시작) | O |
| `CAMERA_DOWN` | 카메라가 `ON_HEALTH_OFFLINE_SEC`(60초) 넘게 OFFLINE | O |
| `CAMERA_FLAPPING` | 10분 안에 3번 OFFLINE이 됨 | O |
| `CAMERA_RECOVERED` | 영상이 다시 옴 / 다시 ONLINE | 알린 문제만 |
| `CAMERA_ONLINE` / `CAMERA_OFFLINE` | 상태가 바뀔 때마다(이유 포함). 설정 변경으로 인한 재시작은 INFO | 아니오 |
| `CAPTURE_PARTIAL` | 신발 한 번의 캡처에서 **일부 카메라 사진이 빠짐**. 어느 카메라가 왜(`offline`, `stale frame ...`) 빠졌는지 | O |
| `CAPTURE_FAILED` | 한 장도 저장하지 못함 | O |
| `CAPTURE_DROPPED` / `CAPTURE_SKIPPED` | 저장이 밀려 버려졌음 / 기준 카메라 사진이 없어 건너뜀 | O |
| `QR_CONFLICT` | 한 신발 캡처에서 서로 다른 QR 값이 읽힘(이웃 신발의 QR 가능성). 고른 값과 뺀 값이 적힘 | 아니오 |
| `DISK_STOP` | 디스크가 가득 차서 자동 캡처가 꺼짐 | O |
| `ABNORMAL_RESTART` | 앱이 지난번에 **정상 종료 없이** 끝났음(충돌, `kill -9`, 정전) | O |
| `APP_ERROR` | 스레드나 프로그램이 예외로 죽음 | O |
| `APP_START` / `APP_STOP` | 시작 / 종료 | 아니오 |
| `STATUS` | `ON_HEALTH_STATUS_SEC`(60초)마다 카메라별 fps와 마지막 사진 시각, Mac 부하 한 줄. **문제 직전 몇 분의 추이**를 보는 용도 | 아니오 |
| `ALERT_SENT` / `ALERT_FAILED` | 알림 전송 결과(알림 자체의 기록이라 알림은 보내지 않음) | 아니오 |

`STATUS` 줄로 "멈추기 전에 fps가 떨어졌는가", `CAMERA_STALLED`로 "어느 카메라가 함께 멈췄는가"를 볼 수 있습니다. 함께 멈추는 카메라는 같은 USB 허브/컨트롤러를 쓸 가능성이 큽니다.

## Google Chat 알림 설정
### 1. 웹훅 주소 만들기
알림을 받을 Google Chat 스페이스에서 만듭니다(메뉴 이름은 Chat 버전이나 조직 설정에 따라 조금 다를 수 있고, 관리자가 웹훅을 막아 둔 조직에서는 만들 수 없습니다).
1. 스페이스 이름을 눌러 메뉴를 엽니다.
2. **앱 및 통합**(Apps & integrations) → **웹훅**(Webhooks)을 고릅니다.
3. 웹훅을 추가하고 이름(예: ON-Vision)을 정한 뒤 저장합니다.
4. 만들어진 **URL을 복사**합니다. `https://chat.googleapis.com/v1/spaces/.../messages?key=...&token=...` 모양입니다.

이 URL에는 키와 토큰이 들어 있어서, **아는 사람은 누구나 그 스페이스에 글을 올릴 수 있습니다.** 비밀로 다루세요. 앱은 이 주소를 로그나 화면에 절대 쓰지 않습니다.

### 2. `.env`에 넣기
`.env`는 git에 올라가지 않으므로 장비(Mac)마다 직접 넣습니다.
```
ON_ALERT=1
ON_ALERT_WEBHOOK_URL="https://chat.googleapis.com/v1/spaces/.../messages?key=...&token=..."
# ON_ALERT_PREFIX=[ON-Vision]               # 메시지 첫 줄 앞에 붙는 말
# ON_ALERT_COOLDOWN_SEC=1800                # 같은 문제는 이 시간에 한 번만 알림
# ON_ALERT_DIGEST_SEC=60                    # 이 시간 안의 문제는 한 메시지로 묶음
```
주소는 큰따옴표로 감싸세요(`&`와 `#`가 들어 있어도 안전합니다). 바꾼 뒤 앱을 다시 시작합니다.

### 3. 시험 문구 보내기
Mac의 프로젝트 폴더에서, 앱 설정(`.env`)을 그대로 읽어 보냅니다.
```bash
.venv/bin/python -m backend.cli.test_alert
```
표준 시험 메시지가 스페이스에 올라옵니다. **원하는 문구**를 보내려면 뒤에 적으세요.
```bash
.venv/bin/python -m backend.cli.test_alert "ON-Vision 알림 시험입니다"
```
성공하면 `Sent.`, 실패하면 이유(`HTTP 404 ...`, 연결 오류 등)를 보여 줍니다. 주소는 출력하지 않습니다.

앱과 상관없이 웹훅 주소만 확인하려면 `curl`로도 보낼 수 있습니다(주소는 큰따옴표로 감싸세요).
```bash
curl -X POST -H "Content-Type: application/json; charset=UTF-8" -d '{"text":"ON-Vision 시험 메시지"}' "웹훅주소"
```

## 알림이 폭주하지 않게 하는 규칙
- **같은 문제는 30분에 한 번**(`ON_ALERT_COOLDOWN_SEC`). 그 사이 반복은 세어 두었다가 다음 메시지에 "+12 similar since the last message"로 알려 줍니다.
- 1분 안에 생긴 문제들은 **한 메시지**로 묶입니다(`ON_ALERT_DIGEST_SEC`). 메시지 끝에는 그 순간 모든 카메라의 상태표가 붙습니다. 길면 여러 메시지로 나눕니다(Google Chat 길이 제한 때문).
- **복구 메시지**는 문제 메시지를 보낸 경우에만 갑니다.
- 웹훅이 안 되면 30초, 2분, 10분 뒤에 다시 시도하고, 그래도 안 되면 `collect.log`에 `ALERT_FAILED`로 남기고 다음 메시지에 합칩니다. 알림 문제 때문에 수집이 느려지는 일은 없습니다(별도 스레드).
- 웹훅 주소(키와 토큰)는 어떤 로그에도 남지 않습니다. 서버가 오류 응답에 주소를 되돌려 줘도 가려서 기록합니다.

## 자동 재시작
카메라가 ONLINE인데 영상이 오지 않는 상태(`CAMERA_STALLED`)가 `ON_HEALTH_RESTART_SEC`(기본 60초) 이어지면 다음 순서로 복구합니다.
1. **그 카메라만 재시작**: 파이프라인을 닫고 다시 엽니다. 다른 카메라와 캡처는 그대로 돕니다. 재시작이 끝날 때까지(수 초~십수 초) 그 카메라의 사진은 캡처에서 빠집니다(`CAPTURE_PARTIAL`). `CAMERA_RESTART`가 로그와 Chat에 남습니다.
2. 그래도 멎어 있으면 **30초마다 다시** 시도합니다.
3. **10분 안에 `ON_HEALTH_RESTART_MAX`(3)번** 재시작했는데도 멎으면 **앱 전체를 종료**하고 `APP_RESTART` 알림을 보냅니다. 서비스(`install_launchd.sh`)로 돌릴 때는 launchd가 10초 뒤 다시 시작합니다. `./bin/run.sh`로 직접 돌린 경우에는 다시 시작되지 않으니 서비스를 쓰세요. 이때 저장 대기 중이던 캡처는 사라질 수 있고, 다음 시작에서 `ABNORMAL_RESTART`가 한 번 더 남는 것이 정상입니다.
- 멎은 장치를 닫는 호출 자체가 끝나지 않는 경우(드라이버가 멈춤)에도 3번 시도 뒤 앱 종료로 이어지므로, 최대 약 2분 반 안에 복구됩니다.
- OFFLINE인 카메라는 이 기능이 아니라 카메라 스레드가 5초마다 다시 연결을 시도합니다(`CAMERA_DOWN`은 60초 넘게 OFFLINE일 때 알림).
- 값: `ON_HEALTH_RESTART_SEC`(0이면 끔, 신발 간격이 8~9초라면 20~30초도 좋음), `ON_HEALTH_RESTART_MAX`.

## 감시 기준 조정
`ON_HEALTH_STALL_SEC`(기본 5), `ON_HEALTH_OFFLINE_SEC`(60), `ON_HEALTH_INTERVAL_SEC`(2), `ON_HEALTH_STATUS_SEC`(60, 0이면 STATUS 줄 없음). 영상이 잠깐 끊기는 것까지 잡고 싶으면 `ON_HEALTH_STALL_SEC`를 줄이세요.
