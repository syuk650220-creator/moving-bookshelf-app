'use client'

import { use, useEffect, useState } from 'react'
import Link from 'next/link'
import { supabase } from '@/lib/supabaseClient'

type BookForm = {
  isbn: string
  title: string
  author: string
  shelfLevel: number | ''
}

export default function AdminBookEditPage({
  params,
}: {
  params: Promise<{ id: string }>
}) {
  const { id } = use(params)
  const [form, setForm] = useState<BookForm>({
    isbn: '',
    title: '',
    author: '',
    shelfLevel: '',
  })
  const [isLoading, setIsLoading] = useState(true)
  const [isSaving, setIsSaving] = useState(false)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const [successMessage, setSuccessMessage] = useState<string | null>(null)

  useEffect(() => {
    let isMounted = true

    async function fetchBook() {
      const { data, error } = await supabase
        .from('books')
        .select('isbn, title, author, shelf_level')
        .eq('id', id)
        .single()

      if (!isMounted) return

      if (error) {
        setErrorMessage(`本の取得に失敗しました: ${error.message}`)
      } else {
        setForm({
          isbn: data.isbn ?? '',
          title: data.title,
          author: data.author ?? '',
          shelfLevel: data.shelf_level,
        })
      }
      setIsLoading(false)
    }

    fetchBook()
    return () => {
      isMounted = false
    }
  }, [id])

  function updateField<K extends keyof BookForm>(field: K, value: BookForm[K]) {
    setForm((currentForm) => ({ ...currentForm, [field]: value }))
    setErrorMessage(null)
    setSuccessMessage(null)
  }

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setErrorMessage(null)
    setSuccessMessage(null)

    const cleanIsbn = form.isbn.replace(/[０-９]/g, (character) =>
      String.fromCharCode(character.charCodeAt(0) - 0xfee0),
    ).replace(/\s+/g, '')
    const title = form.title.trim()
    const author = form.author.trim()

    if (!title) {
      setErrorMessage('タイトルを入力してください。')
      return
    }
    if (cleanIsbn && !/^\d{10}$|^\d{13}$/.test(cleanIsbn)) {
      setErrorMessage('ISBNは10桁または13桁の数字で入力してください。')
      return
    }
    if (
      form.shelfLevel === '' ||
      !Number.isInteger(Number(form.shelfLevel)) ||
      Number(form.shelfLevel) < 1 ||
      Number(form.shelfLevel) > 5
    ) {
      setErrorMessage('本棚の段数は1〜5の整数で入力してください。')
      return
    }

    setIsSaving(true)
    const { error } = await supabase
      .from('books')
      .update({
        isbn: cleanIsbn || null,
        title,
        author: author || null,
        shelf_level: Number(form.shelfLevel),
      })
      .eq('id', id)

    if (error) {
      setErrorMessage(`本の更新に失敗しました: ${error.message}`)
    } else {
      setForm((currentForm) => ({ ...currentForm, isbn: cleanIsbn, title, author }))
      setSuccessMessage('本の情報を更新しました。')
    }
    setIsSaving(false)
  }

  return (
    <main className="mx-auto max-w-xl p-6">
      <div className="flex items-center justify-between gap-3">
        <div>
          <p className="text-sm text-gray-500">管理者メニュー</p>
          <h1 className="text-2xl font-bold text-gray-900">本の情報を編集</h1>
        </div>
        <Link href="/admin/books" className="text-sm text-blue-600 underline">
          本の管理へ戻る
        </Link>
      </div>

      <p className="mt-3 rounded-md border border-blue-200 bg-blue-50 p-3 text-sm text-blue-800">
        タイトル、著者、ISBN、本棚の段数を変更できます。貸出状態は貸出・返却処理で管理されるため変更しません。
      </p>

      {isLoading ? (
        <p className="mt-6 text-gray-500">本の情報を読み込んでいます...</p>
      ) : errorMessage && !form.title ? (
        <p role="alert" className="mt-6 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">
          {errorMessage}
        </p>
      ) : (
        <form onSubmit={handleSubmit} className="mt-6 space-y-5 rounded-xl border border-gray-200 bg-white p-6 shadow-sm">
          <div>
            <label htmlFor="isbn" className="mb-1 block text-sm font-medium text-gray-700">
              ISBN
            </label>
            <input
              id="isbn"
              type="text"
              value={form.isbn}
              onChange={(event) => updateField('isbn', event.target.value)}
              className="w-full rounded-md border px-4 py-2 font-mono text-gray-900 focus:ring-2 focus:ring-blue-500"
            />
          </div>

          <div>
            <label htmlFor="title" className="mb-1 block text-sm font-medium text-gray-700">
              タイトル
            </label>
            <input
              id="title"
              type="text"
              required
              value={form.title}
              onChange={(event) => updateField('title', event.target.value)}
              className="w-full rounded-md border px-4 py-2 text-gray-900 focus:ring-2 focus:ring-blue-500"
            />
          </div>

          <div>
            <label htmlFor="author" className="mb-1 block text-sm font-medium text-gray-700">
              著者
            </label>
            <input
              id="author"
              type="text"
              value={form.author}
              onChange={(event) => updateField('author', event.target.value)}
              className="w-full rounded-md border px-4 py-2 text-gray-900 focus:ring-2 focus:ring-blue-500"
            />
          </div>

          <div>
            <label htmlFor="shelf-level" className="mb-1 block text-sm font-medium text-gray-700">
              本棚の段数（1〜5）
            </label>
            <input
              id="shelf-level"
              type="number"
              min="1"
              max="5"
              required
              value={form.shelfLevel}
              onChange={(event) =>
                updateField('shelfLevel', event.target.value === '' ? '' : Number(event.target.value))
              }
              className="w-full rounded-md border px-4 py-2 text-gray-900 focus:ring-2 focus:ring-blue-500"
            />
          </div>

          {errorMessage && (
            <p role="alert" className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">
              {errorMessage}
            </p>
          )}
          {successMessage && (
            <p role="status" className="rounded-md border border-green-200 bg-green-50 p-3 text-sm text-green-700">
              {successMessage}
            </p>
          )}

          <button
            type="submit"
            disabled={isSaving}
            className="w-full rounded-md bg-blue-600 px-4 py-3 font-bold text-white transition-colors hover:bg-blue-700 disabled:cursor-wait disabled:bg-gray-400"
          >
            {isSaving ? '保存中...' : '変更を保存する'}
          </button>
        </form>
      )}
    </main>
  )
}