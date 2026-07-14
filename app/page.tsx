import { supabase } from '@/lib/supabaseClient'
import Link from 'next/link'
import SearchBox from './searchbox'

export default async function Home({
  searchParams,
}: {
  searchParams: Promise<{ q?: string }>
}) {
  const { q } = await searchParams

  let query = supabase.from('books').select('*')

  if (q) {
    // タイトルまたは著者名で部分一致検索
    query = query.or(`title.ilike.%${q}%,author.ilike.%${q}%`)
  }

  const { data, error } = await query

  if (error) {
    return <p className="text-red-500">エラーが発生しました</p>
  }

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

      <div className="mt-4">
        <SearchBox />
      </div>

      {(!data || data.length === 0) ? (
        <p className="mt-4 text-gray-500">
          {q ? '該当する本が見つかりませんでした' : '本が登録されていません'}
        </p>
      ) : (
        data.map((book) => (
          <div key={book.id} className="border p-2 mt-2">
            <a href={`/books/${book.id}`} className="font-bold text-blue-600 underline">
              {book.title}
            </a>
            <p>{book.author}</p>
            <p>{book.shelf_level}段目</p>
            <p className={book.status === 'available' ? 'text-green-600' : 'text-red-500'}>
              {book.status === 'available' ? '在庫あり' : '貸出中'}
            </p>
          </div>
        ))
      )}
    </main>
  )
}