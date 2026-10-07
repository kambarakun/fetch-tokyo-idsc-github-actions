# 開発手順

ローカル開発・依存管理・GitHub Actions の運用・メタデータスキーマ変更の手順をまとめる。エージェント向けの規約と不変条件は [`AGENTS.md`](../AGENTS.md)、Markdown の書き方は [`docs/markdown-style.md`](markdown-style.md)、依存更新の監視は [`docs/dependency-pipeline.md`](dependency-pipeline.md)、Dependabot PR の判定は [`docs/dependabot-pr-review.md`](dependabot-pr-review.md) を参照する。

## セットアップ

1. uv を `.tool-versions` の版で用意する (mise を使う場合は `mise install`)。インストーラを直接実行すると pin を無視して最新版が入るため使わない
2. 依存を lockfile どおりに入れる: `uv sync --all-extras --locked`
3. pre-commit フックを有効にする: `uv run pre-commit install`

Python パッケージ管理は uv だけを使う。pip / poetry / conda と `uv pip install` は使わない。`uv.lock` は必ずコミットする。

```bash
uv run pytest --cov=src --cov-branch --cov-fail-under=100
uv run pre-commit run --all-files
```

CI と同じフラグで実行したいときは `.github/workflows/test.yml` のコマンドをそのまま使う。

## 依存の追加

- 本番依存: `uv add <pkg>`
- 開発依存: `uv add --optional dev <pkg>`

開発依存は `pyproject.toml` の `[project.optional-dependencies] dev` に置く。次の 3 箇所がこの表を読むため、`[dependency-groups]` には置かない。

- pre-commit の Python ツールフック (`.pre-commit-config.yaml` の isort / black / ruff / mypy。`--extra dev` 付きで起動する)
- watchdog の直接依存判定 (`scripts/check_dependency_pipeline.py::direct_requirements`)
- `tests/test_dependabot_config.py`

## uv 本体のバージョン固定

