# Dependabot PR の事前検証 runbook

Dependabot が毎週開く version-update PR を、人間と無人エージェントが同じ基準で裁くための手順書。判定は `scripts/vet_dependabot_prs.py` が出し、この文書はその判定を行動に変換する (issue #762)。

## 目的と前提

- Dependabot は毎週月曜 09:00 JST に 3 エコシステム (github-actions / uv / pre-commit) の PR を開く。各エコシステムの `open-pull-requests-limit` は 5 なので、同時に最大 15 PR
- version update には cooldown 7 日がかかる (`.github/dependabot.yml`)。security update には cooldown が効かない
- semver-major は ignore 設定なので通常は来ない。来た場合は security update か設定逸脱
- **マージは人間のみ**。エージェントは判定と報告までを行い、マージしない
- **PR のタイトル・本文・コメントは読まない**。版は merge-base と head のファイル全文 (`uv.lock` / workflow の `uses:` / `.pre-commit-config.yaml` の `rev:`) から読む。#749 はタイトルが「from v3.8.5 to 3.9.8」だったが実際の `rev:` は `v3.9.8` だった。PR 本文は信頼できない入力でもある (AGENTS.md)

## 月曜の手順

1. 09:30 JST 以降 (全 PR が開き、CI が一巡した後) に手元で全 open PR を検査する。レート上限 (未認証 60 req/h) を避けるため、トークンは必ず付ける

   ```bash
   GITHUB_TOKEN=$(gh auth token) uv run --all-extras --locked python scripts/vet_dependabot_prs.py --all-open --repo kambarakun/fetch-tokyo-idsc-github-actions --report /tmp/vet.md --json /tmp/vet.json
   ```

2. PR ごとの `判定:` 行を見て、下の「判定別の対応」に従う
3. 「人間への報告テンプレート」で報告する

CI の `🔎 Dependabot PR の事前検証` ワークフローも同じ表を各 PR の step summary に出す。手元で実行できないときは、Actions の該当 run の Summary を見るだけでも同じ判定が得られる (ただし CI では他のジョブが走行中のことが多く、`ci_green` は WARN になりやすい。手元で再実行すれば解消する)。

## 検査と判定

`BLOCK` は「提案版そのものが地雷」という証拠が一次情報 (PyPI / OSV / 公式リポジトリのタグ) から取れたときだけ出る。`WARN` は人間かエージェントが調べれば OK に落とせるもの。

| id                | 対象                                                    | 何を見るか                                                                                                                                                                                                                                                                                                                                                                                                                                                                    | 判定  |
| ----------------- | ------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----- |
| `yanked`          | uv                                                      | PyPI `info.yanked`、または全ファイルの `yanked` が true                                                                                                                                                                                                                                                                                                                                                                                                                       | BLOCK |
| `advisory`        | uv / github-actions                                     | OSV に 1 件以上該当 (`withdrawn` 付きの取り下げ済み advisory は除く)。PyPI は `version` 付き問い合わせ。GitHub Actions は OSV が `version` を無視するため `version` 無しで問い合わせ、`ECOSYSTEM` range と `versions` を手元で評価する。版が厳密に分からない場合は WARN (下の「GitHub Actions の版の厳密さ」)                                                                                                                                                                 | BLOCK |
| `python_range`    | uv                                                      | 新版の `requires_python` が head の `pyproject.toml` `requires-python` の下限 (`>=3.11` → `3.11`) を含まない                                                                                                                                                                                                                                                                                                                                                                  | BLOCK |
| `tag_sha`         | github-actions / pre-commit (`rev` が 40 桁 SHA のとき) | タグが 404、または (annotated なら deref した) commit SHA が pin と不一致。版コメントの無い SHA pin は照合先が無いので WARN                                                                                                                                                                                                                                                                                                                                                   | BLOCK |
| `tag_exists`      | pre-commit (`rev` がタグのとき)                         | `GET /git/ref/tags/{rev}` が 404                                                                                                                                                                                                                                                                                                                                                                                                                                              | BLOCK |
| `action_metadata` | github-actions (`uses:` のパスごと)                     | 旧 pin と新 pin の **commit SHA** で Action metadata (`action.yml`、無ければ `action.yaml`) を読み、`runs.using` の変更・必須 input の追加・任意 → 必須への変更・既存 input の default の追加・変更・削除、input の削除、output の削除を比較する。置き換えた旧 pin が無い (新規追加など)・metadata が 404・解釈できない・再利用ワークフロー (`.github/workflows/` 直下の `.yml` / `.yaml`。それより深いパスは Action として比較する)・不正なサブパスは「比較不能」として WARN | WARN  |
| `cooldown`        | 全部                                                    | PR `created_at` と新版の公開時刻の UTC 暦日差が `dependabot.yml` の `cooldown.default-days` (無ければ 3) 未満。公開時刻が取れないときも WARN                                                                                                                                                                                                                                                                                                                                  | WARN  |
| `superseded`      | 全部                                                    | 新版より新しい非 yank・非 prerelease の版が、新版公開から 7 日以内に出ている。release が無いリポジトリ (タグのみ) と、github-actions の浮動タグ・版コメント無しの SHA pin は評価不能として OK                                                                                                                                                                                                                                                                                 | WARN  |
| `major_bump`      | 全部                                                    | 旧版と新版の major が異なる                                                                                                                                                                                                                                                                                                                                                                                                                                                   | WARN  |
| `pr_hygiene`      | PR                                                      | `head.ref` 接頭辞がどのエコシステムにも一致しない / 変更ファイルが期待集合の外 (uv: `pyproject.toml`, `uv.lock`。github-actions: `.github/workflows/*.yml` / `*.yaml`。pre-commit: `.pre-commit-config.yaml`) / Dependabot 以外の commit がある / bump を 1 件も検出できない                                                                                                                                                                                                  | WARN  |
| `ci_green`        | PR                                                      | head の check run が 0 件、または `status != completed`、または `conclusion` が `success` / `skipped` / `neutral` 以外。検証ジョブ自身 (`vet`) は除外                                                                                                                                                                                                                                                                                                                         | WARN  |

補足:

- 公開時刻は、PyPI は当該版の全ファイルの最小アップロード時刻、GitHub は `releases/tags/{tag}` の `published_at` → annotated tag の `tagger.date` → タグが指す commit の `committer.date` の順に取る
- cooldown を暦日差で数えるのは Dependabot に合わせるため。#745 (setup-uv v10.2.0 の公開 2026-09-21T13:15Z、PR 作成 2026-09-28T00:07Z) は経過 6.45 日だが暦日差 7 日で、Dependabot 自身は cooldown 充足として提案している
- 同じ bump (同じ依存・旧版・新版・SHA) は複数ファイルにあっても 1 回だけ検査する。同じ版コメントでも SHA が違えば別々に `tag_sha` を評価する
- OSV スキーマの特殊値に従う: `affected[].package.name` の `*` はエコシステム内の全 Action に当てる。`introduced: "0"` はどの版よりも前として扱う (`fixed` / `last_affected` / `limit` の `"0"` は数値の 0)
- Action の版と range の境界は、数値だけ (`1.2.3` / `7` など) のときに限って順序づける。`1.0.0-1` のような SemVer の prerelease / build は、PEP 440 では post-release (1.0.0 より後) と解釈され SemVer と順序が逆になるため使わない。版がそうなら「不明」と同じ扱い、境界がそうなら評価できない range として WARN にする
- OSV の range に `limit` イベントがあれば、OSV の評価手順どおり、どの `limit` より前でもない版 (`*` は無限大) は非該当とする
- レポートの表のセルは PR 由来の文字列 (repo URL・`rev`・action 名) を含むので、`|` / バッククォート / `[` / `]` / `<` をエスケープし、リンク先は percent-encode する。PyPI の `yanked_reason` (公開者が書く自由文) は表に載せない
- `action_metadata` は版コメントやタグではなく pin の SHA で読む (タグは動かせる)。`github/codeql-action/init` と `.../analyze` のように同じ repo の別サブパスは別の Action として行を分ける。比較する旧 pin は、その `uses:` 行が書き換わる前に指していた pin とする。Dependabot は pin だけをその場で書き換えるため、pin と版コメントを除いたファイルが新旧で完全に一致するときに限り、版コメントの有無や版の並びに関係なく行ごとに旧 → 新を組む (v1 / v2 → v3 / v4 は v1 → v3・v2 → v4、別々の pin から同じ新 pin へ集約されたら旧 pin それぞれと、据え置きの pin へ寄せた行もその pin と比較する)。書き換わっていない行は比較しない。それ以外 (行の追加・削除、step の入れ替えなど) では、どの step がどれになったかも、残った pin が同じ step のままかも分からないため、系列を推測せず、新しい各 pin をそのパスの旧 pin すべてと比較する (a → b・b → c のように、残った pin が別の step の前身である場合も拾う)。metadata は YAML 1.1 の真偽値変換をせずに読み、`on` / `yes` のような ID を別物として扱う。比較の根拠は [Action の metadata 構文](https://docs.github.com/en/actions/reference/workflows-and-actions/metadata-syntax)。`required: true` だけでは未指定の input を runner が拒否するとは限らず、宣言されていない output も設定できるため、差分は「破壊の証拠」ではなく「release notes を読む理由」として WARN にする。必須 input の行には `default` の有無を添える。既存 input の `default` が追加・変更・削除されたときは、省略していた呼び出し側に渡る値が変わるため WARN にする (値そのものは第三者の文字列なのでレポートに出さない。YAML の型ごと比較し、`'1'` と `1` は別の値として扱う)。input そのものが消えたときも、渡していた値が無視される・default に頼っていた呼び出し側に値が渡らなくなるため WARN にする (消えた input の行には `default` の有無を添える)。任意 input の追加だけでは WARN にしない。metadata の取得が 404 以外で失敗したときは他の検査と同じく exit 2
- `action_metadata` の根拠に載せる input / output 名と `runs.using` は第三者の metadata 由来なので、input ID の形 (ASCII の英数字・`_`・`-`。Unicode の文字は通さない) に合わない名前は「(表示できない名前)」に置き換え、形の合わない `runs.using` は解釈不能として扱う。比較に使う欄の型 (`runs.using` が文字列、`inputs` / `outputs` の各定義が mapping、`required` が真偽値、`default` が sequence / mapping でない値) が違う metadata も解釈不能の WARN とし、「変化なし」の OK には倒さない。差分が多い Action でもレポートが GitHub のコメント上限 (65,536 文字) を超えないよう、根拠に並べる差分と削除された output 名はそれぞれ 10 件 (`METADATA_DIFF_LIMIT`) までとし、残りは「ほか N 件」と件数だけを示す (全件は根拠のリンクの metadata で確認する)
- `advisory` の GitHub Actions 照合は、workflow の綴りと正規名 (`GET /repos/{owner}/{repo}` の `full_name`) の両方に大文字小文字を無視して当てる。移管・改名された repo は旧名でも動き続けるが OSV は新名で登録するため、名前が違えば両方の名前で OSV に照会して和集合を取る

### GitHub Actions の版の厳密さ

workflow の版コメントで、`advisory` と `superseded` の扱いが決まる。

| 版コメント                                                | 厳密さ | `advisory`                                                                                                                                                                                                                 | `superseded`      |
| --------------------------------------------------------- | ------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------- |
| `vX.Y.Z` (数値 3 要素)                                    | 厳密   | `versions` の完全一致か `ECOSYSTEM` range に入れば BLOCK                                                                                                                                                                   | 後続版を調べる    |
| `vN` / `vN.M`                                             | 浮動   | 接頭辞に属する版を含みうる advisory があれば WARN。`versions` に接頭辞一致の版があるか、range が `[N.0.0, (N+1).0.0)` (`vN.M` なら `[N.M.0, N.(M+1).0)`) と交わるか (`last_affected` は閉区間) で決める。交わらなければ OK | 評価不能として OK |
| 無し (SHA だけ)、または数値だけでない版 (`v1.0.0-1` など) | 不明   | 取り下げ済みを除く advisory が 1 件でもあれば WARN、無ければ OK                                                                                                                                                            | 評価不能として OK |

評価できない range 型 (`SEMVER` / `GIT`、版として読めない境界) は、厳密・浮動・不明のどれでも WARN になる。「評価できない」を「該当しない」と読まないため。

## 判定別の対応

| 判定  | 主体         | 行動                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| ----- | ------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| OK    | エージェント | 表と `判定: OK` を貼って人間に「merge 可能」と報告する。マージはしない                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| WARN  | エージェント | id ごとに調べる。`cooldown`: Dependabot alerts / advisory を確認し security update なら OK。`superseded`: 後続版の release notes に当該版の regression 修正があれば BLOCK、無ければ OK。`ci_green`: 未完了なら待つ、赤が bump と無関係 (データ更新 PR の失敗など) なら理由を書いて OK、関係するなら BLOCK。`major_bump`: advisory を確認、無ければ BLOCK。`advisory` (浮動タグ・版コメント無しで厳密判定不能): pin の SHA に対応する release を確認し (`git ls-remote --tags https://github.com/<owner>/<repo>` で SHA を指すタグを引く、または release ページで照合)、その版を detail の advisory ID の `versions` / range と手で照合する。該当すれば BLOCK、しなければ OK。`advisory` (評価できない range): advisory 本文の影響範囲 (`SEMVER` の版域や `GIT` の commit) と pin の SHA / 版を手で照合する。`superseded` (公開時刻を取得できない): タグと release を確認して手で判断する。`action_metadata` (差分あり): 根拠のリンクで新旧の metadata を開き、release notes と突き合わせる。`runs.using` の変更は runner の対応状況 (GitHub-hosted runner は自動更新) を、必須 input の追加・任意 → 必須は workflow 側でその input を渡しているか、`default` が意図どおりかを、input の削除は workflow がその input を `with:` で渡していないか (`default` に頼っていた場合は代わりの値が要るか) を、output の削除は workflow が `steps.<id>.outputs.<name>` を参照していないかを `rg` で確認する。問題なければ OK、参照が壊れるなら BLOCK。`action_metadata` (比較不能): 新規追加なら新版の metadata を読んで用途に合うか確認する。404 / 解釈不能は Action のリポジトリを pin の SHA で開いて確認する。`pr_hygiene`: 余剰ファイルが uv 本体の pin の同乗更新 (issue #682 の手順。`.tool-versions` と、uv 本体の更新経路を書いた `docs/development.md`) なら OK、それ以外は BLOCK。結論と根拠を PR コメントに書いてから OK / BLOCK として扱う |
| BLOCK | エージェント | 理由コメント → (必要なら) `@dependabot ignore …` コメント。`yanked` / `tag_sha` / `tag_exists` / `python_range` はその版だけの問題なので閉じるだけ。`advisory` は修正版が出るまで `ignore this patch version`。regression 系で系列ごと避けたいときだけ `minor version`。報告には「クローズ済み (理由)」を書く                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |

```mermaid
flowchart TD
    Start[月曜 09:30 JST 以降<br/>vet_dependabot_prs.py --all-open] --> Verdict{PR ごとの判定}

    Verdict -->|OK| ReportOk[表と 判定: OK を貼り<br/>merge 可能 と報告]
    Verdict -->|WARN| Investigate[WARN の id ごとに調査<br/>結論と根拠を PR コメント]
    Verdict -->|BLOCK| Reason[理由コメントを投稿]

    Investigate --> Resolved{調査の結論}
    Resolved -->|問題なし| ReportOk
    Resolved -->|問題あり| Reason

    Reason --> Scope{問題の範囲}
    Scope -->|その版だけ<br/>yanked / tag_sha / tag_exists / python_range| Close[PR を閉じる]
    Scope -->|修正版待ち<br/>advisory| IgnorePatch[@dependabot ignore this patch version]
    Scope -->|系列ごと避ける<br/>regression| IgnoreMinor[@dependabot ignore this minor version]

    Close --> ReportClosed[クローズ済み 理由 と報告]
    IgnorePatch --> ReportClosed
    IgnoreMinor --> ReportClosed
    ReportOk --> Human[人間がマージを判断]

    style ReportOk fill:#bfb,stroke:#333,stroke-width:2px
    style Reason fill:#f9f,stroke:#333,stroke-width:2px
    style Investigate fill:#bbf,stroke:#333,stroke-width:2px
```

## クローズとコマンド

投稿は 2 つに分ける。先に理由コメント (検査表と調査結果)、次にコマンドコメント。

| PR の種類                      | 修正版を待つ (patch を飛ばす)             | 系列ごと避ける                            |
| ------------------------------ | ----------------------------------------- | ----------------------------------------- |
| 単独 PR                        | `@dependabot ignore this patch version`   | `@dependabot ignore this minor version`   |
| グループ PR (`build-tools` 等) | `@dependabot ignore <name> patch version` | `@dependabot ignore <name> minor version` |

- `@dependabot ignore …` コマンドはそれ自体が PR を閉じる。別途 close する必要はない
- yank / SHA 不一致 / タグ無し / Python 非互換のように「その版だけ」が問題なら、コマンドは使わず理由を書いて PR を閉じるだけで良い。Dependabot は閉じた版を再提案しない旨を返信する
- ignore を解除するときは `@dependabot unignore …` を使う (コマンドの一覧は [Dependabot のコメントコマンド](https://docs.github.com/en/code-security/reference/supply-chain-security/dependabot-pull-request-comment-commands))

## 人間への報告テンプレート

PR ごとに次を貼る。

```markdown
### PR #<番号> (<エコシステム>)

<スクリプトが出した表をそのまま>

判定: <OK | WARN (n 件) | BLOCK (n 件)>

- CI: <ci_green の結果。未完了なら「待機中」>
- リンク: <PR URL> / <CI run URL>
- WARN の調査結果: <id ごとの結論と根拠。無ければ省略>
- 結論: merge 可能 | 要判断 (WARN の調査結果) | クローズ済み (理由)
```

## CI での動作

- ワークフロー: `.github/workflows/dependabot-pr-vetting.yml` (`🔎 Dependabot PR の事前検証`)、ジョブ名 `vet`
- 条件: `pull_request` (`opened` / `synchronize` / `reopened`) のうち `github.event.pull_request.user.login == 'dependabot[bot]'` のときだけ走る。`github.actor` ではなく PR の author を見るので、人間が #745 のように commit を積んでも再検査が走る。人間の PR ではジョブはスキップされる
- checkout は PR の base revision (`github.event.pull_request.base.sha`) で行う。提案された `uv.lock` を vetter 自身の環境に入れないため。スクリプト・`uv.lock`・`.tool-versions` は base 側のものを使い、提案内容は API 経由でだけ読む
- 権限: `contents: read` / `pull-requests: read` / `checks: read` のみ。Dependabot トリガでは `GITHUB_TOKEN` が read-only になるが、読み取りしか使わないので影響しない。`pull_request_target` は使わない
- 終了コード: 0 = BLOCK 無し (WARN は含む)、1 = BLOCK が 1 件以上、2 = 検査自体の失敗 (ネットワーク・パース・引数・想定外の例外)。2 のときは判定を出さず、レポートが無いこともある
- 表は `$GITHUB_STEP_SUMMARY` に出し、`report.md` / `report.json` を artifact `dependabot-pr-vetting-report` (30 日保持) に残す
- `WARN` は `::warning::` 注釈を出すだけでジョブは緑。`BLOCK` と exit 2 はジョブを赤くする
- required status check にはしていない (マージは止めない)。required 化は人間が ruleset で判断する
- `--comment` は CI では拒否される (exit 2)。投稿は手元から明示的に行う
- Markdown レポート (標準出力・`--report`・`--comment`) の表は 1 PR あたり 60,000 文字 (`REPORT_BODY_LIMIT`。GitHub のコメント上限 65,536 文字の内側) に収まる行だけを BLOCK → WARN → OK の順に載せ、載せきれない行は「表に載せきれない N 行を省略 (WARN a / OK b)」と件数で示す。行は PR の内容 (bump ごと、Action のパスごと、行が動いたときは旧 pin × 新 pin の組ごと) で増えるため。末尾の判定行は省略した行も含めて数え、全行は `--json` に残る

## 手動検証

録画済み応答によるオフライン再生 (ネットワーク不要):

```bash
uv run --all-extras --locked python scripts/vet_dependabot_prs.py --pr 748 --repo kambarakun/fetch-tokyo-idsc-github-actions --fixture tests/fixtures/vet_dependabot_prs/pr-748; echo exit=$?
```

録画 (実 API を叩いて応答を保存しながら判定する。スクリプトが読むキーだけを保存し、PR のタイトル・本文・patch、check run の `output`、候補版以下の PyPI release 履歴は保存しない。fixture は合計 300,000 bytes 未満に保つ):

```bash
GITHUB_TOKEN=$(gh auth token) uv run --all-extras --locked python scripts/vet_dependabot_prs.py --pr 748 --repo kambarakun/fetch-tokyo-idsc-github-actions --record tests/fixtures/vet_dependabot_prs/pr-748
```

`--pr 748` (ruff 0.16.7 → 0.16.8) の期待表:

| 検査           | 結果 | 根拠                                              |
| -------------- | ---- | ------------------------------------------------- |
| `yanked`       | OK   | PyPI `info.yanked: false`                         |
| `advisory`     | OK   | OSV の応答が `{}`                                 |
| `python_range` | OK   | `>=3.7` ⊇ 3.11                                    |
| `cooldown`     | OK   | 2026-09-16 → 2026-09-28 = 12 日 (≥ 7)             |
| `superseded`   | OK   | 0.16.9 は 8.2 日後で 7 日の窓の外                 |
| `major_bump`   | OK   | major 0 のまま                                    |
| `pr_hygiene`   | OK   | dependabot/uv/、pyproject.toml, uv.lock、commit 1 |
| `ci_green`     | OK   | success ×3 + skipped ×1                           |

末尾は `判定: OK`、終了コード 0。

## 制限と follow-up

- PyPI provenance / attestation の比較は未実装。2026-10 時点で `ruff` / `pyyaml` の provenance API が 404 で、信号として弱い
- レート上限: 1 PR あたり GitHub API を 10〜15 回呼ぶ。未認証 60 req/h では `--all-open` が途中で枯渇し得るので、手元でも `GITHUB_TOKEN` を付ける。429 / 403 は exit 2 になり、OK や BLOCK には倒れない
- HTTP の送信部 (ヘッダ・トークンを付けるホストの制限・timeout 30 秒) は `scripts/http_fetch.py` で `scripts/check_dependency_pipeline.py` (依存更新 watchdog) と共有する (#765)。失敗の表示は各スクリプトに残す。watchdog は第三者のエラー本文を stderr にだけ出し、例外 (= 追跡 issue に載る文) には入れない
- `action_metadata` は fixture (#748 は uv の PR) では再生されない。テストは URL をキーにした合成応答で行っている。`--record` は Action の metadata (`.github/actions/` 配下や `.github/workflows/` 以下の Action を含む。PR 自身が変更した workflow (`pulls/N/files` に載るファイル) だけは原文のまま) を比較に使う欄 (`runs.using`、各 input の `required` / `default`、output 名) だけに削って保存し、説明文や `branding` は残さない。`default` と、レポートに出せない形の input / output 名は型ごとの SHA-256 digest に置き換え (比較結果は再生でも同じ)、第三者の文字列を長さに関係なく残さない。解釈できない metadata は本文を残さず 1 行の印に置き換え、再生でも同じ「解釈できない」WARN になる。検査そのものには取得した全文を渡す
- `pull_request` イベントではワークフロー定義そのものが PR の revision から読まれる。Dependabot が `astral-sh/setup-uv` や `actions/checkout` を上げる PR では、`vet` の検査より前に提案された Action が runner 上で実行される。`pull_request_target` は使わない方針 (#683) のため PR 内では防げない。ただし同じ PR の `test` / `lint` / `actionlint` も同じ提案版を実行するので、`vet` が露出を増やしてはいない。根本対策 (main 側の定義で `--all-open` を schedule / workflow_dispatch 実行する、または Action を使わず checksum 固定で uv を入れる) は #765 で扱う
