from __future__ import annotations


def normalized_path_key(path: object) -> str:
    """把路径文本规范化为小写、正斜杠形式，供文本匹配和稳定 key 使用。"""
    return str(path).strip().replace("\\", "/").casefold()
