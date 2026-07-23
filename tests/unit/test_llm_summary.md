# test_llm.py 测试摘要

## 覆盖率结果

- **当前覆盖率**: 95% (103/108 行)
- **起始覆盖率**: 36% (36/108 行)
- **提升**: +59%
- **未覆盖行**: 仅 `__main__` 块（279-283），这是可接受的

## 测试分类

### 1. Gemini Provider 测试 (7 个测试)
- ✅ 基本调用
- ✅ profile 切换 (dialogue/summary)
- ✅ system_instruction
- ✅ cached_content (Explicit Cache)
- ✅ response_schema (JSON 结构化输出)
- ✅ 参数覆盖 (model/temperature/thinking_level/max_output_tokens)
- ✅ 多轮对话格式

### 2. Client 管理测试 (3 个测试)
- ✅ Gemini client 惰性初始化
- ✅ API key 变更时重建 client
- ✅ 缺少 API key 时抛出异常

### 3. OpenAI 兼容 Provider 测试 (5 个测试)
- ✅ Grok provider 基本调用
- ✅ Hermes provider 基本调用
- ✅ 缺少 key 时抛出异常
- ✅ key 变更时重建 client
- ✅ base_url 变更时重建 client

### 4. thinking_level 映射测试 (3 个测试)
- ✅ thinking_level → reasoning_effort 映射
- ✅ 不支持 reasoning_effort 时自动重试
- ✅ 非 reasoning_effort 错误重新抛出

### 5. 结构化输出测试 (2 个测试)
- ✅ OpenAI strict schema 转换 (additionalProperties: false)
- ✅ 嵌套对象的 strict 转换

### 6. messages 格式转换测试 (3 个测试)
- ✅ 单轮字符串转换
- ✅ 多轮对话转换
- ✅ 多 part 文本拼接

### 7. 端到端测试 (3 个测试)
- ✅ dialogue/summary profile 切换
- ✅ provider 从 Gemini 切换到 Grok
- ✅ __main__ 脚本执行

## 关键测试点

### Provider 切换
- Gemini / Grok / Hermes 三个 provider 都有完整测试
- 验证了 client 的惰性初始化和重建逻辑

### 参数传递
- per-call 参数覆盖正确性
- profile (dialogue/summary) 参数选择正确性
- thinking_level 映射到 reasoning_effort

### Explicit Cache (Gemini 专有)
- cached_content 存在时不重复传 system_instruction
- OpenAI 兼容后端自动退回内联模式

### 错误处理
- reasoning_effort 不支持时自动重试
- 其他异常正确重新抛出
- 缺少 API key 时抛出清晰错误

### 结构化输出
- Gemini: response_schema 直接传递
- OpenAI: 自动转换成 strict schema (additionalProperties: false)

## Mock 策略

### Gemini Mock
```python
patch("scripts.llm.genai.Client")  # Mock Gemini client
patch("scripts.llm.types")          # Mock types.ThinkingConfig / GenerateContentConfig
```

### OpenAI Mock
```python
patch("openai.OpenAI")  # Mock OpenAI client (惰性导入)
```

### Settings Mock
```python
patch("scripts.llm.dialogue_params")
patch("scripts.llm.summary_params")
patch("scripts.llm.get_api_key")
patch("scripts.llm.get_xai_key")
patch("scripts.llm.get_hermes_key")
patch("scripts.llm.hermes_base_url")
patch("scripts.llm.get_provider")
```

## 测试运行

```bash
# 运行 llm.py 测试
pytest tests/unit/test_llm.py -v

# 查看覆盖率
pytest tests/unit/test_llm.py --cov=scripts.llm --cov-report=term-missing

# 生成 HTML 覆盖率报告
pytest tests/unit/test_llm.py --cov=scripts.llm --cov-report=html
```

## 设计亮点

1. **完整的 Provider 覆盖**: 三个 provider (Gemini/Grok/Hermes) 都有独立测试
2. **边缘情况处理**: reasoning_effort 不支持的重试逻辑
3. **Client 管理**: 惰性初始化和 key/base_url 变更重建
4. **参数传递**: per-call 覆盖和 profile 选择都正确验证
5. **格式转换**: Gemini ↔ OpenAI messages 格式转换完整测试

## 未来改进

1. 可以添加性能测试（client 复用 vs 重建）
2. 可以添加并发调用测试（多线程场景）
3. 可以添加更多边缘 schema (array/enum/anyOf 等)
