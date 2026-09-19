# -*- coding: utf-8 -*-
"""WorldInfoEntryAlias 数据模型。"""

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel

from app.core.ids import generate_id


class WorldInfoEntryAlias(SQLModel, table=True):
    """世界书条目的别名模型。

    Attributes:
        id: 别名唯一标识符（nanoid）。
        entry_id: 所属世界书条目 ID。
        alias: 展示用别名文本，保留用户输入的大小写。
        normalized_alias: 按字面匹配规则归一化后的别名，用于唯一性与检索。
        position: 用户输入顺序，从 0 开始。
    """

    __tablename__ = "world_info_entry_aliases"
    __table_args__ = (
        UniqueConstraint(
            "entry_id",
            "normalized_alias",
            name="uq_world_info_entry_alias_target",
        ),
    )

    id: str = Field(default_factory=generate_id, primary_key=True)
    entry_id: str = Field(index=True, foreign_key="world_info_entries.id")
    # 展示文本与归一化结果长度上限均为 100，与 knowledge_contracts 的
    # MAX_ALIAS_CHARS 保持一致；数量与长度在服务层校验。
    alias: str = Field(max_length=100)
    normalized_alias: str = Field(max_length=100)
    position: int = Field(default=0)
