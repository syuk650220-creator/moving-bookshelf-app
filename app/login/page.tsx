"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { getGuestName, setGuestName, isGuestName, makeGuestName, GUEST_LABEL } from "@/lib/guestName";

// 💡 ここに研究室のメンバーの名前を登録します（自由に変更してください）
const STUDENT_DATA = {
  B4: ["三瀬", "高元"],
  M1: ["森本", "刑部"],
  M2: ["水谷さん", "大津さん"],
};

// 選択できるカテゴリの型
type Category = "B4" | "M1" | "M2" | "ゲスト" | "";

export default function Login() {
  const router = useRouter();

  // カテゴリ（所属）と名前、エラーメッセージを管理する state
  const [category, setCategory] = useState<Category>("");
  const [name, setName] = useState("");
  const [error, setError] = useState("");

  // 前回入力した名前が保存されていれば、復元する
  useEffect(() => {
    const saved = getGuestName();
    if (saved) {
      if (isGuestName(saved)) {
        // この端末のゲストの名前（例: ゲスト-7K2Q）をそのまま使う。
        // 前の作りで「ゲスト」だけを保存していた端末は、ここで端末ごとの名前に替える
        // eslint-disable-next-line react-hooks/set-state-in-effect -- 外部ストア(localStorage)からの初期値読み込みのため
        setCategory("ゲスト");
        setName(saved === GUEST_LABEL ? makeGuestName() : saved);
      } else {
        // 保存された名前から、その人がどのカテゴリ（B4, M1, M2）かを探してセットする
        let foundCategory: Category = "";
        for (const [cat, names] of Object.entries(STUDENT_DATA)) {
          if (names.includes(saved)) {
            foundCategory = cat as Category;
            break;
          }
        }
        if (foundCategory) {
          setCategory(foundCategory);
          setName(saved);
        }
      }
    }
  }, []);

  // 名前が空かどうかを判定
  const isButtonDisabled = name.trim() === "";

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();

    if (isButtonDisabled) {
      setError("名前を選択してください");
      return;
    }

    setError("");
    setGuestName(name); // ゲスト名を localStorage に保存
    router.push("/books");   // 本一覧（ホーム）へ移動
  };

  return (
    <main className="p-6 max-w-md mx-auto">
      <h1 className="text-2xl font-bold mb-2">ログイン / 利用者選択</h1>
      <p className="text-sm text-gray-600 mb-6">
        本を借りる・返す際に、誰が操作したかを記録します。ご自身の名前を選択して「使う」ボタンを押してください。
      </p>

      <form onSubmit={handleSubmit} className="space-y-4">
        
        {/* 1. 所属カテゴリの選択プルダウン */}
        <div>
          <label htmlFor="category" className="block text-sm font-medium text-gray-700 mb-1">
            所属
          </label>
          <select
            id="category"
            value={category}
            onChange={(e) => {
              const selectedCat = e.target.value as Category;
              setCategory(selectedCat);
              
              // 所属が変わったら、名前をリセットする（ただしゲストの場合は、この端末のゲストの名前をセット）
              // ゲストの名前は端末ごとに違う（例: ゲスト-7K2Q）。全員が同じ名前だと、ロボの呼出が混ざるため
              if (selectedCat === "ゲスト") {
                const saved = getGuestName();
                setName(saved && isGuestName(saved) && saved !== GUEST_LABEL ? saved : makeGuestName());
              } else {
                setName("");
              }
              if (error) setError("");
            }}
            className="w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 text-gray-900 bg-white"
          >
            <option value="">所属を選択してください</option>
            <option value="B4">B4</option>
            <option value="M1">M1</option>
            <option value="M2">M2</option>
            <option value="ゲスト">ゲスト</option>
          </select>
        </div>

        {/* 2. 名前の選択プルダウン（B4, M1, M2が選ばれた時だけ表示） */}
        {category && category !== "ゲスト" && (
          <div>
            <label htmlFor="name" className="block text-sm font-medium text-gray-700 mb-1">
              お名前
            </label>
            <select
              id="name"
              value={name}
              onChange={(e) => {
                setName(e.target.value);
                if (error) setError("");
              }}
              className="w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 text-gray-900 bg-white"
            >
              <option value="">名前を選択してください</option>
              {STUDENT_DATA[category].map((studentName) => (
                <option key={studentName} value={studentName}>
                  {studentName}
                </option>
              ))}
            </select>
            {error && <p className="mt-1 text-sm text-red-600">{error}</p>}
          </div>
        )}

        {/* ゲスト選択時のメッセージ表示（任意） */}
        {category === "ゲスト" && (
          <p className="text-sm text-blue-600 bg-blue-50 p-2 rounded">
            ※ゲストとしてアプリを利用します（この端末の名前: {name}）
          </p>
        )}

        <button
          type="submit"
          disabled={isButtonDisabled}
          className={`w-full py-2 px-4 rounded-md font-medium text-white transition-colors mt-6 ${
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