uv 本体のバージョンは `.tool-versions` の 1 行で固定する (issue #681)。mise と CI の `astral-sh/setup-uv` (`version-file: .tool-versions`) が読み、uv 自身は読まない唯一の共通フォーマットである。

- `pyproject.toml` の `[tool.uv] required-version` や `uv.toml` には置かない。uv 自身が強制するハードガードのため、同梱 uv を使う Dependabot の uv エコシステム job が起動時に落ち、通常更新もセキュリティ更新も無音で止まる (2026-07-27 から 2026-09-09 に実際に発生)
- `.mise.toml` は作らない。同一ディレクトリでは mise が `.mise.toml` を優先し、pin の二重ソースになる
- `astral-sh/setup-uv` には必ず `version-file: .tool-versions` を明示する。未指定だと `uv.toml` → `pyproject.toml` → latest の順で解決される
- `.tool-versions` に `python` 行は足さない。setup-uv v10 以降は `version-file` の python 行も読み、`.python-version` と二重定義になる
- `tests/test_dependabot_config.py::test_uv_version_pin_lives_outside_uv_config` が上記を検証する

## uv 本体の更新経路

Dependabot には `.tool-versions` を扱うエコシステムが無いため、uv 本体の pin は手動で更新する (issue #682)。

**ルール: `.tool-versions` の uv は、pin している `astral-sh/setup-uv` が checksum を知る最新版に合わせる。**

- 根拠: setup-uv は既知 checksum の無い uv を、検証をスキップしてインストールする ([checksum.ts](https://github.com/astral-sh/setup-uv/blob/v10.2.0/src/download/checksum/checksum.ts))。警告は debug ログにしか出ないので、uv だけ先に上げると CI の uv バイナリが未検証になる
- 判定: setup-uv の release notes (`chore: update known checksums for X`)、または pin している版の [known-checksums.json](https://github.com/astral-sh/setup-uv/blob/v10.2.0/src/download/checksum/known-checksums.json) (v10.1.0 で `.ts` から分離) に `x86_64-unknown-linux-gnu-<version>` キーがあるかで判断する。リンクは v10.2.0 を指しているので、pin を上げたらタグを読み替える
- 順序: setup-uv を上げてから uv を上げる。Dependabot (github-actions) が setup-uv の minor / patch PR を出したら、その PR に `.tool-versions` の bump を積んで一緒にマージする。major は Dependabot が `semver-major` を ignore するため手動 PR で行う
- Dependabot 同梱の uv とは同一 minor 内の乖離を許容する (0.12.4 と 0.12.7 で `uv lock --check` の差分なし・`revision` 不変を実測)。major / minor が食い違ったら追随する
- 乖離の検知は watchdog の検査 3 が行う。検査内容・閾値・アラート別の対応は [`docs/dependency-pipeline.md`](dependency-pipeline.md) を参照する

## GitHub Actions の SHA pin 運用

外部 Action は可変タグ (`@vX`) ではなく `@<40 桁 SHA> # vX` の形で参照する。目的は、ワークフロー実行の再現性、サプライチェーンリスクの低減、実行された Action の版の監査性である。

Dependabot の PR は [`docs/dependabot-pr-review.md`](dependabot-pr-review.md) の runbook で判定する。github-actions の PR では、あわせて次を確認する。

1. Action のリリースノートで変更内容を確認する
2. SHA が公式リポジトリのタグの解決値と一致するか確認する
3. 権限・入力パラメータ・破壊的変更の有無を確認する
4. CI の成功を確認する

GitHub Actions の依存は自動マージしない (レビュー必須、マージは人間のみ)。緊急時は `git revert <commit>` で戻す。

pin 漏れは次のコマンドで確認する。何も出力せず終了コード 1 なら、外部 Action はすべて `@<40 桁 SHA> # v<版>` の形で pin されている。ローカル参照 (`./`) は対象外。

```bash
git grep -nP '^\s*(- )?uses:\s*[^./][^@]*@(?![0-9a-f]{40}\b)|^\s*(- )?uses:\s*[^./][^@]*@[0-9a-f]{40}(?!\s+#\s*v\d)' -- .github/workflows/
```

- `git grep -P` は PCRE 対応ビルドの git が必要 (Apple Git 2.39.5 で動作を確認済み)。非対応ビルドではエラーで止まる
- 追跡済みファイルだけを見る。新規ファイルは `git add` してから実行する
- 前半の分岐はタグなど 40 桁 SHA 以外の参照を、後半の分岐は SHA の直後に `-xxx` などの接尾辞が続くもの (可変な ref) と、版コメント `# v<版>` の無い SHA を検出する。版コメントは Dependabot PR の事前検証がタグと SHA を照合する手がかりになる
- 代替: PCRE2 対応の ripgrep があれば `rg -n --pcre2 '<同じパターン>' .github/workflows/`
- 同じパターンは `tests/test_docs_consistency.py` が全ワークフローに対して検査する

## ワークフロー静的検証 (actionlint)

`.github/workflows/` 配下は actionlint で静的検証する (issue #308)。ローカルでは pre-commit 経由で実行する。actionlint の版は `.pre-commit-config.yaml` の `rev:` を正とする。

```bash
uv run pre-commit run actionlint --all-files
```

- CI では `.github/workflows/actionlint.yml` が、ワークフローを変更した PR と main への push で実行し、違反があれば失敗する
- shellcheck 統合も有効。reviewdog は shellcheck の style / info も error として扱うため、新しい shellcheck 違反を入れると CI が落ちる
- shellcheck の過剰検知で止まる場合の暫定回避は、shellcheck 統合を無効にすること。CI は `.github/workflows/actionlint.yml` の `actionlint_flags: -shellcheck=`、ローカルは `.pre-commit-config.yaml` の actionlint フックに `args: ["-shellcheck="]` を足す

## ログ

- `fetch-data` は `--log-file <path>` を指定したときだけファイルにログを書く。指定しなければ標準出力だけに出る
- CI のログは `.github/workflows/_fetch-data-common.yml` の `Upload logs` ステップがアップロードする artifact を参照する
- `*.log` は `.gitignore` で除外されており、コミットされない

## メタデータスキーマの変更手順

フィールドの意味は `README.md` の「メタデータ管理」節と、CI が検証に使う正本の schema (`schemas/metadata-v1.3.schema.json`) を参照する。スキーマを変えるときは次の順で進める。

1. `src/models/metadata.py::METADATA_VERSION` を上げる
2. `schemas/` に新版の schema を追加し、`scripts/validate_metadata_schema.py::DEFAULT_SCHEMA` を新しいファイルに向け直す
3. `src/cli/migrate_metadata.py` に移行関数を追加し、`@migration_registry.register(from_version=..., to_version=...)` で登録する
4. 変更内容を確認する: `uv run migrate-metadata --dry-run`
5. schema 検証を通す: `uv run python scripts/validate_metadata_schema.py`

`uv run migrate-metadata` を `--dry-run` なしで実行すると、コミット済みの `data/` 配下のメタデータを書き換える。実データへの適用は人間の明示的な指示があるときだけ行う ([`AGENTS.md`](../AGENTS.md) の Safety 節)。
