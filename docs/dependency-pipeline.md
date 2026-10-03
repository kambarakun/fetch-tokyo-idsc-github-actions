# 依存更新パイプラインの生存確認 (issue #683)

`.github/workflows/dependency-pipeline-watchdog.yml` と `scripts/check_dependency_pipeline.py` の運用ドキュメント。

## なぜ必要か

依存更新の停止は「PR が失敗する」ではなく「**PR が来ない**」という無変化として現れ、PR の一覧からは「更新対象が無い」と見分けられない。updater の各実行は Actions の "Dependabot Updates" (API ではワークフロー `dynamic/dependabot/dependabot-updates`) に結論 (success / failure) 付きで残るが、原因が分かるログはリポジトリ所有者しか閲覧できない。

実際に issue #681 では、`pyproject.toml` の `[tool.uv] required-version` が Dependabot 同梱の uv と一致せず、uv エコシステムの更新が **2026-07-27 から 6 週間 (スケジュール 6 回分) 無音で停止**していた。Dependabot Security Updates も同じ updater を通るため、CVE が出ても PR が作られない状態が同時に発生していた。

issue #680 で更新経路を uv 1 系統へ集約したことにより、この経路は単一障害点になった。そのバックストップが本ワークフローである。

## 実行タイミング

- `schedule`: 毎週水曜 09:23 JST (`cron: "23 0 * * 3"`)。Dependabot の週次実行 (月曜 09:00 JST) の 2 日後に見る
- `workflow_dispatch`: 閾値を入力で下げられる。故意にアラートを起こす検証に使う

