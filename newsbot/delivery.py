import time

from .core import render


async def deliver(store, row, transport, *, compact=False):
    db = store.db
    n = row["id"]
    try:
        if row["state"] in ("sending", "uncertain"):
            # 전체 시도 구간을 검사해야 재시작 뒤 오래된 성공 응답도 찾을 수 있다.
            found = await transport.find(row)
            if found:
                with db:
                    db.execute(
                        "update items set state='sent',sent=?,message=?,error=null where id=?",
                        (found[1], str(found[0]), n),
                    )
                return
        channel = row["channel"] or transport.channel(row)
        body = render(store, row, compact=compact)
        now = int(time.time())
        with db:
            db.execute(
                """update items set state='sending',started=coalesce(started,?),channel=?,
                          attempt=attempt+1 where id=?""",
                (now, str(channel), n),
            )
        message = await transport.send(int(channel), n, body)
        with db:
            db.execute(
                "update items set state='sent',message=?,sent=?,error=null where id=?",
                (str(message), int(time.time()), n),
            )
    except Exception as exc:
        status = getattr(exc, "status", None)
        state = (
            "retry"
            if status in (400, 401, 403, 404, 429)
            and row["state"] not in ("uncertain", "sending")
            else "uncertain"
        )
        with db:
            db.execute(
                "update items set state=?,due=?,error=? where id=?",
                (
                    state,
                    int(time.time()) + min(900, 15 * 2 ** min(row["attempt"], 6)),
                    type(exc).__name__,
                    n,
                ),
            )
