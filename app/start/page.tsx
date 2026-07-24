import Link from "next/link";

export default function Home() {
  return (
    <main className="relative min-h-screen flex flex-col justify-between items-center p-6 max-w-md mx-auto overflow-hidden bg-gradient-to-b from-blue-50 via-white to-indigo-50 text-center">
      
      {/* ✨ 背景のほわっとした光の装飾（グラデーションオーブ） */}
      <div className="absolute top-12 -left-10 w-40 h-40 bg-yellow-200 rounded-full mix-blend-multiply filter blur-2xl opacity-60"></div>
      <div className="absolute top-24 -right-10 w-40 h-40 bg-blue-200 rounded-full mix-blend-multiply filter blur-2xl opacity-60"></div>
      <div className="absolute -bottom-10 left-10 w-48 h-48 bg-pink-200 rounded-full mix-blend-multiply filter blur-2xl opacity-60"></div>

      {/* 上部のちょっとしたバッジ（アクセント） */}
      <div className="w-full pt-8 z-10">
        <span className="inline-flex items-center gap-1 py-1.5 px-3 rounded-full bg-blue-100 text-blue-800 text-xs font-bold tracking-wider shadow-sm">
          <span>🤖</span> SMART BOOKSHELF
        </span>
      </div>

      {/* 📚 メインイラスト＆タイトルエリア */}
      <div className="z-10 flex flex-col items-center my-auto py-6">
        
        {/* イラスト代わりのにぎやかアイコンカード */}
        <div className="relative mb-8 mt-4">
          {/* 背景の大きな本のカード */}
          <div className="w-36 h-36 bg-gradient-to-tr from-blue-500 to-indigo-600 rounded-3xl shadow-xl flex items-center justify-center text-7xl transform -rotate-3 hover:rotate-0 transition-transform duration-300">
            📚
          </div>
          {/* 右下に跳ねるロボットのアイコン */}
          <div className="absolute -bottom-3 -right-3 w-16 h-16 bg-yellow-400 rounded-2xl shadow-lg flex items-center justify-center text-3xl animate-bounce">
            🤖
          </div>
          {/* 周りのキラキラ装飾 */}
          <div className="absolute -top-4 -left-4 text-3xl animate-pulse">
            ✨
          </div>
          <div className="absolute top-2 -right-6 text-2xl transform rotate-12">
            📖
          </div>
          <div className="absolute -bottom-2 -left-6 text-2xl transform -rotate-12">
            🚀
          </div>
        </div>

        {/* アプリタイトル */}
        <h1 className="text-3xl font-extrabold mb-3 text-gray-900 tracking-tight">
          自動移動本棚
          <span className="block text-transparent bg-clip-text bg-gradient-to-r from-blue-600 to-indigo-600 mt-1 text-4xl">
            貸出アプリ
          </span>
        </h1>

        <p className="text-sm text-gray-600 max-w-xs mx-auto leading-relaxed mt-2">
          本棚を呼び出して、本の
          <span className="font-bold text-blue-600 bg-blue-50 px-1 rounded mx-0.5">借りる</span>
          <span className="font-bold text-indigo-600 bg-indigo-50 px-1 rounded mx-0.5">返す</span>
          をもっと楽しく、スマートに。
        </p>
      </div>

      {/* 🚀 ログイン画面への移動ボタン＆フッター */}
      <div className="w-full pb-8 z-10 space-y-4">
        <Link
          href="/login"
          className="group relative block w-full py-4 px-6 rounded-2xl font-bold text-white bg-gradient-to-r from-blue-600 via-indigo-600 to-blue-700 hover:from-blue-500 hover:to-indigo-500 transition-all duration-300 shadow-lg hover:shadow-xl transform hover:-translate-y-0.5 active:translate-y-0 text-lg text-center overflow-hidden"
        >
          <span className="relative z-10 flex items-center justify-center gap-2">
            <span>アプリを開く</span>
            <span className="group-hover:translate-x-1 transition-transform inline-block">➔</span>
          </span>
        </Link>

        <p className="text-xs text-gray-400 font-medium">
          © Moving Bookshelf Project
        </p>
      </div>
    </main>
  );
}