# Mac 운영 가이드 (이전, 자동 시작 서비스, 운영 팁)

## 맥으로 옮길 때
- 프로젝트 폴더는 `~/Desktop`, `~/Documents`, `~/Downloads` **밖**에 두세요 (예: `~/apps/on_labeling`). macOS가 이 폴더들을 백그라운드 서비스에서 막습니다.
- `.venv`, `captures`, `exports`, `__pycache__`는 복사하지 마세요. Windows에서 만든 `.venv`는 맥에서 쓸 수 없습니다.
  예: `rsync -a --exclude .venv --exclude captures --exclude exports --exclude __pycache__ <원본>/ <맥>:~/apps/on_labeling/`
- 스크립트가 실행되지 않으면 `chmod +x bin/*.sh`, 줄바꿈 오류(`bad interpreter ... ^M`)가 나면 `sed -i '' 's/\r$//' bin/*.sh`.
- 패키지는 개발 PC에서 검증한 버전(`requirements.txt`)으로 설치됩니다. 맞는 휠이 없으면 버전을 느슨하게(`depthai>=3.0.0` 등) 고치세요.
- Docker는 쓰지 마세요. macOS의 Docker는 컨테이너에 USB 장치(OAK)를 연결하지 못합니다.

## 자동 시작 서비스 (`bin/install_launchd.sh`)
- `install` / `uninstall` / `restart` / `status` / `logs` / `ui`. 앱 로그는 `logs/labeler.log`.
- **화면 자동 표시**: `install`은 앱 서비스(`com.on.labeling`)와 함께, 로그인할 때 브라우저로 실시간 화면(`http://localhost:8090/`)을 여는 작은 서비스(`com.on.labeling.ui`, 로그 `logs/ui.log`)도 설치합니다. 앱이 응답할 때까지(최대 3분) 기다렸다가 한 번만 엽니다. 앱이 재시작되어도 다시 열지 않습니다(열린 화면은 알아서 다시 연결됩니다). `./bin/install_launchd.sh ui`로 지금 바로 열 수도 있습니다.
  - `.env`의 `ON_OPEN_BROWSER=0`이면 화면을 열지 않고, `ON_BROWSER_APP`으로 브라우저를 고르며(비우면 기본 브라우저, 예: `Safari`, `Google Chrome`), `ON_BROWSER_KIOSK=1`이면 주소창 없는 전체화면으로 엽니다(Chrome 계열만, 별도 프로필 사용, 끄려면 Cmd+Q). 이 값들은 바꿔도 다시 설치할 필요가 없습니다.
- LaunchAgent라서 **로그인된 세션에서만** 실행됩니다. 재부팅 후 자동 실행하려면 시스템 설정 → 사용자 및 그룹 → 자동 로그인을 켜세요. 화면도 그 데스크톱에 열립니다.
- **프로젝트가 `~/Desktop`, `~/Documents`, `~/Downloads` 아래에 있으면 서비스가 시작되지 못합니다.** 로그에 `Operation not permitted`가 반복되고 `status`에 종료 코드 126이 보입니다. 위 "맥으로 옮길 때"처럼 폴더 밖(예: `~/apps/on_data_collector`)으로 옮기고 `./bin/setup_mac.sh`를 다시 실행한 뒤 `install` 하세요.
- 카메라가 아직 인식되지 않았거나 USB가 빠지면 프로세스가 종료되고 10초 뒤 자동으로 다시 시작합니다.
- 서비스가 켜져 있을 때 `./bin/run.sh`를 직접 실행하면 서로 포트를 뺏습니다. 먼저 `./bin/install_launchd.sh uninstall` 하세요.
- 스크립트 위치가 `bin/`으로 바뀐 버전으로 올린 뒤에는 서비스를 한 번 다시 설치하세요: `./bin/install_launchd.sh install`.

## 운영 팁
- 처음 실행하면 "Python이 네트워크 수신 연결을 허용하시겠습니까?" 창이 뜹니다. 다른 PC에서 접속하려면 허용하세요. 이 맥에서만 쓰려면 `.env`에서 `ON_HOST=127.0.0.1`.
- 헤드리스로 쓰면 시스템 설정 → 에너지에서 "디스플레이가 꺼져 있을 때 자동 잠자기 방지"와 "정전 후 자동으로 시동"을 켜는 것을 권합니다.
- OAK는 USB 3 포트에 좋은 케이블로 직접 연결하세요. 카메라를 여러 대 쓸 때는 전원이 있는 허브를 쓰세요.
