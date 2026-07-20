import os
import smtplib
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, Request, status
from fastapi.responses import JSONResponse

# .env 설정 파일 로드
load_dotenv()

app = FastAPI(title="Grafana Alert Webhook Server")

# 환경 변수 바인딩
SMTP_SERVER = os.getenv("SMTP_SERVER")
SMTP_PORT = int(os.getenv("SMTP_PORT", 587))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
SMTP_FROM_ADDRESS = os.getenv("SMTP_FROM_ADDRESS", SMTP_USER)
SMTP_FROM_NAME = os.getenv("SMTP_FROM_NAME", "모니터링 시스템")
TO_EMAIL_RAW = os.getenv("TO_EMAIL", "")
EMAIL_LIST = [email.strip() for email in TO_EMAIL_RAW.split(",") if email.strip()]

# 네트워크 지표 제외 대상 서버 목록 (하드코딩)
EXCLUDE_NETWORK_SERVERS = {
    "DW-MES1 (MES 청주지점)",
    "DW-MES2 (MES 이천지점)",
    "Linux-Test-Server (On-Premise)",
    "Windows-Test-Server (On-Premise)",
    "Zabbix server"
}

def safe_float(value) -> float:
    """문자열이나 None 값이 유입되어도 안전하게 실수(float)로 변환합니다."""
    try:
        return float(value)
    except (ValueError, TypeError):
        return 0.0

