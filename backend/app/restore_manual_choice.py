"""撤销旧版本尚未接手的自动交接；主动申请和已接手的人工服务保留。"""
from sqlalchemy import select
from .db import SessionLocal
from .models import Conversation, now
from .conversations import latest_handoff, add_message


def restore_manual_choice(db):
    count = 0
    for c in db.scalars(select(Conversation).where(Conversation.status == 'waiting_human').with_for_update()):
        h = latest_handoff(db, c, lock=True)
        if h and h.reason == '上一轮结束后用户继续咨询' and not h.taken_at and not h.ended_at:
            h.ended_at = now()
            c.status = 'ai_ready'
            add_message(db, c, 'system', '已恢复普通咨询。如需人工接待，请主动点击“转人工”。')
            count += 1
    return count


if __name__ == '__main__':
    with SessionLocal() as db:
        count = restore_manual_choice(db)
        db.commit()
    print(f'已撤销 {count} 个尚未接手的自动交接；历史、主动申请和已接手会话保留。')
