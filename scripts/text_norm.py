"""检索层的文本归一化：把繁体统一成简体。

**为什么需要**：语料库（转写工具输出的逐字稿）全是简体，但使用者常打繁体。繁体 query 打进来时
ngram FTS 完全对不上（「水煙」和「水烟」零字符重叠），dense 检索也会漏——实测「水煙」检索不到
语料里唯一提到「水烟」的那两个块，而「水烟」两个都能命中。

**约定（系统不变量）**：所有进入检索/语义匹配的文本一律先过 to_simplified()，展示用的原文不动。
落点只有三处：
- scripts/chunk.py 生成 Chunk.text（索引字段）时——Chunk.raw_text 保留原文供展示/喂 LLM
- scripts/ask.py retrieve() 的 query（覆盖 embed + FTS + reranker 三条腿）
- scripts/ask.py find_relevant_graph_nodes() 的 question（图谱节点文本是简体）

现有语料本来全简体（实测 3049 个 chunk 里繁体字符 0 个），所以 ingest 侧加这一步对旧数据是
no-op——**不需要重建索引、不需要数据迁移**，只是让未来混入的繁体语料自动被统一。

**降级**：zhconv 未安装或转换抛异常时原样返回。宁可漏掉一次归一化（退回今天的行为），
也不能让检索整条链路挂掉。
"""
try:
    from zhconv import convert as _convert
except ImportError:  # 没装 zhconv：降级为原样返回，检索仍可用（只是繁体 query 会漏）
    _convert = None


def to_simplified(text: str) -> str:
    """繁体 → 简体。已是简体则原样返回（幂等），非中文字符不受影响。

    Args:
        text: 任意文本（query / chunk 文本 / 图谱节点文本）

    Returns:
        简体化后的文本；zhconv 不可用或转换失败时返回原文

    Examples:
        >>> to_simplified("我喜歡抽水煙")
        '我喜欢抽水烟'
        >>> to_simplified("抽水烟排解焦虑")
        '抽水烟排解焦虑'
    """
    if not text or _convert is None:
        return text
    try:
        return _convert(text, "zh-hans")
    except Exception as e:  # noqa: BLE001
        print(f"[text_norm] 繁简转换失败，按原文处理：{e}", flush=True)
        return text
