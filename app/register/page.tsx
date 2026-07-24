"use client";

import { useState } from "react";
import { supabase } from "@/lib/supabaseClient";
import Link from "next/link";

export default function RegisterPage() {
  const [isbn, setIsbn] = useState("");
  const [shelfLevel, setShelfLevel] = useState<number | "">(1);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState<{ text: string; isError: boolean } | null>(null);

  // 全角数字(０-９)だけを半角(0-9)に変換する関数
  const toHalfWidth = (str: string) => {
    return str
      .replace(/[０-９]/g, (s) => String.fromCharCode(s.charCodeAt(0) - 0xfee0))
      .replace(/\s+/g, "");
  };

  const handleRegister = async (e: React.FormEvent) => {
    e.preventDefault();
    setMessage(null);

    // 💡 登録ボタンが押された「この瞬間」に、全角から半角へと変換する！
    const cleanIsbn = toHalfWidth(isbn).trim();
    
    // 画面の入力欄の表示も、このタイミングできれいな半角13桁に直してあげる
    setIsbn(cleanIsbn);

    // バリデーションチェック (10桁または13桁の半角数字)
    if (!/^\d{10}$|^\d{13}$/.test(cleanIsbn)) {
      setMessage({
        text: `無効なISBNです (入力値: "${cleanIsbn}")。10桁または13桁の半角数字であるか確認してください。`,
        isError: true,
      });
      return;
    }

    if (shelfLevel === "" || Number(shelfLevel) <= 0) {
      setMessage({ text: "正しい本棚の段数を入力してください。", isError: true });
      return;
    }

    setLoading(true);

    try {
      // 1. openBD API から書籍情報を取得
      const res = await fetch(`https://api.openbd.jp/v1/get?isbn=${cleanIsbn}`);
      const data = await res.json();

      if (!data || !data[0]) {
        setMessage({ text: "openBDに該当する書籍が見つかりませんでした。", isError: true });
        setLoading(false);
        return;
      }

      const summary = data[0].summary || {};
      const title = summary.title || "タイトル不明";
      const author = summary.author || "著者不明";

      // 2. Supabase に書籍情報を登録
      const bookData = {
        isbn: cleanIsbn,
        title: title,
        author: author,
        status: "available",
        shelf_level: Number(shelfLevel),
      };

      const { error } = await supabase.from("books").upsert(bookData);

      if (error) {
        setMessage({ text: `Supabase登録エラー: ${error.message}`, isError: true });
      } else {
        setMessage({ text: `「${title}」を正常に登録しました！`, isError: false });
        setIsbn(""); // 次のスキャン用にクリア
      }
    } catch (err) {
      setMessage({ text: "通信エラーが発生しました。ネット環境を確認してください。", isError: true });
    } finally {
      setLoading(false);
    }
  };

  return (
    <main className="p-6 max-w-md mx-auto">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold text-gray-900">📚 本の新規登録</h1>
        <Link href="/" className="text-sm text-blue-600 underline">
          ホームへ戻る
        </Link>
      </div>

      <form onSubmit={handleRegister} className="space-y-5 border p-6 rounded-xl shadow-sm bg-white">
        {/* ISBN入力欄 */}
        <div>
          <label htmlFor="isbn" className="block text-sm font-medium text-gray-700 mb-1">
            ISBN（バーコードの数字）
          </label>
          <input
            id="isbn"
            type="text"
            placeholder="例: 9784875932673"
            value={isbn}
            // 💡 リアルタイムの半角変換をやめて、スキャンされた文字を「そのまま」受け取る！
            onChange={(e) => setIsbn(e.target.value)}
            required
            className="w-full px-4 py-2 border rounded-md focus:ring-2 focus:ring-blue-500 text-gray-900 font-mono"
          />
          <p className="text-xs text-gray-500 mt-1">※全角でスキャンされても自動で半角に変換して登録されます</p>
        </div>

        {/* 本棚の段数入力欄 */}
        <div>
          <label htmlFor="shelf" className="block text-sm font-medium text-gray-700 mb-1">
            配置する棚の段数
          </label>
          <input
            id="shelf"
            type="number"
            min="1"
            value={shelfLevel}
            onChange={(e) => setShelfLevel(e.target.value === "" ? "" : Number(e.target.value))}
            required
            className="w-full px-4 py-2 border rounded-md focus:ring-2 focus:ring-blue-500 text-gray-900"
          />
        </div>

        {/* 登録ボタン */}
        <button
          type="submit"
          disabled={loading}
          className={`w-full py-3 rounded-md font-bold text-white transition-colors ${
            loading ? "bg-gray-400 cursor-wait" : "bg-blue-600 hover:bg-blue-700"
          }`}
        >
          {loading ? "openBDで検索＆登録中..." : "本を登録する"}
        </button>

        {/* 結果メッセージ表示 */}
        {message && (
          <div
            className={`p-3 rounded-md text-sm text-center font-medium ${
              message.isError
                ? "bg-red-50 text-red-600 border border-red-200"
                : "bg-green-50 text-green-700 border border-green-200"
            }`}
          >
            {message.text}
          </div>
        )}
      </form>
    </main>
  );
}
