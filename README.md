# fde-fit

`fde-fit`은 특정 회사의 원티드 활성 채용공고를 모두 확인하고, 실제 업무가
FDE(Forward Deployed Engineer)에 가까운 공고를 찾아 후보자와의 직무 적합도를
분석하는 프로그램입니다.

공고 제목에 `FDE`가 없어도 고객 문제 정의, 직접 구현과 배포, 고객 현장 협업
등의 업무가 포함되어 있으면 FDE형 공고로 판단할 수 있습니다. 선택된 공고는
LangGraph 워크플로로 분석하며, 결과를 JSON과 브라우저에서 열 수 있는 HTML로
저장합니다.

## 한눈에 보기

원티드에서 데이원컴퍼니의 활성 공고를 수집하고, FDE 유사도를 분류한 뒤, 선택한
`Forward Deployed Engineer` 공고를 회사·직장 조사와 후보자 적합도 분석으로
연결합니다.

```mermaid
flowchart LR
  A[원티드 활성 공고] --> B[전체 공고 수집]
  B --> C[FDE 유사도 분류]
  C --> D[데이원컴퍼니 FDE 선택]
  D --> E[회사·직장 조사]
  E --> F[직무 적합도 분석]
  F --> G[JSON + HTML 보고서]
```

### 예시 결과: 데이원컴퍼니 FDE

아래 화면은 후보자 식별 정보와 연봉 등 보상 정보를 제거한 공개용 HTML 예시입니다.

[![데이원컴퍼니 FDE 직무 적합도 예시](docs/day1-fde-example.png)](docs/day1-fde-example.html)

예시 원문: [docs/day1-fde-example.html](docs/day1-fde-example.html)

## 주요 기능

- 회사명으로 원티드 회사 검색
- 해당 회사의 활성 채용공고 전체 조회
- 공고 상세 내용 병렬 수집
- 모든 활성 공고의 FDE 유사도 분석
- FDE형 공고의 점수, 신뢰도와 판정 근거 출력
- 회사 및 직장 정보 병렬 조사
- 포지션의 FDE/SI 특성 분석
- 후보자 우선순위를 반영한 최종 직무 적합도 평가
- 회사명 기반 JSON 및 HTML 보고서 생성
- 기존 로컬 `job.txt` 분석 지원

## 전체 처리 흐름

```mermaid
flowchart TD
    A[회사명 입력] --> B[원티드 회사 검색]
    B --> C[활성 채용공고 전체 조회]
    C --> D[공고 상세 병렬 조회]
    D --> E[공고별 FDE 유사도 분석]

    E --> F{FDE 유사도 6점 이상?}
    F -- 없음 --> G[분석 종료]
    F -- 있음 --> H[FDE형 공고 목록 출력]

    H --> I{FDE형 공고가 여러 개인가?}
    I -- 예 --> J[사용자가 공고 선택]
    I -- 아니오 --> K[공고 자동 선택]
    J --> L[LangGraph 직무 분석]
    K --> L

    L --> M[회사 및 직장 정보 병렬 조사]
    M --> N[최종 직무 적합도 평가]
    N --> O[회사명 JSON 생성]
    N --> P[회사명 HTML 생성]
```

## FDE형 공고 판정

회사의 모든 활성 공고를 최대 5개씩 묶어 `gpt-5-mini`로 분석합니다. 직무명보다
공고의 주요 업무와 자격요건을 우선적으로 평가합니다.

### 주요 판단 기준

- 고객 또는 고객사 현장에서 직접 긴밀하게 일하는가
- 고객의 모호한 문제를 기술 문제와 요구사항으로 정의하는가
- 엔지니어가 직접 코드를 작성하고 솔루션을 구현하는가
- PoC부터 배포와 운영 안정화까지 end-to-end로 책임지는가
- 현장에서 얻은 피드백을 공통 제품이나 플랫폼 개선으로 환원하는가
- 제품팀과 고객 사이에서 기술적 가교 역할을 하는가

### FDE로 판단하지 않는 사례

