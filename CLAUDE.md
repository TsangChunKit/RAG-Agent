## Session 開始規則（必須遵守）

每次新 session 開始時，在做任何任務之前，必須先讀取並理解項目文檔。

**必讀文檔（按順序）**：
1. `README.md` - 項目概覽和用戶文檔
2. `CLAUDE.md` - 開發規則（本文件）
3. `docs/API_REFERENCE.md` - API 規範和函數簽名
4. `docs/ARCHITECTURE.md` - 系統架構和模塊依賴

**根據任務類型選讀**：
- 開發新功能 → `docs/DEVELOPMENT_GUIDE.md`
- 提升測試覆蓋率 → `docs/TESTING_COVERAGE_PLAN.md`
- 查看進度 → `docs/COVERAGE_MILESTONE_REPORT.md`

## 架構變更規則（必須遵守）

當使用者提出架構改動需求時：

1. **先討論，後實施**：
   - 先進行完整的技術分析和方案設計
   - 列出多個方案的優缺點、改動範圍、風險評估
   - 等待使用者確認選定方案後，才開始寫代碼
   
2. **禁止邊討論邊寫代碼**：
   - 在架構還在計劃階段時，不要寫任何實際代碼
   - 不要「順便先實施一部分」或「做個快速 demo」
   - 保持討論的純粹性，避免使用者被迫接受已寫好的代碼

3. **方案評估必須使用反脆弱性原則**：
   - 不只看「省多少 token」或「改多少行」，要評估系統的反脆弱性
   - 反脆弱性評估維度（按優先級排序）：
     * **簡單性（Via Negativa）**：複雜性本身就是脆弱性。優先刪除、減少、抽象，而不是新增機制。活動部件越少越好；一個能解釋清楚的方案，勝過三個「聰明但耦合」的方案。
     * **可逆性**：出問題時能否快速、低成本回退？改動是否可以逐步部署、局部驗證？不可逆的決策要付出極高代價才能通過。
     * **選擇權（Optionality）**：方案是否保留未來的選擇空間？還是把系統鎖死在特定路徑上？優先選擇「現在簡單 + 未來可擴展」的設計，而不是「現在最優但未來難以轉向」的設計。
     * **降級能力**：失敗時是否有應急備選？能否優雅降級而非崩潰？系統在壓力下應該「變差但仍可用」，而不是「突然不可用」。
     * **失敗可見性**：失敗是否容易被發現和定位？還是會悄悄累積成技術債或黑盒行為？不可觀測的失敗是隱形脆弱。
     * **用戶掌控**：用戶能否手動覆蓋算法／自動化決策？還是被鎖在黑盒裡？保留人工介入點是反脆弱的保險。
     * **冗餘度**：有沒有備選路徑？單一組件失敗時系統是否還能繼續工作？適度冗餘優於過度精簡。
   - 避免過度優化：多個「聰明機制」堆疊 = 多個潛在斷裂點與交互失敗面
   - 優先選擇「簡單到難以失敗」的方案，而不是「複雜但理論最優」的方案。當兩者衝突時，簡單性永遠優先。

4. **確認後再動工**：
   - 使用者明確說「開始實施」或「就這樣改」時，才開始修改代碼
   - 實施前再次確認改動範圍和檔案清單

## 文檔維護規則（必須遵守）

**核心原則：代碼和文檔必須同步更新**

### 每次開發任務的文檔更新責任

| 開發類型 | 必須更新的文檔 | 更新內容 |
|---------|---------------|---------|
| **新增函數/模塊** | `docs/API_REFERENCE.md` | 添加函數簽名、參數、返回值、用法示例 |
| **修改函數簽名** | `docs/API_REFERENCE.md` | 更新受影響的函數文檔 |
| **新增模塊依賴** | `docs/ARCHITECTURE.md` | 更新模塊依賴圖、數據流圖 |
| **新增數據流** | `docs/ARCHITECTURE.md` | 添加數據流說明、關鍵接口 |
| **新增 Workspace 功能** | `docs/ARCHITECTURE.md` | 更新 Workspace 隔離機制說明 |
| **修改開發流程** | `docs/DEVELOPMENT_GUIDE.md` | 更新對應的 Phase 或檢查清單 |
| **新增測試** | `docs/TESTING_STRATEGY.md` | 更新測試覆蓋率數據 |
| **達成覆蓋率里程碑** | `docs/COVERAGE_MILESTONE_REPORT.md` | 創建新報告或更新現有報告 |
| **修改 Python 版本** | `docs/API_REFERENCE.md` | 更新兼容性說明 |
| **新增常見錯誤** | `docs/API_REFERENCE.md` | 添加到常見錯誤速查 |

