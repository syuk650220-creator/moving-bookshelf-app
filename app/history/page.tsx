'use client'
import { useEffect, useState } from 'react'
import { supabase } from '@/lib/supabaseClient'
import { getGuestName } from '@/lib/guestName'
import Link from 'next/link'

type LoanWithBook = {
  id: string
  book_id: string
  guest_name: string | null
  borrowed_at: string
  returned_at: string | null
  books: {
    title: string
  } | null
}

export default function History() {
  const [loans, setLoans] = useState<LoanWithBook[]>([])
  const [errorMsg, setErrorMsg] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [guestName, setGuestNameState] = useState<string | null>(null)

  useEffect(() => {
    const name = getGuestName()
    // eslint-disable-next-line react-hooks/set-state-in-effect -- 外部ストア(localStorage)からの初期値読み込みのため
    setGuestNameState(name)

    async function fetchLoans() {
      if (!name) {
        setLoading(false)
        return
      }

      const { data, error } = await supabase
        .from('loans')
        .select('id, book_id, guest_name, borrowed_at, returned_at, books(title)')
        .eq('guest_name', name)
        .order('borrowed_at', { ascending: false })

      if (error) {
        setErrorMsg('履歴の取得に失敗しました')
      } else {
        setLoans((data as unknown as LoanWithBook[]) ?? [])
      }
      setLoading(false)
    }

    fetchLoans()
  }, [])

  return (
    <main className="p-6 max-w-2xl mx-auto">
      <Link href="/books" className="text-sm text-blue-600 underline">
        ← 一覧に戻る
      </Link>

      <h1 className="mt-3 text-xl font-bold text-gray-900">貸出履歴</h1>
      {guestName && (
        <p className="mt-1 text-sm text-gray-500">
          {guestName} さんの履歴。本を押すと、その本の画面（返却はここから）が開きます。
        </p>
      )}

      {!guestName && (
        <p className="mt-6 text-sm text-gray-600">
          履歴を見るには
          <Link href="/login" className="text-blue-600 underline mx-1">
            名前を入力
          </Link>
          してください。
        </p>
      )}

      {errorMsg && (
        <p className="mt-6 text-sm text-red-600">{errorMsg}</p>
      )}

      {guestName && loading && (
        <p className="mt-6 text-sm text-gray-500">読み込み中...</p>
      )}

      {guestName && !loading && !errorMsg && loans.length === 0 && (
        <p className="mt-6 text-sm text-gray-500">貸出履歴がありません</p>
      )}

      {guestName && !loading && !errorMsg && loans.length > 0 && (
        <ul className="mt-6 space-y-3">
          {loans.map((loan) => (
            <li key={loan.id}>
              {/* 本を押すと、その本の画面へ（未返却なら「返す」ボタンがある画面） */}
              <Link
                href={`/books/${loan.book_id}`}
                className="block border border-gray-200 rounded-lg p-4 shadow-sm transition-colors hover:border-blue-400 hover:bg-blue-50"
              >
                <div className="flex items-start justify-between gap-3">
                  <p className="text-base font-semibold text-gray-900">
                    {loan.books?.title ?? '(不明な本)'}
                  </p>
                  <span
                    className={`shrink-0 text-sm font-medium ${
                      loan.returned_at ? 'text-blue-600' : 'text-orange-600'
                    }`}
                  >
                    {loan.returned_at ? '本の画面へ →' : '返却する →'}
                  </span>
                </div>
                <div className="mt-2 space-y-1 text-sm text-gray-600">
                  <p>借りた日: {new Date(loan.borrowed_at).toLocaleString('ja-JP')}</p>
                  <p>
                    返却日:{' '}
                    {loan.returned_at ? (
                      new Date(loan.returned_at).toLocaleString('ja-JP')
                    ) : (
                      <span className="text-orange-600 font-medium">未返却</span>
                    )}
                  </p>
                </div>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </main>
  )
}