"""Owner-facing summaries from the local SQLite ledger."""

import json
from datetime import datetime

from .core import KST


def activity(db, now):
    cutoff = now - 3600
    new, duplicates = db.execute(
        """select count(*),count(*) filter(where state='duplicate')
        from items where found>=? and state!='baseline'""",
        (cutoff,),
    ).fetchone()
    sent = db.execute(
        "select count(*) from items where urgent and sent>=?", (cutoff,)
    ).fetchone()[0]
    lines = [f"최근 1시간 · 새 글 {new}건 · 속보 발송 {sent}건 · 중복 {duplicates}건"]
    if not new:
        lines.append("최근 1시간 새 글 없음")
    last = db.execute(
        "select * from items where urgent and sent is not null order by sent desc,id desc limit 1"
    ).fetchone()
    if last:
        stamp = datetime.fromtimestamp(last["sent"], KST).strftime("%m-%d %H:%M")
        lines.append(
            f"마지막 속보 발송 {stamp} · {last['publisher']} · "
            + (last["ko"] or json.loads(last["data"])["title"])[:80]
        )
    else:
        lines.append("아직 발송된 속보 없음")
    pending = dict(
        db.execute("select state,count(*) from items where urgent group by state")
    )
    outgoing = db.execute(
        "select count(*) from items where urgent and state in ('ready','retry','sending')"
    ).fetchone()[0]
    lines.append(
        f"속보 번역 대기 {pending.get('translate', 0)} · 발송 대기 {outgoing} · 응답 확인 필요 {pending.get('uncertain', 0)}"
    )
    return lines


def scorecard(db, owner):
    rows = db.execute(
        """select p.*,v.content,i.data from predictions p join votes v on v.item=p.item
            join items i on i.id=p.item where v.owner=? and v.content<>0 order by p.item desc""",
        (str(owner),),
    )
    groups = {
        k: []
        for k in ("잘 맞힌 자료", "놓칠 뻔한 자료", "과대평가한 자료", "낮은 점수·👎")
    }
    total = old_ok = new_ok = 0
    for r in rows:
        high = r["score"] >= r["threshold"]
        liked = r["content"] == 1
        total += 1
        old_ok += high == liked
        new_ok += (r["trial"] >= r["threshold"]) == liked
        label = (
            ("잘 맞힌 자료" if liked else "과대평가한 자료")
            if high
            else ("놓칠 뻔한 자료" if liked else "낮은 점수·👎")
        )
        groups[label].append((r["score"], json.loads(r["data"])["title"]))
    if not total:
        return "아직 비교 가능한 평가가 없습니다. 업데이트 후 도착한 자료에 👍 또는 👎를 눌러주세요."
    lines = [
        f"수신 시점 점수 · 평가 {total}건 · 기준점 이상을 👍로 예측",
        f"기존 적중 {old_ok}/{total} · 시험 가중치 적중 {new_ok}/{total}",
    ]
    for label, items in groups.items():
        lines.append(f"{label} {len(items)}건")
        lines.extend(f"  {score:.1f}점 · {title[:60]}" for score, title in items[:2])
    return "\n".join(lines)


def source_report(db, owner, now):
    rows = db.execute(
        """select i.source,count(*) total,count(*) filter(where i.state in ('sent','reviewed')) delivered,
        count(*) filter(where i.state='duplicate') dup,count(*) filter(where v.content=1) likes,
        count(*) filter(where i.state in ('sent','reviewed') and not exists(select 1 from items d where d.related=i.id and d.state='duplicate' and d.source<>i.source)) sole
        from items i left join votes v on i.id=v.item and v.owner=? where i.found>=? and i.state!='baseline'
        group by i.source order by delivered desc,i.source""",
        (str(owner), now - 7 * 86400),
    )
    return "\n".join(
        [
            "최근 7일 · 먼저 포착해 보낸 대표 자료 기준",
            *(
                f"{src} · 수집 {total} · 대표 발송 {sent} · 단독 {sole} · 중복 {dup} · 👍 {likes}"
                for src, total, sent, dup, likes, sole in rows
            ),
        ]
    )