### 文檔更新檢查清單

**提交前必須確認**：

- [ ] 如果新增了函數，是否更新了 API_REFERENCE.md？
- [ ] 如果修改了模塊依賴，是否更新了 ARCHITECTURE.md？
- [ ] 如果改變了開發流程，是否更新了 DEVELOPMENT_GUIDE.md？
- [ ] 如果提升了測試覆蓋率，是否更新了相關文檔？
- [ ] 所有代碼示例是否與實際代碼一致？
- [ ] 所有鏈接是否仍然有效？

### 文檔質量標準

1. **準確性**
   - 函數簽名必須與實際代碼一致
   - 參數類型必須正確（Python 3.9 兼容）
   - 示例代碼必須可運行

2. **完整性**
   - 所有公開函數都有文檔
   - 所有參數都有說明
   - 所有返回值都有說明

3. **可讀性**
   - 使用清晰的標題和表格
   - 代碼示例帶注釋
   - 錯誤示例和正確示例對比

4. **可維護性**
   - 使用相對鏈接（不要硬編碼路徑）
   - 模塊化（大文檔拆分成多個小文檔）
   - 版本標記（重大變更時記錄日期）

### 文檔審查

**自我審查問題**：
1. 如果我是第一次看這個項目，這個文檔能幫我理解嗎？
2. 文檔中的例子能直接複製粘貼運行嗎？
3. 文檔和代碼有沒有不一致的地方？
4. 半年後回來看，這個文檔還有用嗎？

**如果答案是"否"，必須改進文檔。**

### 文檔衝突解決

如果文檔和代碼不一致：
1. **優先相信代碼**（代碼是唯一真相）
2. **立即更新文檔**（文檔過時比無文檔更危險）
3. **添加測試**（防止再次不一致）

### 示例：完整的文檔更新流程

```python
# 場景：添加新函數 validate_input()

# 1. 寫測試（TDD）
def test_validate_input():
    assert validate_input("valid") == True

# 2. 實現函數
def validate_input(text: str) -> bool:
    """驗證輸入文本。"""
    return len(text) > 0

# 3. 更新 API_REFERENCE.md
"""
#### `validate_input()`
\`\`\`python
def validate_input(text: str) -> bool:
    \"\"\"
    驗證輸入文本。
    
    Args:
        text: 待驗證的文本
    
    Returns:
        True 如果有效，False 如果無效
    \"\"\"
\`\`\`
"""

# 4. 如果是新模塊，更新 ARCHITECTURE.md 的依賴圖

# 5. 提交時檢查：文檔已更新 ✅
```

### 違規處理

**如果發現文檔和代碼不一致**：
- ⚠️ 第一次：警告，立即修復
- ❌ 第二次：阻止提交（通過 git hook）
- 🔴 第三次：標記為技術債，優先處理

**文檔更新是強制性的，不是可選的。**

---

## Git 行為規則（必須遵守）

1. 每完成一個**完整邏輯單元**的工作後，必須自己執行 git commit。
2. **Commit message 必須由你自己撰寫**，格式使用 Conventional Commits：
   - feat: 新功能
   - fix: 修復
   - refactor: 重構
   - docs: 文件
   - chore: 雜項
   - style: 格式調整
   - test: 測試相關

3. Commit message 要求：
   - 第一行簡短清楚（≤ 72 字元）
   - 必要時可加 body 說明改了什麼、為什麼改
   - 不要寫無意義的 "update"、"wip"、"changes"

4. 整個 task 全部完成後：
   - 執行 `git add -A`
   - 用你寫好的 message 做 `git commit`
   - 然後執行 `git push`

5. 除非使用者明確說不要 push，否則 task 完成後都要 push。

## 測試開發規則（必須遵守）

### 核心原則：TDD（Test-Driven Development）

**强制要求**：任何新功能 / 修改必須先有測試，否則不允許提交。

### 為什麼測試通過但 UI 還有錯誤？

**核心問題**：測試覆蓋率不足 + 沒有測試實際執行路徑

**教訓總結**：

