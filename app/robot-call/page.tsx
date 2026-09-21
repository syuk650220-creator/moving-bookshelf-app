'use client'
import { Suspense, useCallback, useEffect, useRef, useState } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import { supabase } from '@/lib/supabaseClient'
import { getGuestName } from '@/lib/guestName'
import { borrowBook } from '@/lib/loans'
import Link from 'next/link'

type Book = { id: string; title: string; status: string }
type StopPoint = { id: number; label: string }

// robot_calls に books / stop_points を結合した行
type CallRow = {
  id: string
  status: string
  requested_by: string | null
  created_at: string
  book_id: string | null
  books: { title: string; status: string } | null   // status が available なら「借りるための呼出」
  stop_points: { label: string } | null
}

// robot_status.current_call_id が指している呼出（ロボがいま担当している 1 件）
type CurrentCall = {
  id: string
  status: string
  requested_by: string | null
  books: { title: string } | null
  stop_points: { label: string } | null
}

// ロボ側（ブリッジ）は走行中 1 秒ごとに robot_status を更新する。
// これより長く更新が無ければ「ブリッジが止まっている」とみなし、帰還待ちのロックだけは外す
const ROBOT_STALE_MS = 60_000

const CALL_LABEL: Record<string, string> = {
  queued: '順番待ち', moving: '移動中', arrived: '到着（受取待ち）',
}
const CALL_COLOR: Record<string, string> = {
  queued: 'bg-gray-100 text-gray-700',
  moving: 'bg-blue-100 text-blue-700',
  arrived: 'bg-green-100 text-green-700',
}
// robot_status.state（段階3で localizing＝自己位置推定中 が増えた。sql/03 参照）
const STATE_LABEL: Record<string, string> = {
  idle: '待機中', localizing: '自己位置推定中', moving: '移動中', arrived: '到着', returning: '本棚へ帰還中',
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
  const [robotDetail, setRobotDetail] = useState<string | null>(null)   // 「席2 へ移動中（残り 1.2 m）」など
  const [currentCall, setCurrentCall] = useState<CurrentCall | null>(null)   // ロボがいま担当している呼出
  const [robotUpdatedAt, setRobotUpdatedAt] = useState<string | null>(null)
  const [nowMs, setNowMs] = useState(0)   // 「更新が止まっているか」の判定用（ポーリングのたびに更新）
  const [isCalling, setIsCalling] = useState(false)
  const [isReceiving, setIsReceiving] = useState(false)
  const [message, setMessage] = useState<{ kind: 'ok' | 'err'; text: string } | null>(null)

  const aliveRef = useRef(true)
  // 自分の呼出が一覧から消えたとき「ロボが届かなかった」のかを見分けるための控え
  const myCallIdRef = useRef<string | null>(null)          // 直前のポーリングで進行中だった自分の呼出
  const selfCanceledRef = useRef<Set<string>>(new Set())   // 自分で取り消した呼出（知らせなくてよい）

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
        // id=0 は「本棚の場所」（ロボの定位置・帰る先）なので届け先には出さない（席は id ≥ 1）
        supabase.from('stop_points').select('id, label').gt('id', 0).order('id'),
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
        .select('id, status, requested_by, created_at, book_id, books(title, status), stop_points(label)')
        .in('status', ['queued', 'moving', 'arrived'])
        .order('created_at', { ascending: true }),
      // detail 列は sql/03 で増えたもの。無い環境でも動くよう '*' で読む。
      // robot_calls(...) は current_call_id の先（ロボがいま担当している呼出）を一緒に引く
      supabase
        .from('robot_status')
        .select('*, robot_calls(id, status, requested_by, books(title), stop_points(label))')
        .eq('id', 1)
        .single(),
    ])
    if (!aliveRef.current) return
    const rows = q.data ? (q.data as unknown as CallRow[]) : null
    if (rows) setQueue(rows)
    if (st.data) {
      const row = st.data as unknown as {
        state: string
        detail?: string | null
        updated_at?: string | null
        robot_calls?: CurrentCall | null
      }
      setRobotState(row.state)
      setRobotDetail(row.detail ?? null)
      setCurrentCall(row.robot_calls ?? null)
      setRobotUpdatedAt(row.updated_at ?? null)
    }
    setNowMs(Date.now())

    // ★自分の呼出が一覧から消えたとき★
    // 「本を取得した」（done）か自分の取り消しなら何もしない。ロボ側が canceled にした
    // （席まで届かなかった・ブリッジを止めた）ときは、だまって消えると分からないので知らせる
    if (rows) {
      const me = getGuestName()
      const mine = me ? rows.find((c) => c.requested_by === me) ?? null : null
      const lost = myCallIdRef.current
      myCallIdRef.current = mine?.id ?? null
      if (lost && lost !== mine?.id && !selfCanceledRef.current.has(lost)) {
        const r = await supabase.from('robot_calls').select('status').eq('id', lost).maybeSingle()
        if (aliveRef.current && r.data?.status === 'canceled') {
          setMessage({
            kind: 'err',
            text: 'ロボが席まで届きませんでした（走行の失敗か中止）。貸出は記録されていません。もう一度呼ぶか、自分で本を取ったときは本の画面の「直接借りる」で記録してください。',
          })
        }
      }
    }
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

  // ---------------- 自分の呼出が進行中か（1 人 1 件） ----------------
  //
  // 「ロボを呼ぶ」は、自分の呼出が 呼出 → 到着 →「本を取得した」→ 本棚へ帰還 → 待機中 まで
  // 終わるまで押せない。ほかの人は今までどおり呼べる（順番待ちに入る）。
  //   ① 自分の呼出がキューにある（順番待ち・移動中・到着）
  //   ② 自分の呼出は完了（done）したが、ロボがまだ本棚の場所へ帰っている途中
  //      （ブリッジは帰還中も robot_status.current_call_id にその呼出を入れている）

  const myActiveCall = guestName ? queue.find((c) => c.requested_by === guestName) ?? null : null
  const robotStale =
    robotUpdatedAt !== null && nowMs > 0 && nowMs - new Date(robotUpdatedAt).getTime() > ROBOT_STALE_MS
  const myReturning =
    !myActiveCall &&
    !!guestName &&
    robotState !== 'idle' &&
    currentCall?.requested_by === guestName &&
    !robotStale   // ブリッジが止まったまま帰還中で固まったときは、永久に押せなくならないよう外す
  const myBusy = !!myActiveCall || myReturning
  const myBusyCall = myActiveCall ?? (myReturning ? currentCall : null)

  // ---------------- 操作 ----------------

  const handleCall = async () => {
    if (!bookId || seatId === null || !guestName || isCalling || myBusy) return
    setIsCalling(true)
    setMessage(null)

    // ★1 人 1 件★ 画面の表示は最大 1.5 秒遅れるので、書き込む直前に DB でも確かめる
    // （連打・別のタブ・別の端末から同じ名前で呼んだ場合の二重呼出を防ぐ）
    const { data: mine, error: checkError } = await supabase
      .from('robot_calls')
      .select('id')
      .eq('requested_by', guestName)
      .in('status', ['queued', 'moving', 'arrived'])
      .limit(1)
    if (checkError) {
      setMessage({ kind: 'err', text: `呼出の確認に失敗しました: ${checkError.message}` })
      setIsCalling(false)
      return
    }
    if (mine && mine.length > 0) {
      setMessage({ kind: 'err', text: 'あなたの呼出がまだ進行中です。ロボが本棚の場所に戻るまでお待ちください。' })
      await fetchQueue()
      setIsCalling(false)
      return
    }

    const { error } = await supabase.from('robot_calls').insert({
      book_id: bookId,
      seat_id: seatId,
      status: 'queued',
      requested_by: guestName,
    })
    if (error) {
      setMessage({ kind: 'err', text: `呼出に失敗しました: ${error.message}` })
    } else {
      setMessage({ kind: 'ok', text: 'ロボを呼びました。到着したら「本を取得した」を押してください。' })
      await fetchQueue()
    }
    setIsCalling(false)
  }

  // 到着した呼出を「本を取得した」→ done
  // （ブリッジはこれを見て robot_status を returning にし、本棚の場所へ帰ってから次の呼出へ進む）
  //
  // 本が在庫ありなら、ここで貸出も記録する（本の画面の「借りる」→ この画面 → 到着 → 取得、が 1 本の流れ）。
  // 借りた人は、ボタンを押した人ではなく「呼んだ人」（requested_by）。
  // 貸出中の本の呼出（返却のためにロボを呼んだ場合）は、貸出を記録しない。
  const handleReceive = async (callId: string) => {
    if (isReceiving) return
    setIsReceiving(true)
    const call = queue.find((c) => c.id === callId) ?? null
    const { data, error } = await supabase
      .from('robot_calls')
      .update({ status: 'done' })
      .eq('id', callId)
      .eq('status', 'arrived')   // 到着済みのときだけ（競合対策）
      .select('id')
    if (error) {
      setMessage({ kind: 'err', text: `取得の記録に失敗しました: ${error.message}` })
    } else if (!data || data.length === 0) {
      setMessage({ kind: 'err', text: 'この呼出はすでに処理されています' })
    } else {
      const borrower = call?.requested_by ?? guestName
      if (call?.book_id && call.books?.status === 'available' && borrower) {
        const result = await borrowBook(call.book_id, borrower)
        if (result.ok) {
          setMessage({
            kind: 'ok',
            text: `本の取得を記録し、「${borrower}」さんの貸出として記録しました。ロボは本棚の場所へ帰ります。`,
          })
        } else if (result.reason === 'not_available') {
          // 到着を待つあいだに、だれかが「直接借りる」で先に記録した
          setMessage({ kind: 'ok', text: '本の取得を記録しました（この本はすでに貸出中でした）。ロボは本棚の場所へ帰ります。' })
        } else {
          setMessage({
            kind: 'err',
            text: 'ロボは本棚の場所へ帰りますが、貸出の記録に失敗しました。本の画面の「直接借りる」で記録してください。',
          })
        }
        // 本の状態（在庫あり → 貸出中）を選択欄の表示にも反映する
        const b = await supabase.from('books').select('id, title, status').order('title')
        if (aliveRef.current && b.data) setBooks(b.data as Book[])
      } else {
        setMessage({ kind: 'ok', text: '本の取得を記録しました。ロボは本棚の場所へ帰ります。' })
      }
    }
    await fetchQueue()
    setIsReceiving(false)
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
      selfCanceledRef.current.add(callId)
      setMessage({ kind: 'ok', text: '呼出を取り消しました。' })
    }
    await fetchQueue()
  }

  // ---------------- 表示 ----------------

  const selectedBook = books.find((b) => b.id === bookId)
  // 「本を取得した」は、自分の呼出が到着したときだけ押せる（ボタン自体はいつも同じ場所に出しておく）
  const canReceive = myActiveCall?.status === 'arrived'

  return (
    <main className="p-6 max-w-md mx-auto">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">
          {bookParam && selectedBook?.status === 'available' ? '借りる（ロボを呼ぶ）' : 'ロボを呼ぶ'}
        </h1>
        <Link
          href={bookParam ? `/books/${bookParam}` : '/books'}
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
        {robotDetail && <span className="ml-2 text-xs text-gray-500">{robotDetail}</span>}
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
          <div>
            <p className="text-sm">
              {selectedBook.status === 'available' ? '借りる本' : '呼ぶ本'}:{' '}
              <span className="font-bold">{selectedBook.title}</span>
            </p>
            <p className="mt-1 text-xs text-gray-500">
              {selectedBook.status === 'available'
                ? 'ロボが席に着いて「本を取得した」を押すと、貸出として記録されます（途中でやめた・届かなかった場合は記録されません）。'
                : 'この本は貸出中です。返却のために本棚ロボを席まで呼べます（返却の記録は、本の画面の「返す」で）。'}
            </p>
          </div>
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
          disabled={!bookId || seatId === null || isCalling || myBusy}
          className={`mt-4 w-full rounded-md py-3 font-bold text-white transition-colors ${
            !bookId || seatId === null || isCalling || myBusy
              ? 'bg-gray-400 cursor-not-allowed'
              : 'bg-blue-600 hover:bg-blue-700'
          }`}
        >
          {isCalling ? '呼出中...' : myBusy ? '⏳ あなたの呼出が進行中です' : '🤖 ロボを呼ぶ'}
        </button>
        {myBusy && (
          <div className="mt-2 rounded-md border border-amber-300 bg-amber-50 p-2 text-xs text-amber-800">
            <p className="font-bold">
              {myBusyCall?.books?.title ?? '本'} → {myBusyCall?.stop_points?.label ?? '席'}：
              {myReturning
                ? '本棚へ帰還中'
                : CALL_LABEL[myActiveCall?.status ?? ''] ?? myActiveCall?.status}
            </p>
            <p className="mt-0.5">
              {myReturning
                ? 'ロボが本棚の場所に戻ると、次の呼出ができます。'
                : myActiveCall?.status === 'arrived'
                  ? 'すぐ下の「本を取得した」を押すとロボが本棚の場所へ帰ります。戻ったら次の呼出ができます。'
                  : myActiveCall?.status === 'queued'
                    ? 'ロボが本棚の場所に戻るまで、次の呼出はできません。やめるときは下の「呼出を取り消す」。'
                    : 'ロボが本を届けて本棚の場所に戻るまで、次の呼出はできません。'}
            </p>
            {/* 到着後のボタン待ちの間はロボ側が状態を書き換えないので、止まって見えるのは正常。警告は移動中だけ */}
            {robotStale && myActiveCall?.status === 'moving' && (
              <p className="mt-0.5 text-red-700">
                ロボ側の更新が 1 分以上止まっています。ロボ担当（管理者）に知らせてください。
              </p>
            )}
          </div>
        )}

        {/* 本を取得した（いつも見えている。ロボが席に着くまでは押せない） */}
        <button
          type="button"
          onClick={() => myActiveCall && handleReceive(myActiveCall.id)}
          disabled={!canReceive || isReceiving}
          className={`mt-3 w-full rounded-md py-3 font-bold text-white transition-colors ${
            canReceive && !isReceiving ? 'bg-green-600 hover:bg-green-700' : 'bg-gray-400 cursor-not-allowed'
          }`}
        >
          {isReceiving ? '記録中...' : '📗 本を取得した'}
        </button>
        <p className="mt-1 text-center text-xs text-gray-500">
          {canReceive
            ? myActiveCall?.books?.status === 'available'
              ? `押すと「${myActiveCall.requested_by ?? 'ゲスト'}」さんの貸出として記録し、ロボは本棚の場所へ帰ります`
              : '押すとロボは本棚の場所へ帰ります'
            : myReturning
              ? '本の取得は記録済みです。ロボは本棚の場所へ帰っています'
              : myActiveCall
                ? 'ロボが席に到着すると押せるようになります'
                : 'ロボを呼んで、席に到着すると押せるようになります'}
        </p>

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

                {/* 状態に応じた操作（自分の呼出の「本を取得した」は上のボタン。ここはほかの人の分） */}
                {c.status === 'arrived' && c.id !== myActiveCall?.id && (
                  <button
                    type="button"
                    onClick={() => handleReceive(c.id)}
                    className="mt-2 w-full rounded-md bg-green-600 py-2 text-sm font-bold text-white hover:bg-green-700"
                  >
                    📗 本を取得した（ロボを本棚へ帰す）
                  </button>
                )}
                {c.status === 'arrived' && c.id === myActiveCall?.id && (
                  <p className="mt-1 text-center text-xs text-green-700">上の「本を取得した」を押してください</p>
                )}
                {c.status === 'arrived' && c.id !== myActiveCall?.id && c.books?.status === 'available' && (
                  <p className="mt-1 text-center text-xs text-gray-500">
                    押すと「{c.requested_by ?? 'ゲスト'}」さんの貸出として記録されます
                  </p>
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
