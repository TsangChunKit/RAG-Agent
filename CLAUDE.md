## Session 開始規則（必須遵守）

每次新 session 開始時，在做任何任務之前，必須先讀取並理解 `README.md`。

- 用 Read 工具讀取 README.md
- 確認專案目標、架構、當前狀態後，再開始執行任務

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