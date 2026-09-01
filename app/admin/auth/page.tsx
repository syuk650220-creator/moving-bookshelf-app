"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";

export default function AdminAuthPage() {
  const [pin, setPin] = useState("");
  const [error, setError] = useState("");
  const router = useRouter();

  // 💡 研究室で決める暗証番号（本登録画面 /register/auth と同じ値にしています）
  // ※本来は環境変数やDBで管理しますが、ゼミのプロトタイプならコード内固定で十分機能します
  const CORRECT_PIN = "1234";

  const handleVerify = (e: React.FormEvent) => {
    e.preventDefault();

    if (pin === CORRECT_PIN) {
      setError("");
      router.push("/admin");
    } else {
      setError("暗証番号が間違っています。管理者に確認してください。");
      setPin("");
    }
  };

  return (
    <main className="p-6 max-w-md mx-auto h-screen flex flex-col justify-center">
      <div className="border border-gray-200 p-6 rounded-xl shadow-md bg-white">
        <h1 className="text-xl font-bold text-gray-900 mb-2">管理者認証</h1>
        <p className="text-sm text-gray-600 mb-6">
          ロボットの手動操作画面に入るには、研究室の管理暗証番号を入力してください。
        </p>

        <form onSubmit={handleVerify} className="space-y-4">
          <div>
            <label htmlFor="pin" className="block text-sm font-medium text-gray-700 mb-1">
              暗証番号 (4桁)
            </label>
            <input
              id="pin"
              type="password"
              inputMode="numeric"
              pattern="[0-9]*"
              value={pin}
              onChange={(e) => {
                setPin(e.target.value);
                if (error) setError("");
              }}
              placeholder="••••"
              className="w-full px-4 py-3 border border-gray-300 rounded-md text-center text-2xl tracking-widest focus:outline-none focus:ring-2 focus:ring-blue-500 text-gray-900"
              maxLength={4}
            />
            {error && <p className="mt-2 text-sm text-red-600 text-center">{error}</p>}
          </div>

          <button
            type="submit"
            disabled={pin.length < 4}
            className={`w-full py-2.5 px-4 rounded-md font-medium text-white transition-colors ${
              pin.length < 4
                ? "bg-gray-400 cursor-not-allowed"
                : "bg-blue-600 hover:bg-blue-700"
            }`}
          >
            認証して進む
          </button>
        </form>

        <div className="mt-4 text-center">
          <Link href="/" className="text-sm text-gray-500 hover:underline">
            キャンセルして本一覧に戻る
          </Link>
        </div>
      </div>
    </main>
  );
}