- 고객 접점이 없는 일반적인 내부 제품 개발
- 코딩과 구현 책임이 없는 영업, CSM, PM 또는 컨설팅
- 단순 기술지원
- 정해진 요구사항만 구현하거나 유지보수하는 일반 개발
- 회사 소개에만 AI나 B2B가 있고 해당 포지션 업무에는 FDE 신호가 없는 경우

### 점수 기준

| 점수 | 의미 |
| ---: | --- |
| 0~2 | FDE와 무관 |
| 3~5 | 일부 유사하지만 FDE로 보기 어려움 |
| 6~7 | 직무명이 달라도 실제 업무가 FDE에 가까움 |
| 8~10 | 명백한 FDE 역할 |

FDE 유사도가 `6점 이상`이면 FDE형 공고로 취급합니다. 제목에 `FDE` 또는
`Forward Deployed Engineer`가 명시된 공고는 누락되지 않도록 최소 8점으로
보정합니다.

## LangGraph 분석 구조

선택된 FDE형 공고는 기존 분석 입력 형식으로 변환된 뒤 다음 그래프에서
처리됩니다.

```mermaid
flowchart TD
    A[parse_job] --> B[research_company]
    A --> C[research_workplace]

    B --> D[analyze_fde_si]
    C --> E[analyze_workplace]

    D --> F[final_job_fit]
    E --> F
```

`research_company`와 `research_workplace`는 `parse_job`이 끝난 후 병렬로
실행됩니다. 두 분석 브랜치가 모두 끝나야 `final_job_fit`이 한 번 실행됩니다.

### 노드별 역할

| 노드 | 역할 |
| --- | --- |
| `parse_job` | 공고에서 회사명, 포지션, 주요 업무, 자격요건과 기술 스택 추출 |
| `research_company` | 제품, 고객 사례, 사업 모델과 최근 활동 조사 |
| `research_workplace` | 연봉, 워라밸, 조직문화, 경영진, 안정성과 성장성 조사 |
| `analyze_fde_si` | 포지션이 FDE와 SI 중 어디에 가까운지 평가 |
| `analyze_workplace` | 직장 환경의 강점, 위험과 면접 확인 질문 생성 |
| `final_job_fit` | 후보자 프로필과 모든 분석 결과를 종합해 최종 적합도 평가 |

## 요구 사항

