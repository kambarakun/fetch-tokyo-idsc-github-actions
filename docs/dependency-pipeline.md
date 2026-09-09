# 依存更新パイプラインの生存確認 (issue #683)

`.github/workflows/dependency-pipeline-watchdog.yml` と `scripts/check_dependency_pipeline.py` の運用ドキュメント。

## なぜ必要か

依存更新の停止は「PR が失敗する」ではなく「**PR が来ない**」という無変化として現れる。リポジトリ上に痕跡が残らず、原因は Dependabot の job ログの中にしか存在しない。そのログはリポジトリ所有者しか閲覧できない。

実際に issue #681 では、`pyproject.toml` の `[tool.uv] required-version` が Dependabot 同梱の uv と一致せず、uv エコシステムの更新が **2026-07-27 から 6 週間 (スケジュール 6 回分) 無音で停止**していた。Dependabot Security Updates も同じ updater を通るため、CVE が出ても PR が作られない状態が同時に発生していた。

issue #680 で更新経路を uv 1 系統へ集約したことにより、この経路は単一障害点になった。そのバックストップが本ワークフローである。

## 実行タイミング

- `schedule`: 毎週水曜 09:23 JST (`cron: "23 0 * * 3"`)。Dependabot の週次実行 (月曜 09:00 JST) の 2 日後に見る
- `workflow_dispatch`: 閾値を入力で下げられる。故意にアラートを起こす検証に使う

