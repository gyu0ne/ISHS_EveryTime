# ISHS_EveryTime 개선 작업 리뷰 / 검증 메모 (lane3)

이 문서는 병렬 개선 작업을 리더가 바로 통합할 수 있도록 현재 코드 기준 영향 범위, 검증 포인트, 남은 리스크를 정리한 메모입니다.

## 범위

- lane1: 레벨업 시 포인트 지급, 인곽콘 업로드 제한 100개 상향, 서버/클라이언트 유효성 검증 정리
- lane2: iframe/XSS/매크로 방어 강화, 사진/인곽콘 로드 성능 최적화
- lane3: 변경 영향 분석, 회귀 검증 관점 정리, 수동 점검 체크리스트 수집

## 변경 영향 분석

### lane1 — 레벨업 포인트 지급

핵심 터치포인트:

- `app.py:update_exp_level()`
  - 모든 경험치 증감이 최종적으로 모이는 공용 함수입니다.
  - 현재는 `level`, `exp`만 갱신하고 포인트 지급 부수효과는 없습니다.
- 경험치 증가 호출부
  - `app.py` 게시글 작성: `update_exp_level(author_id, 50)`
  - `app.py` 댓글 작성: `update_exp_level(author_id, 10)`
  - `app.py` 인곽콘 댓글 작성: `update_exp_level(author_id, 10)`
- 경험치 감소 호출부
  - `app.py` 게시글 삭제: `update_exp_level(post['author'], -50, False)`
  - `app.py` 댓글/답글 삭제: `update_exp_level(comment['author'], -10)`, `update_exp_level(reply['author'], -10)`

리뷰 메모:

- 포인트 지급 로직은 호출부마다 흩뿌리기보다 `update_exp_level()` 안에서 "실제 레벨 상승분" 기준으로 계산해야 중복 지급과 회귀를 줄일 수 있습니다.
- 삭제/롤백 경로에서 경험치가 내려가므로, 포인트를 레벨업 때만 지급하고 레벨다운 시 자동 차감하지 않을지 정책을 명확히 해야 합니다. 정책이 없으면 게시글/댓글 삭제 후 포인트 보존 여부가 불명확합니다.
- 현재 코드상 포인트 사용은 상점 구매(`app.py` 인곽콘 구매 시 차감) 쪽이 이미 존재하므로, 포인트 적립 추가 시 사용자 마이페이지/상점 잔액 표시 회귀를 함께 확인해야 합니다.

### lane1 — 인곽콘 업로드 제한 100개 상향

핵심 터치포인트:

- 서버 제한: `app.py` 인곽콘 등록 요청 처리부에서 `len(etacon_files) > 10` 차단
- 등록 UI: `templates/etacon/request.html`
  - 현재 최대 개수 안내 문구가 없습니다.
  - 클라이언트 단 사전 차단 스크립트도 없습니다.

리뷰 메모:

- 서버 제한만 100으로 올리면 UI가 여전히 현재 동작을 설명하지 못합니다.
- 100개 허용 시 업로드 시간/메모리 사용량이 커지므로, 기존처럼 한 번에 Pillow 검증/저장을 수행하는 구조에서 실패 시 사용자 체감 지연이 커질 수 있습니다.
- 최소한 서버/클라이언트 문구와 개수 검증 조건은 함께 맞추는 편이 안전합니다.

### lane2 — iframe / XSS / 매크로 방어

핵심 터치포인트:

- 게시글 작성 sanitization: `app.py` 일반 글 작성, 게스트 글 작성
- 게시글 수정 sanitization: `app.py` 일반 글 수정, 게스트 글 수정
- 댓글 작성/수정 sanitization: `app.py` 댓글 작성, 댓글 수정
- 렌더링: `templates/post_detail.html`
- 공통 레이아웃: `templates/base.html`

리뷰 메모:

- HTML sanitize 정책이 여러 엔드포인트에 중복돼 있고, 댓글 쪽은 `bleach.clean(content)` 수준으로 더 약합니다. iframe 출처 제한을 추가할 때 공통 정책으로 묶지 않으면 누락 가능성이 큽니다.
- 게시글 경로는 현재 `iframe` 태그와 `http/https/data` 프로토콜을 넓게 허용합니다. 허용 출처 최소화가 목적이면 `src` 도메인 검증이 별도로 필요합니다.
- 저장형 XSS 방어 수준이 엔드포인트마다 달라 lane2 작업 후에도 작성/수정/게스트/댓글 경로가 동일 규칙을 쓰는지 재확인해야 합니다.
- 저장소 검색 기준, 전역 보안 헤더(`Content-Security-Policy`, `X-Frame-Options`, `X-Content-Type-Options`, `Referrer-Policy`)를 세팅하는 `after_request` 훅은 현재 없습니다.
- 요청 빈도 제한 관련 코드(`Flask-Limiter` 등)도 현재 없습니다. 저비용 방어를 넣을 경우 로그인/댓글/글쓰기/API성 엔드포인트에 집중하는 편이 충돌을 줄입니다.

