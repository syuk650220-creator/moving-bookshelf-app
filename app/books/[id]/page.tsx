'use client'
import { use, useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { supabase } from '@/lib/supabaseClient'
import { getGuestName } from '@/lib/guestName'
import { borrowBook } from '@/lib/loans'
import Link from 'next/link'

type Book = {
  id: string
  title: string
  author: string
  shelf_level: number
  status: string
}

type Loan = {
  id: string
  book_id: string
  borrower_type: string
  guest_name: string | null
  borrowed_at: string
  returned_at: string | null
  returned_by: string | null
}

const LOAN_PERIOD_DAYS = 14

// 日時を「2026年7月22日 14時30分」形式に変換する
function formatDateTime(isoString: string) {
  const d = new Date(isoString)
  return d.toLocaleString('ja-JP', {
    year: 'numeric',
    month: 'long',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

export default function BookDetailPage({
  params,
}: {
  params: Promise<{ id: string }>
}) {
  const { id } = use(params)
  const router = useRouter()
  const [book, setBook] = useState<Book | null>(null)
  const [loans, setLoans] = useState<Loan[]>([])
  const [errorMsg, setErrorMsg] = useState<string | null>(null)
  const [isProcessing, setIsProcessing] = useState(false)
  // 画面を差し替えずに上に出すお知らせ（「すでに貸出中です」など）
  const [notice, setNotice] = useState<string | null>(null)

  async function fetchData() {
    // 本を1件取得
    const { data: bookData, error: bookError } = await supabase
      .from('books')
      .select('*')
      .eq('id', id)
      .single()

    if (bookError) {
      setErrorMsg('本の取得に失敗しました')
      return
    }
    setBook(bookData)

    // この本の貸出履歴を取得
    const { data: loanData, error: loanError } = await supabase
      .from('loans')
      .select('*')
      .eq('book_id', id)
      .order('borrowed_at', { ascending: false })

    if (loanError) {
      setErrorMsg('履歴の取得に失敗しました')
      return
    }
    setLoans(loanData ?? [])
  }

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- マウント時の初回フェッチ（setStateはawait後にのみ実行される）
    fetchData()
  }, [id])

  if (errorMsg) {
    return <p className="p-6 text-red-500">{errorMsg}</p>
  }

  if (!book) {
    return <p className="p-6 text-gray-500">読み込み中...</p>
  }

  // 現在貸出中のloan（未返却のもの）を探す
  const currentLoan = loans.find((loan) => loan.returned_at === null)

  // 貸出期限と残り日数を計算する
  let dueDateText: string | null = null
  let daysLeftText: string | null = null
  let isOverdue = false

  if (currentLoan) {
    const borrowedDate = new Date(currentLoan.borrowed_at)
    const dueDate = new Date(borrowedDate)
    dueDate.setDate(dueDate.getDate() + LOAN_PERIOD_DAYS)

    dueDateText = dueDate.toLocaleDateString('ja-JP', {
      year: 'numeric',
      month: 'long',
      day: 'numeric',
    })

    const today = new Date()
    today.setHours(0, 0, 0, 0)
    dueDate.setHours(0, 0, 0, 0)

    const diffMs = dueDate.getTime() - today.getTime()
    const diffDays = Math.round(diffMs / (1000 * 60 * 60 * 24))

    if (diffDays > 0) {
      daysLeftText = `あと${diffDays}日`
    } else if (diffDays === 0) {
      daysLeftText = '本日期限'
    } else {
      daysLeftText = `期限切れ（${Math.abs(diffDays)}日超過）`
      isOverdue = true
    }
  }

  // 「直接借りる」処理（ロボを呼ばず、自分で本棚から取るとき）
  // ※ロボに運んでもらう「借りる」は /robot-call へのリンクで、貸出の記録は到着後の「本を取得した」で行う
  async function handleBorrow() {
    if (isProcessing) return

    // S-6で保存したゲスト名を読み出す。未設定なら入力画面へ誘導する
    const guestName = getGuestName()
    if (!guestName) {
      router.push('/login')
      return
    }

    setIsProcessing(true)
    setErrorMsg(null)
    setNotice(null)

    try {
      // 貸出の記録（books を貸出中に → loans に1行）。ロボ呼出の「本を取得した」と同じ処理
      const result = await borrowBook(id, guestName)
      if (!result.ok) {
        if (result.reason === 'not_available') {
          // ほかの人が先に借りた。画面を最新にして知らせる
          setNotice(result.message)
          await fetchData()
        } else {
          setErrorMsg(result.message)
        }
        return
      }

      // 画面を最新化
      await fetchData()

    } catch (e) {
      setErrorMsg('予期せぬエラーが発生しました')
    } finally {
      setIsProcessing(false)
    }
  }

  // 「返す」処理
  async function handleReturn() {
    if (isProcessing) return
    setIsProcessing(true)
    setErrorMsg(null)

    try {
      // ① 未返却のloanを取得（複数あっても最新の1件だけ使う）
      const { data: loanDataList, error: selectError } = await supabase
        .from('loans')
        .select('*')
        .eq('book_id', id)
        .is('returned_at', null)
        .order('borrowed_at', { ascending: false })
        .limit(1)

      if (selectError) {
        setErrorMsg('返却対象の取得に失敗しました')
        return
      }

      if (!loanDataList || loanDataList.length === 0) {
        setErrorMsg('返却対象が見つかりませんでした')
        return
      }

      const loanData = loanDataList[0]

      // ② 取得したloanのreturned_atとreturned_byを更新
      const { error: updateLoanError } = await supabase
        .from('loans')
        .update({
          returned_at: new Date().toISOString(),
          returned_by: loanData.guest_name ?? 'guest',
        })
        .eq('id', loanData.id)

      if (updateLoanError) {
        setErrorMsg('返却処理に失敗しました')
        return
      }

      // ③ booksのstatusをavailableに戻す
      const { error: updateBookError } = await supabase
        .from('books')
        .update({ status: 'available' })
        .eq('id', id)

      if (updateBookError) {
        // booksの更新に失敗したら、loansのreturned_at/returned_byを元に戻す（ロールバック）
        await supabase
          .from('loans')
          .update({
            returned_at: null,
            returned_by: null,
          })
          .eq('id', loanData.id)

        setErrorMsg('状態の更新に失敗しました')
        return
      }

      // ④ 画面を最新化
      await fetchData()

    } catch (e) {
      setErrorMsg('予期せぬエラーが発生しました')
    } finally {
      setIsProcessing(false)
    }
  }

  return (
    <main className="p-6">
      <Link href="/books" className="text-blue-600 underline">
        ← 一覧に戻る
      </Link>

      <h1 className="mt-4 text-2xl font-bold">{book.title}</h1>
      <p className="mt-1 text-gray-600">{book.author}</p>
      <p className="mt-1">{book.shelf_level}段目</p>
      <p className={`mt-1 font-bold ${book.status === 'available' ? 'text-green-600' : 'text-orange-600'}`}>
        {book.status === 'available' ? '在庫あり' : '貸出中'}
      </p>

      {/* 期限切れ警告バナー（貸出中 かつ 期限切れのときだけ表示） */}
      {isOverdue && (
        <div className="mt-2 inline-block bg-red-600 text-white font-bold px-3 py-1 rounded">
          ⚠ 期限切れ
        </div>
      )}

      {/* 貸出期限（貸出中のときだけ表示） */}
      {currentLoan && dueDateText && (
        <p className="mt-2">
          <span className="text-gray-600">貸出期限: {dueDateText}　</span>
          <span
            className={
              isOverdue
                ? 'text-red-600 font-bold text-lg'
                : 'text-amber-600 font-bold text-lg'
            }
          >
            {daysLeftText}
          </span>
        </p>
      )}

      {notice && (
        <p className="mt-3 rounded-md border border-amber-300 bg-amber-50 p-2 text-sm text-amber-800">
          {notice}
        </p>
      )}

      {/* 借りる・返すボタン（statusで出し分け） */}
      {book.status === 'available' ? (
        <div className="mt-4">
          <div className="flex flex-wrap gap-4">
            {/* 借りる ＝ ロボを呼ぶ画面へ。貸出の記録は、ロボが着いて「本を取得した」を押したとき */}
            <Link
              href={`/robot-call?book=${id}`}
              className="bg-indigo-600 text-white px-4 py-2 rounded hover:bg-indigo-700"
            >
              🤖 借りる
            </Link>
            <button
              className="border border-blue-500 text-blue-600 px-4 py-2 rounded hover:bg-blue-50 disabled:opacity-50"
              disabled={isProcessing}
              onClick={handleBorrow}
            >
              {isProcessing ? '処理中...' : '直接借りる'}
            </button>
          </div>
          <p className="mt-2 text-xs text-gray-500">
            「借りる」は本棚ロボが席まで本を運びます。「直接借りる」はロボを呼ばず、自分で本棚から取るときに押します。
          </p>
        </div>
      ) : (
        <div className="mt-4 flex flex-wrap gap-4">
          <button
            className="bg-gray-500 text-white px-4 py-2 rounded disabled:opacity-50"
            disabled={isProcessing}
            onClick={handleReturn}
          >
            {isProcessing ? '処理中...' : '返す'}
          </button>
          {/* 返却のために本棚ロボを席まで呼ぶ */}
          <Link
            href={`/robot-call?book=${id}`}
            className="bg-indigo-600 text-white px-4 py-2 rounded hover:bg-indigo-700"
          >
            🤖 ロボを呼ぶ
          </Link>
        </div>
      )}

      {/* 貸出履歴 */}
      <h2 className="mt-6 text-xl font-bold">貸出履歴</h2>
      {loans.length === 0 ? (
        <p className="mt-2 text-gray-500">履歴なし</p>
      ) : (
        <ul className="mt-2 space-y-2">
          {loans.map((loan) => (
            <li key={loan.id} className="border p-3 rounded">
              <p>借りた人: {loan.guest_name ?? '不明'}</p>
              <p>借りた日: {formatDateTime(loan.borrowed_at)}</p>
              <p>
                返却日: {loan.returned_at ? formatDateTime(loan.returned_at) : '未返却'}
              </p>
            </li>
          ))}
        </ul>
      )}
    </main>
  )
}