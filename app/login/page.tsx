"use client";

import { useState } from "react";

export default function Login() {
  // 入力された名前と、エラーメッセージを管理する state
  const [name, setName] = useState("");
  const [error, setError] = useState("");

  // 名前が空（空白文字だけの場合も含む）かどうかを判定
  const isButtonDisabled = name.trim() === "";

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();

    // 空チェック（バリデーション）
    if (isButtonDisabled) {
      setError("名前を入力してください");
      return;
    }

    setError("");
    
    // 今回のスコープでは画面遷移は作らないため、仮の動作としてコンソール出力とアラートを表示します
    // ※実際の遷移処理や保存処理は次段（#7）で実装します
    console.log("入力されたゲスト名:", name);
    alert(`「${name}」として利用を開始します（※実際の画面遷移は次のIssueで実装）`);
  };

  return (
    <main className="p-6 max-w-md mx-auto">
      <h1 className="text-2xl font-bold mb-2">ログイン / ゲスト利用</h1>
      <p className="text-sm text-gray-600 mb-6">
        本を借りる・返す際に、誰が操作したかを記録します。お名前を入力して「使う」ボタンを押してください。
      </p>

      <form onSubmit={handleSubmit} className="space-y-4">
        <div>
          <label
            htmlFor="name"
            className="block text-sm font-medium text-gray-700 mb-1"
          >
            お名前（ゲスト名）
          </label>
          <input
            id="name"
            type="text"
            value={name}
            onChange={(e) => {
              setName(e.target.value);
              // 入力を始めたらエラー表示をクリアする
              if (error) setError("");
            }}
            placeholder="例: 山田 太郎"
            className="w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 text-gray-900"
          />
          {/* エラーがある場合のみメッセージを表示 */}
          {error && <p className="mt-1 text-sm text-red-600">{error}</p>}
        </div>

        <button
          type="submit"
          disabled={isButtonDisabled}
          className={`w-full py-2 px-4 rounded-md font-medium text-white transition-colors ${
            isButtonDisabled
              ? "bg-gray-400 cursor-not-allowed"
              : "bg-blue-600 hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2"
          }`}
        >
          使う
        </button>
      </form>
    </main>
  );
}
