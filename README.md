# Grafana Alert Webhook Server

Grafana의 경보(Alert) 웹훅을 수신하여 사내 인프라 자원의 사용 현황을 분석하고, SMTP 서버를 통해 일일 종합 보고서를 HTML 이메일로 자동 발송하는 FastAPI 기반 알림 중계 서버입니다.

---

## 💡 동작 원리 및 아키텍처

이 프로젝트는 Grafana 모니터링 시스템에서 인프라 임계치 초과 등의 경보(Alert) 이벤트가 발생했을 때, 지정된 URL로 HTTP POST 요청을 보내 실시간으로 데이터를 푸시(Push)해 주는 **웹훅(Webhook) 메커니즘**을 기반으로 동작합니다. 

이러한 웹훅 수신 이후, 이메일 발송 완료까지 수반되는 SMTP TLS 세션 연결 및 HTML 템플릿 렌더링 지연이 Grafana의 웹훅 송신 측 타임아웃을 유발하지 않도록 **FastAPI의 `BackgroundTasks`**를 이용한 비동기 백그라운드 아키텍처를 채택하고 있습니다.

```
[Grafana Alerting]
        │ 
        │ (1) 실시간 경보 발생 시 HTTP POST 웹훅 전송 (/webhook)
        ▼
┌──────────────────────────────────────────────┐
│ FastAPI Webhook Handler                      │
│  - Payload 추출 및 유효성 검사                  │
│  - 백그라운드 태스크 등록 (`BackgroundTasks`)   │
│  - (2) 응답 즉시 리턴 (200 OK)                │
└───────────────┬──────────────────────────────┘
                │ 
                │ (3) 백그라운드 스레드 위임
                ▼
┌──────────────────────────────────────────────┐
│ Background Task (`send_mail_task`)           │
│  - 1. 수치 데이터 검증 및 임계치 분석          │
│  - 2. HTML 메일 템플릿 렌더링                 │
│  - 3. SMTP TLS 세션 연결 및 메일 발송         │
└──────────────────────────────────────────────┘
```

---

## 🔍 핵심 기능 상세

### 1. 지표 자동 정렬 및 가독성 개선
수신된 원시 로그 데이터를 사용자가 읽기 쉽도록 일관성 있는 순서로 가동 정렬하여 보고서를 빌드합니다.
- **정렬 순서**: `CPU 사용률` ➔ `메모리 사용률` ➔ `네트워크` ➔ `디스크`

### 2. 임계치 기반 위험 자원 조기 식별
수집된 실시간 수치 중 사전에 지정한 **임계치를 초과한 위험 인프라 자원**은 이메일 최상단에 **🚨 위험 지표 요약** 섹션으로 시각화되어 집중 배치됩니다.
- **위험 판단 임계치**:
  - **CPU / 메모리 / 디스크 사용률**: `90%` 이상
  - **네트워크 대역폭**: `800 Mbps` 이상

### 3. 방어적 데이터 분석 및 파싱
Grafana의 테스트 알림이나 비정상 패인로드(Null 값 유입 등)로 인해 웹훅 서버가 중단되는 현상을 방지하도록 안전장치가 마련되어 있습니다.
- **안전한 타입 변환 (`safe_float`)**: 입출력 수치의 훼손이나 공백 유입 시 예외를 발생시키지 않고 `0.0`으로 자동 캐스팅합니다.
- **네트워크 단위 표준화**: 지표 원시 단위(bps)를 사람이 파악하기 좋은 대역폭 단위(`Mbps`)로 자동 스케일링합니다.
- **자원별 스키마 예외 처리**: 디스크 자원처럼 최고치(Max) 수집이 불필요한 지표는 메일 내용에서 `-` 기호로 표시하여 명확성을 띱니다.

---

## 📂 프로젝트 구조

```text
grafana-webhook/
├── .env                 # SMTP 서버 연결 및 이메일 수신자 설정 파일
├── .gitignore           # Git 추적 제외 항목 설정
├── main.py              # FastAPI 서버 기동 및 웹훅 처리 비즈니스 로직
├── requirements.txt     # 프로젝트 라이브러리 의존성 정의 파일
└── README.md            # 본 안내 문서
```

---

## 📡 API 명세 요약

### 웹훅 수신 API

- **Endpoint**: `POST /webhook`
- **Content-Type**: `application/json`
- **Request Payload 예시 (Grafana 규격)**:
  ```json
  {
    "receiver": "webhook-reporting-server",
    "status": "firing",
    "alerts": [
      {
        "status": "firing",
        "labels": {
          "alertname": "Daily-CPU",
          "host": "production-db-01"
        },
        "values": {
          "B": 92.5,
          "C": 98.1
        }
      }
    ]
  }
  ```
- **Responses**:
  - **`200 OK`**: 백그라운드 발송 태스크가 안전하게 등록됨
    ```json
    { "status": "success" }
    ```
  - **`400 Bad Request`**: 잘못된 바디 혹은 필수 필드 (`alerts`) 유실
    ```json
    { "status": "bad request" }
    ```

---

## 🛠 기술 스택

- **언어**: Python 3.x
- **웹 프레임워크**: FastAPI (v0.138.0)
- **ASGI 서버**: Uvicorn (v0.49.0)
- **데이터 검증 및 환경 변수**: Pydantic (v2.13.4) 및 python-dotenv (v1.2.2)

---

## ⚙️ 시작하기 및 설정 방법

### 1. 환경 변수 구성 (`.env`)
프로젝트 루트 디렉터리에 `.env` 파일을 생성하고 아래 항목들을 정의합니다.

```env
SMTP_SERVER=your.smtp.server.com
SMTP_PORT=587
SMTP_USER=sender@company.com
SMTP_PASSWORD=your_smtp_password
TO_EMAIL=recipient@company.com
```

### 2. 가상환경 활성화 및 의존성 패키지 설치
```bash
# 1. 가상환경 생성 (최초 1회 실행)
python -m venv venv

# 2. 가상환경 활성화 (Windows PowerShell 기준)
.\venv\Scripts\Activate.ps1
# Mac/Linux: source venv/bin/activate

# 3. 필요한 의존성 라이브러리 설치
pip install -r requirements.txt
```

### 3. 서버 구동
Uvicorn을 가동하여 애플리케이션 서비스를 제공합니다.
```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```
- 구동 후 `http://localhost:8000/docs`에서 OpenAPI 규격의 자동 대화식 API 문서를 열람할 수 있습니다.

---

## 🖥️ Grafana 알림 연동 방법

1. Grafana 웹 UI에 접속 후 **Alerting** ➔ **Contact Points** 탭으로 이동합니다.
2. **Add contact point**를 생성하고 Integration 항목에 **Webhook**을 설정합니다.
3. URL 주소에 아래의 수신 API 경로를 기입합니다:
   - `http://<서버IP>:8000/webhook`
4. **Test** 버튼을 눌러 연동 상태와 수신자 메일함으로 테스트 알림 메일이 도착하는지 최종 검토 후 저장합니다.