def generate_html_table(alerts: list) -> str:
    """그라파나 JSON 데이터를 파싱하여 요약 표, 대상 리스트, 상세 현황 HTML을 생성합니다."""
    
    server_groups = {}
    critical_rows = ""  # 위험 지표 요약 표에 담길 행 데이터
    critical_hosts = set()  # 임계치를 초과한 지표가 있는 서버명을 보관할 집합
    
    # 지표 정렬을 위한 기준 정의 (CPU -> 메모리 -> 네트워크 -> 디스크)
    metric_order = {
        "Daily-CPU": 1,
        "Daily-Memory": 2,
        "Daily-Network": 3,
        "Daily-Disk": 4
    }

    # 1. 데이터 분류 및 위험 자원 선별 추출
    for alert in alerts:
        labels = alert.get('labels') or {}
        values = alert.get('values') or {}  # 그라파나 테스트 알림(null 유입) 대응 방어 코드
        host = labels.get('host', 'Unknown Server')
        alert_name = labels.get('alertname', 'Unknown')
        item = labels.get('item', '-')

        # 특정 서버의 네트워크 지표 제외 처리
        if alert_name == "Daily-Network" and host in EXCLUDE_NETWORK_SERVERS:
            continue

        # 서버 이름별 데이터 그룹화
        if host not in server_groups:
            server_groups[host] = []
        server_groups[host].append(alert)

        # 지표별 수치 추출 및 임계치 설정 (네트워크는 E,F / 나머지는 B,C 사용)
        if alert_name == "Daily-Network":
            avg_raw = safe_float(values.get('E', 0)) / 1000  
            max_raw = safe_float(values.get('F', 0)) / 1000  
            threshold = 800
            unit = " Mbps"
        else:
            avg_raw = safe_float(values.get('B', 0))
            max_raw = safe_float(values.get('C', 0))
            threshold = 90
            unit = "%"

        avg_val = f"{avg_raw:.2f}{unit}"
        max_val = f"{max_raw:.2f}{unit}" if alert_name != "Daily-Disk" else "-"

        # 임계치 초과 여부 점검 및 스타일 정의
        is_critical = False
        avg_style = ""
        max_style = ""

        if alert_name == "Daily-Disk":
            if avg_raw >= threshold:
                avg_style = "color: red; font-weight: bold;"
                is_critical = True
        else:
            if avg_raw >= threshold:
                avg_style = "color: red; font-weight: bold;"
                is_critical = True
            if max_raw >= threshold:
                max_style = "color: red; font-weight: bold;"
                is_critical = True

        # 한글 지표 명칭 매핑
        if alert_name == "Daily-CPU":
            type_name = "CPU 사용률"
        elif alert_name == "Daily-Memory":
            type_name = "메모리 사용률"
        elif alert_name == "Daily-Network":
            type_name = f"네트워크 ({item})"
        elif alert_name == "Daily-Disk":
            type_name = f"디스크 ({item})"
        else:
            type_name = alert_name

        # 위험 수치 발견 시 요약 표 행 데이터 구성
        if is_critical:
            critical_hosts.add(host)
            critical_rows += f"""
                <tr style="height:25px; background-color: #fff5f5;">
                    <td style="border: 1px solid #999999; text-align: left; padding-left: 15px; font-weight: bold;">{host}</td>
                    <td style="border: 1px solid #999999; text-align: left; padding-left: 15px;">{type_name}</td>
                    <td style="border: 1px solid #999999; {avg_style}">{avg_val}</td>
                    <td style="border: 1px solid #999999; {max_style}">{max_val}</td>
                </tr>
            """

    # 2. 모니터링 대상 서버 명단 행 생성 (이름 순 정렬)
    server_list_rows = ""
    for idx, host in enumerate(sorted(server_groups.keys()), 1):
        host_style = "color: red;" if host in critical_hosts else ""
        server_list_rows += f"""
            <tr style="height:25px;">
                <td style="border: 1px solid #999999;">{idx}</td>
                <td style="border: 1px solid #999999; text-align: left; padding-left: 15px; font-weight: bold; {host_style}">{host}</td>
            </tr>
        """

    # 3. HTML 레이아웃 조립
    html_result = ""

    # [표 1] 위험 지표 요약 표 배치
    html_result += "<h3 style=\"font-family:'Malgun Gothic'; color:#333333; margin-top:10px; margin-bottom:8px;\">🚨 <span style=\"color:#d9534f;\">위험 지표 요약</span> (임계치 초과 자원)</h3>"
    if critical_rows:
        html_result += f"""
        <table border="1" cellpadding="8" cellspacing="0" style="border-collapse:collapse; width:100%; max-width:700px; font-family:'Malgun Gothic', sans-serif; font-size:14px; border-color:#999999; text-align:center; margin-bottom:25px; border: 2px solid #d9534f;">
            <thead>
                <tr style="background-color:#f2dede; color:#a94442; font-weight:bold; height:30px;">
                    <th style="border: 1px solid #999999; width: 30%;">대상 서버</th>
                    <th style="border: 1px solid #999999; width: 30%;">지표 구분</th>
                    <th style="border: 1px solid #999999; width: 20%;">24시간 평균 (현재값)</th>
                    <th style="border: 1px solid #999999; width: 20%;">24시간 최고값</th>
                </tr>
            </thead>
            <tbody>
                {critical_rows}
            </tbody>
        </table>
        """
    else:
        html_result += """
        <div style="font-family:'Malgun Gothic'; font-size:14px; color:#3c763d; background-color:#dff0d8; border: 1px solid #d6e9c6; padding:12px; max-width:675px; border-radius:4px; margin-bottom:25px; font-weight: bold;">
            ✔ 현재 임계치를 초과한 위험 상태의 인프라 자원이 없습니다. 모든 장비가 정상 범위 내에서 운영 중입니다.
        </div>
        """

    # [표 2] 단순 모니터링 대상 서버 리스트 표 배치
    html_result += f"""
    <h3 style="font-family:'Malgun Gothic'; color:#333333; margin-top:20px; margin-bottom:8px;">🖥️ 모니터링 대상 서버 리스트</h3>
    <table border="1" cellpadding="8" cellspacing="0" style="border-collapse:collapse; width:100%; max-width:700px; font-family:'Malgun Gothic', sans-serif; font-size:14px; border-color:#999999; text-align:center; margin-bottom:25px;">
        <thead>
            <tr style="background-color:#e6e6e6; color:#333333; font-weight:bold; height:30px;">
                <th style="border: 1px solid #999999; width: 20%;">번호</th>
                <th style="border: 1px solid #999999; width: 80%;">서버명</th>
            </tr>
        </thead>
        <tbody>
            {server_list_rows}
        </tbody>
    </table>
    """

    # [표 3] 서버별 전체 자원 현황 상세 표 배치
    html_result += """
    <hr style="border:solid 1px #ccc; max-width:700px; margin-left:0; margin-bottom:20px;">
    <h3 style="font-family:'Malgun Gothic'; color:#111111; margin-bottom:5px;">📋 서버별 전체 자원 현황</h3>
    """

    for host, host_alerts in sorted(server_groups.items()):
        # 지정한 순서(CPU -> 메모리 -> 네트워크 -> 디스크)대로 내부 알림 데이터를 정렬합니다.
        sorted_alerts = sorted(
            host_alerts, 
            key=lambda x: metric_order.get((x.get('labels') or {}).get('alertname', ''), 99)
        )

        if host in critical_hosts:
            host_title_html = f"🖥️ 서버: <span style=\"color: red;\">{host}</span>"
        else:
            host_title_html = f"🖥️ 서버: {host}"

        html_result += f"""
        <h4 style="font-family:'Malgun Gothic'; color:#333333; margin-top:20px; margin-bottom:6px;">{host_title_html}</h4>
        <table border="1" cellpadding="8" cellspacing="0" style="border-collapse:collapse; width:100%; max-width:700px; font-family:'Malgun Gothic', sans-serif; font-size:14px; border-color:#999999; text-align:center; margin-bottom:15px;">
            <thead>
                <tr style="background-color:#e6e6e6; color:#333333; font-weight:bold; height:30px;">
                    <th style="border: 1px solid #999999; width: 40%;">지표 구분</th>
                    <th style="border: 1px solid #999999; width: 30%;">24시간 평균 (현재값)</th>
                    <th style="border: 1px solid #999999; width: 30%;">24시간 최고값</th>
                </tr>
            </thead>
            <tbody>
        """

        for alert in sorted_alerts:
            labels = alert.get('labels') or {}
            values = alert.get('values') or {}
            alert_name = labels.get('alertname', 'Unknown')
            item = labels.get('item', '-')

            avg_style = ""
            max_style = ""

            if alert_name == "Daily-Network":
                avg_raw = safe_float(values.get('E', 0)) / 1000  
                max_raw = safe_float(values.get('F', 0)) / 1000  
                threshold = 800
                unit = " Mbps"
            else:
                avg_raw = safe_float(values.get('B', 0))
                max_raw = safe_float(values.get('C', 0))
                threshold = 90
                unit = "%"

            # 수치가 0.00인 경우 표기에서 제외
            if alert_name == "Daily-Disk":
                if avg_raw == 0.00:
                    continue
            else:
                if avg_raw == 0.00 and max_raw == 0.00:
                    continue

            avg_val = f"{avg_raw:.2f}{unit}"
            max_val = f"{max_raw:.2f}{unit}" if alert_name != "Daily-Disk" else "-"

            if alert_name == "Daily-Disk":
                if avg_raw >= threshold:
                    avg_style = "color: red; font-weight: bold;"
            else:
                if avg_raw >= threshold:
                    avg_style = "color: red; font-weight: bold;"
                if max_raw >= threshold:
                    max_style = "color: red; font-weight: bold;"

            if alert_name == "Daily-CPU":
                type_name = "CPU 사용률"
            elif alert_name == "Daily-Memory":
                type_name = "메모리 사용률"
            elif alert_name == "Daily-Network":
                type_name = f"네트워크 ({item})"
            elif alert_name == "Daily-Disk":
                type_name = f"디스크 ({item})"
            else:
                type_name = alert_name

            html_result += f"""
                <tr style="height:25px;">
                    <td style="border: 1px solid #999999; text-align: left; padding-left: 15px;">{type_name}</td>
                    <td style="border: 1px solid #999999; {avg_style}">{avg_val}</td>
                    <td style="border: 1px solid #999999; {max_style}">{max_val}</td>
                </tr>
            """
        html_result += "</tbody></table>"

    return html_result

