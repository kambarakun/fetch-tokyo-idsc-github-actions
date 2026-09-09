# 依存更新パイプラインの生存確認 (issue #683)

`.github/workflows/dependency-pipeline-watchdog.yml` と `scripts/check_dependency_pipeline.py` の運用ドキュメント。

## なぜ必要か

依存更新の停止は「PR が失敗する」ではなく「**PR が来ない**」という無変化として現れる。リポジトリ上に痕跡が残らず、原因は Dependabot の job ログの中にしか存在しない。そのログはリポジトリ所有者しか閲覧できない。

実際に issue #681 では、`pyproject.toml` の `[tool.uv] required-version` が Dependabot 同梱の uv と一致せず、uv エコシステムの更新が **2026-07-27 から 6 週間 (スケジュール 6 回分) 無音で停止**していた。Dependabot Security Updates も同じ updater を通るため、CVE が出ても PR が作られない状態が同時に発生していた。

issue #680 で更新経路を uv 1 系統へ集約したことにより、この経路は単一障害点になった。そのバックストップが本ワークフローである。

## 実行タイミング

- `schedule`: 毎週水曜 09:23 JST (`cron: "23 0 * * 3"`)。Dependabot の週次実行 (月曜 09:00 JST) の 2 日後に見る
- `workflow_dispatch`: 閾値を入力で下げられる。故意にアラートを起こす検証に使う

判定に使うのは日時・ラベル・バージョン文字列などの構造化フィールドのみで、PR / issue の本文とタイトルは読まない (AGENTS.md のプロンプトインジェクション方針)。権限は `contents: read` + `issues: write` のみで、`pull_request_target` は使わない。

## 検査と閾値

| 検査 | 内容                                                                    | 既定の閾値 | 重要度    |
| ---- | ----------------------------------------------------------------------- | ---------- | --------- |
| 1    | エコシステム別の最終 Dependabot PR からの経過日数                       | 21 日      | 🔴 high   |
| 2    | cooldown を過ぎた直接依存の滞留件数                                     | 3 件       | 🔴 high   |
| 3a   | `.tool-versions` の uv が、pin 中の setup-uv の既知 checksum に含まれる | -          | 🔴 high   |
| 3b   | `.tool-versions` の uv が既知 checksum の上限に達している               | -          | 🟢 low    |
| 3c   | `.tool-versions` の uv と dependabot-core 同梱 uv の major.minor が一致 | -          | 🟡 medium |

閾値の根拠:

- **検査 1 の 21 日** は週次スケジュール 3 回分。issue #681 の停止 (2026-07-27 開始) なら 2026-08-17 に発火しており、人間が気付いた 2026-09-09 より 3 週間早い
- **検査 2 の 3 件** は正常時の実測が 0 件であることに基づく。停止期間中は 5 件まで積み上がっていた。「Dependabot が提案できるのに提案していない」ものだけを数えるため、以下は**滞留に数えない**。いずれも数えると正常時に発火してノイズになる
  - **major バンプ**: `dependabot.yml` が `version-update:semver-major` を無視する
  - **pre-release と yank 済み**: Dependabot が提案しない
  - **宣言レンジの Python を切ったリリース**: Dependabot も uv も提案できない。数えると恒久的に解消しない滞留になる。判定は実行中インタプリタではなく `pyproject.toml` の `requires-python` (`>=3.11,<3.12`) の**下限**に対して行う。uv は宣言レンジ全体に対して解決するため、`>=3.11.10` のようにパッチレベルで下限を上げたリリースは、監視の実行環境 (3.11.15 など) では動いても lock できない
  - **最後の updater 実行時点で cooldown 内だったリリース**: 判定の基準時刻は「実行時刻」ではなく **`dependabot.yml` のスケジュールから求めた直近の updater 実行時刻**である。本ワークフローは水曜、updater は月曜なので、月曜時点で 6 日だったリリースは水曜には 8 日になる。実行時刻で判定すると、Dependabot に提案の機会が無かったものを停止と誤判定する
  - なお `info.version` ではなくリリース履歴全体を走査する。頻繁にリリースされるパッケージでは、滞留中の版の上に major 版や cooldown 内の版が来た瞬間に滞留が見えなくなり、**まさに updater が止まっているときに検知できない**ため
- **検査 3 の比較対象は upstream 最新版ではない**。setup-uv は既知 checksum の無い uv を検証をスキップしてインストールするため、pin の上限は「pin 中の setup-uv が checksum を知る最新版」である (CLAUDE.md「uv 本体の更新経路」)。upstream 最新と比較すると正常状態が常時アラートになる

## アラート別の対応

### 検査 1: 特定エコシステムの PR が途絶えた

1. Insights -> Dependency graph -> Dependabot で該当エコシステムの job ログを開く (リポジトリ所有者のみ)
2. `Required uv version ...` / `tool_version_not_supported` が出ていれば issue #681 と同じ再発。`.tool-versions` と `pyproject.toml` を確認し、uv の pin が uv 自身の読むファイルへ戻っていないか調べる
3. ログが正常で検査 2 も 0 件なら、単に更新対象が無いだけの偽陽性。閾値の引き上げを検討する

### 検査 2: 直接依存が滞留している

検査 1 と同時に発火していれば経路の停止。検査 2 のみなら、対象 PR が open のまま放置されている / CI が赤いままである可能性が高いので open PR を確認する。

### 検査 3a: uv pin が既知 checksum に含まれない

CI が checksum 未検証の uv バイナリを導入している状態なので即対応する。`.tool-versions` を、pin 中の setup-uv が知る最新版まで戻す。**setup-uv を上げてから uv を上げる**という順序は CLAUDE.md「uv 本体の更新経路」のとおり。

### 検査 3b: 検証つきで上げられる uv がある

次の setup-uv の Dependabot PR に `.tool-versions` の bump を同乗させる。単独では対応しない。

### 検査 3c: Dependabot 同梱 uv と系列がずれた

`uv.lock` を書く側 (Dependabot) と検証する側 (CI) の系列が違う状態。同一 minor 内の乖離は許容しているため、これが出たときは major / minor の追随を検討する。

## 手動検証

閾値を下げて故意にアラートを起こし、issue が起票されることを確認する。

```bash
# ローカル: 全検査の実行 (GITHUB_TOKEN があれば search API のレート制限が緩和される)
uv run --locked python scripts/check_dependency_pipeline.py --repo <owner>/<repo>

# ローカル: アラート経路の確認
uv run --locked python scripts/check_dependency_pipeline.py --repo <owner>/<repo> --max-pr-age-days 1
```

GitHub 上では Actions から `🔍 依存更新パイプラインの生存確認` を `workflow_dispatch` で起動し、`max_pr_age_days` に `1` を指定する。

## 終了コード

| コード | 意味                       | ワークフローの挙動                          |
| ------ | -------------------------- | ------------------------------------------- |
| 0      | 全検査を通過               | 追跡 issue が open なら自動でクローズ       |
| 1      | 1 件以上のアラート         | 追跡 issue を起票、既にあればコメントで追記 |
| 2 以上 | 検査自体が実行できなかった | ジョブを失敗させる                          |

終了コード 2 をジョブ失敗にするのは、**無音で失敗する監視は本 issue が対象とする不具合そのものを再現するため**である。
