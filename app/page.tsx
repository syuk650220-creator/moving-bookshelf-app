import { supabase } from "@/lib/supabaseClient";
import Link from 'next/link'

export default async function Home() {
  const { data, error } = await supabase.from("books").select("*");
  
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

      {error && (
        <p className="mt-4 text-red-600">エラーが発生しました</p>
      )}

      {!error && data && data.length === 0 && (
        <p className="mt-4 text-gray-600">本がありません</p>
      )}

      {!error && data && data.length > 0 && (
      <ul className="mt-4 space-y-2">
        {data?.map((book) => (
          <li key={book.id} className="border p-3 rounded">
            <div className="font-bold">{book.title}</div>
            <div>{book.author}</div>
            <div>{book.shelf_level}段目</div>
            <div className={book.status === 'available' ? 'text-green-600' : 'text-orange-600'}>
              {book.status === 'available' ? '在庫あり' : '貸出中'}
            </div>
          </li>
        ))}
      </ul>
      )}
    </main>
  );
}
