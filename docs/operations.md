# Mac 운영 가이드 (이전, 자동 시작 서비스, 운영 팁)

## 맥으로 옮길 때
- 프로젝트 폴더는 `~/Desktop`, `~/Documents`, `~/Downloads` **밖**에 두세요 (예: `~/apps/on_labeling`). macOS가 이 폴더들을 백그라운드 서비스에서 막습니다.
- `.venv`, `captures`, `exports`, `__pycache__`는 복사하지 마세요. Windows에서 만든 `.venv`는 맥에서 쓸 수 없습니다.
  예: `rsync -a --exclude .venv --exclude captures --exclude exports --exclude __pycache__ <원본>/ <맥>:~/apps/on_labeling/`
- 스크립트가 실행되지 않으면 `chmod +x bin/*.sh`, 줄바꿈 오류(`bad interpreter ... ^M`)가 나면 `sed -i '' 's/\r$//' bin/*.sh`.
- 패키지는 개발 PC에서 검증한 버전(`requirements.txt`)으로 설치됩니다. 맞는 휠이 없으면 버전을 느슨하게(`depthai>=3.0.0` 등) 고치세요.
- Docker는 쓰지 마세요. macOS의 Docker는 컨테이너에 USB 장치(OAK)를 연결하지 못합니다.

## 자동 시작 서비스 (`bin/install_launchd.sh`)
- `install` / `uninstall` / `restart` / `stop` / `start` / `status` / `logs` / `ui` / `quiet-install` / `quiet-uninstall`. 앱 로그는 `logs/labeler.log`.
- **화면 자동 표시**: `install`은 앱 서비스(`com.on.labeling`)와 함께, 로그인할 때 브라우저로 실시간 화면(`http://localhost:8090/`)을 여는 작은 서비스(`com.on.labeling.ui`, 로그 `logs/ui.log`)도 설치합니다. 앱이 응답할 때까지(최대 3분) 기다렸다가 한 번만 엽니다. 앱이 재시작되어도 다시 열지 않습니다(열린 화면은 알아서 다시 연결됩니다). `./bin/install_launchd.sh ui`로 지금 바로 열 수도 있습니다.
  - `.env`의 `ON_OPEN_BROWSER=0`이면 화면을 열지 않고, `ON_BROWSER_APP`으로 브라우저를 고르며(비우면 기본 브라우저, 예: `Safari`, `Google Chrome`), `ON_BROWSER_KIOSK=1`이면 주소창 없는 전체화면으로 엽니다(Chrome 계열만, 별도 프로필 사용, 끄려면 Cmd+Q). 이 값들은 바꿔도 다시 설치할 필요가 없습니다.
- LaunchAgent라서 **로그인된 세션에서만** 실행됩니다. 재부팅 후 자동 실행하려면 시스템 설정 → 사용자 및 그룹 → 자동 로그인을 켜세요. 화면도 그 데스크톱에 열립니다.
- **프로젝트가 `~/Desktop`, `~/Documents`, `~/Downloads` 아래에 있으면 서비스가 시작되지 못합니다.** 로그에 `Operation not permitted`가 반복되고 `status`에 종료 코드 126이 보입니다. 위 "맥으로 옮길 때"처럼 폴더 밖(예: `~/apps/on_data_collector`)으로 옮기고 `./bin/setup_mac.sh`를 다시 실행한 뒤 `install` 하세요.
- 카메라가 아직 인식되지 않았거나 USB가 빠지면 프로세스가 종료되고 10초 뒤 자동으로 다시 시작합니다.
- 서비스가 켜져 있을 때 `./bin/run.sh`를 직접 실행하면 서로 포트를 뺏습니다. 먼저 `./bin/install_launchd.sh uninstall` 하세요.
- 스크립트 위치가 `bin/`으로 바뀐 버전으로 올린 뒤에는 서비스를 한 번 다시 설치하세요: `./bin/install_launchd.sh install`.

## 밤에는 서비스 멈추기 (조용한 시간, `quiet-install`)
작업이 없는 밤(기본 23:00~06:00)에는 앱을 멈춰서 카메라, 감지, QR 스캔의 부하를 없애고, 아침에 다시 시작합니다.
```bash
./bin/install_launchd.sh quiet-install      # 설치 (23:00에 멈추고 06:00에 시작). 지금이 조용한 시간 안이면 바로 멈춥니다
./bin/install_launchd.sh quiet-uninstall    # 되돌리기 (하루 종일 실행. 멈춰 있었다면 앱을 다시 시작)
./bin/install_launchd.sh status             # 조용한 시간 설정과 지금 안인지 밖인지
./bin/install_launchd.sh stop | start       # 앱을 손으로 멈추고 시작
./bin/install_launchd.sh logs quiet         # 멈추고 시작한 기록 (logs/quiet.log)
```
- 시간은 `.env`의 `ON_QUIET_START`(기본 23:00), `ON_QUIET_END`(기본 06:00), 형식 `HH:MM`입니다. **시간을 바꾼 뒤에는 `quiet-install`을 다시 실행**하세요(예약 시각이 설치할 때 정해집니다). 자정을 넘기는 구간도 됩니다.
- 앱은 **정상 종료**로 멈춥니다(충돌이 아니라서 `ABNORMAL_RESTART` 알림이 가지 않음). 로그에는 `APP_STOP`과 아침의 `APP_START`가 남습니다.
- 멈춰 있는 동안에는 **화면(Live, Browse, QR)도 열리지 않습니다.** 06:00에 앱이 다시 뜨면 열려 있던 화면은 알아서 다시 연결됩니다.
- 로그인할 때도 시간에 맞춰 한 번 정리합니다(조용한 시간 안에 로그인하면 앱을 멈춤).
- `./bin/install_launchd.sh install`을 조용한 시간 안에 실행하면 앱이 켜지고 **다음 경계(06:00)까지 그대로** 돕니다(손으로 한 일을 우선합니다).
- **Mac이 잠들면** 06:00 예약은 깨어날 때 실행됩니다. 아침에 제시간에 시작하려면 깨우기 일정을 두세요(시스템 설정 → 에너지 → 일정, 또는 터미널에서 `sudo pmset repeat wakeorpoweron MTWRFSU 05:55:00`. 시스템 설정을 바꾸는 명령이라 직접 실행하세요). 잠자기를 아예 끄면 필요 없습니다.
- 백업과 함께 쓰기: `./bin/capture_backup.sh`는 23:59에 시작하는데, 그 시각에는 앱이 이미 멈춰 있어 **쓰는 중인 세트가 없고 부하도 없는 가장 좋은 때**입니다([capture-backup.md](capture-backup.md)).

## 운영 팁
- 처음 실행하면 "Python이 네트워크 수신 연결을 허용하시겠습니까?" 창이 뜹니다. 다른 PC에서 접속하려면 허용하세요. 이 맥에서만 쓰려면 `.env`에서 `ON_HOST=127.0.0.1`.
- 헤드리스로 쓰면 시스템 설정 → 에너지에서 "디스플레이가 꺼져 있을 때 자동 잠자기 방지"와 "정전 후 자동으로 시동"을 켜는 것을 권합니다.
- OAK는 USB 3 포트에 좋은 케이블로 직접 연결하세요. 카메라를 여러 대 쓸 때는 전원이 있는 허브를 쓰세요.
