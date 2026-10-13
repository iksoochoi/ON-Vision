# 수집 로그

## 형식과 읽는 법 (`logs/capture.log`)
이미지 저장이 동작할 때마다 **한 줄씩** 기록합니다. 자동 캡처는 신발 한 번(전·중·후)당 한 줄, 수동 캡처는 한 번당 한 줄입니다. 설치 서비스의 앱 로그(`labeler.log`)와 같은 `logs/` 폴더에 있고, `tail -f logs/capture.log`로 실시간으로 볼 수 있습니다.
```
2026-10-12 10:40:16.098 PARTIAL auto   id=20261012_104015_587 pass=20261012_104100_000-t2 cuts=3 cameras=4/5 images=12/15 shoe=- took=0.5s | top 3/3 front 3/3 back 0/3 [offline] left 3/3 right 3/3 | run: captures=3 images=32 failed_images=3 shoes=1 dropped=0 skipped=0 | total: sets=3 cuts=7 images=32 shoes=1 sets_without_shoe_id=2
```
- **결과**: `SAVED`(모든 카메라 사진 저장) · `PARTIAL`(일부 카메라 사진이 빠짐) · `FAILED`(하나도 저장 못 함) · `DROPPED`(저장 대기열이 가득 차 버려짐) · `SKIPPED`(기준 카메라 사진이 없어 건너뜀). 저장하지 못한 요청도 남기므로 로그가 모든 캡처를 보여줍니다. 앱을 시작하면 `START` 줄이 찍힙니다.
- **카메라별**: `back 0/3 [offline]`은 컷 3개 중 0개가 저장됐고 이유가 offline이라는 뜻입니다. 이유는 `offline`, `no frame yet`, `stale frame (-540 ms)`, `save failed: ...`(디스크 오류 등) 등입니다. `cameras=4/5`는 모든 컷의 사진이 저장된 카메라 수, `images=12/15`는 저장된 사진 수 / 있어야 할 사진 수입니다.
- **신발**: `shoe="test1234TEST"(capture)`는 QR 값과 어디서 읽었는지(`capture`, `live`, `capture_zoom`, `live_late`)이고, 못 읽으면 `shoe=-`입니다. `took`는 저장에 걸린 시간입니다(QR을 기다린 시간 포함).
- **`run:`** 이번 실행의 누계(캡처 수, 저장한 이미지 수, 실패한 이미지 수, 읽은 신발 수(중복 제외), 버려진/건너뛴 수). 앱을 다시 시작하면 0부터 셉니다.
- **`total:`** 지금 디스크에 저장되어 있는 전체 현황. `sets`=신발이 한 번 지나간 세트, `cuts`=그중 한 시점, `images`=이미지, `shoes`=서로 다른 신발(QR 값)의 수, `sets_without_shoe_id`=QR을 못 읽은 세트. 사진을 지우면 줄어듭니다.
- 파일이 `ON_CAPTURE_LOG_MAX_MB`(기본 10MB)가 되면 `capture.log.1`로 넘어가고 5개까지 보관합니다. `ON_CAPTURE_LOG=0`이면 기록하지 않고, 로그를 쓰다 문제가 생겨도 사진 저장에는 영향이 없습니다.
