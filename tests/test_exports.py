from __future__ import annotations

import json

from app.main import encode_export


SAMPLES = [
    {
        "question": "什么是高质量数据？",
        "answer": "可追溯、可验证的数据。",
        "source": {"evidence": "产品文档中的定义"},
    }
]


def test_all_export_formats_are_jsonl_parseable():
    for name in ("jsonl", "alpaca", "chatml"):
        rows = encode_export(SAMPLES, name)
        assert len(rows) == 1
        assert isinstance(json.loads(rows[0]), dict)
