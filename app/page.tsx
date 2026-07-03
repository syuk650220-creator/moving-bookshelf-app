import { supabase } from '@/lib/supabaseClient'
import Link from 'next/link'

export default async function Home() {
  const { data, error } = await supabase.from("books").select("*");

  if (error) {
    return <p className="text-red-500">エラーが発生しました</p>;
  }

  if (!data || data.length === 0) {
    return <p className="p-6 text-gray-500">本が登録されていません</p>;
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

      {(data ?? []).map((book) => (
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
