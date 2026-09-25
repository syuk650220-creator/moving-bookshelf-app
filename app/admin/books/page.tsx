'use client'

import { useCallback, useEffect, useState } from 'react'
import Link from 'next/link'
import { supabase } from '@/lib/supabaseClient'

type Book = {
  id: string
  isbn: string | null
  title: string
  author: string | null
  shelf_level: number
  status: 'available' | 'on_loan'
}

export default function AdminBooksPage() {
  const [books, setBooks] = useState<Book[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const [successMessage, setSuccessMessage] = useState<string | null>(null)

  const fetchBooks = useCallback(async () => {
    setIsLoading(true)
    setErrorMessage(null)

    const { data, error } = await supabase
      .from('books')
      .select('id, isbn, title, author, shelf_level, status')
      .order('title')

    if (error) {
      setErrorMessage(`本の取得に失敗しました: ${error.message}`)
    } else {
      setBooks((data ?? []) as Book[])
    }
    setIsLoading(false)
  }, [])

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- 初回データ取得後に状態を更新する
    fetchBooks()
  }, [fetchBooks])

  async function handleDelete(book: Book) {
    if (deletingId) return

    const confirmed = window.confirm(
      `「${book.title}」を削除しますか？\nこの操作は取り消せません。`,
    )
    if (!confirmed) return

    setDeletingId(book.id)
    setErrorMessage(null)
    setSuccessMessage(null)

    const { error } = await supabase.from('books').delete().eq('id', book.id)

    if (error) {
      setErrorMessage(
        `「${book.title}」を削除できませんでした。貸出履歴や呼出履歴がある本は削除できません。(${error.message})`,
      )
    } else {
      setBooks((currentBooks) => currentBooks.filter((currentBook) => currentBook.id !== book.id))
      setSuccessMessage(`「${book.title}」を削除しました。`)
    }
    setDeletingId(null)
  }

  return (
    <main className="mx-auto max-w-3xl p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="text-sm text-gray-500">管理者メニュー</p>
          <h1 className="text-2xl font-bold text-gray-900">登録した本の管理</h1>
        </div>
        <div className="flex gap-3 text-sm">
          <Link href="/admin" className="text-blue-600 underline">
            管理者画面へ
          </Link>
          <Link href="/books" className="text-blue-600 underline">
            本一覧へ
          </Link>
        </div>
      </div>

      <p className="mt-3 rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">
        削除した本は元に戻せません。貸出履歴やロボット呼出履歴がある本は、履歴を守るため削除できません。
      </p>

      {errorMessage && (
        <p role="alert" className="mt-4 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">
          {errorMessage}
        </p>
      )}
      {successMessage && (
        <p role="status" className="mt-4 rounded-md border border-green-200 bg-green-50 p-3 text-sm text-green-700">
          {successMessage}
        </p>
      )}

      {isLoading ? (
        <p className="mt-6 text-gray-500">本を読み込んでいます...</p>
      ) : books.length === 0 ? (
        <p className="mt-6 rounded-md border border-gray-200 bg-white p-6 text-center text-gray-500">
          登録されている本はありません。
        </p>
      ) : (
        <ul className="mt-6 space-y-3">
          {books.map((book) => {
            const isDeleting = deletingId === book.id
            return (
              <li
                key={book.id}
                className="flex flex-col gap-4 rounded-xl border border-gray-200 bg-white p-4 shadow-sm sm:flex-row sm:items-center sm:justify-between"
              >
                <div className="min-w-0">
                  <h2 className="truncate font-bold text-gray-900">{book.title}</h2>
                  <p className="mt-1 text-sm text-gray-600">{book.author || '著者不明'}</p>
                  <p className="mt-1 text-xs text-gray-500">
                    ISBN: {book.isbn || '未登録'} ・ {book.shelf_level}段目 ・{' '}
                    {book.status === 'available' ? '在庫あり' : '貸出中'}
                  </p>
                </div>
                <div className="flex shrink-0 gap-2">
                  <Link
                    href={`/admin/books/${book.id}`}
                    className="rounded-md border border-blue-600 px-4 py-2 text-sm font-bold text-blue-600 transition-colors hover:bg-blue-50"
                  >
                    編集
                  </Link>
                  <button
                    type="button"
                    onClick={() => handleDelete(book)}
                    disabled={deletingId !== null}
                    className="rounded-md bg-red-600 px-4 py-2 text-sm font-bold text-white transition-colors hover:bg-red-700 disabled:cursor-not-allowed disabled:bg-gray-400"
                  >
                    {isDeleting ? '削除中...' : '削除'}
                  </button>
                </div>
              </li>
            )
          })}
        </ul>
      )}
    </main>
  )
}