- Python 3.12 이상
- [uv](https://docs.astral.sh/uv/)
- OpenAI API 키
- Tavily API 키

의존성을 설치합니다.

```bash
uv sync
```

프로젝트 루트에 `.env` 파일을 만들고 인증 정보를 설정합니다.

```dotenv
OPENAI_API_KEY=your-openai-api-key
TAVILY_API_KEY=your-tavily-api-key
```

OpenAI는 활성 공고의 FDE 유사도 분류와 LangGraph의 구조화 분석에 사용합니다.
Tavily는 회사와 직장 관련 공개 자료를 검색하는 데 사용합니다. 원티드 공고
조회 자체에는 별도 인증 정보가 필요하지 않습니다.

## 후보자 프로필

기본 후보자 정보는 `candidate_profile.json`에서 읽습니다. 최종 평가는 이 파일의
경력, 선호 조건과 `priorities`를 반영합니다.

다른 프로필을 사용하려면 실행 시 파일을 지정합니다.

```bash
uv run python workflow.py \
  --company "회사명" \
  --candidate-profile another_profile.json
```

## 실행 방법

### 원티드 회사 검색 후 분석

```bash
uv run python workflow.py --company "회사명"
```

활성 공고가 없거나 실제 업무가 FDE에 가까운 공고가 없으면 추가 LangGraph 분석
없이 종료합니다.

### HTML 보고서 자동 열기

```bash
uv run python workflow.py --company "회사명" --open
```

### 특정 FDE형 공고 선택

FDE형 공고가 여러 개면 대화형 터미널에서 분석할 공고를 선택합니다. CI 같은
비대화형 환경에서는 원티드 공고 ID를 명시해야 합니다.

```bash
uv run python workflow.py \
  --company "회사명" \
  --job-id 123456
```

### 결과 저장 위치 지정

```bash
uv run python workflow.py \
  --company "회사명" \
  --output-dir reports
```

### 기존 `job.txt` 분석

`--company`를 생략하면 기존 로컬 공고 파일을 분석합니다.

```bash
uv run python workflow.py
```

다른 공고 파일을 사용하려면 다음과 같이 실행합니다.

```bash
uv run python workflow.py --job-file another_job.txt
```

### 전체 옵션 확인

```bash
uv run python workflow.py --help
```

## 결과 파일

분석이 완료되면 지정된 출력 디렉터리에 JSON과 HTML 파일을 함께 생성합니다.

```text
회사명_final_job_fit.json
회사명_final_job_fit.html
```

JSON은 다음 구조를 유지합니다.

```json
{
  "fit_score": 8.0,
  "confidence": 0.8,
  "recommendation": "지원 추천",
  "why_fit": [
    "후보자와 포지션이 잘 맞는 이유"
  ],
  "concerns": [
    "지원 또는 입사 시 주의할 사항"
  ],
  "must_verify_before_joining": [
    "면접이나 입사 전에 확인해야 할 내용"
  ],
  "final_conclusion": "최종 판단"
}
```

HTML 보고서에는 다음 내용이 표시됩니다.

- 회사명과 포지션
- 원티드 원본 공고 링크
- 직무 적합도와 분석 신뢰도
- 지원 추천
- 적합한 이유와 우려 사항
- 입사 전에 반드시 확인할 내용
- 최종 결론
- 원본 JSON

## 프로젝트 구성

| 파일 | 역할 |
| --- | --- |
| `workflow.py` | CLI, 전체 실행 흐름과 LangGraph 정의 |
| `wanted_jobs.py` | 원티드 회사·공고 검색과 상세 병렬 조회 |
| `classify_fde.py` | 모든 활성 공고의 FDE 유사도 분석 |
| `parse_job.py` | 채용공고 구조화 |
| `research_company.py` | 회사와 사업 조사 |
| `research_workplace.py` | 직장 관련 근거 조사 |
| `analyze_fde_si.py` | FDE 및 SI 특성 평가 |
| `analyze_workplace.py` | 직장 환경 평가 |
| `final_job_fit.py` | 최종 직무 적합도 평가 |
| `job_state.py` | LangGraph 공유 상태 정의 |
| `html_report.py` | JSON 및 HTML 보고서 생성 |
| `candidate_profile.json` | 후보자의 경력과 우선순위 |
| `job.txt` | 로컬 분석용 채용공고 |
| `tests/` | 검색, 분류, 워크플로와 보고서 테스트 |

## 테스트

```bash
uv run python -m unittest discover -s tests -v
```

테스트는 다음 동작을 확인합니다.

- 회사명 정규화 및 선택
- 활성 공고 전체 상세 조회
- 여러 배치에 걸친 전체 공고 FDE 판정
- 일반적인 직무명의 FDE형 판정
- 명시적인 FDE 제목의 누락 방지
- CLI 공고 선택과 분석 종료 조건
- 회사명 기반 JSON 및 HTML 생성
- HTML 특수문자 escaping

## 오류 처리

- 회사 검색 결과가 없으면 후보 회사 정보를 포함한 오류를 출력합니다.
- 일부 활성 공고의 상세 조회가 실패하면 불완전한 상태로 판정하지 않고 중단합니다.
- LLM이 일부 공고 ID를 누락하거나 중복 반환하면 FDE 분류를 중단합니다.
- 비대화형 환경에서 FDE형 공고가 여러 개면 `--job-id` 입력을 요구합니다.
- 잘못된 후보자 JSON이나 존재하지 않는 파일은 오류 코드 `2`로 종료합니다.

## 주의사항

- FDE 판정과 최종 적합도는 AI가 생성한 참고용 분석입니다.
- 모든 활성 공고는 FDE 후보 탐색 단계에서 분석하지만 전체 LangGraph 직무 적합도
  분석은 선택한 FDE형 공고 하나에 대해서만 실행합니다.
- 공개적으로 확인할 수 없는 연봉이나 조직문화 정보는 결과의 신뢰도가 낮을 수
  있습니다.
- 원티드 웹 응답 구조가 변경되면 공고 조회 코드 수정이 필요할 수 있습니다.
- 공고 수에 따라 OpenAI 호출 횟수와 실행 시간이 증가할 수 있습니다.
