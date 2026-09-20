// 貸出の記録を1か所にまとめるモジュール
// 「直接借りる」（S-2 本の詳細）と、ロボが届けたあとの「本を取得した」（S-4 ロボを呼ぶ）の両方から使う
import { supabase } from '@/lib/supabaseClient'

export type BorrowResult =
  | { ok: true }
  | { ok: false; reason: 'not_available' | 'error'; message: string }

// 本を借りる: books を on_loan にして、loans に1行足す
//
// 先に books を「在庫あり のときだけ」貸出中へ更新する（＝早い者勝ちの確保）。
// 同時に2人が押しても、更新できるのは1人だけなので二重貸出にならない。
// loans は削除できない設計（delete のポリシーが無い）なので、失敗時の取り消しは
// 「books を在庫ありに戻す」側で行う。
export async function borrowBook(bookId: string, guestName: string): Promise<BorrowResult> {
  // ① 在庫ありのときだけ貸出中にする
  const { data: claimed, error: claimError } = await supabase
    .from('books')
    .update({ status: 'on_loan' })
    .eq('id', bookId)
    .eq('status', 'available')
    .select('id')

  if (claimError) {
    return { ok: false, reason: 'error', message: '状態の更新に失敗しました' }
  }
  if (!claimed || claimed.length === 0) {
    return { ok: false, reason: 'not_available', message: 'この本はすでに貸出中です' }
  }

  // ② loans に1行 insert
  const { error: insertError } = await supabase.from('loans').insert({
    book_id: bookId,
    borrower_type: 'guest',
    guest_name: guestName,
  })

  if (insertError) {
    // loans に書けなかったら、①で貸出中にした books を元に戻す（ロールバック）
    await supabase.from('books').update({ status: 'available' }).eq('id', bookId)
    return { ok: false, reason: 'error', message: '借りる処理に失敗しました' }
  }

  return { ok: true }
}
