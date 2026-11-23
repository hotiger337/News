"""News headline frequency rankings for live and daily reports."""

import re


STOP = set("""오늘 내일 올해 내년 지난 이번 이번주 통해 대한 위해 함께 기자 소식 뉴스 발표 공개
진행 개최 실시 추진 선정 결과 시작 관련 확인 논의 최대 만에 앞두고 이어 나서 이후 다시 국내
현장 포토 속보 종합 사람 모두 단독 영상 때문 전면 번째 확대 강조 지원 공급 달러 지역 전국 시장
억원 억원대 처음 신규 구축 조성 사업 운영 대상 출시 첫날 방문 기록 상황 현재 밝혔다 말했다
밝혀 마련 까지 기자회견""".split())


def rank(articles):
    groups = {}
    for article in articles:
        words = set()
        for word in re.findall(r"[가-힣]{2,}|[A-Z][A-Za-z0-9-]+", article["title"]):
            word = re.sub(r"(?:에서는|으로는|에서도|으로|에서|에게|까지|부터|만큼|보다|에는|와의)$", "", word)
            if len(word) >= 3:
                word = re.sub(r"[은는이가을를에와과도]$", "", word)
            if len(word) > 1 and word not in STOP and not word.endswith(
                ("하다", "한다", "됐다", "했다", "한다면", "인데", "하고", "다는", "던데", "면서", "처럼", "했다가")
            ):
                words.add(word)
        for word in words:
            groups.setdefault(word, []).append(article["url"])
    return [{"word": word, "count": len(urls), "urls": urls} for word, urls in
            sorted(groups.items(), key=lambda entry: (-len(entry[1]), entry[0]))[:10] if len(urls) >= 2]
