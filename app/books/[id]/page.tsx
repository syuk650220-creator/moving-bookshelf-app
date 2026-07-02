'use client'
import { use, useEffect, useState } from 'react'
import { supabase } from '@/lib/supabaseClient'

type Book = {
  id: string
  title: string
  author: string
  shelf_level: number
  status: string
}

type Loan = {
  id: string
  book_id: string
  borrower_type: string
  guest_name: string | null
  borrowed_at: string
  returned_at: string | null
  returned_by: string | null
}

export default function BookDetailPage({
  params,
}: {
  params: Promise<{ id: string }>
}) {
  const { id } = use(params)
  const [book, setBook] = useState<Book | null>(null)
  const [loans, setLoans] = useState<Loan[]>([])
  const [errorMsg, setErrorMsg] = useState<string | null>(null)

  async function fetchData() {
    // 本を1件取得
    const { data: bookData, error: bookError } = await supabase
      .from('books')
      .select('*')
      .eq('id', id)
      .single()

    if (bookError) {
      setErrorMsg('本の取得に失敗しました')
      return
    }
    setBook(bookData)

    // この本の貸出履歴を取得
    const { data: loanData, error: loanError } = await supabase
      .from('loans')
      .select('*')
      .eq('book_id', id)
      .order('borrowed_at', { ascending: false })

    if (loanError) {
      setErrorMsg('履歴の取得に失敗しました')
      return
    }
    setLoans(loanData ?? [])
  }

  useEffect(() => {
    fetchData()
  }, [id])

if (errorMsg) {
    return <p className="p-6 text-red-500">{errorMsg}</p>
  }

  if (!book) {
    return <p className="p-6 text-gray-500">読み込み中...</p>
  }

  return (
    <main className="p-6">
      {errorMsg && (
  <p className="mb-4 text-red-500">{errorMsg}</p>
)}
      <h1 className="text-2xl font-bold">{book.title}</h1>
      <p className="mt-1 text-gray-600">{book.author}</p>
      <p className="mt-1">{book.shelf_level}段目</p>
      <p className={`mt-1 font-bold ${book.status === 'available' ? 'text-green-600' : 'text-orange-600'}`}>
        {book.status === 'available' ? '在庫あり' : '貸出中'}
      </p>

      {/* 借りる・返すボタン（次のステップで実装） */}
      <div className="mt-4 flex gap-4">
        <button
  className="bg-blue-500 text-white px-4 py-2 rounded"
  onClick={async () => {
    try {
      // ① loansに1行insert
      const { error: insertError } = await supabase
        .from('loans')
        .insert({
          book_id: id,
          borrower_type: 'guest',
          guest_name: '仮の利用者',
        })

      if (insertError) {
        setErrorMsg('借りる処理に失敗しました')
        return
      }

      // ② booksのstatusをon_loanに更新
      const { error: updateError } = await supabase
        .from('books')
        .update({ status: 'on_loan' })
        .eq('id', id)

      if (updateError) {
        setErrorMsg('状態の更新に失敗しました')
        return
      }

      // ③ 画面を最新化
      await fetchData()

    } catch (e) {
      setErrorMsg('予期せぬエラーが発生しました')
    }
  }}
>
  借りる
</button>
        <button
  className="bg-gray-500 text-white px-4 py-2 rounded"
  onClick={async () => {
    try {
      // ① 未返却のloanを1件取得
      const { data: loanData, error: selectError } = await supabase
        .from('loans')
        .select('*')
        .eq('book_id', id)
        .is('returned_at', null)
        .order('borrowed_at', { ascending: false })
        .limit(1)
        .single()

      if (selectError) {
        setErrorMsg('返却対象が見つかりませんでした')
        return
      }

      // ② 取得したloanのreturned_atとreturned_byを更新
      const { error: updateLoanError } = await supabase
        .from('loans')
        .update({
          returned_at: new Date().toISOString(),
          returned_by: loanData.guest_name ?? 'guest',
        })
        .eq('id', loanData.id)

      if (updateLoanError) {
        setErrorMsg('返却処理に失敗しました')
        return
      }

      // ③ booksのstatusをavailableに戻す
      const { error: updateBookError } = await supabase
        .from('books')
        .update({ status: 'available' })
        .eq('id', id)

      if (updateBookError) {
        setErrorMsg('状態の更新に失敗しました')
        return
      }

      // ④ 画面を最新化
      await fetchData()

    } catch (e) {
      setErrorMsg('予期せぬエラーが発生しました')
    }
  }}
>
  返す
</button>
      </div>

      {/* 貸出履歴 */}
      <h2 className="mt-6 text-xl font-bold">貸出履歴</h2>
      {loans.length === 0 ? (
        <p className="mt-2 text-gray-500">履歴なし</p>
      ) : (
        <ul className="mt-2 space-y-2">
          {loans.map((loan) => (
            <li key={loan.id} className="border p-3 rounded">
              <p>借りた人: {loan.guest_name ?? '不明'}</p>
              <p>借りた日: {loan.borrowed_at}</p>
              <p>返却日: {loan.returned_at ?? '未返却'}</p>
            </li>
          ))}
        </ul>
      )}
    </main>
  )
}