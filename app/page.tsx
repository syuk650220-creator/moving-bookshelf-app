'use client'
import { useState, useEffect } from 'react'
import { supabase } from '@/lib/supabaseClient'
import Link from 'next/link'

type Book = {
  id: string
  title: string
  author: string
  shelf_level: number
  status: string
}

export default function Home() {
  const [books, setBooks] = useState<Book[]>([])
const [errorMsg, setErrorMsg] = useState<string | null>(null)
const [loading, setLoading] = useState(true)
const [keyword, setKeyword] = useState('')

useEffect(() => {
  async function fetchBooks() {
    const { data, error } = await supabase.from("books").select("*")
    if (error) {
      setErrorMsg("エラーが発生しました")
    } else {
      setBooks(data ?? [])
    }
    setLoading(false)
  }
  fetchBooks()
}, [])

  if (errorMsg) {
  return <p className="p-6 text-red-500">{errorMsg}</p>;
}

if (loading) {
  return <p className="p-6 text-gray-500">読み込み中...</p>;
}

if (books.length === 0) {
  return <p className="p-6 text-gray-500">本が登録されていません</p>;
}

const filteredBooks = books.filter((book) =>
  book.title.includes(keyword) || book.author.includes(keyword)
)

  return (
    <main className="p-6">
      <h1 className="text-2xl font-bold">本一覧（ホーム） / S-1</h1>

      <nav className="mt-2 flex gap-4">
        <Link href="/register" className="text-blue-600 underline">
          本を登録する
        </Link>
        <Link href="/history" className="text-blue-600 underline">
          履歴を見る
        </Link>
      </nav>

      <input
  type="text"
  placeholder="タイトル・著者で検索"
  value={keyword}
  onChange={(e) => setKeyword(e.target.value)}
  className="mt-4 border rounded px-3 py-2 w-full"
/>

      {filteredBooks.map((book) => (
  <div key={book.id} className="border p-2 mt-2">
    <a href={`/books/${book.id}`} className="font-bold text-blue-600 underline">
      {book.title}
    </a>
    <p>{book.author}</p>
    <p>{book.shelf_level}段目</p>
    <p className={book.status === "available" ? "text-green-600" : "text-red-500"}>
      {book.status === "available" ? "在庫あり" : "貸出中"}
    </p>
  </div>
))}
    </main>
  );
}