判定に使うのは日時・ラベル・バージョン文字列などの構造化フィールドのみで、PR / issue の本文とタイトルは読まない (AGENTS.md のプロンプトインジェクション方針)。権限は `contents: read` + `issues: write` + `pull-requests: read` のみで、`pull_request_target` は使わない。`pull-requests: read` は検査 1 が `GET /repos/{owner}/{repo}/issues` の返す PR を読むために必須で、外すとトークンから PR が見えず全エコシステムを誤検知する (issue #697)。Search API はワークフロートークンでは 403 になるため使わない。

## 検査と閾値

| 検査 | 内容                                                                                | 既定の閾値 | 重要度    |
| ---- | ----------------------------------------------------------------------------------- | ---------- | --------- |
| 1    | エコシステム別の最終 Dependabot PR からの経過日数                                   | 21 日      | 🔴 high   |
| 2    | cooldown を過ぎた直接依存の滞留件数                                                 | 3 件       | 🔴 high   |
| 3a   | `.tool-versions` の uv が、pin 中の setup-uv の既知 checksum に含まれる             | -          | 🔴 high   |
| 3b   | `.tool-versions` の uv が既知 checksum の上限に達している                           | -          | 🟢 low    |
| 3c   | `.tool-versions` の uv と dependabot-core 最新リリース同梱 uv の major.minor が一致 | -          | 🟡 medium |
| 4    | pin 中の Action が同梱する依存の既知脆弱性に、修正版を lock した release が出た     | -          | 🔴 high   |

閾値の根拠:

- **検査 1 の 21 日** は週次スケジュール 3 回分。issue #681 の停止 (2026-07-27 開始) なら 2026-08-17 に発火しており、人間が気付いた 2026-09-09 より 3 週間早い
- **検査 2 の 3 件** は正常時の実測が 0 件であることに基づく。停止期間中は 5 件まで積み上がっていた。「Dependabot が提案できるのに提案していない」ものだけを数えるため、以下は**滞留に数えない**。いずれも数えると正常時に発火してノイズになる
  - **major バンプ**: `dependabot.yml` が `version-update:semver-major` を無視する
  - **pre-release と yank 済み**: Dependabot が提案しない
  - **宣言レンジの Python を満たさないリリース**: Dependabot も uv も提案できない。数えると恒久的に解消しない滞留になる。判定は実行中インタプリタではなく `pyproject.toml` の `requires-python` (`>=3.11,<3.12`) の**レンジ全体**に対して行う。uv はレンジ内の全インタプリタに対して解決するため、下限を上げた版 (`>=3.11.10`)・上限を下げた版 (`<3.11.5`)・レンジ内を除外した版 (`!=3.11.4`, `!=3.11.*`) はいずれも lock できない。監視の実行環境 (3.11.15 など) では動いてしまうため、実行中インタプリタでは判定できない。判定は宣言レンジを具体的なバージョンへ列挙して行う (ワイルドカードや除外の意味論を自前で再実装せず `packaging` に委ねるため。`SpecifierSet.contains` はワイルドカードを引数に取れない)
  - **最後の updater 実行時点で cooldown 内だったリリース**: 判定の基準時刻は「実行時刻」ではなく **`dependabot.yml` のスケジュールから求めた直近の updater 実行時刻**である。本ワークフローは水曜、updater は月曜なので、月曜時点で 6 日だったリリースは水曜には 8 日になる。実行時刻で判定すると、Dependabot に提案の機会が無かったものを停止と誤判定する
  - なお `info.version` ではなくリリース履歴全体を走査する。頻繁にリリースされるパッケージでは、滞留中の版の上に major 版や cooldown 内の版が来た瞬間に滞留が見えなくなり、**まさに updater が止まっているときに検知できない**ため
- **検査 3 の比較対象は upstream 最新版ではない**。setup-uv は既知 checksum の無い uv を検証をスキップしてインストールするため、pin の上限は「pin 中の setup-uv が checksum を知る最新版」である (CLAUDE.md「uv 本体の更新経路」)。upstream 最新と比較すると正常状態が常時アラートになる
- **検査 4 は Dependabot の死角を埋める** (issue #656)。pin した Action は自身の lockfile を同梱して実行される。Dependabot の github-actions エコシステムが追跡するのは **Action 自身のバージョンだけ**で、その中で固定されている依存は見ない。したがって Action 同梱依存の CVE は検査 1〜3 のどれにも映らず、`.github/workflows` の差分にも現れない。監視対象は `scripts/check_dependency_pipeline.py` の `WATCHED_ACTION_DEPENDENCIES` テーブルに 1 行ずつ書く
  - **アラートは「対応可能になった瞬間」だけに絞る**。脆弱版に留まっていること自体では発火させない。追随先が存在しない間に発火させると追跡 issue が数か月 open のままになり、検査 1〜3 の本物のアラートがその中に埋もれる。逆に、追随先が出た週に確実に赤くなる。追随先とは「修正版を lock した release」だけでなく「対象依存を同梱しなくなった release」も含む — どちらへ更新してもこの行が追う脆弱性は解消するため
  - 「上流最新の release」の判定に `/releases/latest` は使えない。anthropics/claude-code-action は浮動の `v1` release を貼り替えて公開しており、このエンドポイントはそれを返す。`v1.2.3` 形式のタグのうち **semver で最大**のものを採る (文字列比較では `v1.0.9` が `v1.0.220` より大きくなる)
  - lockfile が 404 になった場合は検査を通さずエラー終了する。「取得できなかった」を「該当依存は無い」と解釈すると、**検証していない安全宣言**になるため

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

比較対象は dependabot-core の**最新リリースタグ**の `uv/Dockerfile` であり、`main` ブランチではない。`main` には未リリースのコミットや後で revert されたコミットが含まれ、GitHub がホストする updater はそれを実行しないため、`main` と比較すると上流の通常の変更が 3c のアラートになる。ただし GitHub が実際にデプロイしている revision を示す公開情報は存在しないため、リリースからデプロイまでのラグは残る。レポートは比較したタグ名 (`bundled_ref`) を出力するので、アラート時はそのタグが実際にデプロイ済みかを併せて確認する。本検査を 🟡 medium に留めているのはこのラグがあるためである。

### 検査 4: pin 中 Action 同梱依存の修正版 release が出た

1. レポートの `latest_release` タグの lockfile を直接見て、修正版が入っている (または対象依存が消えている) ことを確認する。release 番号や公開日では判定できない

   ```bash
   curl -fsSL https://raw.githubusercontent.com/anthropics/claude-code-action/<tag>/bun.lock \
     | grep -o '"shell-quote@[0-9.]*"' | sort -V
   ```

   `-f` は必須である。`curl -s` は HTTP 404 でも終了コード 0 で `404: Not Found` を stdout に流すため、grep の空出力が「対象依存なし」と見分けられなくなる (自動検査が lockfile の 404 をエラー終了させているのと同じ理由)。`sort -V` の**先頭が最小のコピー**で、露出を決めるのはこれ。curl が成功したうえで出力が空なら依存自体が消えており、その release へ更新すれば解消するが、入れ替わり先が同じ問題を抱えていないかを併せて確認する

2. 公式タグの実 commit SHA を確認し、`.github/workflows/claude.yml` と `claude-code-review.yml` の pin を同じ SHA へ更新する (両ファイルは同一 SHA を pin する。ずれると検査 4 自体がエラー終了する)
3. 7 日 cooldown 後に取り込む。security release として前倒しする場合は PR にその根拠を書く
4. Dependabot が同じ更新を提案していれば、その PR に相乗りしてよい
5. `WATCHED_ACTION_DEPENDENCIES` の該当行は、解消後に削除してよい (残しても検査は緑のまま)

## 手動検証

閾値を下げて故意にアラートを起こし、issue が起票されることを確認する。

```bash
# ローカル: 全検査の実行 (GITHUB_TOKEN があれば未認証の 60 req/h 制限を避けられる)
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
