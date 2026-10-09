"""将旧版本结束后有新留言的会话恢复到人工待处理；不改上一轮历史。"""
from sqlalchemy import select
from .db import SessionLocal
from .models import Conversation
from .conversations import latest_handoff, open_handoff


def reopen_legacy(db):
    count = 0
    for c in db.scalars(select(Conversation).where(Conversation.status == 'ai_ready').with_for_update()):
        previous = latest_handoff(db, c, lock=True)
        if previous and previous.ended_at:
            open_handoff(db, c, previous, '上一轮结束后用户继续咨询',
                         '已开始新一轮咨询，正在等待人工接待，可以继续补充文字。')
            count += 1
    return count


if __name__ == '__main__':
    with SessionLocal() as db:
        count = reopen_legacy(db)
        db.commit()
    print(f'已恢复 {count} 个旧版本继续咨询的会话；上一轮历史保留。')
