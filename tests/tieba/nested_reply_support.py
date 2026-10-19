"""Shared real-sample fixtures for nested reply tests."""

URL = "https://tieba.baidu.com/p/10897336006"
THREAD_ID = "10897336006"
FORUM_ID = "195707"
PARENT_ID = "153765210147"
PARENT_FLOOR = 4

PREVIEW = [
    {
        "reply_id": f"n{index}",
        "content": f"第 {index} 条楼中楼",
        "author": f"吧友{index}",
        "posted_at": f"2026-08-{index:02d} 10:00",
        "floor": PARENT_FLOOR,
        "parent_reply_id": PARENT_ID,
        "nested": True,
        "nested_preview": True,
    }
    for index in range(1, 5)
]
PARENT = {
    "reply_id": PARENT_ID,
    "floor": PARENT_FLOOR,
    "content": "北区热水十一点停。",
    "sub_post_number": 17,
}
