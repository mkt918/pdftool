# 11_pdftool — Claude への指示

PDF ユーティリティ（圧縮・画像書き出し・ページ操作・サムネイル）。本人専用の Windows デスクトップアプリ。

## 最初に読むもの
1. `docs/PLAN.md` — 仕様・確定事項・フェーズ分担（Opus / Sonnet）
2. `docs/API.md` — core / utils / UI 骨格の公開インタフェース。**Sonnet フェーズでは変更しない**

## 絶対条件
- `core/` は PySide6 を import しない
- 原本を上書きしない。出力名は `utils/naming.py` 経由
- 重い処理は `MainWindow.run_worker()` で別スレッド
- コメント・UI 文言は日本語、識別子は英語

## 開発
```
.venv\Scripts\python -m pytest -q          # 全テスト（UI は offscreen）
.venv\Scripts\python -m pdftool [PDF]      # 起動（PYTHONPATH=src が必要。run.bat 参照）
```
実 Ghostscript のテストは検出時のみ走る。未検出なら skip。
