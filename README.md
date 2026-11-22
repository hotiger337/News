# 개인 뉴스 봇

## 텔레그램 개인 대화 설정

텔레그램은 언론사가 표시한 속보만 개별 즉시 발송한다. 일반 자료는 한국시간 **12시·20시**에 한 메시지로 모은다. 번역을 마친 중복 제거 기사들을 수집 시점의 관심 점수순으로 네 건씩 보여주고, 다음 페이지에서 나머지도 볼 수 있다. 각 기사 번호 옆 👍·👎는 해당 기사에만 적용된다. 평가 후 표시된 버튼으로 취소한 뒤 다시 선택할 수 있다. 무응답은 미평가다. 제목을 눌러 원문을 열거나 ‘원문 제목’으로 전환한다. 최근 묶음은 /review로 다시 본다. 첫 설치 직후에는 다음 리뷰 시각까지 기다린다. 새 자료가 없는 회차에는 알림을 보내지 않는다. 장애로 놓친 회차는 재가동 후 아직 배정하지 않은 기사부터 합쳐서 처리한다. 텔레그램 응답을 잃은 리뷰는 자동 반복 발송을 보류하고 /status에 표시한다.


1. 텔레그램 공식 @BotFather에서 /mybots → 내 봇 → API Token으로 기존 토큰을 확인한다. 같은 봇을 이미 다른 프로그램에서 사용 중이라면 동시에 폴링할 수 없으므로 뉴스용 봇을 별도로 선택한다.
2. Azure 저장소 디렉터리에서 `.venv/bin/python -m newsbot telegram-id`를 실행한다. 토큰은 숨김 입력된다. 화면에 표시되는 봇 링크를 열고 개인 대화에 일회용 /start 명령을 보내면 본인 TELEGRAM_OWNER_ID가 서버 터미널에 출력된다.
3. 아래 Azure 설치 절차를 사용하고 /etc/newsbot.env에 TELEGRAM_BOT_TOKEN과 TELEGRAM_OWNER_ID를 입력한다. 텔레그램 모드는 DISCORD 항목을 비워둔다. 개인 대화 하나로 운영하면 두 TELEGRAM_*_CHAT_ID 값도 비워둔다. 별도 채널을 지정할 경우 해당 채널에 봇의 게시 권한이 필요하다. 이전에 디스코드로 운영했다면 텔레그램 전용 NEWS_DB 경로를 지정한다.
4. 서비스 시작 후 봇 대화에서 /status, /interests, /recent를 확인한다. /add 이름, /remove 이름으로 관심사를 편집하고 /held로 보류 후보를 조회한다. 기사 버튼은 재시작 후에도 본인만 사용할 수 있다. Telegram Bot API는 발송 기록 검색을 제공하지 않아 응답 유실 시 /status에 기사 번호를 남긴다. 대화에 해당 알림이 없는지 확인한 뒤 /retry 번호로 재발송한다.


Azure Linux 서버 한 대에서 공식 RSS·Atom, 선택한 메일함과 계정을 주기적으로 읽고, 비공개 디스코드 서버에 먼저 메시지를 보낸다. Python 3.11 이상, discord.py 2.x, SQLite(WAL), systemd를 사용한다. 기본 조회 간격은 60초다. 실제 지연에는 매체의 피드 게시 지연, 조회 주기, 해외 속보 번역 및 디스코드 응답 시간이 포함된다.

## 수신 범위와 실제 확인 결과

2026-11-22 UTC 개발 환경에서 확인했다. 최초 조회 표본이며 항목 수는 조회 시각에 따라 달라진다.

