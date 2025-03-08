from typing import List, TypedDict


class ItemType(TypedDict):
    id: int
    text: str
    mark: int
    target: str
    created_at: int
    view_count: int
    status_id: int
    reply_count: int
    share_count: int
    sub_type: int


class XueQiu7x24(TypedDict):
    next_max_id: int
    items: List[ItemType]
    next_id: int
