# 173 — SM 1.0 최종 플랜 v3: 사용자가 무엇을 하고, 무엇을 보고, 왜 하는가

**날짜:** 2026-09-06 (v3 = v2 + 사용자 역할 8명 검증의 delta) · **상태:** 최종 검토용, 코드 없음 · **전제:** docs/172 part 1 (`6465c0e`) + §1b (`f2215d6`), 브랜치 `feat/physics-daily-flow`, 미push

**먼저 읽을 세 문장 (검증 라운드의 결론)**

1. v2의 원칙 2(“모든 쓰기는 보이고 되돌릴 수 있다”)는 **노드가 직접 쓰는 값에 대해서는 거짓**이었다. 노드는 `QUAM_STATE_PATH`로 live 파일을 가리키고 자기 `machine.save()`로 쓴다(`run_experiment.py:199`). 그래서 v2의 ask-writes는 정작 중요한 쓰기를 못 막고, write 카드의 [되돌리기] 뒤에는 Ctrl+Z가 없었다. v3의 한 가지 핵심 변경: **에이전트가 돌리는 노드는 working copy의 임시 사본을 가리키고, 끝나면 그 diff를 tray에 한 그룹으로 올린다.** 그때부터 원칙 2가 참이 된다.
2. 방을 비우는 사용자 5명(중급 a, 전문가 a·b, 관리자 a·b)은 같은 벽에 부딪혔다: 사람은 에이전트로부터 보호되지만, **OPX·고아 worker·Stop·보류 값·동료의 통째 저장**은 CLAUDE.md의 문장에 맡겨져 있었다. 전부 `run_node` / `take_live` **안의 거부(데이터로 반환)**로 옮긴다.
3. `auto` 기본값은 살아남는다. 단, **불변 규칙 0(하드웨어는 클릭으로만 시작, 문장으로는 절대 아님)**, plan 카드의 **plan별 모드 선택**, SM의 문이 모드와 무관하게 강제하는 **Limits**가 있을 때만이다. 이 셋이 없으면 관리자 둘은 auto를 금지했고 초보자 둘은 [시작]을 누르지 않았다.

검증 점수(“이대로면 쓰겠는가”, 1–5): 초보 a 3 · 초보 b(관찰자) 2 · 중급 a(Codex) 3 · 중급 b(제어형) 2 · 전문가 a 3 · 전문가 b(장비 책임) 3 · 관리자 a(PI) 3 · 관리자 b(랩 매니저) 3. 읽는 절반(카드·digest·journal)은 모두 4 이상으로 읽었고, 점수를 끌어내린 것은 전부 **모는 절반**이다. v3는 그 절반을 고친다.

## 0. 원칙 (v3)

1. **에이전트는 자율이다.** 기본 모드 `auto`: 실험을 돌리고 칩에 쓴다. 단 **규칙 0**: 어떤 모드에서든 하드웨어의 첫 시작은 plan 카드의 [시작] 클릭이다. 문장(“q3 rabi 돌려”)은 plan 카드를 만들 뿐이다. `auto`는 [시작] 이후를 다스린다.
2. **모든 쓰기는 같은 문을 지나 보이고 되돌릴 수 있다.** 에이전트의 `state_edit`도, 에이전트가 돌린 노드의 저장도 tray를 지난다(임시 사본 → diff → tray 그룹). `auto`면 즉시 apply(write 카드 + Ctrl+Z), `ask-writes`면 승인 카드. 사람이 QUAlibrate GUI로 돌린 노드의 저장은 지금처럼 live에 직접 가고 SM이 docs/87 방식으로 따라간다.
3. **사람의 작업은 에이전트가 덮지 않는다.** §6의 불변 규칙.
4. **화면의 단위는 카드다.** run 카드(누가 돌렸든), write 카드(누가 썼든). because 옆에 항상 node outcome과 gate. 없는 정보는 빈 칸.
5. **기록은 사용자 소유의 .md.** 에이전트의 **이유 줄**(`journal_append`)은 기본 켜짐(`by_claude`/`by_codex`), 에이전트의 **턴 종료 문장**(`agent_says`)은 기본 꺼짐. 저자는 `by_claude` · `by_codex` · `human:<이름>` · `qualibrate` · `unknown` · `sm`.
6. **권한이 아니라 귀속.** 관리자 개념은 없다. 대신 모든 모드 변경·승인·Stop·쓰기에 누가(창의 “지금 키보드 앞” 이름) 했는지가 남는다. 관찰자 창(§3.7)은 권한이 아니라 사고 방지 장치다.

## 1. 메뉴와 첫 화면

### 1.1 사이드바

| 위치 | 이전 | 1.0.0 | 왜 |
|---|---|---|---|
| Projects / State Load / Generate Config / Instrument Wiring / Chip Components ▾ / Diagnostics / Chip Status / Compare / Live State Edit ▾ / Datasets ▾ | | 그대로 | |
| State History ▾ (Param History) | | **State History** 단독 | Param History가 아래로 |
| Experiment Runner / Fit Replay / Auto Calibrate | | **사라짐** (`SM_EXPERIMENTAL=1`에서만) | |
| Experiment Runner 자리 | | **Calibration log ▾** (하위: Param History) | 한 번 클릭 |
| 도구 줄 | | **Agent** 추가 (다른 페이지에서 같은 세션의 떠다니는 패널) | |
| 상단바 | Sync pill | **Agent pill** + **“지금 키보드 앞: [이름]”** 선택기 | 귀속 |
| Ctrl-K 팔레트 / Getting started | | 트리오 제거, Calibration log·Agent 추가 | |

### 1.2 첫 화면 = Agent home (칩이 열려 있을 때)

```
┌──────────────────────────────────────────────┬─────────────────────────────┐
│ PJ_10082026 · 20 qubits         [Agent pill]  │ 지금                         │
│ 예약: 김OO · 14:02 → ~16:40 (auto)            │ ▶ 05_power_rabi · q4 · 3m    │
│ ┌ 대화 ──────────────────────────────────┐    │   보통 4m · by_claude        │
│ │ (카드: 답변 · plan · run · write · 승인) │    │ 오늘 31 runs · ✗ 2 · 쓰기 12  │
│ │                                        │    │ 가장 큰 Δ: q17.f_01 −6.2 MHz │
│ └────────────────────────────────────────┘    │ 승인 대기 1  ■ Stop ▾        │
│ [ 무엇이 궁금하세요? / 무엇을 할까요?        ] │ 오늘의 digest                │
│ [1Q bringup] [readout tuneup] [CZ tuneup]     │ q3 res✓ qspec✓ rabi✓ rams✗  │
│ (preset은 초안만 채움 · 시작은 plan 카드에서) │ …  [Calibration log 열기]     │
└──────────────────────────────────────────────┴─────────────────────────────┘
```

- 본문에는 **Stop·모드·대상 체크박스가 없다**(초보자·관찰자·PI 셋이 “실행 장치처럼 보여 피한다”). Stop은 오른쪽 “지금” 칼럼과 pill에, 모드는 Settings와 plan 카드에, 대상은 preset을 누른 뒤 초안 안에 “초안에 넣기”로.
- 입력은 **질문**과 **지시** 둘 다. 질문은 **읽기 전용 호출**(별도의 짧은 세션, 읽기 도구만)로 답하고, 지시는 plan 카드가 된다. 그래서 plan이 돌고 있어도 질문은 항상 된다.
- 칩이 없으면: 지금의 project-first 첫 화면 + 버튼 **“QUAlibrate의 현재 프로젝트 <이름> 열기”**(`POST /qualibrate/open`) + 프로젝트 불일치 amber 배지.
- **예약 줄**: 세션이 있으면 “누가 · 언제부터 · 예상 종료 · 모드”. journal에도 한 줄. Calibration log 헤더에도.

## 2. 사용자별 하루 (QUAlibrate GUI와 섞여 쓰는 하루)

표기: **[한다]** · **[본다]** · **[기대]** · **[왜]**. 전부 1.0.0.

### 2.1 초보자 — 한 달 된 대학원생. QUAlibrate GUI로 배우는 중

**아침 (직접 돌린다)**
1. **[한다]** QUAlibrate 웹앱에서 `02_resonator_spectroscopy` q1 실행. 그림을 본다.
   **[본다]** (SM) Datasets “1 new”. Calibration log에 카드 `09:12 · Resonator spectroscopy · q1 · ✓ · gate pass · #1301 · unknown`. 카드 첫 줄에 family 한 줄 설명(“공진기 주파수를 찾습니다”)과 manual 링크.
   **[왜]** 카드의 뼈대는 DatasetStore. hook도 run_node도 없는 run의 저자는 **`unknown`**(회색)이다 — `qualibrate`라고 단정하지 않는다(사람이 터미널에서 돌렸을 수도 있다). 아래 “이건 내가 돌렸어요” 한 칸을 누르면 `human:<이름>`으로 바뀌고 메모를 남길 수 있다.
