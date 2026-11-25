"""Keep previously delivered Telegram reviews readable and rateable."""

import json
from datetime import datetime
from html import escape

from .core import KST

PAGE_SIZE = 4
SCHEMA = """
CREATE TABLE IF NOT EXISTS reviews(slot INTEGER PRIMARY KEY,state TEXT,due INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS review_items(slot INTEGER,pos INTEGER,item INTEGER UNIQUE,PRIMARY KEY(slot,pos));
"""


def retire_schedule(db):
    """Stop unsent reviews without losing evidence of earlier deliveries or votes."""
    db.executescript(SCHEMA)
    with db:
        db.execute("update reviews set state='uncertain' where state='sending'")
        db.execute("update reviews set state='cancelled' where state='pending'")
        db.execute("""update items set state='ready' where not urgent and state='review_queued'
            and id in (select x.item from review_items x join reviews r on r.slot=x.slot
                       where r.state='cancelled')""")


def payload(db, owner, slot, page, original=False):
    count = db.execute(
        "select count(*) from review_items where slot=?", (slot,)
    ).fetchone()[0]
    if not count or page < 0 or page * PAGE_SIZE >= count:
        raise ValueError("unknown review page")
    date = datetime.fromtimestamp(slot, KST)
    label = "점심" if date.hour == 12 else "저녁"
    pages = (count + PAGE_SIZE - 1) // PAGE_SIZE
    lines = [
        f"<b>{date:%m월 %d일} {label} 뉴스 리뷰</b> · {count}건 · {page + 1}/{pages}"
    ]
    keys = []

    def button(label, action, item=0):
        return dict(
            text=label,
            callback_data=f"rv:{slot}:{page}:{int(original)}:{action}:{item}",
        )

    for r in db.execute(
        """select x.pos,i.*,v.content from review_items x join items i on i.id=x.item
            left join votes v on v.item=i.id and v.owner=?
            where x.slot=? and x.pos>=? and x.pos<? order by x.pos""",
        (str(owner), slot, page * PAGE_SIZE, (page + 1) * PAGE_SIZE),
    ):
        d = json.loads(r["data"])
        title = d["title"] if original else (r["ko"] or d["title"])
        num = r["pos"] + 1
        url = d.get("post") or next(iter(d["links"]), None)
        title = escape(title[:210])
        if url:
            title = f'<a href="{escape(url, quote=True)}">{title}</a>'
        source = escape(r["publisher"][:80])
        if d.get("post") and d["links"]:
            source += " · " + " · ".join(
                f'<a href="{escape(link, quote=True)}">기사 원문</a>'
                for link in dict.fromkeys(d["links"])
                if link != url
            )
        lines.append(f"{num}. {title}\n{source} · #{r['id']}")
        if r["content"]:
            choice = "👍" if r["content"] == 1 else "👎"
            keys.append([button(f"{num}  {choice} 평가 취소", "undo", r["id"])])
        else:
            keys.append(
                [
                    button(f"{num}  👍", "up", r["id"]),
                    button(f"{num}  👎", "down", r["id"]),
                ]
            )
    nav = []
    if page:
        nav.append(button("‹ 이전", "prev"))
    nav.append(button("한국어 제목" if original else "원문 제목", "toggle"))
    if page + 1 < pages:
        nav.append(button("다음 ›", "next"))
    return dict(
        text="\n\n".join(lines),
        parse_mode="HTML",
        reply_markup={"inline_keyboard": keys + [nav]},
        link_preview_options={"is_disabled": True},
    )
