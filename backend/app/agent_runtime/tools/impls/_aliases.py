# -*- coding: utf-8 -*-
"""审批预览用的别名参数辅助函数。

``build_interrupt_preview`` 拿到的是未经 ``args_schema`` 校验的原始参数，
因此需要先做类型检查；归一化规则本身仍由 ``knowledge_alias_service`` 唯一提供，
这里只负责「预览失败就放弃预览」的降级处理。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import cast

from app.storage.services import knowledge_alias_service


def is_alias_list(value: object) -> bool:
    """判断原始参数是否形如 ``list[str]``。"""
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def normalize_preview_aliases(value: object, *, entity_name: str) -> list[str] | None:
    """返回预览用别名列表。

    ``None`` 表示参数不合法（类型错误、空别名、超长或超数量），调用方应放弃
    预览，让真正的执行路径给出明确错误。
    """
    if value is None:
        return []
    if not is_alias_list(value):
        return None
    try:
        return knowledge_alias_service.normalize_aliases(
            cast("list[str]", value), entity_name=entity_name
        )
    except ValueError:
        return None


def alias_changes(
    before: Sequence[str],
    after: Sequence[str],
) -> dict[str, list[str]] | None:
    """返回别名的增删；没有变化时返回 ``None``，避免污染既有 diff 形状。"""
    before_set = set(before)
    after_set = set(after)
    added = [alias for alias in after if alias not in before_set]
    removed = [alias for alias in before if alias not in after_set]
    if not added and not removed:
        return None
    return {"added": added, "removed": removed}


__all__ = ["alias_changes", "is_alias_list", "normalize_preview_aliases"]