1. **導入測試（Smoke Test）是第一道防線**
   - **必須優先寫**：在寫任何功能測試前，先寫導入測試
   - 導入測試能捕獲：
     * 語法錯誤
     * 導入錯誤（NameError, ImportError）
     * 類型注解兼容性問題（Python 3.9 vs 3.10+）
     * 模塊級別的執行錯誤（import-time errors）
   
   ```python
   # tests/unit/test_imports.py - 最基本但最重要
   def test_import_all_modules():
       """確保所有模塊都能成功導入"""
       from scripts import (
           workspace_manager,
           chunk,
           parse,
           settings,
           # ... 列出所有核心模塊
       )
       assert True  # 能到這裡就說明導入成功
   ```

2. **測試實際執行路徑，不只是理想情況**
   - ❌ 錯誤：只測試 `parse_filename_date("valid_file.txt")`
   - ✅ 正確：測試 `from scripts import parse`（實際導入路徑）
   - **Import-time errors vs Runtime errors**：
     * Import-time: 導入時執行的代碼（模塊級別，如類型注解）
     * Runtime: 調用函數時執行的代碼
     * 很多錯誤是 import-time，但只測試函數調用無法捕獲

3. **代碼覆蓋率是測試質量的關鍵指標**
   - 目標：至少 80% 覆蓋率
   - 低覆蓋率 = 大量未測試代碼 = 潛在錯誤
   - **案例**：我們的覆蓋率只有 2%，所以測試通過但 UI 有 11 個導入錯誤
   
   ```bash
   # 每次提交前檢查覆蓋率
   pytest --cov=scripts --cov-report=term-missing
   ```

4. **測試金字塔要完整**
   ```
   ╱───────────────╲
   │  E2E/集成測試   │  ← 慢，少量，測試完整流程
   ├───────────────┤
   │   功能測試      │  ← 中速，中量，測試業務邏輯
   ├───────────────┤
   │  導入/單元測試  │  ← 快速，大量，測試基本可用性
   ╲───────────────╱
   ```
   
   **底層必須穩固**：如果導入測試都不通過，功能測試通過也沒意義。

5. **測試開發的優先順序**
   ```
   第一步：導入測試（test_imports.py）
      ↓
   第二步：核心模塊單元測試（workspace_manager, chunk, parse）
      ↓
   第三步：集成測試（完整流程：parse → chunk → ingest → ask）
      ↓
   第四步：UI 測試（Streamlit 應用測試）
   ```

6. **具體實施要求**
   - 每個新模塊必須先有導入測試
   - 每次提交前運行 `pytest tests/unit/test_imports.py`
   - 目標覆蓋率：導入測試 100%，單元測試 80%，整體 70%
   - CI 必須包含覆蓋率檢查，低於閾值拒絕合併

7. **常見陷阱**
   - ❌ 只測試"理想路徑"（happy path）
   - ❌ 測試通過就以為代碼沒問題
   - ❌ 忽略代碼覆蓋率報告
   - ❌ 沒有測試邊緣情況和錯誤處理
   - ❌ Import-time 錯誤沒有被測試覆蓋

### 開發新功能的強制流程（必須嚴格遵守）

**絕對禁止：寫代碼 → 手動測試 → 提交** ❌

**正確流程：測試先行 → 實現功能 → 自動驗證** ✅

#### 流程詳細步驟

1. **需求確認階段**
   - 明確功能需求和邊界條件
   - 確定輸入/輸出規格
   - 識別可能的錯誤情況

2. **測試設計階段**（在寫任何實現代碼前）
   ```python
   # tests/unit/test_new_feature.py
   def test_new_feature_happy_path():
       """測試正常情況"""
       result = new_feature(valid_input)
       assert result == expected_output
   
   def test_new_feature_edge_case():
       """測試邊緣情況"""
       result = new_feature(edge_case_input)
       assert result.is_valid()
   
   def test_new_feature_error_handling():
       """測試錯誤處理"""
       with pytest.raises(ValueError):
           new_feature(invalid_input)
   ```

3. **實現功能**
   - 先實現最簡單能讓測試通過的版本
   - 運行測試：`pytest tests/unit/test_new_feature.py -v`
   - 測試通過 ✅ → 繼續優化
   - 測試失敗 ❌ → 修改實現直到通過

4. **覆蓋率驗證**
   ```bash
   # 新增代碼必須達到 80% 覆蓋率
   pytest tests/unit/test_new_feature.py --cov=scripts.new_feature --cov-report=term-missing
   ```
   
   如果覆蓋率 < 80%，補充測試直到達標

5. **集成測試**
   ```bash
   # 確保新功能不破壞現有功能
   pytest tests/integration/ --integration -v
   ```

6. **靜態檢查**
   ```bash
   python scripts/check_code_patterns.py scripts/new_feature.py
   ```