判定に使うのは日時・ラベル・バージョン文字列などの構造化フィールドのみで、PR / issue の本文とタイトルは読まない (AGENTS.md のプロンプトインジェクション方針)。権限は `actions: read` + `contents: read` + `issues: write` + `pull-requests: read` のみで、`pull_request_target` は使わない。`pull-requests: read` は検査 1 が `GET /repos/{owner}/{repo}/issues` の返す PR を読むために必須で、外すとトークンから PR が見えず全エコシステムを誤検知する (issue #697)。Search API はワークフロートークンでは 403 になるため使わない。`actions: read` は検査 1r が Dependabot Updates の run を読むために必須で、外すと 1r が毎週「検査不能」になる (issue #728)。

## 検査と閾値

| 検査 | 内容                                                                                           | 既定の閾値 | 重要度    |
| ---- | ---------------------------------------------------------------------------------------------- | ---------- | --------- |
| 1    | エコシステム別の最終 Dependabot PR からの経過日数                                              | 21 日      | 🔴 high   |
| 1r   | エコシステム別の Dependabot updater の最新 full run の結論と、前回スケジュール以降の実行の有無 | -          | 🔴 high   |
| 2    | cooldown を過ぎた直接依存の滞留件数 (open な Dependabot PR で提案済みの版は数えない)           | 3 件       | 🔴 high   |
| 3a   | `.tool-versions` の uv が、pin 中の setup-uv の既知 checksum に含まれる                        | -          | 🔴 high   |
| 3b   | `.tool-versions` の uv が既知 checksum の上限に達している                                      | -          | 🟢 low    |
| 3c   | `.tool-versions` の uv と dependabot-core 最新リリース同梱 uv の major.minor が一致            | -          | 🟡 medium |
| 4    | pin 中の Action が同梱する依存の既知脆弱性に、修正版を lock した release が出た                | -          | 🔴 high   |

閾値の根拠:

- **検査 1 の 21 日** は週次スケジュール 3 回分。issue #681 の停止 (2026-07-27 開始) なら 2026-08-17 に発火しており、人間が気付いた 2026-09-09 より 3 週間早い
- **検査 1r に閾値は無い**。最新の full run が success でない、または前回スケジュール以降に full run も refresh run も無ければ赤にする (issue #728)。起動の遅れは 1 日まで許容し、スケジュールから 1 日経つまではその前のスケジュールを基準にする
  - 検査 1 は PR の経過日数しか見ないため、updater が毎週失敗していても他の依存の PR が届く限り緑のままになる。実際に uv の full run は 2026-05-18〜09-08 に 06-01 を除いて毎週 failure だったが、検査 1 が初めて赤くなるのは 08-19 だった
  - run の題名 (API の `display_title`) は `uv in /. - Update #N` (full run: マニフェスト全体の更新) と `uv in / for ruff - Update #N` (refresh run: open PR 1 本の更新) の 2 形式。成否の判定は full run だけで行う
  - refresh run も「動いている」証拠に数えるのは、open PR が上限 (5) の週は Dependabot が refresh run だけを行うため (2026-05-04 / 05-11 に発生)
  - `dependabot.yml` の全エコシステムを対象にし、ラベルは問わない (検査 1 はラベルの無いエコシステムを黙って外す)
  - run を 1 件も取得できないときは「検査不能」にする。権限不足が空の 200 に見える可能性があるため (issue #697 と同じ理由)。`event=dynamic&actor=dependabot[bot]` の絞り込みは run を取りこぼす (2026-09-14 / 09-21 が欠けた) ので使わない
- **検査 2 の 3 件** は、提案済みの版を除外する前の実測 (2026-09-16 に 2 件、09-23 に 3 件で、いずれも全件がレビュー待ちの Dependabot PR で提案済みだった) に余裕を持たせた値。停止期間中は 5 件まで積み上がっていた。「Dependabot が提案できるのに提案していない」ものだけを数えるため、以下は**滞留に数えない**。いずれも数えると正常時に発火してノイズになる
  - **open な Dependabot PR が提案済みの版**: Dependabot が作成した、このリポジトリの `dependabot/uv/` ブランチの open PR について、head の `uv.lock` の版と main の版の大きい方を基準に判定する。提案済みの版より新しい適格版があれば引き続き数える。PR のタイトルと本文は読まない。head の lock を読めないときは「PR なし」とみなさず検査不能にする
  - **major バンプ**: `dependabot.yml` が `version-update:semver-major` を無視する
  - **pre-release と yank 済み**: Dependabot が提案しない
  - **宣言レンジの Python を満たさないリリース**: Dependabot も uv も提案できない。数えると恒久的に解消しない滞留になる。判定は実行中インタプリタではなく `pyproject.toml` の `requires-python` (`>=3.11,<3.12`) の**レンジ全体**に対して行う。uv はレンジ内の全インタプリタに対して解決するため、下限を上げた版 (`>=3.11.10`)・上限を下げた版 (`<3.11.5`)・レンジ内を除外した版 (`!=3.11.4`, `!=3.11.*`) はいずれも lock できない。監視の実行環境 (3.11.15 など) では動いてしまうため、実行中インタプリタでは判定できない。判定は宣言レンジを具体的なバージョンへ列挙して行う (ワイルドカードや除外の意味論を自前で再実装せず `packaging` に委ねるため。`SpecifierSet.contains` はワイルドカードを引数に取れない)
  - **最後の updater 実行時点で cooldown 内だったリリース**: 判定の基準時刻は「実行時刻」ではなく **`dependabot.yml` のスケジュールから求めた直近の updater 実行時刻**である。本ワークフローは水曜、updater は月曜なので、月曜時点で 6 日だったリリースは水曜には 8 日になる。実行時刻で判定すると、Dependabot に提案の機会が無かったものを停止と誤判定する
  - なお `info.version` ではなくリリース履歴全体を走査する。頻繁にリリースされるパッケージでは、滞留中の版の上に major 版や cooldown 内の版が来た瞬間に滞留が見えなくなり、**まさに updater が止まっているときに検知できない**ため
- **検査 3 の比較対象は upstream 最新版ではない**。setup-uv は既知 checksum の無い uv を検証をスキップしてインストールするため、pin の上限は「pin 中の setup-uv が checksum を知る最新版」である (CLAUDE.md「uv 本体の更新経路」)。upstream 最新と比較すると正常状態が常時アラートになる
- **検査 4 は Dependabot の死角を埋める** (issue #656)。pin した Action は自身の lockfile を同梱して実行される。Dependabot の github-actions エコシステムが追跡するのは **Action 自身のバージョンだけ**で、その中で固定されている依存は見ない。したがって Action 同梱依存の CVE は検査 1〜3 のどれにも映らず、`.github/workflows` の差分にも現れない。監視対象は `scripts/check_dependency_pipeline.py` の `WATCHED_ACTION_DEPENDENCIES` テーブルに 1 行ずつ書く
  - **アラートは「対応可能になった瞬間」だけに絞る**。脆弱版に留まっていること自体では発火させない。追随先が存在しない間に発火させると追跡 issue が数か月 open のままになり、検査 1〜3 の本物のアラートがその中に埋もれる。逆に、追随先が出た週に確実に赤くなる。追随先とは「修正版を lock した release」だけでなく「対象依存を同梱しなくなった release」も含む — どちらへ更新してもこの行が追う脆弱性は解消するため
  - 「上流最新の release」の判定に `/releases/latest` は使えない。anthropics/claude-code-action は浮動の `v1` release を貼り替えて公開しており、このエンドポイントはそれを返す。`v1.2.3` 形式のタグのうち **semver で最大**のものを採る (文字列比較では `v1.0.9` が `v1.0.220` より大きくなる)
  - lockfile が 404 になった場合は検査を通さず「検査不能」にする。「取得できなかった」を「該当依存は無い」と解釈すると、**検証していない安全宣言**になるため
- **検査はファミリー (1 / 1r / 2 / 3 / 4) ごとに隔離する** (issue #728)。各ファミリーは別々の第三者ファイルに依存するため、1 つが読めなくなっても他の判定は捨てずに report に残し、アラートは issue 化まで進める。読めなかったファミリーは ⚠️「検査不能」行 (`<ファミリー>:error`) になり、終了コードは 2 のまま (ジョブは赤くなる)。`dependabot.yml` の読み込みだけは全ファミリー共通なので、失敗すると従来どおり report なしで終了コード 2 になる

## 推移依存の更新経路

`.github/dependabot.yml` の uv エントリは `allow: [{dependency-type: "all"}]` で、`uv.lock` にだけ載る推移依存も更新対象にしている (issue #728)。これが無いと更新対象はマニフェストの直接依存だけになり、certifi (データ取得の TLS 検証が使う CA バンドル) などはどこからも更新されない。

- 推移依存は `groups` の**最後**に置いた `transitive` グループ (`patterns: ["*"]`、minor / patch のみ) の 1 本の PR にまとまる。Dependabot は最初に一致したグループを採るため、既存の production / testing / build-tools / type-stubs はそのまま効く
- 既存グループに属さない直接依存 (matplotlib / seaborn / packaging / jsonschema) は `exclude-patterns` で除外し、従来どおり単独 PR にする。直接依存を追加したときは、既存グループの `patterns` か `exclude-patterns` のどちらかに加える (`tests/test_dependabot_config.py` が検証する)
- semver-major の ignore は推移依存にも効く。**CalVer の年替わり (certifi / tzdata / pytz の 2025.x → 2026.x) も major とみなされて除外される**。年替わりの遅れは watchdog でも検知していない (follow-up)
- 検査 2 は直接依存だけを見るため、推移依存の遅れは検査 2 に映らない

## アラート別の対応

### 検査 1: 特定エコシステムの PR が途絶えた

1. Insights -> Dependency graph -> Dependabot で該当エコシステムの job ログを開く (リポジトリ所有者のみ)
2. `Required uv version ...` / `tool_version_not_supported` が出ていれば issue #681 と同じ再発。`.tool-versions` と `pyproject.toml` を確認し、uv の pin が uv 自身の読むファイルへ戻っていないか調べる
3. ログが正常で検査 2 も 0 件なら、単に更新対象が無いだけの偽陽性。閾値の引き上げを検討する

### 検査 1r: updater の full run が失敗している / 動いていない

1. Actions -> "Dependabot Updates" で、レポートの run リンク (該当エコシステムの最新 full run) を開く
2. ログの "Dependencies failed to update" の表で失敗した依存とエラー種別を見る (ログはリポジトリ所有者のみ)
   - `tool_version_not_supported`: issue #681 の再発。`.tool-versions` と `pyproject.toml` を確認し、uv の pin が uv 自身の読むファイルへ戻っていないか調べる
   - `dependency_file_content_not_changed` など単一依存の失敗: その依存を個別に直すか、`dependabot.yml` の `ignore` に足す。直すまで 1r は毎週赤のまま続くが、それは許容する (他の依存の更新は進んでいる)
3. 「前回スケジュール以降の実行: なし」なら updater 自体が起動していない。Insights -> Dependency graph -> Dependabot で該当エコシステムの状態を確認する。`dependabot.yml` を変更すると全エコシステムが即時に再実行される

### 検査 2: 直接依存が滞留している

検査 1 / 1r と同時に発火していれば経路の停止。検査 2 のみなら、対象 PR の CI が赤いまま close された、または Dependabot が提案をやめた可能性があるので、該当依存の PR 履歴を確認する。open な Dependabot PR が既に提案している版は数えていないので、レビュー待ちの PR はこの検査の原因にならない。detail の「提案済みで除外 N 件」は、その除外で数えなかったパッケージの件数で、N が大きければレビュー待ちが溜まっている合図になる。

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
   lock=$(curl -fsSL https://raw.githubusercontent.com/anthropics/claude-code-action/<tag>/bun.lock) \
     && printf '%s\n' "$lock" | grep -o '"shell-quote@[0-9.]*"' | sort -V
   ```

   `-f` を付けたうえで、**grep へパイプで直結しない**。`curl -s` は HTTP 404 でも終了コード 0 で `404: Not Found` を stdout に流すため、grep の空出力が「対象依存なし」と見分けられなくなる。かといって `curl -f ... | grep ...` と繋ぐと、`pipefail` 無しでは `$?` が最後のコマンドのものになり、今度は curl の失敗が終了コードに出ない。上の形なら curl が失敗した時点で `&&` の右側が実行されず、`$?` も curl のものになる (自動検査が lockfile の取得失敗を「検査不能」にしているのと同じ線)。`sort -V` の**先頭が最小のコピー**で、露出を決めるのはこれ。curl が成功したうえで出力が空なら依存自体が消えており、その release へ更新すれば解消するが、入れ替わり先が同じ問題を抱えていないかを併せて確認する

2. 公式タグの実 commit SHA を確認し、`.github/workflows/claude.yml` と `claude-code-review.yml` の pin を同じ SHA へ更新する (両ファイルは同一 SHA を pin する。ずれると検査 4 が「検査不能」になる)
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

| コード | 意味                                   | ワークフローの挙動                                                        |
| ------ | -------------------------------------- | ------------------------------------------------------------------------- |
| 0      | 全検査を通過                           | 追跡 issue が open なら自動でクローズ                                     |
| 1      | 1 件以上のアラート                     | 追跡 issue を起票、既にあればコメントで追記                               |
| 2 以上 | 一部または全部の検査が実行できなかった | 他の検査のアラートは起票・追記し、追跡 issue は閉じずにジョブを失敗させる |

終了コード 2 をジョブ失敗にするのは、**無音で失敗する監視は本 issue が対象とする不具合そのものを再現するため**である。検査不能の行があるときに追跡 issue を閉じないのは、見えていない検査が停止を隠している可能性があるため。
