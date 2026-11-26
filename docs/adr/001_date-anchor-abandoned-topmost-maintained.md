---
id: "001"
status: Implemented
date: "2026-11-24"
---
# ADR 001: date anchor の微調整断念と topmost 基準の維持・強化

## Context

医療費控除用領収書 OCR アプリでは、座標逆引きモデルでラベル・値の抽出を行う。
座標の基準は既定で **topmost/leftmost**（全 box の最小 x/y を 0）だが、抽出精度向上を
目的として **date anchor**（`date` を基準に全 box を再正規化する方式）を導入した
（Issue #39, #42, #50, #51 で拡張）。

しかし、その結果は当初の期待を満たさなかった:

- **精度が上がらない**: 手動切替（Issue #42）と `--force` による強制適用（Issue #50）を
  試しても、実際の検証用レシートで抽出精度は向上しない。`coord_basis==date` へは手動
  切替でなければ移行しない状態が続く。
- **作りが複雑化**: topmost/date の二基準・date anchor の解決条件・`coord_basis`
  ガード・強制補正などが絡み合い、結果算出までの経緯の説明と理解が困難になった
  （`.github/reports/pr-52-review.md` でレビュー側の認識のズレも散見）。
- **見立て違い**: 「一回の OCR 読み込みで `date` の座標値が容易に取れる」という前提が
  崩れた。本来の意図は「キー要素として date の座標値を追加する」であったが、いつの
  間にか「抽出精度の向上」に目的が入れ替わっていた。
- **`date anchor 後の ocr_json` が見当たらない**: コード上は anchor 解決成功時に
  `normalize_coordinates_by_anchor()` が `output_json/*-raw_data.json` を date 基準へ
  上書きし、続けて `update_receipt_ocr_json_by_source()`（db.py）が `receipts.ocr_json`
  も date 基準へ更新する（raw ファイルと DB の両方に永続化される実装。単に
  `output_json` のみではない）。ただし実データでは anchor 解決が成功せず
  （date 候補 0 件・曖昧）、この経路が発火しないため、DB の ocr_json が date 基準に
  なっているのを確認できない。「見当たらない」のは永続化の欠落ではなく、
  **発火条件（anchor 解決成功）が実データで満たされない**ことが原因と考える。
- **現状の Bug**: `coord_basis==date` 後、Web フォーム修正で `templates.coords_corrections`
  が書き換わらなくなる（Issue #51 関連）。

## Decision

date anchor の微調整を繰り返すことは**断念**する。今後は topmost 基準を**維持・強化**し、
date anchor 関連の実装は新機能を実装する都度、徐々に書き換えていく。具体的には:

1. **topmost 基準を正とする**: 座標の基準は topmost/leftmost を維持する。
   date anchor は断念するが、**動作に支障が出ないよう急なリファクタはしない**。
   既存の date anchor コードは段階的に廃止・再構築する。
2. **現状の Bug には直接対応しない**: `coord_basis` は**topmost の補正値を維持**する
   フォールバックで対応する（date 基準へ戻す修正は施さない）。
3. **上記を踏まえた topmost 強化案の検討**（新機能として、実装時に判断）:
   - 案i: フォーム修正で DB に書き込む座標値を、`template_history` の最も新しい
     `coords_corrections` との中間値（平均）とする — 基準座標が大きく狂わないように
     する予防策。
   - 案ii: 補正対象として処理し、抽出の失敗（`""` / `null` がある）があった場合、
     前の値を採用する。2 段構成: ①参照を `templates` → `template_history`、
     ②structured data のチェックで `template_history` 参照の判断。
   - ※ `template_history` の参照判断基準は未決定（Issue 起票時に決める）であり、
     「実装してみないとわからない」範囲は本 ADR では断定しない。
4. **Issue #53 案B（今は `ANCHOR_UNRESOLVED` のみ発火する強制 date 補正）は date anchor には採用しない**。期待する機能（案B の topmost 側再補正）で応用できるか検討する。

## Consequences

現ソース（`app/date_anchor.py`, `app/coord_normalizer.py`, `app/db.py`,
`app/services/ocr_coordinate_service.py` 等）の実装状況を踏まえた、現時点で確定できる
影響:

- **既存機能・テストは維持される**: date anchor は `apply_date_anchor_normalization()`
  （戻り値 `(bool, detail)`）、`apply_date_basis_force()`（`ANCHOR_UNRESOLVED` のみ
  発火・既存の非空値は上書きしない）、`coord_basis` 列の topmost ガード、手動切替は
  残存する。急なリファクタを行わないため、動作・テストは現状通り green を保つ。
- **`coord_basis` はスキーマに残る**: `templates.coord_basis`（NOT NULL DEFAULT
  'topmost'）と `template_history` の収容は現状のまま。date → topmost の逆変換は
  一意でないため行わない（既存ドキュメント記載の理由に整合）。
- **前方テンプレート補正は topmost を維持**: `_apply_template_corrections()` の
  `coord_basis != 'date'` ガード（topmost raw と date template の基底不一致防止）は
  残り、モデル自体は topmost 基準で回る。
- **精度向上の恩恵は断たれる**: date anchor で得られたかもしれない抽出精度の改善は
  放棄する。将来の topmost 強化案（案i / 案ii）によって compensate する。案i/案iiの
  実際の効果は未実装のため、**この ADR では予測しない**。
- **複雑性の削減は緩やかに**: date anchor 関連の理解が困難という問題は、段階的な
  書き換えの中で解消が期待されるが、即時には解消しない（急なリファクタをしない方針の
  トレードオフ）。
- **Bug（#51 の coords_corrections 非書換）**: 直接修正しない代わりに、topmost の
  補正値を維持するフォールバックで運用上の安全をとる。根本解消は後続の書き換え時に
  判断する。

## Alternatives Considered

- **date anchor を継続して微調整する**: 断念した理由は、精度が上がらない上に、
  二基準・`coord_basis`・強制補正の絡み合いが複雑度を増し、レビュー含む関係者の理解
  が追いつかないため。→ 不採用。
- **date anchor を即座に削除（急なリファクタ）**: 動作に支障が出る恐れ（現状の
  Bug 対応・既存テストとの整合）と、将来機能への再利用可能性を考慮し、段階的書き換え
  を選んだ。→ 不採用（段階的に廃止）。
- **Issue #53 案Bを date anchor に適用する**: `ANCHOR_UNRESOLVED` のみ発火する強制
  date 補正をさらに拡張する案だが、date anchor 自体を断念したため、対象を topmost 側
  （案Bの再補正）へ転用する形に変更した。→ date anchor には不採用。