| 출처 | 기본 | 실제 확인 | 필수 속보 판정 |
|---|---|---|---|
| SBS 최신 뉴스 | 켜짐 | 공식 최신 뉴스 RSS 102건, 표식 0건 | 제목 처음의 `[속보]`. 전 분야 대상 |
| BBC News | 켜짐 | 헤드라인 RSS 36건, 후속 조회 35건, 표식 0건 | 제목의 `Breaking:`, `Breaking News:`, `[Breaking]` 등 설정된 접두 표식 또는 정확한 breaking 카테고리 |
| Sky News | 켜짐 | 헤드라인 RSS 6건, 표식 0건 | Sky 항목에 설정된 제목·카테고리 규칙 적용 |
| NVIDIA Newsroom | 켜짐 | 공식 보도자료 RSS 20건 | 기업 발표 추천 후보 |
| Washington Post Breaking News Alerts | 꺼짐 | 설정 필요. 구독 메일 실수신 미검증 | 전용 구독 라벨 안에서 지정 발신 주소와 일치한 모든 새 메일 |
| FxEmbed / @BBCBreaking | 꺼짐 | 계정 타임라인·기사 카드·커서 재조회 성공. 아래 제약 참조 | 활성화 시 해당 속보 전용 계정의 원 게시물 |
| OpenDART | 키 설정 시 활성 | API 키 필요 | 공시 후보. 날짜별 페이지를 순회하고 접수번호로 연결 |
| SEC EDGAR | 연락처 설정 시 활성 | 운영자 연락처 설정 및 개별 기업 Atom 응답 검증 필요 | 공시 후보 |
| CoreWeave IR / AMD IR | 켜짐 | 각각 공식 RSS 10건 | 기업 원문 |
| 연준 Working Papers | 켜짐 | 공식 연구자료 RSS 15건 | 연구 원문 |
| arXiv q-fin | 켜짐 | RSS 200, 표본 0건. 논문 항목 실수신 미확인 | 논문 후보 |

‘모든 속보’는 **연결한 피드·라벨·계정에 남아 있으며, 해당 출처의 규칙으로 명시적 속보라고 판정할 수 있는 항목 전체**다. BBC·Sky의 웹·앱 푸시 표식이 RSS에 전달되는지는 이번 표본에서 확인되지 않았다. 피드가 갱신 사이에 항목을 내리거나 장애가 보관 기간보다 길면 누락될 수 있다. SBS의 `newsflashRssFeed`는 최신 뉴스 전체를 제공한다. 섹션이나 추천 점수로 속보를 거르지 않는다.

