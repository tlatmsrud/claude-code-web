# 로그인 게이트 디자인

## 목적

`claude` CLI가 인증되어 있지 않거나 설치되어 있지 않으면, 채팅 UI 전체를 사용할 수 없게 가리고 상황별 안내 화면만 보여준다. 사용자가 잘못된 상태에서 메시지를 보내 에러만 받는 일을 막는다.

## 트리거 조건

`check_claude_status()`(`app.py:142`)의 반환값에 따라 게이트를 결정한다.

| status | 동작 | 표시 메시지 |
|---|---|---|
| `green` | 게이트 없음. 기존 UI 그대로 진행 | — |
| `yellow` | 게이트 표시 (로그인 안내) | "Claude에 로그인되어 있지 않습니다. 터미널에서 `claude login`을 실행하세요." |
| `red` | 게이트 표시 (설치 안내) | "`claude` CLI를 찾을 수 없습니다. Claude Code를 먼저 설치하세요." |

## 동작

1. `st.set_page_config(...)` 및 CSS 주입 직후, 세션 상태 초기화 블록(`claude_status` 포함)이 끝난 뒤,
2. 캐시된 `st.session_state.claude_status`가 없으면 `check_claude_status()` 호출하여 저장.
3. status가 `green`이 아니면:
   - 화면 중앙에 안내 카드를 `st.markdown(unsafe_allow_html=True)`로 렌더링한다. (스타일은 기존 다크 테마와 일관, `background: #1f2937`, `border: 1px solid #374151`, 둥근 모서리).
   - 카드 내용:
     - 큰 아이콘/이모지 1개 (yellow: 🔒, red: ⛔)
     - 제목 (위 표의 메시지)
     - 코드 블록으로 다음 단계 (`claude login` 또는 설치 링크 URL 텍스트)
   - "다시 확인" Streamlit 버튼을 카드 아래에 렌더링. 클릭 시:
     ```python
     st.session_state.claude_status = check_claude_status()
     st.rerun()
     ```
   - `st.stop()` 호출 → 사이드바, 본문 채팅 UI, 입력창 등 그 이하 모든 렌더링 코드가 실행되지 않음.
4. green이면 기존 흐름 그대로.

## 화면 차단 보장

- `st.stop()`이 호출되면 그 줄 이후의 모든 Streamlit 위젯(사이드바, 채팅 입력 컴포넌트, 본문 메시지 루프 등)은 렌더되지 않는다.
- 사용자가 클릭/입력할 수 있는 위젯이 "다시 확인" 버튼 하나뿐이 된다 → 자연스럽게 "화면을 만질 수 없는" 상태가 됨.
- HTML 오버레이/`pointer-events` 트릭 불필요.

## 구현 위치

- 단일 파일 변경: `claude_code_web/app.py`
- 새 함수 추가: `render_login_gate(status: str, status_msg: str) -> None`
- 호출 위치: 세션 상태 초기화(`claude_status` 키 포함) 직후, `pick_directory_macos` 함수 정의 이전

## 부수 효과

- 사이드바 상단의 상태 점은 green일 때만 보이게 된다 (yellow/red면 사이드바 자체가 안 그려지므로). 정보 중복이라 OK.
- 기존 사이드바의 재체크 버튼(`↻`, `app.py:311`)은 게이트 화면 안의 "다시 확인" 버튼으로 대체된다.

## 비범위 (Out of Scope)

- 주기적 자동 재체크 (X) — 사용자가 명시적으로 "다시 확인" 클릭해야만 재체크.
- `claude login`을 웹에서 직접 트리거 (X) — 보안/UX 모두 위험. 터미널 안내만.
- 게이트 화면에서 사이드바/디렉터리 선택 등 부분 기능 사용 허용 (X) — 전부 차단.
