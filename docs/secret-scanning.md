# 秘密情報の検出 (gitleaks)

通常の commit と CI の `pre-commit run --all-files` で、トークンや鍵などの秘密情報の混入を検出して失敗させる仕組みの説明と運用手順 (issue #736)。

## 構成

| 要素                           | 役割                                                                                                                                  |
| ------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------- |
| `.pre-commit-config.yaml`      | `gitleaks/gitleaks` の hook `gitleaks` を full commit SHA と版コメントで固定する。hook 環境が gitleaks を build して PATH に置く      |
| `scripts/check_secrets.py`     | hook の `entry`。pre-commit が渡したファイルを一時ディレクトリへ hard link (できなければ copy) し、`gitleaks dir --redact` で検査する |
| `.gitleaks.toml`               | gitleaks 既定ルールを継承 (`useDefault = true`) し、確認済みの誤検出だけを狭い allowlist で除く                                       |
| `.gitleaksignore` (必要時のみ) | 個別 finding の fingerprint (`<path>:<rule>:<line>`) で除く。行番号に依存するため `.gitleaks.toml` の allowlist を優先する            |
| `tests/test_secret_scan.py`    | 一時 git repository で実 hook を動かし、staged commit と `--all-files` の検出、redaction、失敗時の扱い、他 hook の除外設定を検証する  |

### 上流 hook の entry を差し替える理由

上流の hook 定義は `gitleaks git --pre-commit --redact --staged --verbose` (`pass_filenames: false`) で、index だけを読む。CI の `pre-commit run --all-files` では staged file がないため、コミット済みの秘密情報があっても何も検査せず `Passed` になる (合成 repository で再現済み)。そこで hook の `entry` を `python3 scripts/check_secrets.py` に差し替え、pre-commit が渡すファイル名を検査する共通経路にしている。

- commit 時: pre-commit は unstaged の変更を退避してから hook を動かすため、作業ツリーの内容は staged の内容と一致する。部分 stage でも検査対象は commit される内容になる
- `--all-files` 時: tracked の text file 全部 (`data/` と `tests/fixtures/` を含む)。binary (`docs/images/*.png`) は `types: [text]` で対象外

### 他 hook との除外設定の関係

`data/`・`tests/fixtures/`・`*.csv`・`*.lock` などは以前 top-level の `exclude` で全 hook から外していた。top-level の `exclude` はどの hook からも上書きできないため、gitleaks 以外の各 hook に同じ正規表現 (`exclude: *repo_exclude`) を移した。gitleaks 以外の hook の対象ファイルは移動の前後で一致することを確認済み。top-level の `exclude` を戻すと gitleaks がこれらを読まなくなるため、`tests/test_secret_scan.py` が検査している。

## 実行方法

```bash
uv run pre-commit run gitleaks --all-files            # tracked text file 全部を検査
uv run pre-commit run gitleaks --all-files --verbose  # 検査したファイル数とバイト数も表示
uv run pre-commit run gitleaks --files <path>...      # 指定ファイルだけ検査
```

`scripts/check_secrets.py` を直接実行すると gitleaks が PATH にないため失敗する。必ず pre-commit 経由で実行する。

初回は pre-commit が gitleaks の repository を clone し、`language_version` の Go toolchain を `dl.google.com` から取得して build する (数十秒~数分)。以降は pre-commit の cache (`~/.cache/pre-commit`) を再利用する。

所要時間の目安 (2026-10-27、開発機の macOS で計測): `--all-files` は 53,889 ファイル・約 4.3 GB を約 85~100 秒で検査した。大半は `data/logs/` の validation log で、gitleaks の CPU 時間が支配的。commit 時は staged のファイルだけを検査する。

## 出力と検出時の対応

検出値は出力しない。gitleaks は `--redact` 付き・`--verbose` なしで動き、wrapper は次の形式で位置とルールだけを表示する。

```text
src/settings.py:1: github-pat (fingerprint src/settings.py:github-pat:1)
check_secrets: 1 candidate secret(s) in 1 file(s); values are not shown. ...
```

次の場合は検出の有無にかかわらず失敗する (fail closed)。

- gitleaks が PATH にない
- gitleaks が自身のエラーで終了した (設定ファイルの構文エラーなど)
- report が読めない、または終了コードと report の内容が一致しない

検出時の判断:

1. 本物の秘密情報の可能性があるとき: 値をログ・issue・PR・チャットに貼らない。commit 前ならファイルから取り除き、環境変数か GitHub の secret に移す。すでに push 済みなら秘密情報の失効と再発行は所有者が行う。履歴の書き換えも所有者の判断で、エージェントは行わない
2. 誤検出と確認できたとき: `.gitleaks.toml` に allowlist を 1 件追加する。`targetRules` でルールを、`paths` でファイルを、`regexTarget = "line"` の `regexes` で行の内容を指定し、`condition = "AND"` で全部を満たすときだけ除く。複数行を含む場合があるため行の正規表現には `(?m)` を付ける。`description` に誤検出と判断した根拠を書く
3. ディレクトリ単位 (`tests/` や `data/` 全体) の除外、ルールの無効化、hook の削除や `SKIP=gitleaks` での回避はしない

### 現在の例外

| 対象                                                  | ルール            | 根拠                                                                                              |
| ----------------------------------------------------- | ----------------- | ------------------------------------------------------------------------------------------------- |
| `tests/test_vet_dependabot_prs.py` の `APP_TOKEN_OLD` | `generic-api-key` | `actions/create-github-app-token` v3.3.0 の公開 commit SHA。変数名に `TOKEN` を含むため検出される |

## 更新

- gitleaks の版は `.pre-commit-config.yaml` の `rev:` (full SHA + 版コメント) を正とする。Dependabot の pre-commit ecosystem が更新 PR を作る。手で更新するときは `pre-commit autoupdate --freeze --repo https://github.com/gitleaks/gitleaks` を使い、release の公開日 (7 日以上経過)、タグが指す commit、gitleaks の公開 security advisory を確認する
- `language_version` は pre-commit が取得する Go toolchain の版。pre-commit は `GOTOOLCHAIN=local` で build するため、gitleaks の `go.mod` の `go` 行以上を保つ。下回ると hook のインストールが失敗する (Dependabot の PR では CI の lint job が落ちる)。Dependabot はこの値を更新しないため手で上げる
- 版を上げた PR では `uv run pre-commit run gitleaks --all-files --verbose` と `uv run pytest tests/test_secret_scan.py` を実行する

現在の固定の根拠 (2026-10-27 確認):

- gitleaks v8.30.1: タグ `v8.30.1` は commit `83d9cd684c87d95d656c1458ef04895a7f1cbd8e` を直接指す (annotated tag ではない)。GitHub release の公開は 2026-03-21。`gitleaks/gitleaks` の公開 security advisory は 0 件。gitleaks の `go.mod` は `go 1.24.11`
- Go 1.27.3: [Go のリリース履歴](https://go.dev/doc/devel/release#go1.27.3) で 2026-10-19 公開 (crypto/x509 のセキュリティ修正)。pre-commit は `https://dl.google.com/go/go1.27.3.<os>-<arch>.tar.gz` から取得する。[go.dev/dl](https://go.dev/dl/) が公開する sha256 は linux-amd64 `65efbc80fdc1fa2cd2631cad5b481777a68e8725f34e89f50fc6ac714e379b40`、darwin-arm64 `dd3908cf20959d798c1e06ebe5b72213f0505ce01373b40ce3110975a6cd99c2` (pre-commit 自体は照合しない)

## 対象外と既知の制約

- git の履歴 (過去の commit) は検査しない。全履歴の再監査は行っていない
- untracked / gitignored のファイル、binary file は検査しない
- `.secrets.baseline` は #5 (f37b42165) で追加された空ファイルで、どの hook も読まない (detect-secrets が `.pre-commit-config.yaml` に入っていた履歴もない)。保持しており、削除は別途判断する
- GitHub 側の secret scanning / push protection の設定は所有者の作業で、この仕組みとは別に確認する
- `tests/test_secret_scan.py` は初回に hook 環境をインストールするため、GitHub と Go の配布元・module proxy への通信が必要 (データ源には接続しない)
- pre-commit による Go toolchain の取得は HTTPS のみで、checksum の照合はしない
