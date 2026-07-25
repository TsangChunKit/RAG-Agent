"""text_norm.py 测试：检索层的繁→简归一化。

背景：语料库全是简体（转写工具的输出），但使用者常打繁体。繁体 query 打进来时，
ngram FTS 完全对不上（「水煙」vs「水烟」零字符重叠），dense 也会漏——实测「水煙」
检索不到语料里唯一那两个提到「水烟」的块，而「水烟」两个都能命中。
所以约定：**所有进入检索/匹配的文本一律先过 to_simplified()**（见 ARCHITECTURE §简繁归一化）。
"""
from unittest.mock import patch


class TestToSimplified:
    """繁→简归一化"""

    def test_traditional_converted(self):
        from scripts.text_norm import to_simplified

        assert to_simplified("我喜歡抽水煙") == "我喜欢抽水烟"
        assert to_simplified("諮詢紀錄") == "咨询纪录"

    def test_simplified_unchanged_is_idempotent(self):
        """已是简体时原样返回——这条保证在 ingest 侧加归一化对现有语料是 no-op，不必重建索引"""
        from scripts.text_norm import to_simplified

        text = "抽水烟排解焦虑"
        assert to_simplified(text) == text
        assert to_simplified(to_simplified(text)) == text

    def test_mixed_and_non_chinese_preserved(self):
        """中英混杂 / 纯英文 / 数字标点：只动繁体汉字"""
        from scripts.text_norm import to_simplified

        assert to_simplified("我喜歡 shisha 水煙 relax") == "我喜欢 shisha 水烟 relax"
        assert to_simplified("normal query 123") == "normal query 123"

    def test_empty_and_none_like(self):
        from scripts.text_norm import to_simplified

        assert to_simplified("") == ""
        assert to_simplified("   ") == "   "

    def test_degrades_to_passthrough_without_zhconv(self):
        """zhconv 不可用时降级为原样返回：宁可漏掉归一化，也不能让检索整条链路挂掉"""
        import scripts.text_norm as tn

        with patch.object(tn, "_convert", None):
            assert tn.to_simplified("我喜歡抽水煙") == "我喜歡抽水煙"

    def test_conversion_failure_falls_back_to_original(self):
        """转换本身抛异常时也返回原文（同样是降级而非崩溃）"""
        import scripts.text_norm as tn

        def boom(text, locale):
            raise RuntimeError("boom")

        with patch.object(tn, "_convert", boom):
            assert tn.to_simplified("我喜歡抽水煙") == "我喜歡抽水煙"