### lane2 — 사진 / 인곽콘 로드 성능

핵심 터치포인트:

- `templates/post_detail.html`
  - 프로필 이미지, 댓글 이미지, 인곽콘 이미지에 `loading="lazy"`가 없습니다.
- `templates/main_logined.html`, `templates/my_page.html`, `templates/profile.html`
  - 프로필 이미지가 즉시 로드됩니다.
- `templates/etacon/shop.html`
  - 상세 인곽콘 이미지 쪽에는 이미 `loading="lazy"`가 적용돼 있습니다.
- `templates/base.html`
  - 알림 드롭다운은 초기 unread-count fetch + SSE + 클릭 시 fetch/read 요청이 섞여 있어, 성능 작업 시 기능 회귀 검증이 필요합니다.

리뷰 메모:

- 이미지 lazy loading은 템플릿 단에서 비교적 안전하게 적용 가능하지만, 인곽콘 모달/댓글 렌더링처럼 즉시 보여야 하는 이미지까지 지연시키면 체감이 나빠질 수 있습니다.
- `templates/etacon/shop.html`에 이미 lazy loading 사용 예시가 있어 동일 패턴을 재사용하는 것이 가장 충돌이 적습니다.

## 통합 전 회귀 검증 체크리스트

### 자동 검증

1. `python3 -m compileall app.py route`
   - Python 문법/기본 import 회귀 확인
2. 가능하면 `pytest`
   - 현재 저장소에는 테스트 파일이 보이지 않아, 없으면 "테스트 스위트 부재"로 보고
3. 변경 파일 대상 정적 확인
   - iframe 허용 출처 목록이 작성/수정/게스트/댓글 경로에 모두 반영됐는지 grep으로 확인
   - 인곽콘 업로드 개수 제한 숫자가 서버/클라이언트 문구에서 일치하는지 확인

### 수동 점검

1. 게시글 작성 후 레벨업 경계값(예: 950 EXP → +50 EXP)에서 포인트가 1회만 지급되는지 확인
2. 레벨업 직후 게시글/댓글 삭제 시 레벨/EXP/포인트 정책이 의도대로 유지되는지 확인
3. 인곽콘 1개, 10개, 100개 업로드 각각에서 검증/실패 메시지가 일관적인지 확인
4. 허용된 iframe은 렌더링되고, 비허용 출처 iframe/script/event handler는 저장 또는 렌더링 단계에서 제거되는지 확인
5. 댓글/게스트 댓글/게시글 수정에서도 동일한 sanitization 결과가 나오는지 확인
6. 게시글 상세/마이페이지/프로필/인곽콘 상점에서 이미지 표시 깨짐 없이 lazy loading이 적용되는지 확인
7. 알림 드롭다운, SSE 알림, 좋아요/댓글/인곽콘 댓글 작성이 기존처럼 동작하는지 확인

## 남은 리스크

- `app.py` 단일 파일 구조라 lane1/lane2가 같은 함수군을 동시에 건드릴 가능성이 높습니다. 특히 게시글/댓글 sanitization 주변은 충돌 위험이 큽니다.
- 포인트 지급 정책이 명확하지 않으면 "레벨업 보상 중복" 또는 "삭제 후 보상 유지/회수" 논쟁이 남습니다.
- 100개 업로드 허용은 기능적으로 단순해 보여도 처리 시간과 메모리 사용량이 증가하므로, 운영 환경에서 요청 시간 초과 여부를 추가 확인해야 합니다.
- 보안 강화가 게시글 작성 UX를 깨뜨릴 수 있으므로, 허용 iframe 목록 변경 시 YouTube 등 실제 허용 대상 샘플로 반드시 수동 확인이 필요합니다.

## 권장 통합 순서

1. lane1의 서버/클라이언트 숫자 상수 및 레벨업 포인트 정책 정리
2. lane2의 sanitize 공통화 또는 최소한 허용 정책 일치화
3. lane2의 lazy loading 적용
4. lane3 체크리스트 기준 자동/수동 검증 후 통합