2. **[한다]** q3 그림이 이상하다. Agent home 입력창에 “q3 res spec 결과가 이상한데 뭐가 문제야?”
   **[본다]** 답 카드(읽기 전용 호출): “**얕은 딥**입니다(manual R3). 신호 대비 잡음이 낮아요(peak_snr 2.9, 기준 4.0). 평균 횟수(`num_averages`)를 4배로 늘리거나 측정 범위(`frequency_span`)를 좁혀 보세요.” 버튼 **[plan으로 만들기]** **[내가 QUAlibrate에서 돌릴게]**.
   **[기대]** 선배 없이 “왜”를 듣는 것. 그리고 **질문이 실험을 시작하지 않는 것**.
   **[왜]** 규칙 0. 첫 버튼도 바로 돌리지 않고 plan 카드를 만든다. 용어 규칙: 노드 이름은 QUAlibrate 목록의 이름, 파라미터는 node.json의 키, 상태 값은 Live State Edit의 열 라벨(dot path는 툴팁). gate 이유는 manual의 평어 문장이 먼저, 지표는 괄호.
3. **[한다]** [내가 QUAlibrate에서 돌릴게].
   **[본다]** **체크리스트 카드**: “QUAlibrate에서 `02_resonator_spectroscopy` 열기 → targets `q3` → `num_averages` 100 → 400 → Run”. 돌리고 오면 새 카드 `#1304`가 `#1303의 후속`으로 붙고 파라미터 diff `num_averages 100 → 400`.
   **[왜]** “에이전트가 돌리기”와 “내가 돌리기”는 같은 값의 선택이고, 후자도 SM이 따라간다.