7. **提交前最終檢查**
   ```bash
   # 運行完整測試套件
   pytest tests/ --integration -v
   ```

#### 覆蓋率目標（強制執行）

| 模塊類型 | 最低覆蓋率 | 推薦覆蓋率 | 狀態 |
|---------|----------|-----------|-----|
| **核心業務邏輯** | 80% | 90% | 🔴 當前 13-35% |
| **配置/工具類** | 60% | 80% | 🟡 當前 50-71% |
| **UI 代碼** | 50% | 70% | 🟢 當前 N/A |
| **批處理腳本** | 40% | 60% | 🔴 當前 0% |
| **整體項目** | 70% | 85% | 🔴 當前 22% |

**核心業務邏輯包括**：
- `scripts/ask.py` - 問答核心（當前 13% ❌）
- `scripts/chunk.py` - 分塊邏輯（當前 35% ❌）
- `scripts/ingest.py` - 入庫邏輯（當前 32% ❌）
- `scripts/build_graph.py` - 圖譜構建（當前 33% ❌）
- `scripts/graph_utils.py` - 圖譜工具（當前 9% ❌）

**當前問題**：這些文件總共 1000+ 行代碼，只有 20% 被測試覆蓋 = 80% 完全沒測試！

#### 新功能開發檢查清單

**在開始寫實現代碼前**：
- [ ] 已設計測試用例（至少 3 個：正常/邊緣/錯誤）
- [ ] 已創建測試文件 `tests/unit/test_<module>.py`
- [ ] 測試文件已加入 git

**實現功能時**：
- [ ] 運行測試並確保通過
- [ ] 使用 `pytest --cov` 檢查覆蓋率
- [ ] 覆蓋率達到最低要求（見上表）

**提交前**：
- [ ] 運行導入測試：`pytest tests/unit/test_imports.py -v`
- [ ] 運行靜態檢查：`python scripts/check_code_patterns.py`
- [ ] 檢查整體覆蓋率：`pytest --cov=scripts --cov-report=term-missing`
- [ ] 新增代碼覆蓋率 ≥ 80%
- [ ] 整體覆蓋率沒有下降
- [ ] 所有測試通過（unit + integration）
- [ ] 實際啟動 UI 驗證：`streamlit run app.py`（如果改了 UI 相關代碼）

### 為什麼覆蓋率這麼低？

**歷史原因**：
- 項目初期沒有測試文化
- 先寫功能，後補測試（實際上從未補）
- 複雜業務邏輯（ask.py 532 行）難以事後補測試

**核心問題**：
```
寫代碼（1小時）→ 手動測試（10分鐘）→ 發現錯誤 → 修復 → 再測試 → 提交
                     ↓
                 沒有自動化
                 下次改代碼又破壞
                 重複手動測試
```

**正確做法**：
```
寫測試（20分鐘）→ 寫代碼（1小時）→ pytest（5秒）→ 全部通過 ✅ → 提交
                     ↓
                 自動化保護
                 任何改動立即知道
                 永久性防護
```

**投資回報**：
- 前期：多花 20 分鐘寫測試
- 後期：節省無數小時調試 + 防止線上事故

### 測試 Checklist（每次提交前）

**必須全部通過，否則不允許提交**：

- [ ] 運行導入測試：`pytest tests/unit/test_imports.py -v`
- [ ] 運行靜態檢查：`python scripts/check_code_patterns.py`
- [ ] 檢查覆蓋率：`pytest --cov=scripts --cov-report=term-missing`
- [ ] 新增代碼覆蓋率 ≥ 80%
- [ ] 整體覆蓋率 ≥ 當前值（不允許下降）
- [ ] 所有測試通過：`pytest tests/ --integration -v`
- [ ] 所有新增模塊都有導入測試？
- [ ] 實際啟動 UI 驗證：`streamlit run app.py`（如果改了 UI）

**記住**：測試不只是為了通過 CI，而是為了確保代碼在實際環境中能正常運行。

---

## Pre-commit Hook（自動化強制執行）

為確保測試規則被嚴格遵守，項目已配置 pre-commit hook。

### Hook 做什麼？

每次 `git commit` 前自動執行：

1. **靜態代碼檢查**
   ```bash
   python scripts/check_code_patterns.py
   ```
   檢查：
   - 路徑函數使用錯誤
   - Python 3.9 類型注解兼容性
   - workspace_id 參數缺失

2. **導入測試**
   ```bash
   pytest tests/unit/test_imports.py --tb=short
   ```
   確保所有模塊能成功導入