[SBS 공식 RSS 목록](https://news.sbs.co.kr/news/rss.do) · [BBC RSS](https://feeds.bbci.co.uk/news/rss.xml) · [Sky RSS](https://feeds.skynews.com/feeds/rss/home.xml) · [NVIDIA Newsroom](https://nvidianews.nvidia.com/releases.xml)

### X 검증 기록

FxEmbed 명세의 `GET /2/profile/{handle}/statuses`는 `count` 1~100, `since`(Unix 초, 1e12 이상은 밀리초), `cursor`를 받는다. 200 응답의 `results[]`와 `cursor.bottom`을 읽는다. 명세상 첫 페이지에서 새 글이 없으면 204다.

- 공개 타임라인에서 19건을 받았다. 첫 항목은 BBC Breaking News(@BBCBreaking), ID `2123798538366722432`, 2026-11-21 00:20:04 KST였다. 본문은 “Anti-migrant activist Daniel Thomas sentenced to 15 months in jail for slashing dinghy in English Channel”이었다. [X 원문](https://x.com/BBCBreaking/status/2123798538366722432) · [게시물의 기사 링크](https://bbc.in/4A02ALl)
- `since=1795188003`은 200과 해당 글을 반환했다. 최신 글 시각인 `1795188004`와 그 다음 초는 204 대신 **404와 빈 results**를 반환했다. 잘못된 `count=101`은 400이었다. 다음 페이지는 첫 시도에서 404, 같은 커서 재시도에서 200과 더 오래된 ID를 반환했다. 단축 기사 링크의 최종 목적지는 개발망 프록시 403으로 확인하지 못했다.
- 같은 응답에는 11월 18일 17:12:29 KST에 Harry Styles, Sam Fender, Billie Eilish의 Glastonbury 공연을 알린 글과 BBC 기사 전체 주소도 함께 들어 있었다. [X 원문](https://x.com/BBCBreaking/status/2122966156327661726) · [BBC 기사](https://www.bbc.co.uk/news/articles/crwy4w381nlpo)
- 실제 시간 경과 후 새로 게시된 글의 자동 도착은 미검증이다. 기본값은 비활성이다. 구현은 204, 정상 페이지에서 새 ID 없음, HTTP 오류를 각각 처리한다. 404를 정상 무소식으로 저장하지 않는다. SQLite의 마지막 ID·시각에서 한 초 겹쳐 읽고, 필요하면 커서를 따라 이전 ID까지 복구한다. 도중 HTTP 실패는 체크포인트를 유지한다. 페이지 끝·커서 반복·100페이지 한도에서 이전 ID를 찾지 못하면 확보한 자료와 공백을 기록한다.
- 운영 전 해당 계정을 `probe`하고 새 게시물이 발생하는 동안 검증한다. 종합 언론은 `rule="marker"`와 해당 매체의 정규식을, 기업·기관·기자·개인 분석 계정은 `rule="none"`과 `role`, `kind`를 지정한다. 한 계정마다 고유한 출처 ID를 사용한다. 재게시와 다른 작성자의 글은 제외한다. 카드 URL과 URL facet에서 확인한 링크만 제공한다. 단축 링크도 매체가 제공한 형태 그대로 남긴다.

[FxEmbed OpenAPI 원본](https://github.com/FxEmbed/FxEmbed/blob/main/docs/specs/fxtwitter-openapi.json) · [실제 계정 조회](https://api.fxtwitter.com/2/profile/BBCBreaking/statuses)

## 디스코드 설정

1. Discord Developer Portal에서 New Application을 만든 뒤 Bot 탭에서 토큰을 발급한다. 토큰은 서버의 환경 파일에만 저장한다. Message Content, Server Members 등 privileged intent는 켤 필요가 없다. [Developer Portal](https://discord.com/developers/applications)
2. Installation 또는 OAuth2 URL Generator에서 Guild Install, `bot`, `applications.commands`를 선택해 개인 서버에 초대한다. 봇 역할에 **View Channel, Send Messages, Read Message History**를 허용한다. 기록 읽기는 응답 유실 시 기존 메시지 확인에 쓰인다. 관리자 권한은 필요 없다.
3. 비공개 텍스트 채널 #속보, #관심자료에서 @everyone의 채널 보기를 끄고 본인과 봇 역할에 접근을 허용한다. 본인에게 Use Application Commands를 허용한다.
4. 디스코드 사용자 설정 → 고급 → 개발자 모드를 켠다. 서버, 본인 프로필, 각 채널에서 ‘ID 복사’를 사용한다. `DISCORD_GUILD_ID`, `DISCORD_OWNER_ID`, `BREAKING_CHANNEL_ID`를 채운다. `INTEREST_CHANNEL_ID`를 비우면 한 채널에서 운영한다.

## Azure Linux 설치

다음은 systemd를 쓰는 Ubuntu/Debian용 예시다. 서버의 `python3 --version`을 확인한다. Python이 3.11 미만이면 설치된 3.11 이상 인터프리터와 해당 venv 패키지를 지정한다. Azure에 별도 인바운드 포트를 열 필요는 없다. 외부 HTTPS, 디스코드 Gateway(WebSocket), 선택한 IMAP TLS(통상 993)로 나갈 수 있어야 한다.

```bash
sudo apt-get update
sudo apt-get install -y python3-venv git ca-certificates tzdata
# 제공받은 코드의 루트에서 실행. /opt/newsbot은 이 봇 전용 신규 경로.
sudo useradd --system --user-group --home-dir /var/lib/newsbot --shell /usr/sbin/nologin newsbot
sudo install -d /opt/newsbot
sudo cp -r newsbot deploy tests README.md requirements.txt sources.toml /opt/newsbot/
sudo chmod -R a+rX /opt/newsbot
sudo python3 -m venv /opt/newsbot/.venv
sudo /opt/newsbot/.venv/bin/pip install -r /opt/newsbot/requirements.txt
sudo install -m 600 .env.example /etc/newsbot.env
sudoedit /etc/newsbot.env
```

환경 파일의 `NEWS_DB=/var/lib/newsbot/news.sqlite3`, `NEWS_CONFIG=/opt/newsbot/sources.toml`을 절대 경로로 설정한다. systemd가 전용 상태 디렉터리를 만든다. 처음 네 가지 디스코드 값(토큰 포함)은 필수다. 키·토큰·메일 내용·SQLite·백업은 비공개로 보관한다. 프로세스는 인증 헤더, 응답 본문, 키가 포함될 수 있는 요청 URL을 로그에 출력하지 않는다.

```bash
cd /opt/newsbot
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m newsbot probe
sudo install -m 644 deploy/newsbot.service /etc/systemd/system/newsbot.service
sudo systemctl daemon-reload
sudo systemctl enable --now newsbot
systemctl status newsbot --no-pager
sudo journalctl -u newsbot --since today
sudo systemctl restart newsbot
```

코드·설정·환경 파일 변경 후 재시작한다. 서비스 파일을 변경했을 때는 daemon-reload도 실행한다. 개발용 전경 실행은 저장소에서 venv 생성 및 의존성 설치 후 `cp .env.example .env && chmod 600 .env`, 값 입력, `python -m newsbot run` 순서다. 같은 DB를 사용하는 봇 인스턴스는 파일 잠금으로 하나만 실행한다.

## 선택 출처와 번역

- **선택형 Azure 번역**은 Azure AI Translator Text API의 기존 리소스 키(`TRANSLATOR_KEY`)와 리소스에 필요한 리전(`TRANSLATOR_REGION`)을 사용한다. 제목 또는 X 본문을 원문 언어에서 한국어로 직접 번역한다. 수치·주요 단위와 등록된 기업명·고유명사는 HTML 번역 제외 구간으로 보내고 반환값에 남았는지 검사한다. 추가 인명은 TOML의 translation.names에 등록한다. 숫자, 단위, 고유명사, 인용 주체와 불확실성은 원문을 병기해 확인할 수 있다. 기계 번역의 정확성을 보장할 수 없으므로 첫 실제 수신 때 대조한다. 번역이 비었거나 한글이 없거나 API 요청이 실패하면 별도 대기열에서 재시도한다. 원문만 먼저 발송하지 않는다. 문자 수 기반 사용량·무료 할당과 과금은 리소스 요금제에 따른다. 설치 과정에 리소스 생성이나 결제 단계는 없다. [Azure Translator 요금](https://azure.microsoft.com/pricing/details/cognitive-services/translator/)
- **WP**는 Breaking News Alerts를 먼저 구독한다. 수신 메일의 발신 주소와 구독 식별 헤더 또는 수신 별칭으로 메일 제공자의 규칙을 만들고 **해당 구독만** 전용 라벨로 보낸다. `WP_FROM`은 실제 메일에서 확인한 정확한 주소, `WP_FOLDER`는 IMAP에 노출되는 전용 폴더명이다(ASCII 권장). `IMAP_HOST/USER/PASSWORD`와 `enabled=true`를 설정한다. 제공자가 허용한 앱 비밀번호 등 IMAP 로그인 수단이 필요하다. OAuth 전용 메일함은 이 어댑터로 로그인할 수 없다. 읽기 전용 선택, UID 검색, BODY.PEEK, From 재검사를 사용한다. 첫 연결은 기존 UID까지 기준점만 잡는다. 메일 본문에서 식별한 WP 기사 링크를 표시한다. 추적 링크만 있는 메일에서는 기사 URL을 생략할 수 있다.
- **OpenDART**는 발급받은 `DART_KEY`를 입력하고 dart를 활성화한다. 공시의 공개 일자만 제공되면 KST 날짜와 ‘시각 미제공’을 표시한다. **EDGAR**는 `SEC_USER_AGENT="운영 프로그램명 연락가능한이메일"`로 설정하고 CIK별 Atom 예시를 활성화한다. 비밀 키는 필요 없지만 SEC 접근 정책·응답을 해당 서버에서 확인한다. [OpenDART](https://opendart.fss.or.kr/) · [SEC 접근 안내](https://www.sec.gov/about/developer-resources)
- **IR·연구 보고서**는 `sources.toml`에서 NVIDIA/SEC 항목을 복사해 고유 ID, 실제 공식 RSS/Atom 주소, 정확한 publisher·lang·kind·company와 `rule="none"`을 지정한다. arXiv 예시는 q-fin 피드다. 일반 HTML 목록이나 PDF URL은 피드로 사용할 수 없다. `python -m newsbot probe 출처ID`는 비활성 출처도 점검하며 DB와 디스코드에 쓰지 않는다. WP probe는 접속·검색만 확인한다. 실제 새 메일 본문 검증에는 기준점 이후의 수신이 필요하다.
- 출처 ID는 체크포인트의 키다. 같은 ID를 다른 계정·메일함·피드로 재사용하지 않는다. 표시 이름 publisher는 같은 매체의 RSS와 X에서 일치시킨다.

## 평가와 추천

`/관심사`, `/관심추가`, `/관심삭제`, `/최근속보`, `/상태`, `/후보`는 지정한 서버의 등록 사용자만 사용한다. `/후보 번호`로 보류한 기사도 평가할 수 있다. 메시지마다 고정 형식의 기사 ID가 있는 버튼을 사용한다. 👍/👎 중 마지막 값 하나만 집계한다. 🕓는 해당 주제 또는 기업×자료 유형의 관심 자료를 저녁으로 미룬다. 평가 취소는 해당 기사의 내용·시간 평가를 함께 지운다. 무응답의 기여는 0이다.

- 초기 관심사는 AI 데이터센터, 반도체, 기업 자금 조달, 퀀트 전략이다. 각 기사에서 기업 별칭·주제·자료 유형·기업×자료 유형을 저장한다. CoreWeave·코어위브·CRWV는 같은 기업으로 계산한다.
- 각 특징의 학습 점수는 `가중치 × (긍정−부정)/(긍정+부정+4)`다. 기본 가중치는 기업×유형 3, 주제 2, 기업 1, 유형 0.5다. 직접 관심사 일치는 각각 1.5, 최신성은 `exp(-경과초/86400)`이다. 직접 추가한 관심사는 제목·분류 이름에 대소문자를 무시하고 매칭한다. 동의어는 TOML에서 추가한다. 실제 가장 크게 기여한 항목을 추천 이유로 표시한다.
- 기본 4점 이상은 즉시 추천한다. 나머지는 최근 72시간 후보에서 한국시간 20:00에 개인화 기여가 양수인 상위 4건, 설정된 인접 주제 최대 1건을 선택한다. 자료별 메시지로 보내므로 두 번째 항목의 버튼은 두 번째 기사만 평가한다. `digest_time`(HH:MM), `top`, `adjacent`(0 또는 1), `instant_threshold`, 특징 가중치와 인접 주제는 TOML에서 조정한다. 매일 같은 날짜의 선정은 한 번만 확정한다.
- `items`에 보류·발송·수정 이력, `votes`에 최종 평가, `rankings`에 최초 판정 및 저녁 선정 시의 점수·기여·결정을 남긴다. 백업 DB에서 `NEWS_DB=/비공개/백업.sqlite3 python -m newsbot compare 새가중치.toml`을 실행하면 같은 저장 특징·평가·조회 시각에서 기사 ID별 구·신 점수와 기여를 JSONL로 비교한다. 과거 시점의 실제 판정은 rankings를 조회한다.

## 장애·중복·복구 확인

1. 처음 켜면 출처별 첫 정상 조회를 기준점으로 저장한다. `/상태`에서 성공 시각을 확인하고 **그 뒤 새로 게시된 표식 기사**를 기다린다. 기존 기사를 재전송하려고 DB를 지울 필요가 없다.
2. 실제 메시지의 발행처·KST·하단 링크와 국내/해외 채널을 확인한다. 해외 속보는 한국어 → 원문 순서다. 버튼 평가를 변경하고 취소한 뒤, 재시작하여 예전 메시지의 버튼과 `/후보`를 확인한다.
3. 각 출처는 독립적으로 조회한다. RSS는 매번 전체 현재 창을 읽고 직전 창과 ID가 전혀 겹치지 않으면 공백을 표시한다. 메일은 UID를 따라잡고 UIDVALIDITY가 바뀌면 보관된 메일을 Message-ID로 대조한다. 수집 결과와 처리 위치는 하나의 트랜잭션이다.
4. 같은 매체·기사 ID, X ID, 확인된 기사 URL로 중복을 연결한다. 다른 매체는 별도 알림으로 유지하며 동일 원문·명시적 via 링크가 확인될 때만 관련 기사를 표시한다. URL 단축·별도 보도·원문 참조 없는 경우 연결이 빠질 수 있다. 같은 기사의 제목 수정이나 RSS 요약에 실린 금액·확정·정정 표현의 변경은 이전 제목/관련 발췌를 병기한다. 기사 본문을 크롤링하지 않으므로 피드에 반영되지 않은 수정은 포착 범위 밖이다.
5. `/상태`에서 출처별 성공·오류·공백, translate(번역 대기), ready(발송 대기), retry(확인된 실패), sending(시도 중), uncertain(응답 유실 가능)을 확인한다. 해외 번역과 국내 전송은 다른 작업이다. 발송 직전에 SQLite에 시도를 기록한다. 응답 유실 시 시도 시각 이후의 해당 채널에서 봇 자신이 보낸 동일 기사 버튼을 찾아 성공 상태를 복구한다. 기록 조회 실패 때는 재발송을 보류하고 재시도한다. 메시지를 운영자가 삭제했거나 서비스가 요청을 뒤늦게 처리하면 완전한 단 한 번 전송까지 보장되지는 않는다.
6. 검증한 자동 테스트는 로컬 SQLite와 Discord/번역/메일 등의 대역을 사용한다. 실제 디스코드 발송 성공은 서버에서 메시지를 확인하고 `/최근속보`의 링크가 열리며 `/상태`에 발송 지연이 기록될 때 확인할 수 있다.

### 온라인 백업과 복원

서비스 실행 중에는 SQLite backup API로 일관된 백업을 만든다. 아래 경로는 운영 예시다. 명령은 /opt/newsbot에서 실행한다.

```bash
sudo install -d -m 700 -o newsbot -g newsbot /var/lib/newsbot/backups
sudo -u newsbot env NEWS_DB=/var/lib/newsbot/news.sqlite3 \
  .venv/bin/python -m newsbot backup "/var/lib/newsbot/backups/news-$(date +%Y%m%d-%H%M%S).sqlite3"
```

복원할 때는 서비스를 멈추고 기존 DB와 같은 이름의 -wal, -shm 파일을 함께 별도 보관한다. 백업을 새 DB 경로로 복사하고 소유자를 newsbot, 권한을 600으로 설정한다. /etc/newsbot.env의 NEWS_DB를 그 경로로 변경한 뒤 서비스를 시작한다. 백업 이후의 발송 기록과 평가가 복원본에는 없으므로, 최근 디스코드 메시지와 백업 시각을 대조한다.

## 텔레그램 운영 업데이트

기존 Azure 설치에서는 저장소에서 `git pull --ff-only && bash deploy/update-telegram.sh`를 실행한다. 이 설치는 텔레그램 전용 서비스 진입점을 사용한다. 기존 DB·인증 파일을 사용하며, 배포본 출처 설정과 로컬 변경 사항은 3방향 병합한다.

- 기사·공시·연구 자료는 같은 봇에서 받는다. 같은 사건에서 번역 대기 중인 해외 속보는 국내 매체도 속보로 표시한 경우에만 국내 기사로 대체한다. 일반 기사끼리 겹치면 발송 가능한 한국어 기사를 먼저 보여준다.
- 개별 알림은 평가 후 버튼이 접힌다. 봇 개인 대화에서 `/rate 기사번호`로 다시 열 수 있다. 점심·저녁 리뷰에서는 평가한 기사 번호 옆에 취소 버튼이 나타난다. 취소하면 다시 선택할 수 있다.
- `/status`는 최근 1시간에 발견한 새 글·중복 건수, 같은 시간 실제 발송 건수, 현재 대기와 마지막 발송 제목을 보여준다. 발송 시간과 발견 시간이 달라 합계는 일치하지 않을 수 있다.
- `/scores`는 업데이트 이후 전체 수신 중 기록한 **평가 전** 점수와 사용자의 최종 내용 평가를 비교한다. 실제 가중치와 시험 가중치로 같은 기사를 동시에 점수화한다. 초기 시험값은 기업×유형 4, 기업 0.5이며 다른 값은 실제 설정과 같다. 두 점수에 같은 instant_threshold를 적용하고 미평가·시간 선호·취소는 제외한다. 적중 수는 직접 평가한 표본의 결과다. 시험값은 자동 적용하지 않는다.
- `/sources`는 최근 7일 출처별 수집, 대표 발송, 단독(다른 출처의 중복이 확인되지 않은 대표), 중복, 👍 건수를 표시한다.
- 공개 원문 출처에 CoreWeave IR, AMD IR, 연준 Working Papers를 추가하고 arXiv q-fin을 활성화했다. DART는 DART_KEY, SEC NVIDIA·CoreWeave Atom은 운영자 연락처가 있는 SEC_USER_AGENT 설정 후 재시작하면 조회한다. 공개 원문의 첫 정상 조회는 과거 기사의 기준점이다.

텔레그램 해외 속보는 한국어 제목, 원문, 출처·한국시간·기사 번호, 원문 링크를 표시한다. 일반 기사는 번역을 마치면 다음 리뷰에 실린다. 번역 키가 비어 있으면 서버 내 영어→한국어 모델을 사용한다. 국내 공개 피드로 연합뉴스 전국, 경향신문 전체, 전자신문 오늘의뉴스 등을 수집한다.


## 키 없는 번역과 국가별 원문

기존 업데이트 명령으로 OPUS-MT TC-big 영어→한국어 모델과 영어·한국어 개별 어휘를 설치한다. 다운로드 약 405 MiB, CPU INT8 추론을 사용한다. 개발 환경에서 단독 번역 프로세스의 최대 메모리가 약 570 MiB였다. 1 GiB VM에서 실제 여유 메모리와 첫 번역 지연은 서버의 다른 작업에 따라 달라진다. 버전과 SHA-256을 고정하고 처음 한 번만 설치한다. [원 모델과 라이선스](https://huggingface.co/Helsinki-NLP/opus-mt-tc-big-en-ko)

TRANSLATOR_KEY가 비어 있으면 기사 텍스트를 외부 번역 서비스로 보내지 않는다. 모델은 CPU INT8, 추론 스레드 1개로 실행하며 영문만 지원한다. 기존 Azure 키를 명시한 설치는 Azure 번역을 계속 사용한다. 로컬 모델은 숫자·등록명·일부 부정 및 불확실성 표현이 사라지면 발송을 보류하고 기존 대기열에서 재시도한다. 이 검사는 전체 번역의 정확성을 보장하지 않는다. 같은 모델에서 재시도해도 품질 검사를 계속 통과하지 못하는 기사는 대기에 남을 수 있다. [추론 엔진](https://github.com/OpenNMT/CTranslate2)

이번에 추가한 국가는 미국·캐나다·멕시코·아르헨티나·독일·프랑스·스페인·일본·인도·파키스탄·스리랑카·싱가포르·필리핀·호주·뉴질랜드·카타르·이집트·나이지리아·케냐·가나다. 각국의 현지 편집국 한 곳을 골라 공식 영문 RSS로 검증했다. 국가 표시는 발행 매체의 소재지이며 개별 기사의 발생 지역과 다를 수 있다. 개인 관심사를 위한 편집국 선정이며 영어판의 보도 범위에 한정된다. 기존 국내 매체와 영국 BBC·Sky는 유지한다. sources.toml의 country, publisher, url 및 verification에서 개별 주소와 수신 표본 수를 확인한다. 신규 피드는 첫 조회를 기준점으로만 저장한다.
