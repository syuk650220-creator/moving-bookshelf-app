'use client'
import { Suspense, useCallback, useEffect, useRef, useState } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import { supabase } from '@/lib/supabaseClient'
import { getGuestName } from '@/lib/guestName'
import Link from 'next/link'

type Book = { id: string; title: string; status: string }
type StopPoint = { id: number; label: string }

// robot_calls に books / stop_points を結合した行
type CallRow = {
  id: string
  status: string
  requested_by: string | null
  created_at: string
  books: { title: string } | null
  stop_points: { label: string } | null
}

const CALL_LABEL: Record<string, string> = {
  queued: '順番待ち', moving: '移動中', arrived: '到着（受取待ち）',
}
const CALL_COLOR: Record<string, string> = {
  queued: 'bg-gray-100 text-gray-700',
  moving: 'bg-blue-100 text-blue-700',
  arrived: 'bg-green-100 text-green-700',
}
const STATE_LABEL: Record<string, string> = {
  idle: '待機中', moving: '移動中', arrived: '到着', returning: '帰還中',
}

function RobotCall() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const bookParam = searchParams.get('book')   // S-2 から ?book=<uuid> で渡される

  const [guestName, setGuestName] = useState<string | null>(null)
  const [books, setBooks] = useState<Book[]>([])
  const [seats, setSeats] = useState<StopPoint[]>([])
  const [bookId, setBookId] = useState<string>(bookParam ?? '')
  const [seatId, setSeatId] = useState<number | null>(null)
  const [queue, setQueue] = useState<CallRow[]>([])
  const [robotState, setRobotState] = useState<string>('')
  const [isCalling, setIsCalling] = useState(false)
  const [message, setMessage] = useState<{ kind: 'ok' | 'err'; text: string } | null>(null)

  const aliveRef = useRef(true)

  // ゲスト名が無ければログインへ（借りる処理と同じルール）
  useEffect(() => {
    const name = getGuestName()
    if (!name) {
      router.push('/login')
      return
    }
    // eslint-disable-next-line react-hooks/set-state-in-effect -- 外部ストア(localStorage)からの初期値読み込みのため
    setGuestName(name)
  }, [router])

  // 初回: 本と席の一覧
  useEffect(() => {
    const load = async () => {
      const [b, s] = await Promise.all([
        supabase.from('books').select('id, title, status').order('title'),
        supabase.from('stop_points').select('id, label').order('id'),
      ])
      if (!aliveRef.current) return
      if (b.data) setBooks(b.data as Book[])
      if (s.data) setSeats(s.data as StopPoint[])
      if (b.error || s.error) {
        setMessage({ kind: 'err', text: '本・席の一覧の取得に失敗しました' })
      }
    }
    load()
  }, [])

  // 1.5秒ごと: 呼出キューとロボ状態
  const fetchQueue = useCallback(async () => {
    const [q, st] = await Promise.all([
      supabase
        .from('robot_calls')
        .select('id, status, requested_by, created_at, books(title), stop_points(label)')
        .in('status', ['queued', 'moving', 'arrived'])
        .order('created_at', { ascending: true }),
      supabase.from('robot_status').select('state').eq('id', 1).single(),
    ])
    if (!aliveRef.current) return
    if (q.data) setQueue(q.data as unknown as CallRow[])
    if (st.data) setRobotState(st.data.state)
  }, [])

  useEffect(() => {
    aliveRef.current = true
    // eslint-disable-next-line react-hooks/set-state-in-effect -- マウント時の初回フェッチ（setStateはawait後にのみ実行される）
    fetchQueue()
    const id = setInterval(fetchQueue, 1500)
    return () => {
      aliveRef.current = false
      clearInterval(id)
    }
  }, [fetchQueue])

  // ---------------- 操作 ----------------

  const handleCall = async () => {
    if (!bookId || seatId === null || !guestName || isCalling) return
    setIsCalling(true)
    setMessage(null)
    const { error } = await supabase.from('robot_calls').insert({
      book_id: bookId,
      seat_id: seatId,
      status: 'queued',
      requested_by: guestName,
    })
    if (error) {
      setMessage({ kind: 'err', text: `呼出に失敗しました: ${error.message}` })
    } else {
      setMessage({ kind: 'ok', text: 'ロボを呼びました。到着したら「受け取った」を押してください。' })
      await fetchQueue()
    }
    setIsCalling(false)
  }

  // 到着した呼出を「受け取った」→ done（ロボは次の呼出へ進む）
  const handleReceive = async (callId: string) => {
    const { data, error } = await supabase
      .from('robot_calls')
      .update({ status: 'done' })
      .eq('id', callId)
      .eq('status', 'arrived')   // 到着済みのときだけ（競合対策）
      .select('id')
    if (error) {
      setMessage({ kind: 'err', text: `受取の記録に失敗しました: ${error.message}` })
    } else if (!data || data.length === 0) {
      setMessage({ kind: 'err', text: 'この呼出はすでに処理されています' })
    } else {
      setMessage({ kind: 'ok', text: '受け取りを記録しました。' })
    }
    await fetchQueue()
  }

  // 順番待ちの呼出を取り消す（移動開始後は取り消せない）
  const handleCancel = async (callId: string) => {
    const { data, error } = await supabase
      .from('robot_calls')
      .update({ status: 'canceled' })
      .eq('id', callId)
      .eq('status', 'queued')    // 順番待ちのときだけ（移動中は取り消せない）
      .select('id')
    if (error) {
      setMessage({ kind: 'err', text: `キャンセルに失敗しました: ${error.message}` })
    } else if (!data || data.length === 0) {
      setMessage({ kind: 'err', text: 'すでにロボが動き出しているため取り消せません' })
    } else {
      setMessage({ kind: 'ok', text: '呼出を取り消しました。' })
    }
    await fetchQueue()
  }

  // ---------------- 表示 ----------------

  const selectedBook = books.find((b) => b.id === bookId)

  return (
    <main className="p-6 max-w-md mx-auto">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">ロボを呼ぶ</h1>
        <Link
          href={bookParam ? `/books/${bookParam}` : '/'}
          className="text-sm text-blue-600 underline"
        >
          ← 戻る
        </Link>
      </div>
      <p className="mt-1 text-sm text-gray-600">
        本と席を選ぶと、本棚ロボがその席まで本を運んできます。
      </p>

      <p className="mt-2 text-sm">
        ロボの状態:{' '}
        <span className={`font-bold ${robotState === 'idle' ? 'text-green-600' : 'text-orange-600'}`}>
          {STATE_LABEL[robotState] ?? robotState ?? '—'}
        </span>
      </p>

      {message && (
        <div
          className={`mt-3 rounded-md border p-3 text-sm ${
            message.kind === 'ok'
              ? 'bg-green-50 border-green-300 text-green-800'
              : 'bg-red-50 border-red-300 text-red-700'
          }`}
        >
          {message.text}
        </div>
      )}

      {/* ---------------- 呼出フォーム ---------------- */}
      <section className="mt-4 rounded-xl border border-gray-200 bg-white p-4 shadow-sm">
        {/* 本の選択（S-2 から来たときは固定表示） */}
        {bookParam && selectedBook ? (
          <p className="text-sm">
            呼ぶ本: <span className="font-bold">{selectedBook.title}</span>
          </p>
        ) : (
          <div>
            <label htmlFor="book" className="block text-sm font-medium text-gray-700 mb-1">
              呼ぶ本
            </label>
            <select
              id="book"
              value={bookId}
              onChange={(e) => setBookId(e.target.value)}
              className="w-full rounded-md border border-gray-300 px-3 py-2 text-gray-900"
            >
              <option value="">— 本を選んでください —</option>
              {books.map((b) => (
                <option key={b.id} value={b.id}>
                  {b.title}
                </option>
              ))}
            </select>
          </div>
        )}

        {/* 席の選択 */}
        <p className="mt-4 text-sm font-medium text-gray-700">届け先の席</p>
        <div className="mt-1 grid grid-cols-3 gap-2">
          {seats.map((s) => (
            <button
              key={s.id}
              type="button"
              onClick={() => setSeatId(s.id)}
              className={`rounded-lg border py-3 text-sm font-bold transition-colors ${
                seatId === s.id
                  ? 'border-blue-600 bg-blue-600 text-white'
                  : 'border-gray-300 bg-white text-gray-700 hover:border-blue-400'
              }`}
            >
              {s.label}
            </button>
          ))}
          {seats.length === 0 && (
            <p className="col-span-3 text-sm text-gray-400">席が登録されていません</p>
          )}
        </div>

        <button
          type="button"
          onClick={handleCall}
          disabled={!bookId || seatId === null || isCalling}
          className={`mt-4 w-full rounded-md py-3 font-bold text-white transition-colors ${
            !bookId || seatId === null || isCalling
              ? 'bg-gray-400 cursor-not-allowed'
              : 'bg-blue-600 hover:bg-blue-700'
          }`}
        >
          {isCalling ? '呼出中...' : '🤖 ロボを呼ぶ'}
        </button>
        {guestName && (
          <p className="mt-2 text-center text-xs text-gray-400">「{guestName}」として呼び出します</p>
        )}
      </section>

      {/* ---------------- 呼出キュー（F-09） ---------------- */}
      <section className="mt-4">
        <h2 className="text-lg font-bold">いまの呼出キュー</h2>
        {queue.length === 0 ? (
          <p className="mt-2 text-sm text-gray-500">呼出はありません</p>
        ) : (
          <ul className="mt-2 space-y-2">
            {queue.map((c, i) => (
              <li key={c.id} className="rounded-lg border border-gray-200 bg-white p-3 shadow-sm">
                <div className="flex items-center justify-between gap-2">
                  <div className="min-w-0">
                    <p className="truncate text-sm font-bold text-gray-900">
                      {i + 1}. {c.books?.title ?? '（不明な本）'}
                    </p>
                    <p className="text-xs text-gray-500">
                      → {c.stop_points?.label ?? `席${'?'}`}／{c.requested_by ?? 'ゲスト'}
                    </p>
                  </div>
                  <span
                    className={`shrink-0 rounded-full px-2.5 py-1 text-xs font-bold ${
                      CALL_COLOR[c.status] ?? 'bg-gray-100 text-gray-600'
                    }`}
                  >
                    {CALL_LABEL[c.status] ?? c.status}
                  </span>
                </div>

                {/* 状態に応じた操作 */}
                {c.status === 'arrived' && (
                  <button
                    type="button"
                    onClick={() => handleReceive(c.id)}
                    className="mt-2 w-full rounded-md bg-green-600 py-2 text-sm font-bold text-white hover:bg-green-700"
                  >
                    ✅ 受け取った（ロボを帰す）
                  </button>
                )}
                {c.status === 'queued' && (
                  <button
                    type="button"
                    onClick={() => handleCancel(c.id)}
                    className="mt-2 w-full rounded-md border border-gray-300 py-1.5 text-xs text-gray-600 hover:bg-gray-50"
                  >
                    呼出を取り消す
                  </button>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>
    </main>
  )
}

// useSearchParams はサスペンド境界の内側で使う必要がある（Next.js の仕様）
export default function RobotCallPage() {
  return (
    <Suspense fallback={<p className="p-6 text-gray-500">読み込み中...</p>}>
      <RobotCall />
    </Suspense>
  )
}