3. **覆蓋率檢查**
   ```bash
   pytest --cov=scripts --cov-fail-under=70
   ```
   確保整體覆蓋率 ≥ 70%（當前閾值，未來提升到 80%）

**任何一項失敗 → commit 被拒絕 → 必須修復後才能提交**

### 如何安裝 Hook？

```bash
# 創建 hook 文件
cat > .git/hooks/pre-commit << 'EOF'
#!/bin/bash
# Pre-commit hook: 運行測試和檢查

echo "🔍 Running pre-commit checks..."

# 1. 靜態檢查
echo "📝 Static code check..."
python scripts/check_code_patterns.py
if [ $? -ne 0 ]; then
    echo "❌ Static check failed! Fix errors before committing."
    exit 1
fi

# 2. 導入測試
echo "📦 Import tests..."
source .venv/bin/activate
pytest tests/unit/test_imports.py --tb=short -q
if [ $? -ne 0 ]; then
    echo "❌ Import tests failed! Fix import errors before committing."
    exit 1
fi

# 3. 覆蓋率檢查
echo "📊 Coverage check..."
pytest --cov=scripts --cov-fail-under=70 --tb=no -q
if [ $? -ne 0 ]; then
    echo "❌ Coverage below 70%! Add more tests before committing."
    echo "Run: pytest --cov=scripts --cov-report=html"
    echo "Then open: htmlcov/index.html"
    exit 1
fi

echo "✅ All checks passed!"
exit 0
EOF

# 賦予執行權限
chmod +x .git/hooks/pre-commit
```

### 如何臨時跳過 Hook？

**僅在緊急情況下使用**（如修復線上事故）：
```bash
git commit --no-verify -m "emergency fix: ..."
```

**警告**：跳過 hook 後必須立即補測試！

### Hook 失敗時怎麼辦？

1. **查看錯誤信息**
   ```bash
   python scripts/check_code_patterns.py  # 查看靜態錯誤
   pytest tests/unit/test_imports.py -v    # 查看導入錯誤
   pytest --cov=scripts --cov-report=html  # 查看覆蓋率報告
   ```

2. **修復錯誤**
   - 靜態錯誤：按提示修復代碼
   - 導入錯誤：修復語法/導入問題
   - 覆蓋率不足：補充測試

3. **重新提交**
   ```bash
   git add -A
   git commit -m "..."  # Hook 會再次運行
   ```

---

## 提升覆蓋率行動計劃

### 優先級 P0（核心業務邏輯，必須儘快達到 80%）

1. **scripts/ask.py** (13% → 80%)
   - 當前：532 行，只測試了 67 行
   - 需要：400+ 行測試覆蓋
   - 重點測試：
     * `retrieve()` 函數（混合檢索邏輯）
     * `answer()` 函數（問答主流程）
     * GraphRAG 引導檢索
     * 歷史壓縮邏輯

2. **scripts/graph_utils.py** (9% → 80%)
   - 當前：99 行，只測試了 9 行
   - 需要：70+ 行測試覆蓋
   - 重點測試：
     * `resolve_graph()` 圖譜歸並算法
     * 節點去重邏輯
     * 邊合併邏輯

3. **scripts/chunk.py** (35% → 80%)
   - 當前：133 行，測試了 47 行
   - 需要：60+ 行新測試
   - 重點測試：
     * 滑動窗口分塊
     * 父塊擴展
     * 上下文前綴生成

### 優先級 P1（工具類，達到 60%）

4. **scripts/ingest.py** (32% → 60%)
5. **scripts/build_graph.py** (33% → 60%)
6. **scripts/session_graph.py** (21% → 60%)

### 如何快速提升？

**方法 1：從失敗測試學習**
```bash
# 運行集成測試，看哪些路徑沒覆蓋
pytest tests/integration/ --integration --cov=scripts --cov-report=html
open htmlcov/index.html  # 紅色 = 未覆蓋
```

**方法 2：補充缺失的測試**
```python
# 找到覆蓋率報告中的紅色行
# 為每個未覆蓋的代碼路徑寫測試

# 示例：ask.py 的 retrieve() 函數
def test_retrieve_with_graph_guidance():
    """測試 GraphRAG 引導檢索"""
    # 這個測試現在不存在，所以相關代碼沒被覆蓋
    pass
```

**方法 3：重構 + 測試**
- 大函數拆成小函數（更易測試）
- 複雜邏輯抽取成純函數（無副作用，易測試）
- 依賴注入（便於 mock）