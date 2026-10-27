# データ PR の認証 (GitHub App への切り替え)

自動データ PR (日次・週次・手動の取得、`process-data.yml`、`migrate-metadata.yml`) の push と PR 作成に使うトークンの選び方と、GitHub App へ切り替える所有者向けの手順です (issue #763)。既定では何も変わりません。

## 1. なぜ切り替えるか

ワークフローの `GITHUB_TOKEN` で作成・更新した PR では、`pull_request` イベントのワークフロー (`test.yml` など) が承認待ちの状態で作られ、書き込み権限を持つ人が承認するまで実行されません。承認なしで実行させるには、`GITHUB_TOKEN` ではなく GitHub App のインストールアクセストークンか PAT で PR を作ります ([GITHUB_TOKEN の公式説明](https://docs.github.com/en/actions/concepts/security/github_token)、[2026-06-11 の変更](https://github.blog/changelog/2026-06-11-bot-created-pull-requests-can-run-workflows-if-approved/))。このリポジトリでは PAT を使わず、専用の GitHub App に切り替えられるようにしています。

## 2. CI が起動することと、マージが CI を待つことは別

App に切り替えて解消するのは「データ PR の CI が承認待ちで止まる」ことだけです。

- 自動マージ (`gh pr merge --auto --squash`) は、main のルールセットに必須ステータスチェックが無いため、CI の完了を待たずにマージします。App に切り替えてもこれは変わりません。
- マージ前の品質保証は、これまでどおりワークフロー内のゲート (取得・処理・処理カバレッジ・メタデータ schema・連続性・データ検証。`scripts/auto_merge_gate.sh`) が担います。
- CI の成功をマージ条件にしたい場合は、必須ステータスチェック (ruleset) を所有者が別途設計・設定してください。この手順書の対象外です。

## 3. 仕組み

| 項目             | 内容                                                                                                                                                                                                                                                        |
| ---------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 切り替え         | repository variable `DATA_PR_USE_APP`。`true` のときだけ App。未設定または `false` は `GITHUB_TOKEN` (既定)                                                                                                                                                 |
| App の資格情報   | variable `DATA_PR_APP_CLIENT_ID` (App の Client ID)、secret `DATA_PR_APP_PRIVATE_KEY` (App の秘密鍵)                                                                                                                                                        |
| 失敗時           | `DATA_PR_USE_APP=true` で資格情報が欠けている・トークン発行に失敗したときは、PR を作る前にジョブを失敗させる。`GITHUB_TOKEN` へ黙って戻さない。`true` / `false` / 未設定以外の値もエラー                                                                    |
| 発行のタイミング | PR を作る直前 (`Create data PR token` ステップ)。dry run と変更なしの run では発行しない。トークンの有効期限は 1 時間なので、長い取得の前には発行しない                                                                                                     |
| 範囲と権限       | このリポジトリだけ。Contents: write と Pull requests: write (ラベルの作成と付与も Pull requests: write で足りる)。Metadata: read は App に必ず付く                                                                                                          |
| 失効             | `actions/create-github-app-token` の post 処理がジョブの終わりに失効させる (`skip-token-revoke` は使わない)                                                                                                                                                 |
| push の認証      | checkout は資格情報を残さない (`persist-credentials: false`)。`scripts/create_pr.sh` は選ばれたトークンを環境変数から読む一時的な credential helper だけで push し、`gh` にも同じトークンを渡す。トークンはコマンド引数・remote URL・`.git/config` に出ない |

実装は `.github/actions/data-pr-token/action.yml` と、それを呼ぶ 3 つのワークフロー、`scripts/create_pr.sh` です。

```mermaid
flowchart TD
    Change{PR が必要?<br/>変更あり・dry run でない} -->|No| Skip[トークンを発行しない]
    Change -->|Yes| Flag{DATA_PR_USE_APP}
    Flag -->|未設定 / false| GT[GITHUB_TOKEN<br/>CI は承認待ち]
    Flag -->|true| Creds{Client ID と秘密鍵がある?}
    Flag -->|それ以外| Fail[ジョブを失敗させる]
    Creds -->|No| Fail
    Creds -->|Yes| Mint[App トークンを発行<br/>このリポジトリだけ・1 時間]
    Mint -->|発行失敗| Fail
    Mint --> PR[push と PR 作成<br/>CI は承認なしで起動]
    GT --> PR0[push と PR 作成]
```

## 4. 導入手順 (所有者)

エージェントはこの手順を実行しません。App の作成・インストール、secret / variable の登録、ワークフローの dispatch は所有者が行います。コマンドは zsh で、リポジトリは `--repo` で明示します。

### 4.1 App を作る

GitHub の Settings → Developer settings → GitHub Apps → New GitHub App で作ります。

- GitHub App name: 任意 (例: `<owner>-data-pr`)。Homepage URL: このリポジトリの URL
- Webhook: Active のチェックを外す
- Repository permissions: Contents を Read and write、Pull requests を Read and write。ほかは No access のまま (Metadata は Read-only が自動で付く)
- Where can this GitHub App be installed?: Only on this account

作成後、App の設定画面で Client ID を控え、Private keys の Generate a private key で秘密鍵 (`.pem`) をダウンロードします。

### 4.2 このリポジトリだけにインストールする

App の設定画面の Install App から、Only select repositories でこのリポジトリだけを選んでインストールします。All repositories は選びません。

### 4.3 資格情報を登録する

Client ID は秘密ではないので variable に入れます。

```zsh
gh variable set DATA_PR_APP_CLIENT_ID --repo kambarakun/fetch-tokyo-idsc-github-actions --body '<Client ID>'
```

秘密鍵はファイルから標準入力で渡します (`--body` やコマンド引数に鍵を書かない)。先に、ファイルが鍵であることを 1 行目だけで確かめます。

```zsh
KEY_FILE="$HOME/Downloads/<ダウンロードした秘密鍵>.pem"
head -n 1 "$KEY_FILE"
```

`-----BEGIN RSA PRIVATE KEY-----` と表示されたら登録します。

```zsh
gh secret set DATA_PR_APP_PRIVATE_KEY --repo kambarakun/fetch-tokyo-idsc-github-actions < "$KEY_FILE"
```

登録後、ダウンロードした `.pem` はパスワードマネージャーなどに移すか削除します。登録を確かめます (secret の値は表示されません)。

```zsh
gh variable list --repo kambarakun/fetch-tokyo-idsc-github-actions
gh secret list --repo kambarakun/fetch-tokyo-idsc-github-actions
```

### 4.4 有効にする

有効にする前に、次の 2 つを確かめます。

1. 7 章の「上流確認」。新しい版の提案は Dependabot が出しますが、いま pin している版に後から出た advisory を知らせる仕組みはありません (7 章)。
2. Claude コードレビューの使用量。`claude-code-review.yml` は Dependabot 以外の PR の作成・更新 (`opened` / `synchronize`) ごとに走ります。`GITHUB_TOKEN` で作ったデータ PR では承認待ちのまま走りませんが、App に切り替えるとデータ PR ごとに走り、`CLAUDE_CODE_OAUTH_TOKEN` のアカウントの使用量を消費します。直近 30 日のデータ PR の件数で、増える回数の見当を付けます (`<30 日前の日付>` は `2026-09-27` の形)。

   ```zsh
   gh pr list --repo kambarakun/fetch-tokyo-idsc-github-actions --state all --limit 1000 --search 'created:>=<30 日前の日付>' --json headRefName --jq '[.[] | select(.headRefName | test("^(data-update|data-process|metadata-migration)-"))] | length'
   ```

   その件数分のレビューが増えても使用量の上限に収まるかを確かめます。収まらない場合は、有効化の前に、データ PR をレビュー対象から外すかどうかを別途決めます (ワークフローの変更は別の PR で行う)。

```zsh
gh variable set DATA_PR_USE_APP --repo kambarakun/fetch-tokyo-idsc-github-actions --body true
```

### 4.5 初回の確認

有効化後に最初にできたデータ PR で確かめます (定期実行を待つか、所有者が取得ワークフローを dispatch する)。

1. 作成元の run の `Create data PR token` ステップに `Data PR token: GitHub App installation token` と、対象がこのリポジトリだけであることを示すログ (`create token for the following repositories:` の下にこのリポジトリ 1 件) が出ている。
2. PR の作成者が App の bot (`<App のスラッグ>[bot]`) になっている。
3. PR のブランチで `pull_request` の run が承認待ち (`action_required`) にならずに始まっている。

   ```zsh
   gh run list --repo kambarakun/fetch-tokyo-idsc-github-actions --branch '<PR のブランチ名>' --json workflowName,event,status,conclusion
   ```

4. 作成元の run の post 処理 (`Post Create data PR token`) に `Token revoked` が出ている。
5. 取得・処理・カバレッジ・schema のゲートの結果が PR 本文の「自動マージゲート」に従来どおり出ている。

## 5. 失敗したとき

| 症状                                                                                                  | 原因と対処                                                                                                                                     |
| ----------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| `Invalid DATA_PR_USE_APP`                                                                             | variable が `true` / `false` / 未設定以外。`true` か `false` にする (大文字や前後の空白も不可)                                                 |
| `Data PR App not configured`                                                                          | `DATA_PR_USE_APP=true` なのにメッセージにある variable / secret が空。4.3 で登録する                                                           |
| `Create data PR token` が App のエラー (installation が見つからない、権限が足りない、鍵が不正) で失敗 | App がこのリポジトリにインストールされているか、App の権限が Contents / Pull requests の Read and write か、secret が App の現在の鍵か確かめる |
| push が 403 (`Permission ... denied`)                                                                 | App の Contents が Read and write でない、またはインストール先にこのリポジトリが無い                                                           |
| PR 作成が `Resource not accessible by integration`                                                    | App の Pull requests が Read and write でない                                                                                                  |

トークンの選択・発行で失敗した run では、push の前に止まるので、PR もデータのブランチもできません。`scripts/create_pr.sh` は push のあとに PR を作るため、PR 作成で失敗した run では、データのブランチ (`data-update-*` / `data-process-*` / `metadata-migration-*`) が remote に残ります。push で失敗した run でも残ることがあります。上の表の 403 のような拒否なら remote は変わりませんが、remote 側で ref が更新された後に応答だけが途切れる通信断では、`git push` が失敗を返してもブランチは remote にあります (この通信断を実際に起こして確かめてはいません)。そのため、push と PR 作成のどちらで失敗した run でも、ログにある `Creating branch:` のブランチ名で、まずそのブランチの PR を確かめます (確認だけで、何も変更しません)。

```zsh
BRANCH='<ブランチ名>'
gh pr list --repo kambarakun/fetch-tokyo-idsc-github-actions --head "$BRANCH" --state all --json number,state,url
```

ブランチが remote にあるかは次で確かめます (これも確認だけです)。SHA が出ればあり、`Not Found` (HTTP 404) なら残っていません。

```zsh
BRANCH='<ブランチ名>'
gh api "repos/kambarakun/fetch-tokyo-idsc-github-actions/git/ref/heads/$BRANCH" --jq .object.sha
```

`[]` (PR が無い) のときだけ、ブランチを削除します。次のコマンドは、ブランチ名がデータのブランチの形でない場合、PR の照会に失敗した場合、PR が 1 件でもある場合には、何も削除せずに失敗します。

```zsh
BRANCH='<ブランチ名>'
case "$BRANCH" in
  data-update-*|data-process-*|metadata-migration-*) ;;
  *) echo "データのブランチ名ではありません: $BRANCH" >&2; false ;;
esac &&
  COUNT=$(gh pr list --repo kambarakun/fetch-tokyo-idsc-github-actions --head "$BRANCH" --state all --json number --jq length) &&
  { [ "$COUNT" = 0 ] || { echo "PR が ${COUNT} 件あるので削除しません" >&2; false; }; } &&
  gh api -X DELETE "repos/kambarakun/fetch-tokyo-idsc-github-actions/git/refs/heads/$BRANCH"
```

必要ならそのあと取得ワークフローを再実行します。

## 6. 無効化・戻し方

すぐ戻すときは variable を `false` にします。次の run から `GITHUB_TOKEN` に戻り、データ PR の CI は再び承認待ちになります。

```zsh
gh variable set DATA_PR_USE_APP --repo kambarakun/fetch-tokyo-idsc-github-actions --body false
```

App をやめるときは、`false` にしたうえで secret と variable を削除し、App をアンインストールします。

```zsh
gh secret delete DATA_PR_APP_PRIVATE_KEY --repo kambarakun/fetch-tokyo-idsc-github-actions
gh variable delete DATA_PR_APP_CLIENT_ID --repo kambarakun/fetch-tokyo-idsc-github-actions
```

秘密鍵を更新するときは、App の設定画面で新しい鍵を作り、4.3 の `gh secret set` で登録し直してから、古い鍵を App の設定画面で削除します。鍵が漏れた疑いがあるときは、先に古い鍵を削除します。

## 7. 保守

- `actions/create-github-app-token` の SHA pin は `.github/actions/data-pr-token/action.yml` にあります。自動化されている範囲と、手動で残る範囲は次のとおりです。

  | 対象                                     | 担当                                                                                                                                                                                                                                                                                                                                                |
  | ---------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
  | 新しい版の提案                           | 自動。Dependabot の github-actions 設定が `directories` で `/.github/actions/data-pr-token` も走査し (issue #763)、週次で PR を出す (cooldown 7 日、major は ignore)                                                                                                                                                                                |
  | 提案された版の検査                       | 自動。`scripts/vet_dependabot_prs.py` が workflow と同じ検査 (タグと SHA の一致・cooldown・OSV advisory・後続版・Action metadata の差分) をかける ([docs/dependabot-pr-review.md](dependabot-pr-review.md))。マージは人間                                                                                                                           |
  | いま pin している版に後から出た advisory | **手動**。vet は PR が来たときに提案版だけを見る。dependency graph にもこの pin は載っていない (2026-10-27 の SBOM で確認。workflow の Action だけが載る) ので、Dependabot alerts も当てにできない。watchdog の検査 4 は `WATCHED_ACTION_DEPENDENCIES` に書いた Action の同梱依存だけを見る ([docs/dependency-pipeline.md](dependency-pipeline.md)) |

- **上流確認** (手動): `DATA_PR_USE_APP=true` にする前に 1 回、有効化後は月 1 回 (月初の月曜の Dependabot 確認と同時) 行います。版コメントの版 (`# vX.Y.Z`) を `<版>`、pin の SHA を `<SHA>` として読みます。
  1. リリース一覧で、pin の版より新しいリリースや、修正リリースが出ていないか。

     ```zsh
     gh api repos/actions/create-github-app-token/releases --jq '.[0:5][] | [.tag_name, .published_at] | @tsv'
     ```

  2. pin の SHA がタグの解決値と一致するか。`<SHA>` と同じ値が出ることを確かめます。

     ```zsh
     gh api 'repos/actions/create-github-app-token/git/ref/tags/<版>' --jq .object.sha
     ```

  3. 公開 advisory が無いか。

     ```zsh
     gh api repos/actions/create-github-app-token/security-advisories --jq length
     gh api '/advisories?affects=actions/create-github-app-token&ecosystem=actions' --jq length
     ```

     どちらも `0` であることを確かめます。`0` でなければ advisory の影響範囲に `<版>` が入るかを照合し、入るなら修正版への更新を急ぎます (Dependabot の PR が cooldown 中なら、その PR の根拠に advisory を書いて前倒しする)。

  4. 未解決の不具合報告が無いか ([issues](https://github.com/actions/create-github-app-token/issues))。

- 更新は Dependabot の PR で受け取ります (手で差し替えるときも、SHA と同じ行の版コメントを一緒に変える)。`tests/test_data_pr_token.py` は特定の SHA を持たず、公式 Action を 40 桁の SHA と `# vX.Y.Z` の版コメントで pin していることだけを確かめるので、更新 PR でテストを直す必要はありません。

- コミットの author / committer は従来どおり `github-actions[bot]` (各ワークフローの Configure git) です。App が行うのは push と PR 操作です。
- 失敗通知の issue (`Notify pipeline anomaly`) は引き続き `GITHUB_TOKEN` で作ります。
