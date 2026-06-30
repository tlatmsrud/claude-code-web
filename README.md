# Claude Code Web For MTG

Streamlit 기반의 Claude Code CLI 웹 래퍼. 터미널 대신 브라우저에서 Claude Code를 사용한다.

## 어떤 서비스인가

- 로컬에 설치된 `claude` CLI를 `subprocess`로 호출해 채팅 UI로 감싼 단일 페이지 웹앱
- 한 턴 = `claude -c --dangerously-skip-permissions -p "USER INPUT" --output-format json` 한 번
- 비스트리밍 방식 — Claude가 응답을 다 만든 뒤에 한 번에 화면에 표시
- 사이드바에서 작업 디렉터리(`cwd`) 선택, 토큰/비용 누적 표시, 대화 초기화 가능
- `/` 입력 시 사용 가능한 빌트인 명령 + 사용자/프로젝트/플러그인 스킬·커맨드를 자동완성 팝업으로 보여줌

## 왜 만들었나

- **사내망 이슈**: 사내 방화벽에서 클로드 서버에 대한 스트리밍 응답을 위변조하고 있어 서비스 사용이 불가.

## 사용 방법

### 1) 사전 준비

- macOS + `claude` CLI 설치 + 로그인 완료 (`claude login`)
- Python 3.10+
- 사이드바 좌상단의 초록색 점이 켜져 있어야 정상 (노랑/빨강이면 메시지 참고)

### 2) 실행

```bash

- run.sh 실행 시 `.venv` 생성 + `streamlit` 설치 + `서비스실행` 됨.
- 기본 포트 `8501` → http://localhost:
  
  * 단, run.sh 오류가 발생할 경우 아래 방식으로 수동 실행

1. 가상환경 실행

2. cd claude_code_web

3. pip install -r requirements.txt

4. streamlit run app.py
```

### 3) 기본 흐름

1. 사이드바 **📁 Select folder…** 로 작업 디렉터리(=Claude가 작업할 폴더) 지정
   - 해당 폴더에 최근 세션이 있을 경우, 세션 대화를 이어서 하는 구조임. 새로 시작하고싶다면 /clear 명령어를 입력하거나, clear 버튼 클릭.

2. 하단 입력창에 메시지 입력 → Enter (Shift+Enter 는 줄바꿈)

3. `/` 를 입력하면 빌트인 명령 + 보유 스킬/커맨드 팝업
   - `↑/↓` 이동, `Enter` 선택, `Tab` 자동완성, `Esc` 닫기
4. **Clear conversation** 으로 세션 리셋 (다음 메시지부터 `-c` 없이 새 세션 시작)

### 4) UI 요약

| 위치 | 기능 |
|---|---|
| 사이드바 상단 점 | Claude CLI 상태 (초록=준비, 노랑=인증 미감지, 빨강=CLI 없음) |
| 사이드바 디렉터리 | 현재 `cwd`, 폴더 선택 시 대화 초기화 |
| 사이드바 토큰 | 누적 input/output/cache_read/cache_write, 턴 수 |
| 본문 | 대화 말풍선 (You / Claude / Error) |
| 화면 하단 입력창 | 메시지 입력, `/` 슬래시 팝업 |

## 주의사항

- ⚠️ **`--dangerously-skip-permissions` 가 항상 켜져 있다.** Claude가 파일 쓰기·셸 실행을 묻지 않고 바로 수행한다. 신뢰하는 작업 디렉터리에서만 사용할 것
- ⚠️ **비스트리밍**: 응답이 다 끝나야 화면에 표시된다. 긴 작업은 수 분 이상 멍 때리는 것처럼 보일 수 있음 (타임아웃 100분)
- ⚠️ **세션 연속성은 `claude -c` 에 의존**한다. CLI 쪽 세션 캐시가 꼬이면 의도와 다른 컨텍스트로 응답할 수 있음 — 그럴 땐 **Clear conversation** 클릭
- ⚠️ **로컬 전용 데모**. 인증 없는 Streamlit 서버 그대로 외부에 노출하면 누구나 당신의 Claude 계정으로, 당신 컴퓨터에서, 권한 스킵 모드로 임의 명령을 실행할 수 있다. **외부 공개 금지**
- 토큰/비용 카운터는 매 턴 CLI가 반환한 `usage` 합산값이라 CLI가 비용을 보고하지 않으면 0으로 표시될 수 있음
- 디렉터리 선택은 macOS `osascript`(AppleScript) 기반 — 다른 OS에서는 동작하지 않음
- `/resume`, `/help` 같은 빌트인 슬래시는 CLI 인터랙티브 모드용이라 `-p` 비대화 모드에서는 동작이 다르거나 무효일 수 있음 (대화 리셋은 사이드바 버튼 사용)