**오후 (맡긴다)**
4. **[한다]** [1Q bringup] → 초안 “1Q bringup: q3, q4, q5 …” + “q3는 res spec 방금 했으니 qubit spec부터” 덧붙여 Enter.
   **[본다]** **plan 카드**:
   - 대상 × family 표. q3 res spec “건너뜀(사용자가 #1304로 완료)”.
   - family마다 한 줄 설명 **과 “바뀔 수 있는 값”**: `q4 · Qubit frequency (f_01) · 지금 4.8098 GHz · 마지막 변경 선배 김OO 8/29`. 이 줄이 이 카드의 핵심이다.
   - **파라미터 표**(노드별, 지난 run의 node.json으로 채움, 편집 가능).
   - `env: cqt · simulate: OFF · timeout 30m/노드 · 예상 종료 ~16:40`.
   - **모드 선택**: 이 칩의 기본은 `ask-writes`(새 칩의 기본; 누군가 `auto`로 바꾸기 전까지). 이 plan만 바꿀 수 있다.
   - 버튼 **[시작 — 값 6개가 바뀔 수 있음]** **[취소]**.
   **[기대]** 무엇이 바뀔지, 누가 만든 값인지 알고 누르는 것.
   **[왜]** 초보자가 [시작]을 안 누르는 이유는 “씁니다”가 무엇을 쓰는지 몰라서였다. `who_changed`와 families의 update target이 그 답을 준다. 새 칩의 기본이 `ask-writes`인 이유: auto는 랩이 선택하는 것이지 물려받는 것이 아니다. 바꾼 사람과 시각은 journal `sm` 줄로 남는다.
5. **[한다]** [시작]. 수업에 간다. 예약 줄에 “박OO · 14:02 → ~16:40”.
   **[본다]** (돌아와서) plan 카드가 **핀 고정**된 채 `8 / 11 완료 · ✗ 0 · 건너뜀 1 · 완료 16:31`. 그 아래 run 카드 8개, 승인 카드 3개(ask-writes): “`q4 · Qubit frequency` 4.8098 → 4.8123 GHz Δ +2.5 MHz · because: … · [칩에 쓰기] [거절] [값 고쳐서 쓰기]”. 카드에 “q4의 다음 노드(rabi)는 승인 후 이어집니다”.
   **[기대]** 내가 없는 동안 뭘 했고, 내 결정이 무엇인지.
   **[왜]** ask-writes는 **그 대상의 체인을 멈춘다**(다른 대상은 계속). 승인은 사람의 행위라 `human:박OO`로 기록된다. 값을 고쳐 쓰면 에이전트의 값은 “거절됨”으로 남고 에이전트는 다음 run_node 전에 “사람이 q4.f_01을 X로 씀”을 통지받는다.
6. **[한다]** 선배가 “q4 f_01 왜 바꿨어, 되돌려”.
   **[본다]** write 카드에 “이후 #1315, #1316이 이 값 위에서 측정됨”. plan 카드 끝에 **[이 plan의 쓰기 6개 전부 되돌리기]**(plan 시작 전 Versions 스냅샷으로).
   **[왜]** 되돌리기의 순서를 초보자가 계산하게 하지 않는다.
7. **[한다]** (다른 날) 수업 중에 선배가 QUAlibrate GUI로 q4 T1을 돌렸다.
   **[본다]** 대화에 카드 “QUAlibrate에서 사람이 실험을 시작해 **다음 run 후 멈췄습니다** (#1330 T1 q4)”. pill `human ran T1 · 12 min ago`. plan 카드 `Stopped by SM (human run)`. 예약 줄 해제.
   **[왜]** §6 규칙 5: 하드웨어 충돌은 SM이 **run_node 안에서** 거부한다(`human_active`, 기본 30분). 문장이 아니라 코드다. 멈춘 뒤 재개는 사람의 클릭.

### 2.2 중급자 — Codex 사용자. 낮에는 QUAlibrate GUI, 밤에는 에이전트

1. **[한다]** 처음 한 번, 도구 줄 Agent → 설정. 이 PC에 이미 된 것(선배가 등록한 MCP·hook·로그인)은 ✓로 접혀 있고, 남은 것만 보인다: “실행 환경: cqt ✓ (QUAlibrate 프로젝트 PJ에서 가져옴)”, “Journal: `D:/data/PJ/journal/` (프로젝트 데이터 폴더 옆, 이 PC 계정을 쓰는 모두가 공유)”, “내 vault로 미러(선택)”.
   **[왜]** 설정은 “아직 안 된 것만”. 기본 journal 위치는 instance가 아니라 **프로젝트 데이터 폴더 옆**(재설치에 살아남고 프로젝트를 따라간다). vault는 칩별 미러.
2. **[한다]** 낮: QUAlibrate GUI로 q1–q10 rabi. 중간에 “지금까지 rabi 결과 요약, suspect만”.
   **[본다]** 표 카드(SM이 `runs_summary`로 먼저 만들고 모델 문장은 뒤에): qubit · #run · outcome · gate(anchor: 그 run 폴더의 state) · 쓴 값(old → new) · 이 값이 지금 칩 값인지. “q7은 김OO이 12:31에 Live State Edit로 0.31로 바꿈(human)”.
   **[왜]** gate는 **run의 속성**이라 run 폴더 안의 `quam_state/state.json`을 anchor로 계산해 캐시한다(지금 값으로 다시 계산하면 답이 시간에 따라 바뀐다). 저자는 SnapshotMeta와 undo unit에 찍힌 actor.
3. **[한다]** 퇴근 전: “q11–q20 rabi부터 ramsey까지 밤새. |Δf_01| > 5 MHz면 쓰지 말고 아침에 물어봐.” plan 카드에서 모드 `auto` 확인, 파라미터 표에서 q15만 `span` 수정. [시작].
   **[본다]** plan 카드 조건 줄 “|Δf_01| > 5 MHz → **보류(hold)**”. 예약 “이OO · 19:10 → ~03:00 (auto)”.
   **[왜]** 조건 초과 값은 tray에 **hold 플래그**로 남고, 나머지 체인은 자기 쓰기 위에서 계속된다(ask-writes처럼 체인을 세우지 않는다). hold는 사람이 tray에서 체크하기 전에는 어떤 apply에도 실리지 않는다.
4. **[한다]** 새벽 2시, 동료가 QUAlibrate GUI로 q3 T1을 돌린다(에이전트는 모른다).
   **[본다]** (에이전트 쪽) 다음 run_node가 `human_active`로 거부 → plan은 “다음 run 후 멈춤” 상태. 다음 apply는 `stale_live` → `take_live` 호출 → 응답 `changed: [{qubits.q3.T1, old, new, actor: unknown, run: #1330}]`, `overlap: []`, `reverted: []`. 에이전트는 겹침이 없으니 보류 값 위에 재기저(hold는 유지)하고 상태를 기록. 하지만 `human_active`라서 **재개는 사람 몫**.
   **[기대]** 동료의 결과가 덮이지 않고, 동료가 돌리는 동안 에이전트가 OPX를 잡지 않는 것.
   **[왜]** take_live는 “목록”이 아니라 **판단 재료**(겹침·되돌려짐)를 준다. 동료의 통째 저장이 에이전트의 값을 예전 값으로 되돌렸다면 `reverted`로 표시되고 빨간 write 카드 “#1330(unknown)이 되돌림 · [다시 적용]”이 뜬다.
5. **[한다]** 아침 Calibration log.
   **[본다]** 헤더에 “예약 종료 03:00 · Stopped by SM (human run 02:11)”. 승인 대기 1(hold): `q17.f_01 Δ −6.2 MHz by_codex [칩에 쓰기] [거절] [값 고쳐서]`. 카드 사이에 동료의 `T1 · q3 · unknown` 카드. `[재개]` 버튼.
6. **[한다]** 값 토큰 🕘로 history와 #run figure(썸네일 클릭 → 그 run의 Interactive 탭)를 보고 [칩에 쓰기].
   **[본다]** write 카드 `human:이OO`.
7. **[한다]** 낮에 GUI로 q17 ramsey 재확인. 카드가 붙는다.

Codex 특이점(정직하게): `codex exec`는 **턴 하나**다. plan이 도는 동안 지시창은 “Codex가 턴 안에 있습니다 — 말을 걸려면 Stop after this run” 으로 잠기고, 질문은 읽기 전용 호출로 계속 된다. Claude Code는 stream-json 입력으로 턴 중에도 메시지를 줄 수 있다. 터미널 Codex의 pill은 hook 페이로드를 S4 spike에서 확인하기 전까지 `idle · 추적 안 됨` 툴팁.

### 2.3 전문가 — 터미널 Claude Code, 밤새, Obsidian, 냉장고 둘

1. **[한다]** 설정: 남은 것만. “두 칩(PJ, CQT)을 한 PC에서” → SM이 **칩별 mcp-config**(`SM_URL` + `SM_CHIP`)를 만들어 준다. 터미널용 전역 `claude mcp add`는 체크박스(“SM이 꺼져 있으면 에러가 납니다”). 캘리브레이션 저장소의 `.claude/settings.local.json`에 **allow 규칙**(`mcp__quam-state-manager__*`, `Bash(python *:*)`)을 미리보기·백업 후 쓴다.
   **[왜]** 이 사람의 실제 막힘은 도구가 아니라 **허가 프롬프트**였다. 그리고 두 냉장고에서 브리지는 `SM_CHIP`이 다르면 **거부**한다.
2. **[한다]** 랩 컨텍스트 단계. SM이 state/wiring을 읽고 묻는다: flux-tunable? tunable coupler? Purcell? SQUID 대칭/비대칭? (모름은 모름).
   **[본다]** 문서 초안 = **소자 사실 + 규칙**만(“최근 상태 숫자”는 넣지 않는다 — 공유 저장소에 시간 지난 숫자를 박는 것은 관리자 둘과 중급자가 반대). 규칙: 시작 시 `chip_summary()` 먼저, 노드 전 `journal_append(reason)`, run 후 `check_fit`, 노드가 state를 썼으면 `take_live`, state.json 직접 편집 금지. 쓰기는 **diff 미리보기**, 칩 키가 붙은 fenced 구간(`<!-- sm:lab-context chip=… -->`)에 append, git 추적 파일 경고, 기본은 미추적 `CLAUDE.local.md`/`AGENTS.local.md`.
3. **[한다]** 터미널에서 `claude`로 밤 작업. `run_node`를 쓰라는 규칙이 있지만 `python node.py`도 막지 않는다.
   **[본다]** (SM) pill `running 02_resonator_spectroscopy · q3 · by_claude · 1m`. 25분짜리 T1이 돌 때도 `running · 25m · 보통 22m`(stalled 오판 없음: 살아있음 = worker/agent PID 생존 또는 run 폴더 성장).
   **[왜]** `python node.py` 경로는 §6-5가 강제되지 않는 유일한 경로다. 문서에 그렇게 쓴다: 이 경로의 Ctrl+Z는 스냅샷 기반이고 저자는 `unknown`일 수 있다.
4. **[한다]** 아침. vault의 `.md`.
   **[본다]** `by_claude` 이유 줄(기본 켜짐), `hook` 줄(무엇을 돌렸고 #run), `sm` 줄(쓴 것·모드·Stop). 턴 종료 문장은 없다(`agent_says` 꺼짐; 켜면 `> ` 인용 불릿).
   **[왜]** “다 남기고 싶다”와 “모델 산문은 싫다”는 둘 다 맞다. 이유 줄과 산문을 분리했다.
5. **[한다]** SM 창을 닫으려 한다(노드가 도는 중).
   **[본다]** “05_power_rabi가 돌고 있습니다. 창을 닫으면 실험이 종료됩니다. [그래도 닫기] [취소]”.
   **[왜]** SM 창이 chassis를 죽인다(main.py). 정직하게 경고한다. “SM 꺼진 밤” 서사는 `python node.py` 경로에만 해당한다.
6. **[한다]** Notion.
   **[본다]** /help 한 줄: “Notion은 파일이 아닙니다. 별도의 **읽기 전용** 에이전트 세션에 Notion MCP를 붙여 하루의 journal을 옮기게 하세요(캘리브레이션 세션에는 붙이지 마세요).”

### 2.4 관리자 — PI(a)와 랩 매니저(b)

1. **[한다]** (PI) 22:00 첫 화면.
   **[본다]** 예약 줄 “이OO · 19:10 → ~03:00 · auto”. “지금”: `05_power_rabi · q14 · 3m · 보통 4m`, 오늘 31 runs · ✗ 2 · 쓰기 12 · 가장 큰 Δ `q17.f_01 −6.2 MHz`, 승인 대기 1. ✗ 2를 누르면 Calibration log가 `outcome=fail|gate=fail`로 필터된 채 열린다.
   **[한다]** “오늘 실패한 두 개?”
   **[본다]** SM이 먼저 표(`runs_summary`), 모델 문장은 뒤에.
2. **[한다]** (PI) Settings → **Limits**(칩별): plan당 최대 쓰기 수, family별 최대 |Δ|(pre-run anchor 대비; 초과는 **모든 모드에서 hold**), stop-loss(연속 gate fail N → 대상 정지, 총 K → plan 정지), **Stop by HH:MM**, webhook URL(plan_done / waiting / agent_failure / agent_stalled / limited).
   **[왜]** “auto를 허용하려면 무엇이 필요한가”의 답. 백엔드·모드와 무관하게 SM의 문이 강제한다. `stoploss.py`와 `notify.py`가 이미 있다.
3. **[한다]** (매니저) 새벽 2시 구독 한도.
   **[본다]** pill `limited · resets 14:50`, journal `sm` 줄, plan 카드 “이 계정으로 HH:MM부터 세션”. digest에 칩·소유자별 오늘 토큰. 자동 재개는 opt-in(기본 꺼짐)이고 켜도 run_node 게이트를 전부 지난다.
   **[왜]** 이번 세션에서 우리가 직접 겪은 상태다. 냉장고 시간을 짜려면 보여야 한다.
4. **[한다]** (매니저) 학생 A의 세션이 도는데 학생 B가 급하다.
   **[본다]** B의 창: 예약 줄에 A의 이름. **[Stop after this run and hand over]**. A의 PID가 죽어 있으면 [넘겨받기]. 둘 다 양쪽 이름으로 journal.
5. **[한다]** (PI) 폰.
   **[본다]** 동기화 폴더의 `.md`. 설정 단계에서 SM이 “이 폴더가 동기화 폴더가 아니면 폰에서는 안 보입니다”라고 미리 말한다. digest strip의 “텍스트로 복사”, `GET /chip-status/report` 링크.
6. **[한다]** (매니저) 설정 페이지에서 무엇이 어느 파일에 써지는지 본다.
   **[본다]** 백엔드별로 “쓴 것” 목록과 백업 경로, **[SM에서 분리]**(SM이 넣은 항목만 정확히 제거).

## 3. 화면 규격

### 3.1 Agent pill (우선순위 순: waiting > limited > stalled > failed > running > between > human-ran > idle)

| 상태 | 표시 | 근거 |
|---|---|---|
| waiting | 노랑 `승인 대기 N` | hold 또는 ask 모드의 승인 카드 |
| limited | `limited · resets HH:MM` | 백엔드 스트림의 usage/limit 결과 |
| stalled | 주황 `no sign of life 17m` | run 진행 중 아님 **그리고** 15분 무이벤트, 또는 PID 사망 |
| failed | 빨간 `✗ N` | 오늘 실패 수(클릭 → 필터된 log) |
| running | 점멸 `05_power_rabi · q3 · 25m · 보통 22m` | 짝 없는 Pre 또는 run_node 진행 + (PID 생존 또는 run 폴더 성장) |
| between | `thinking · by_claude` | 살아있고 도구 실행 중 아님 |
| human-ran | `human ran T1 · 12 min ago` (사실, 물음표 없음) | run_watch가 본 hook/run_node 없는 run |
| idle | 회색 `Agent` | 캘리브레이션 세션 이벤트 없음 |

pill 텍스트에 현재 모드. 예약(소유자·종료 예정)은 pill 옆 줄.

### 3.2 Calibration log (`/journal`, 최상위)

- 헤더: 칩 · 날짜(← →, 주간 = 여러 날 .md 이어붙임) · 필터(qubit/pair) · 저자 필터 · 원본 .md · 폴더 · 예약/모드 줄 · “쓰기 오늘 12 · 가장 큰 Δ …”.
- since-last-visit · 승인 대기(hold 포함).
- digest strip(대상별 family pill, 색 = outcome, “텍스트로 복사”).
- **run 카드**: 헤더 `시간 · family(QUAlibrate 이름) · 대상 · outcome · gate(anchor = 그 run의 state, 캐시) · #run · 저자 · plan_id`. 본문: family 한 줄 설명 + manual 링크 · 이 run이 쓴 값 `라벨 old → new Δ(단위)`와 그것이 지금 칩 값인지 · 이전 같은 노드(또는 `parent_id`) 대비 파라미터 diff · because(없으면 “기록 없음”) · 썸네일(클릭 → Interactive 탭) · [Run again](같은 파라미터 표) · “이건 내가 돌렸어요”/메모 · details(이벤트, 에러 꼬리). 라이브 플롯은 없다는 문구.
- **write 카드**: `시간 · N개 · 저자 · Δ · [되돌리기] · “이후 #… 이 값 위에서 측정됨” · from #run → 플롯 열기`. 빨간 변종 `reverted by #N (unknown) · [다시 적용]`.
- 생성 규칙: ① 뼈대 DatasetStore ② journal 부착 = `#run` 우선, 없으면 `[start−60s, end+300s]` + 대상 겹침 ③ 저자 = run_node → 그 에이전트(확정), hook 이벤트 → 그 에이전트, 둘 다 없음 → **`unknown`**(“이건 내가” 클릭으로 `human:<이름>`) ④ 바뀐 값 = 그 run 스냅샷의 change point ⑤ gate = run anchor 캐시 ⑥ `unassigned` 줄은 상단 “이 칩으로 옮기기” ⑦ 모든 run/write에 `plan_id`.

### 3.3 대화

- 카드: 답변(표 먼저, 숫자마다 #run, 썸네일) · plan(§2.1-4의 구성; 핀 고정 + 진행 `8/11 · ✗ · 건너뜀 · 남은 시간` + 종료 `완료 HH:MM / Stopped by <who> / agent exited (code)` + [이 plan 전부 되돌리기]) · run(승격) · write(auto) · 승인(ask 또는 hold; 행 편집 가능) · Stop 결과 · 체크리스트(“내가 GUI에서”).
- 결정론 입력: `/run <node> <targets> k=v` 는 LLM을 거치지 않고 plan 카드를 만든다(정확한 숫자를 바꿔 말하지 않는다).
- 질문과 [왜?]는 **읽기 전용 호출**(Claude: `-p --allowedTools` 읽기 도구만; Codex: 읽기 전용 sandbox). 모는 세션은 plan/[시작] 경로만 쓴다.
- Stop ▾: **after this run**(= run_node가 다음 노드를 거부; Codex에도 됨) / **now**(세션 파일에 `agent_stop` → run_node가 먼저 거부 → 백엔드 프로세스 트리 종료 → chassis cancel). 문구: “클라이언트를 종료합니다. OPX는 현재 시퀀스를 끝냅니다. SM의 칩 쓰기는 원자적입니다. 현재 run 폴더는 불완전할 수 있습니다.” 남의 세션이면 소유자 이름을 보이고 한 번 확인. 모든 Stop은 journal(누가, 어느 창).
- 세션: 칩당 하나. `agent_sessions/<chip>.json` = backend · session_id · mode · owner · started · until · pid · agent_stop. 세션은 **쓰기 도구(run_node/apply_to_live)를 처음 부를 때** 칩을 잡는다; 읽기 전용 호출은 잡지 않는다. 브리지는 `initialize`에 `(pid, SM_CHIP)`을 등록. lock 줄은 잡은 도구·소유자·시작·마지막 이벤트를 적는다.
- 재개: `--resume` / `codex exec resume`. **고아 worker가 살아 있으면 재개 금지**(사람이 “OPX가 비었음”을 누르기 전까지). 재개 프롬프트에 “자리를 비운 사이” 블록(DatasetStore).

### 3.4 에이전트 백엔드

| | Claude Code 2.1.x | Codex 0.153.x |
|---|---|---|
| 모는 세션 | `claude -p --input-format stream-json --output-format stream-json --include-partial-messages`(턴 중 메시지 가능) | `codex exec --json`(턴 하나; 중간 입력 잠금) + `codex exec resume` |
| 질문 | `-p --allowedTools <읽기 도구>` | 읽기 전용 sandbox |
| SM 도구 | `--mcp-config <칩별 json> --strict-mcp-config` | `codex mcp add` / config.toml |
| 컨텍스트 | CLAUDE(.local).md | AGENTS(.local).md |
| 작업 디렉터리 | 캘리브레이션 폴더(파일이 실제로 읽히도록) | 동일 |
| 사용량/한도 | 스트림 결과에서 | 동일 |
| 터미널 모드 “지금” | hook | 0.153 hook 페이로드를 S4 spike에서 확인; 안 되면 `idle · 추적 안 됨` |

**`run_node(node, targets, params, timeout_s?)`** = 권장 경로이자 유일하게 저자가 확정되는 경로. Experiment Runner의 chassis를 **realbackend의 구동 루프**로 돌린다(heartbeat `touch_ui`, 90초 UI-pause 우회, 재시작, cancel). 노드는 **working copy의 임시 사본**(`instance/agent_runs/<run>/quam_state/`)을 `--state-path`로 받고, 끝나면 사본 vs working copy diff를 tray에 **한 그룹**(actor 에이전트, plan_id)으로 올린다. `auto`: 즉시 apply. `ask-writes`: 승인 카드 + 그 대상 체인 정지. Limits 초과: hold. **거부는 데이터로**: `human_active`(N분 내 unknown/qualibrate run) · `orphan_running`(worker PID 생존 / foreign owner) · `stopped_by_human` · `awaiting_approval` · `no_start_token`(규칙 0) · `simulate_on_in_auto` · `past_stop_by`. 결과 분류에 `hardware_contention`(qm 닫힘/다른 qm 열림 시그니처) 추가 — 에이전트는 이것에 재시도하지 않는다. 편집 잠금 409 문구: “Agent가 05_power_rabi를 돌리는 중 — 편집이 잠겼습니다. Stop을 누르면 편집할 수 있습니다.”

`take_live` → `{changed:[{path, old, new, actor, run}], overlap:[…], reverted:[…]}`. `undo_mine`(에이전트 그룹만). `state_get`/`runs`에 `stale_since`. `apply_to_live`는 held 그룹을 건너뛰고, 사람 그룹이 있으면 거부. 브리지는 `SM_CHIP` 불일치 시 모든 도구 거부.

### 3.5 설정 (Agent → 설정; 이 PC에서 아직 안 된 것만 보임)

1. **에이전트**: 감지(설치·로그인). [SM에 연결]: 칩별 mcp-config 생성, hook 등록, allow 규칙(`.claude/settings.local.json`), 전역 `claude mcp add`는 체크박스. 미리보기 · 백업 · 클릭. [SM에서 분리]. 로그인은 “터미널에서 `claude`/`codex` 한 번, 담당: <이름>”.
   1b. **실행 환경**: QUAlibrate 프로젝트에서 env·calibrations 폴더·state 경로를 가져오고, SM의 칩과 다르면 거부(amber). simulate, timeout 표시.
2. **Journal**: 기본 = 프로젝트 데이터 폴더 옆 `journal/`(공유 안내), 칩별 vault 미러(선택), 동기화 폴더 감지(폰 안내), Obsidian 안내(“`![[<chip>/<date>]]`로 embed, SM 파일에 직접 타이핑 금지”). 이유 줄 켜짐(고정), `agent_says` 꺼짐(선택).
3. **랩 컨텍스트**: 질문만(§2.3-2). diff 미리보기 → fenced 구간 append → 기본 `.local.md`.
4. **Limits**(§2.4-2) + 기본 모드(새 칩 = ask-writes; 바꾸면 journal).
5. **테스트**: 실제 호출, 시간과 답 그대로.

### 3.6 Q&A 도구

기존 19개 + `run_node` · `runs_summary(since, qubit, family)`(reason·written_paths·현재값 여부 포함) · `chip_summary()` · `who_changed(path, since)`(SnapshotMeta/undo unit의 actor; 없으면 `unknown`) · `undo_mine` · `journal_read(range)`. 답변 카드는 SM이 표를 먼저 만들고 모델은 문장만.

### 3.7 관찰자 창

브라우저 창 단위 토글(서버 세션 쿠키). 켜지면 그 창에서 `/state/apply-to-live`·`/undo`·run_node·Stop·모드 변경·넘겨받기가 “관찰자 창”으로 거부되고 [되돌리기]는 비활성. 질문은 된다. 권한이 아니라 사고 방지.

## 4. 데이터 흐름

```
사용자 ── QUAlibrate GUI ──▶ live state.json 직접 (docs/87 따라감) + run 폴더 ──▶ DatasetStore ─┐
에이전트 ── run_node ──▶ chassis(임시 사본 state) ──▶ run 폴더 + diff ──▶ tray 그룹 ──▶ auto: apply | ask: 승인 | 초과: hold ─┤
에이전트 ── python node.py ──hook──▶ jsonl ──▶ SM (저자: 에이전트, 문 밖 쓰기, docs/87 따라감) ────────────────────────────┤
에이전트 ── state_edit / apply_to_live ──▶ tray ──▶ live ────────────────────────────────────────────────────────────────┤
사용자 ── Live State Edit / 승인 카드 ──▶ tray ──▶ Apply ──▶ live (human:<이름>) ────────────────────────────────────────┤
                                                                                                                       ▼
                       run 카드 · write 카드 (plan_id) ──▶ Calibration log ──▶ vault/<chip>/<날짜>.md (by_claude / by_codex / human:<이름> / unknown / sm)
```

## 5. 모드

| 모드 | 첫 시작 | 실험 | 노드의 쓰기 · state_edit | 누구 |
|---|---|---|---|---|
| auto | [시작] 클릭 | 묻지 않음 | 즉시 apply, write 카드, Ctrl+Z | 맡기는 사람 |
| ask-writes (새 칩 기본) | [시작] 클릭 | 묻지 않음 | 승인 카드, 그 대상 체인 정지 | 값은 내가 |
| ask-all | [시작] 클릭 | 노드마다 허가 카드 | 승인 카드 | 처음, 관찰 |

어느 모드에서든: Limits 초과 값은 **hold**, 말로 준 조건(“5 MHz 넘으면”)도 hold. plan 카드에서 이 plan의 모드를 고른다(초기값 = 칩 기본).

## 6. 불변 규칙 (모드·백엔드 무관)

0. **하드웨어의 첫 시작은 클릭이다.** run_node는 [시작]이 발급한 start token 없이는 거부한다. 한 노드짜리 plan도 plan 카드다.
1. tray에 사람의 그룹이 있으면 에이전트의 apply는 거부. 사람의 Apply는 보이는 전부를 쓴다.
2. live가 SM 밖에서 바뀌면 에이전트의 apply는 `stale_live`; `take_live`는 changed/overlap/reverted를 준다. 겹치면 “재스테이징”이 아니라 “그 대상의 노드를 다시 돌려라”.
3. 사용자 설정 파일과 저장소의 컨텍스트 파일은 설정 화면의 클릭에만, 미리보기와 백업과 함께.
4. 사람의 Stop이 이긴다. Stop은 run_node가 먼저 본다.
5. 칩당 세션 하나(쓰기 도구를 부를 때 잡음). OPX 동시 사용은 `human_active`·`orphan_running`·`hardware_contention`으로 run_node가 거부한다. **강제되지 않는 유일한 경로는 터미널의 `python node.py`** 이며 문서에 그렇게 쓴다.
6. Limits는 모드와 무관하게 SM의 문이 강제한다.

## 7. 빌드 순서와 시간 (전부 1.0.0)

| # | 작업 | 시간 |
|---|---|---|
| S1 | `core/story.py`: 카드 모델(DatasetStore + journal 시간창 + change point) · SnapshotMeta/undo unit에 actor · 저자 `unknown` 규칙 · `plan_id` · gate anchor = run state, 캐시 | 6 |
| S2 | Calibration log 페이지 + 사이드바(최상위, Param History 하위) + 팔레트/불릿 · 주간(이어붙임) · digest 복사/report 링크 · 쓰기 줄 · family 한 줄 · 메모/“내가 돌렸어요” | 6 |
| S3 | Agent pill 8상태(우선순위) · run 진행 기반 liveness · `human ran N min ago` · `limited` · 모드 표시 · 예약 줄 | 3 |
| S3b | **Limits + stoploss/notify 연결 + Stop-by + webhook 이벤트** | 3 |
| S4 | 백엔드 추상화(Claude stream-json / Codex exec --json) · usage·limited 감지 · Codex 입력 잠금 · **읽기 전용 Q&A 호출** · cwd = 캘리브레이션 폴더 · 세션 파일/resume/Stop · 이벤트 정규화. **첫 시간 spike**: permission-prompt-tool 대신 SM 게이트로 가는지, 스트리밍 전송, Codex hook 페이로드, Codex 턴 중 입력 | 9 |
| S5 | `run_node` = realbackend 구동 루프 위에: **임시 사본 state + diff → tray 그룹** · 게이트(start token / orphan / stop / human_active / awaiting_approval / simulate / stop_by) · `hardware_contention` 분류 · heartbeat 우회 · per-call timeout · **hold** · `take_live` changed/overlap/reverted · `undo_mine` · `SM_CHIP` 핀 · `stale_since` · 편집 잠금 문구 | 12 |
| S6 | Agent home + 떠다니는 패널 + 카드 렌더러: plan 카드(바뀔 값 미리보기 · 파라미터 표 · 진행 · 전부 되돌리기 · 모드 선택) · [plan으로 만들기] · 체크리스트 카드 · 승인 행 편집 · 썸네일 → Interactive · 관찰자 토글 · 본문 제어 제거 · 용어 규칙 · `/run` 결정론 입력 · 창 닫기 경고 · 칩 없음 첫 화면 버튼 | 11 |
| S7 | 설정: 안 된 것만 표시 · 1b 실행 환경 · 연결(+allow 규칙, 백업, 분리) · journal 기본 위치/미러/동기화 감지 · 랩 컨텍스트(질문만, diff, fenced, .local) · 테스트 · Limits/모드 | 6 |
| S8 | journal: 이유 줄 켜짐 / `agent_says` 분리 · 백엔드 유래 저자 · `unknown`/`human:<이름>` · 모드 변경·Stop·예약 줄 · 키보드 앞 이름 선택기 | 2 |
| S9 | 핀 + mutation + CDP 재생(2.1-1~7, 2.2-3~6 동료 run 포함, 고아/heartbeat/hold/두 인스턴스 핀) + 실 CLI ×2 + docs + 1.0.0 승격 | 11 |
| | **합계** | **69시간 ≈ 9 작업일** |

순서: S1→S2→S3→S3b(“읽는 SM”, 2.5일) → S4→S5→S6(“모는 SM”, 4일; S5가 S6보다 먼저 — plan 카드는 run_node가 강제하는 것을 그린다) → S7→S8(1일) → S9(1.5일). 늦을 때 빼는 순서: 주간 이어붙임 → 관찰자 토글 → 토큰 줄 → Codex hook. **빼지 않는 것: 채팅, Calibration log, 규칙 0, 임시 사본 쓰기 문, run_node 게이트, hold, Limits, Codex.** 플랜에서 아예 뺀 것: 랩 컨텍스트의 데이터 읽기, Notion 빌드 항목(도움말 한 줄), `human-running?` 추정, “Stop now가 state.json을 깨뜨릴 수 있다” 문구, 첫 화면 본문의 Stop/모드/체크박스.

## 8. 검증

- 핀: `test_story`(생성 규칙·저자·anchor 캐시·plan_id) · `test_journal_page` · `test_agent_pill`(우선순위·liveness) · `test_agent_session`(가짜 백엔드: 정규화·resume·Stop·limited·Codex 잠금) · `test_agent_tools`(run_node 게이트 전부, 임시 사본 diff → tray, hold, take_live 세 목록, undo_mine, SM_CHIP, 사람 그룹 거부) · `test_limits` · `test_agent_setup`(감지·백업·분리·allow 규칙·fenced 구간) · 관찰자 창. 전부 mutation sweep.
- 실브라우저(CDP): 2.1-1~7, 2.2-3~6(동료 run 실제 폴더 복사), 2.4-1·4를 가짜 백엔드로 재생.
- 실 CLI: Claude와 Codex 각각 `sm_status`·`runs_summary`·`run_node`(simulate)·`state_edit`·`apply_to_live`·`take_live` 한 바퀴(칩 사본).
- 회귀: 사이드바/온보딩/edit·apply 문/autofit routes(273 green) + Experiment Runner 테스트(chassis 재사용이 깨뜨리지 않음).

## 9. 검증 라운드에서 기각한 것 (이유와 함께)

- 브라우저별 최소 모드(초보 a): 모드는 run의 속성이지 보는 사람의 속성이 아니다(두 창이 두 진실을 보임). plan별 모드 선택 + 규칙 0으로 같은 보호.
- 관찰자·모드 변경·연결에 암호(초보 b): 공유 OS 계정에서 인증이 되지 않는다. 귀속으로 대신.
- Agent home 대신 떠다니는 패널만(중급 b): 채팅이 1.0의 핵심이다. 위험했던 본문 제어를 뺐다.
- Codex를 1.0에서 제외(중급 b·매니저): 범위다. 대신 정직하게: hook은 spike 확인 전엔 없음, 턴 중 입력 잠금.
- ✗ run의 그림을 vault에 복사(PI): 동기화 충돌·용량. webhook과 SM 링크가 폰 뷰.
- 별도 `<date>.sm.md`(전문가 a): 한 이야기 파일을 쪼갠다. embed 안내로 대신.
- Stop now에 소유자 이름 타이핑(매니저): 인증 없는 마찰. 이름을 보이고 journal에 남기는 것이 강제 가능한 부분.
- `human-running?` 줄 삭제(초보 a): 사실 줄로 바꿈.

## 10. 남은 결정 (권고)

1. 새 칩의 기본 모드 `ask-writes`(누군가 auto로 바꿀 때까지) — **권고: 채택.**
2. journal 기본 위치 = 프로젝트 데이터 폴더 옆 `journal/` — **권고: 채택.**
3. 저자 `unknown` 기본 + “이건 내가 돌렸어요” — **권고: 채택.**
4. 규칙 0(문장은 절대 하드웨어를 시작하지 않음; 한 클릭 추가) — **권고: 채택.**
5. 69시간(≈9일) 일정 — 양해 범위인지.

## 11. 구현 기록 (브랜치 `feat/agent-cockpit`, main은 0.9.9 그대로)

### S1–S3b (커밋 afc3dd7 · c87f12b · 2262d62, 2026-09-06)

- S1 `core/story.py`: run 카드 = DatasetStore 위에 journal 줄(`#run` 또는 시간창)과 undo unit·스냅샷 change point를 붙인 것. 저자 사다리 claimed > SM-ran > hook-inferred > `unknown`(qualibrate라고 짐작하지 않음). 게이트는 그 run 자신의 `quam_state/state.json`에 anchor, `GATES_REV`로 캐시. 모든 기록에 actor(`by_claude`/`by_codex`/`human:<이름>`/`human`).
- S2 `/journal` Calibration log 페이지(사이드바 최상위, Param History가 아래), 필터·claim·adopt·raw. 실제 467 run 위에서 확인.
- S3/S3b `agent-pill.js`(우선순위 waiting > limited > stalled > failed > running > between > human-ran > idle; 한 번의 wake = `RunWatcher.bump` + `agent_seq`), `core/agent_session.py`, `core/limits.py`(새 칩 `ask-writes`, stop_by, max_delta → hold, webhook). 실제 hook 이벤트에 266 ms.

### S4 — 채팅 백엔드·세션 매니저·라우트 (2026-09-06)

**들어간 것.** `core/agent_backend.py`(Claude `-p --input-format stream-json --output-format stream-json`, Codex `exec --json`; 한 가지 정규화 이벤트 모양 Init/PreToolUse/PostToolUse(Failure)/Text/Result/Stop/Error; usage·limited 감지; `AgentProcess` = 프로세스 하나 + 리더 스레드) · `core/agent_chat.py`(칩당 모는 세션 하나: Claude는 프로세스 하나가 대화 전체, Codex는 턴마다 프로세스 + `resume <thread>`, 턴 중 메시지는 큐; 읽기 전용 질문은 별도 단발 프로세스; Flask를 모름) · `web/chat_api.py`(`/api/agent/chat/backends|status|start|send|end|events|ask|ask/<id>`; 모든 이벤트는 hook이 쓰는 같은 `agent_events/<날>.jsonl`에 **디스크 먼저**, 그다음 링·journal 줄·wake 한 번) · `mcp.py` READ_ONLY 모드(`SM_MCP_MODE=readonly`: 쓰기 도구 7개가 목록에서 사라짐) · `agent_api` 변경(세션 Stop 문이 `now`면 프로세스 트리 kill, chat 이벤트는 자기 칩을 앎, `limited_until` 문자열 관용) · `tests/fake_agent_cli.py`(두 방언을 말하는 가짜 CLI: FAKE_FAIL/LIMIT/CRASH/TOOL/ECHO_STDIN/LINGER) · `test_agent_backend`(16) · `test_chat_api`(26).

**실 CLI가 찾아낸 것(전부 PJ 사본 위, 실제 칩은 읽기만).**

1. Windows에서 npm `.cmd` 셔임(codex) 경유, 또는 `shell=True`면 argv 원소 안의 **개행에서 명령줄 전체가 잘린다**. 측정: `["a", "line1\nline2", 'q"uote', "x"]` → 자식은 `["a", "line1"]`. Claude의 `--append-system-prompt`도 같은 길이었다. → Codex 프롬프트는 stdin(`codex exec`는 인자가 없으면 stdin을 읽는다 — 도움말 그대로), exe는 `shutil.which`로 해석해 shell 없이 실행(`.cmd`도 CreateProcess가 돈다 — 측정), 셔임이면 남은 argv의 개행을 공백으로(`resolve_command`).
2. `-c mcp_servers.sm.env={PYTHONPATH="D:\work\…"}`: TOML basic string에서 `\w`는 **잘못된 이스케이프** → Codex는 값 전체를 raw string으로 읽고 `Error loading config.toml: invalid type: string`으로 자기 설정을 거부했다(spike는 슬래시 경로였다). → `toml_str`: 리터럴 문자열 `'…'`, 따옴표가 든 값만 basic string 이스케이프. `tomllib` 왕복 핀.
3. Codex 프로세스는 `turn.completed` 뒤 **잠깐 더 산다**. 그 순간 보낸 메시지는 큐에 들어가고 Stop 이벤트는 이미 지나가 영원히 안 나갔다(180 s 타임아웃). → `AgentProcess.on_exit` 콜백이 큐를 배출. 가짜 CLI의 `FAKE_LINGER=1`이 그 상태를 재현.
4. calibrations 폴더가 없을 때 cwd가 **서버의 cwd(= SM 저장소)**였다. Claude는 저장소의 CLAUDE.md를 랩의 것으로 읽고 Bash로 `claude -p`를 직접 띄우려 3분을 썼다. → 빈 `instance/agent_home`이 기본 cwd. SM 저장소는 절대 아니다.
5. 재시작 후 링이 어제·오늘의 chat 이벤트를 **옛 `n`으로 재생**하는데 카운터는 1부터 → `after=`를 든 클라이언트에 새 이벤트가 하나도 안 보였다(세 턴이 보이지 않았다). → 카운터는 링의 최대 n에서 시작.
6. haiku는 MCP 도구가 바로 있어도 먼저 ToolSearch/Bash로 우회를 시도한다(3회 중 3회; `python -m quam_state_manager.mcp sm_status`, `claude -p --tool …`). 규칙 한 줄 추가("mcp__sm__*는 이미 있다 — Bash·python -m·다른 claude/codex로 부르지 마라"). 완전히 사라지진 않을 것이다 — S6 카드가 Bash 우회를 눈에 띄게 보여준다.
7. `_limited`가 돌려주는 시각 문자열("2:50pm")이 pill의 `float()`에 들어가면 500이었다 → `reset_timestamp`(오늘, 지났으면 내일) + `_limit_until`(못 읽으면 문자열 그대로 보임).

**시간(실 CLI, PJ 사본, Claude haiku / Codex 기본).** Claude 1턴 16.2 s(ToolSearch → sm_status → journal_append → 한 줄 답), 2턴 2.1 s(컨텍스트 유지), 읽기 전용 질문 12–20 s(state_edit 없음을 스스로 말함). Codex 1턴 22.2 s, 2턴 `resume`으로 컨텍스트 유지, 질문 21–24 s(`--approve-for-me` + `SM_MCP_MODE=readonly`; "read tools에 제한돼 바꿀 수 없다"). end 뒤 프로세스 소멸, Stop now 뒤 Stop 이벤트 `stopped=True`. **4차(모든 수정 뒤, 실패 0):** Claude 1턴 7.9 s(Bash 우회 없이 ToolSearch → sm_status → journal_append), 2턴 1.8 s; Codex 1턴 22.2 s, 2턴 6.1 s(같은 thread로 resume, 큐 `0`); 질문 Claude 11.2 s / Codex 19.3 s. 앞의 세 실행(1–3차)이 위 결함 1–6을 하나씩 드러냈다 — 실 CLI 없이는 어느 것도 픽스처에서 나오지 않았다.

**Mutation.** 1라운드 26개 중 23 RED. GREEN 셋은 모두 **픽스처가 그 상태에 못 닿은 것**(문 없이 쓰인 stop 플래그의 배출 거부, 가짜 CLI가 시스템 프롬프트를 볼 수 없었음, `claude_says` 기본 OFF라 "사람의 Stop을 에이전트 말로 적지 않는다" 핀이 공허) → 픽스처를 넣고 7/7, 종료 콜백·home 4/4, 커서 1/1. **38/38.**

**S4에서 뒤로 넘긴 것.** `--include-partial-messages`(S6 패널의 글자 스트리밍) · 터미널 Codex hook 페이로드(pill 툴팁 `idle · 추적 안 됨`, S6/S8) · `agent_says` 기본 ON + `by_claude`/`by_codex` 라벨(S8; 지금은 기본 OFF, `Claude:`/`Codex:` 접두만) · 세션의 칩 점유를 쓰기 도구 첫 호출로(S5) · 남의 세션 Stop 확인 문구(S6) · PyInstaller 번들에서 MCP 서버의 python(`sys.executable`이 exe가 됨; S7 설정에서 env python으로).

### S5 — `run_node`: SM이 노드를 돌린다, 그래서 저자가 확정된다 (2026-09-06)

**들어간 것.** `core/agent_runs.py`(엔진, Flask 모름: `check_gates` = 거부를 **데이터로** 순서대로 — `no_env` · `no_calibrations_folder` · `node_not_found`(+`available`) · `not_a_node`(graph/훅 없음) · `stopped_by_human` · `past_stop_by` · `no_start_token`(규칙 0) · `run_active` · `awaiting_approval`(같은 대상에 미결 승인 → 그 체인 정지; ask-all은 run 승인 요청을 먼저 파일) · `human_active` · `orphan_running`(foreign owner / 살아 있는 worker) · `simulate_on_in_auto`; `make_scratch` = `instance/agent_runs/<key>/quam_state` + `before/`; `diff_states`(merged state+wiring의 leaf diff, NaN=NaN, 타입 변화는 변화); `classify`(ok / hardware_contention / node_error / timeout / cancelled / skipped / unattributed); `limits_hold`(max_writes_per_plan, max_delta per family); `Registry`(키별 meta.json, `wait(key, wait_s)`, 재시작 뒤엔 `interrupted`); `_drive` = chassis에 `add_item(state_path=scratch)` → `start` → 1 초마다 `touch_ui` 하트비트 + Stop now → `cancel` + per-call timeout → `cancel` → `tail_log` → 아이템 제거 → diff → 귀속(`_attribute`: 이름·시간창, 6 s 재폴, 스토어 없으면 즉시 포기) → 쓰기 라우팅 → `story.record_agent_run` → journal 줄 → notify) · `core/approvals.py`(**hold는 tray 플래그가 아니라 승인 큐**: working copy는 통째로 apply되므로 tray 안의 held 행을 문 하나가 건너뛸 수 없다 — 보류된 쓰기는 tray 밖 `instance/agent_approvals/<chip>.json`에 run 하나당 레코드 하나, pill의 `waiting`이 그 수, approve는 사람의 press로 같은 문을 통과, 행 편집 가능) · `web/agent_api.py`(`POST /run-node`, `GET /run/<key>`, `GET /runs/agent`, `POST /session/arm|disarm`, `GET /approvals`, `POST /approvals/<id>/approve|reject`, `POST /undo-mine`, `GET /live-diff`; `_stage_writes` = 한 그룹(`agent:<key>`, actor by_*)으로 staging 후 **같은 문** `/state/apply-to-live`를 `test_request_context`로 호출 — 에이전트의 press는 `X-SM-Agent`, 승인은 사람의 `X-SM-Actor`; 문이 거부하면 그룹을 도로 undo하고 승인으로 보냄; `/chip`에 `waiting`·`run_active`·`stale_since`) · `routes.py`(chassis 잠금 가드가 에이전트 run 중엔 **에이전트의 문구** `agent_running`으로 답함 — 에이전트 자신의 state_edit도 기다린다; `/state/apply-to-live`는 `X-SM-Agent` press에 사람 행이 있으면 `human_groups` 409) · `scheduler.py`(아이템별 `state_path` — chassis 변경은 이 한 필드) · `agent_session.py`(`start_token`/`armed_by`/`armed_at`/`run_key`; Stop은 토큰을 회수) · `mcp.py`(`run_node`·`run_wait`·`approvals`·`undo_mine`; `SM_CHIP` 핀 = 다른 칩이 열려 있으면 **모든** 도구 `chip_mismatch`; `take_live`가 `changed/overlap/reverted`를 돌려줌) · `agent_backend`/`agent_link`(run_node가 wait_s만큼 막히므로 Claude `MCP_TOOL_TIMEOUT`, Codex `tool_timeout_sec`, 링크의 per-call timeout).

**핀.** `tests/test_agent_runs.py` 28개 — chassis는 진짜(큐·worker 스레드·start/cancel·하트비트), 서브프로세스(`scheduler._run_item`)만 가짜(아이템의 scratch state를 qualibrate 노드처럼 편집). 가짜가 찾아낸 것 셋: ① Dry run(`global_simulate`)은 **칩 스코프별** 설정(`_shared.json`이 아님) — 픽스처가 잘못된 파일에 썼다; ② Windows에서 worker가 `os.replace`로 큐 파일을 다시 쓰는 순간 잠금 없는 `load_queue`는 **빈 상태**를 돌려준다(관용 로더) → "the queue item vanished"; 읽기도 `_QLOCK` 아래로; ③ 귀속 재폴이 스토어 없는 칩에서 20 s를 기다려 wait_s를 넘겼다 → `list_runs() is None`이면 즉시 포기, 6 s. **Mutation 35/35** — 첫 sweep에서 GREEN 셋: ① 귀속이 옛 run을 잡아도 통과(픽스처의 최신 run이 먼저 매치) → "2020년 run만 있는 칩"이 `unattributed` 핀; ② 스토어 없는 칩의 6 s 재폴 → 시간 핀(< 4.5 s); ③ **"문이 거부하면 그룹을 되돌린다"가 공허** — 문은 칩이 움직인 것을 **저장 뒤에** 알아채므로(docs/65의 re-apply stash) 되돌릴 change log가 이미 비어 있고, 에이전트의 값은 tray에서 사라진 채 working copy에 반쯤 남았다. 고침: 문을 두드리기 전 `live_diverged_now`로 먼저 묻고, 거부면 그룹을 되돌려 승인 큐로; 승인 클릭도 **먼저 문 통과, 실패면 pending 유지 + 이유**(`take live, then approve again`). 이 셋을 재현하는 픽스처(칩이 노드 실행 중 밖에서 움직임)를 넣고 35/35. Sweep 스크립트 자체의 앵커 실수 둘(파일 오지정)로 두 번 죽었고 — 그 사이 사용자가 "멈춘 것 같다"고 알아챘다 — 대기 루프는 성공 문자열만 기다리고 있었다: 완료 알림 방식으로 바꿈.

**S5에서 넘긴 것.** 실제 노드 서브프로세스 한 바퀴(qualibrate 설정을 scratchpad 저장소로 핀한 `--config-file`; S9 실 CLI 루프) · `hardware_contention` 시그니처는 qm 클라이언트 문구의 첫 컷(실 로그 corpus 없음 — S9) · 세션의 칩 점유(`claimed_by_tool`)는 run_node에서 기록만 · 새 키(created)/삭제된 키는 staging하지 않고 `unstaged`로 이름만(Datasets → Apply to chip이 그 길).

### S6 — Agent home · 떠다니는 패널 · 카드 (2026-09-06)

**들어간 것.** `core/agent_plans.py`(plan = 데이터: 에이전트의 `plan_propose` 또는 사람의 `/run <node> <targets> k=v` 한 줄(모델을 거치지 않음; 값은 int/float/bool/null로 강제)에서 만든 step 목록; `draft → running → done|failed|stopped|cancelled`, step은 `run_node(plan_id, step)`가 보고한 것만으로 갱신되고 plan 상태는 step에서 **유도**된다) · `web/agent_api.py`(`GET /chat/cards` = **하나의 피드**: 디스크에 있는 chat 이벤트(사람의 메시지 `User` 이벤트 포함 — 재시작 뒤에도 카드가 남는다)를 카드로, 답변은 journal 렌더러의 HTML로; `live.plans/runs/approvals`는 id로 upsert; `/plans` GET/POST, `/plans/<id>` GET, `/plans/<id>/mode|start|cancel`; `/chip`에 `plan`) · **규칙 0이 라우트다**: `POST /plans/<id>/start`는 사람의 클릭만(`by_*` 403) — 세션을 arm하고, **plan 시작 전 스냅샷**(`before plan <제목>` 라벨; 카드의 "state before this plan"이 State History로 간다)을 찍고, 카드의 모드를 Limits·세션에 적용하고, 모는 세션에 plan을 첫 메시지로 보낸다(세션이 없으면 그 메시지로 시작); Stop은 plan 레코드도 `stopped`로 닫는다(`ended_by`, 남은 step `cancelled`) · `_may_change`: families의 **update target 템플릿**(run-derived, docs/78 D-14)을 대상별로 채우고 칩이 지금 가진 값을 붙인다 — 모르는 family는 아무것도 주장하지 않는다 · `web/static/agent.js`(**하나의 렌더러, 두 mount**: 칩이 열려 있을 때 `GET /`은 Agent home(`_agent_home.html`), 다른 페이지에선 도구 줄 **Agent** 버튼 → body-level 떠다니는 패널(`#agent-popover`, FloatPanel drag/resize, 같은 피드); 카드 = user/answer/tool/error/limited/stop + plan(step 표, 바뀔 수 있는 값, 모드 선택, **Start — N value(s) may change** / Cancel / 진행 / Stop / state-before) + run(run 링크, writes 표, log tail) + approval(**행 편집 가능**, Write to chip / Reject); 오른쪽 "지금" 칼럼 = pill 문장(AgentPill.describe 재사용) · 세션 줄 · armed/not armed · 오늘 events/✗/waiting · Arm/Disarm · Stop after/now · End · **observer 토글**(localStorage, 이 창의 Start/Stop/approve/Arm 문을 숨김) · Calibration log 링크; 입력: `/run …`은 `/api/agent/plans`로, 나머지는 살아 있는 세션이면 `send`, 없으면 `start`(백엔드 선택, 없는 CLI는 disabled); preset 셋은 **초안만** 채움; `beforeunload`는 턴 중일 때만; polling은 live-wake `sm:runs-changed`(agent_seq) + 활동 중 4 s / 유휴 30 s) · `routes.home()`(칩 열림 → Agent home; 아니면 기존 랜딩 + **"QUAlibrate의 현재 프로젝트 <이름> 열기"** 버튼) · base.html(core script `agent.js`, 도구 줄 버튼, 팔레트 "Agent home") · `mcp.py`(`plan_propose`/`plan_status`, `run_node`에 `step`; 규칙 문장에 "하드웨어를 돌릴 지시는 먼저 plan_propose") · `chat_api`(사람 메시지를 `User` 이벤트로 기록).

**핀.** `tests/test_agent_panel.py` 11(피드·plan·Start의 네 효과·run_node → 카드·Stop → plan·홈/랜딩·브리지 도구) + `tests/agent_panel_selfcheck.cjs` 26(jsdom, 진짜 agent.js: 카드 렌더, Start POST, 편집된 승인 값, `/run` 분기, 세션 유무 분기, observer, 떠다니는 mount) — **mutation 18/18**. 기존 랜딩 핀 둘은 설계 변경(칩이 열려 있으면 `/`는 Agent home)에 맞춰 "칩을 닫으면 랜딩"으로 고침.

**실브라우저(CDP, headless Chrome, PJ 사본, 실 claude).** `/` → Agent home mount → `/run 05_power_rabi q1 num_shots=200` → 초안 카드 7 ms("Start — 2 value(s) may change"; "pi amplitude `qubits.q1.xy.operations.x180.amplitude` now: not set (operation x180 assumed)") → observer 토글이 Start/Arm을 숨기고 되돌림 → **Start 클릭** → 카드 RUNNING 616 ms · armed · `[Start] plan` 카드 → 실 Claude의 **새** 답변 6.2 s("Running plan … step 0 now: checking SM status, journaling the reason, then launching 05_power_rabi on q1") → Stop now → "stopped" + Stop 카드 → `/bulk`에서 Agent 버튼 → 떠다니는 패널 327 ms(compact, 같은 65개 카드). 프로브의 실수 둘(첫 실행에서 옛 답변 카드를 새 것으로 읽음, 하루의 이전 plan 카드를 초안으로 집음)은 프로브를 고쳤고, `now: not set`은 PJ 사본의 `x180`이 포인터 alias라 `store.get_value`가 못 푸는 것 — S9에서 resolved 읽기로.

**S6에서 넘긴 것.** `--include-partial-messages` 글자 스트리밍(카드는 턴 단위) · 답변 카드의 "표 먼저·숫자마다 #run·썸네일"은 모델의 markdown에 맡김(S8/S9에서 runs_summary 도구가 표를 만들면 연결) · "남의 세션 Stop 확인" 문구(관찰자 토글로 대신, 이름 선택기는 S8) · 용어 규칙의 라벨(Live State Edit 열 라벨)은 dot path 꼬리 + 툴팁으로 근사 · `/qualibrate/open`은 여전히 `/qubits`로 감(S9에서 Agent home으로).

### 검증 라운드 1 — S4·S5·S6 적대 리뷰 (커밋 c7cc5cd, 2026-09-06)

네 차원(엔진·웹·기록·UX)으로 S4(채팅)·S5(run_node)·S6(Agent home)을 리뷰했다. **가능하면 실제 브라우저에서** — 리뷰어는 실 claude/codex CLI와 실 headless Chrome(CDP)에서 재현한 뒤에만 발견을 인정했다. 모든 발견은 고치기 전에 다시 실행해 확인했다. 리뷰어의 재현 테스트 38개를 고친 코드에 돌려 24개가 통과(결함 닫힘), 14개는 실패 — 그중 **진짜 프로덕션 결함은 5개**, 나머지는 shipped된 안전한 설계(예: `queue_not_empty` 가드가 아예 거부)와 리뷰어가 `_chip_name()`을 라우트의 `_chip_key()` 자리에 쓴 테스트 아티팩트였다.

**기록(R3/R4) — 진짜 결함.**
- **이벤트가 이름이 아니라 키를 달고 있었다.** chat 이벤트는 칩의 **records 키**(`<name>-<hash8>`)를 달았는데 피드·events는 **이름**으로 거른다 — `agent_backend._mk`가 이미 `chip`을 AgentProcess의 키로 찍고 `ChatSession._on_event`가 `setdefault`만 했다. 이제 `rec["chip"] = self.display`로 **강제**한다(프로세스는 키를 알고, 이벤트는 이름을 단다).
- **Calibration log가 다른 칩의 run을 주장했다.** `build_day`가 agent-run 인덱스를 **칩으로 거르지 않아** 칩 A의 #104가 칩 B의 #104를 저자했다(run id는 데이터 폴더별). `load_agent_runs(inst, chip)`으로 고침.
- **커서 n이 경쟁했다.** 요청 스레드가 사람의 `User` 이벤트를, 프로세스 리더 스레드가 `Init`을 동시에 기록하면 둘이 같은 `cur`를 읽어 같은 n을 찍었다 — `after=n`을 든 페이지가 진 쪽을 영영 못 본다. read-modify-write를 `_N_LOCK` 아래로. 그리고 hook 이벤트 폭주가 링에서 chat 이벤트를 밀어내도 다음 n이 그 위로 가도록, 카운터를 **링이 아니라 당일 파일**에서 끌어올린다.
- **journal 렌더러가 앵커를 중첩시켰다.** 링크 href 안의 `#N`(예: `[z](/a?x=#123)`)이 **중첩 `<a>`**가 됐다. `#run` 링커를 `_LINK`가 만든 앵커 **바깥** 세그먼트에서만 돌린다.

**문·run(R1).** 승인 하나 = run 하나(`used_by_run`; 같은 id로 두 번째 run은 거부); all-or-nothing — 새/삭제 키처럼 staging 못하는 값이 하나라도 있으면 그룹을 거부하고 이름을 대며 **쓰기를 사람에게 park**(반쯤 안 남긴다); `run_active` 뷰는 칩 **키**로 조회; simulate run의 값에는 `simulated` 표시.

**패널(R2) — 실 Chrome 확인.** 카드는 도착 순서와 무관하게 **시간순**으로 자리 잡는다(live 객체는 매 폴, chat 카드는 한 번뿐이라 append하면 옛 plan이 새 답변 밑으로 갔다); 손가락이 올라간 카드는 재렌더하지 않고 focus가 떠나면 따라잡는다; 안 바뀐 카드는 재렌더 안 함; 피드는 읽는 이가 바닥에 있을 때만 따라간다; **Enter 전송·Shift+Enter 줄바꿈**; 거부된 줄은 상자에 남는다; 닿지 않는 SM은 그렇다고 말한다; run 요청은 **Allow run**; Stop now는 흰 글씨 채운 빨강(`rgb(136,57,53)` 확인); Send 버튼은 `width:auto`(53 px vs 행 694 px); 떠다니는 패널의 **home**은 순수 href(hx-get 아님); 팝오버는 폼을 고정하고 카드만 스크롤(`overflow-y:auto`, formInside 확인). mutation 19/19, jsdom 50 asserts.

**핀.** `test_journal`(중첩 앵커) · `test_story`(칩별 저자) · `test_chat_api`(커서 동시성 + 링 축출) · `test_agent_runs`(승인=run 하나, all-or-nothing) · `test_agent_api`(이벤트 날짜·한 번만·claude_says) · `test_agent_panel`(순수 home 링크) · `agent_panel_selfcheck.cjs`(R2 전부). CDP: `/` → Agent home, Send 폭, home 순수 링크, Stop now 색, simulated 플래그, 승인 라벨, 떠다니는 폼 고정 — 전부 확인.

**리뷰가 드러낸 문서 정정 8개(앞 절들에 흩어진 주장).**
1. 승인은 **디스크에 산다**(`instance/agent_approvals/<chip>.json`) — tray 플래그가 아니다. run 하나당 레코드 하나.
2. 페이지 커서 n은 재시작을 건너 단조 — **당일 파일**까지 스캔하므로 링 축출로도 리셋되지 않는다(앞서 "링 800줄/2일" 표현은 backstop 하나만 가리켰다).
3. Stop now는 **프로세스 간** — 두 번째 창의 Stop이 첫 창의 살아 있는 claude pid를 죽인다(`kill_tree`), 파일의 stop 플래그와 별개로.
4. Registry는 재시작 뒤 in-flight run을 `interrupted`로 표시하고 **그 plan step을 failed·plan을 stopped로** 화해시킨다(meta의 `chip`은 키).
5. agent-run 인덱스는 run id로 키되지만 `build_day`·`_human_ran_recently`는 **칩으로 거른다**(둘 다 프로덕션 호출은 `_chip_key()`를 넘긴다).
6. `human_active`는 에이전트 자신의 unattributed run을 사람의 것으로 보지 않는다 — `unattributed_agent_runs(chip)`가 활성 칩으로 걸러 자기 것을 안다.
7. `User`(사람 메시지) 이벤트는 세션의 다른 이벤트와 같은 `owner`/`mode`를 달고 디스크에 남아 재시작을 견딘다.
8. 전부 skip된 plan은 `done`이 아니라 `skipped`(아무것도 안 돌았다).

### S7 — 설정: 이 PC를 SM에 연결, 미리보기 다음 클릭 (커밋 8c20b71, 2026-09-06)

Agent → 설정. 랩이 Claude/Codex를 SM에 잇는 한 곳. **이 PC에서 아직 안 된 것만** 펼쳐 보이고, 모든 쓰기는 **미리보기(diff) 먼저·클릭 다음**, SM이 손대는 파일마다 날짜 붙은 백업(`*.sm-backup-YYYYMMDD-HHMMSS`)이 옆에 남는다. 유일한 실 호출은 진짜 read-only 질문 하나(시간과 답을 그대로).

**들어간 것.** `core/agent_setup.py`(레코드·백업·hook 명령·MCP 서버 스펙; Claude `~/.claude.json` mcpServers `quam-state-manager` + `~/.claude/settings.json` hooks 병합 + `.claude/settings.local.json` allow 규칙; Codex `config.toml` 마커 사이 블록; 랩 컨텍스트 파일을 `<!-- sm:lab-context:start/end -->`로 fenced; `detect_facts`/`questions`/`status`) · `web/setup_api.py`(`GET /api/agent/setup` 상태, `POST /connect`(apply 없으면 미리보기)·`/disconnect`·`/journal`·`/context`·`/test`; `GET /agent/setup` 페이지) · `static/agent-setup.js` + `_agent_setup.html`(페이지는 `GET /api/agent/setup`에서 렌더; 안 된 항목만 펼침; diff는 LCS 줄 단위) · `app.py`가 blueprint 둘 등록 · base.html이 페이지를 자기 번들(`agent_setup`)로 include · hook.py가 커스텀 instance dir에 `--instance <dir>`를 hook 줄에 쓴다.

**핀.** `test_agent_setup.py`(14) · `agent_setup_selfcheck.cjs`(16) · `test_agent_panel`의 설정-페이지 배선 핀 · `test_hook`의 `--instance` 핀. 실 Chrome: `/agent/setup` 렌더, 번들·페이지 표, HX 폼은 partial 하나.

**S7에서 넘긴 것.** UI에서 Limits/모드 카드(패널의 "지금" 칼럼이 이미 arm/mode를 쥔다) · journal 미러/동기화 감지의 실 SMB 경로(S9) · PyInstaller 번들에서 MCP 서버의 python은 설정의 env python으로(스펙엔 있으나 실 exe 빌드 검증은 S9).
