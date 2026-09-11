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
        setLoans((data as any) ?? [])
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
        <p className="mt-1 text-sm text-gray-500">{guestName} さんの履歴</p>
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
            <li
              key={loan.id}
              className="border border-gray-200 rounded-lg p-4 shadow-sm"
            >
              <p className="text-base font-semibold text-gray-900">
                {loan.books?.title ?? '(不明な本)'}
              </p>
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
            </li>
          ))}
        </ul>
      )}
    </main>
  )
}