def send_mail_task(alerts_list: list):
    """사내 SMTP 서버를 통해 완성된 HTML 표 메일을 발송합니다."""
    html_content = generate_html_table(alerts_list)

    msg = MIMEMultipart('alternative')

    # SMTP_USER 대신 SMTP_FROM_ADDRESS를 사용하여 보낸 사람 주소를 표시
    msg['From'] = f"{Header(SMTP_FROM_NAME, 'utf-8').encode()} <{SMTP_FROM_ADDRESS}>"

    msg['To'] = ", ".join(EMAIL_LIST)
    msg['Subject'] = "[보고] 인프라 자원 일일 종합 보고서"

    full_html = f"""
    <html>
      <body>
        <h2 style="font-family:Malgun Gothic; color:#222222;">📊 인프라 자원 일일 종합 보고</h2>
        <p style="font-family:Malgun Gothic; font-size:14px; color:#555555; margin-bottom:4px;">지난 24시간 동안 수집된 인프라 장비의 자원 요약 상태 정보입니다.</p>
        <br>
        <p style="font-family:Malgun Gothic; font-size:14px; color:#333333; margin-top:0; margin-bottom:2px;">※ 임계치를 초과한 수치는 빨간색으로 표시되며, 수치가 0.00인 데이터는 표에서 제외됩니다.</p>
        <p style="font-family:Malgun Gothic; font-size:13px; color:#666666; margin-top:0; margin-bottom:15px;">&nbsp;&nbsp;(기준 - CPU/메모리/디스크: 90% 이상, 네트워크: 800 Mbps 이상)</p>
        <p style="font-family:Malgun Gothic; font-size:14px; color:#333333; margin-top:0; margin-bottom:2px;">※ 트래픽 발생량이 적은 특정 서버의 네트워크 지표는 제외됩니다.</p>
        <p style="font-family:Malgun Gothic; font-size:13px; color:#666666; margin-top:0; margin-bottom:15px;">&nbsp;&nbsp;(예 - DW-MES1, DW-MES2, Linux-Test-Server, Windows-Test-Server, Zabbix server)</p>
        <br>
        {html_content}
        <br>
        <hr style="border:solid 1px #eee; max-width:700px; margin-left:0;">
        <p style="font-family:Malgun Gothic; font-size:12px; color:#888888;">본 메일은 Grafana 알림 데이터를 기반으로 자동 생성된 시스템 보고서입니다.</p>
      </body>
    </html>
    """
    msg.attach(MIMEText(full_html, 'html'))

    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()

            # 로그인은 실제 계정(SMTP_USER)으로 진행
            server.login(SMTP_USER, SMTP_PASSWORD)

            # 실제 메일을 송신 할 때는 SMTP_FROM_ADDRESS를 사용
            server.sendmail(SMTP_FROM_ADDRESS, EMAIL_LIST, msg.as_string())
        print("메일 발송 완료.")
    except Exception as e:
        print(f"메일 발송 오류: {e}")

@app.post("/webhook")
async def webhook_handler(request: Request, background_tasks: BackgroundTasks):
    payload = await request.json()
    if payload and 'alerts' in payload:
        background_tasks.add_task(send_mail_task, payload['alerts'])
        return {"status": "success"}
    return JSONResponse(status_code=status.HTTP_400_BAD_REQUEST, content={"status": "bad request"})