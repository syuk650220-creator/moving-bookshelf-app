import Link from "next/link";

export default function Home() {
  return (
    <main className="p-6 max-w-md mx-auto text-center h-screen flex flex-col justify-center items-center">
      {/* アプリのタイトルやロゴ */}
      <h1 className="text-3xl font-bold mb-4 text-gray-900">
        自動移動本棚 貸出アプリ
      </h1>
      <p className="text-sm text-gray-600 mb-8 max-w-xs mx-auto">
        本棚を呼び出して、本の「借りる」「返す」を行うためのアプリケーションです。
      </p>

      {/* ログイン画面への移動ボタン */}
      <div className="w-full px-4">
        <Link
          href="/login"
          className="block w-full py-3 px-6 rounded-md font-medium text-white bg-blue-600 hover:bg-blue-700 transition-colors shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 text-lg text-center"
        >
          アプリを開く
        </Link>
      </div>
    </main>
  );
}