'use client'
import { useCallback, useEffect, useRef, useState } from 'react'
import { supabase } from '@/lib/supabaseClient'
import Link from 'next/link'

// 動作番号（simulink の動作表 = robot/manual_control.py と同じ）
const STOP = 0
const MOTION_LABEL: Record<number, string> = {
  0: '停止', 1: '前進', 2: '後退', 3: '左横', 4: '右横', 5: '左回転', 6: '右回転',
}

const STATE_LABEL: Record<string, string> = {
  idle: '待機中', moving: '移動中', arrived: '到着', returning: '帰還中',
}

// キーボード → 動作番号（pi_controller.py と同じ割り当て + 矢印キー）
const KEY_MOTION: Record<string, number> = {
  w: 1, arrowup: 1, s: 2, arrowdown: 2,
  a: 3, d: 4,
  q: 5, arrowleft: 5, e: 6, arrowright: 6,
}

const KEEPALIVE_MS = 500   // 動作中はこの間隔で指令を再送する（Pi側デッドマンは1.2秒）
const RPM_MIN = 15         // これ未満はモーターのPIDが効かない領域（robot_params 参照）
const RPM_MAX = 60         // 0.25 m/s 相当。会場裁定の速度上限

type ManualView = {
  enabled: boolean
  motion: number
  rpm: number
  pi_age: number | null
}

type RobotStatus = {
  state: string
  current_call_id: string | null
}

export default function AdminPage() {
  const [enabled, setEnabled] = useState(false)
  const [motion, setMotion] = useState(STOP)
  const [rpm, setRpm] = useState(30)
  const [view, setView] = useState<ManualView | null>(null)
  const [status, setStatus] = useState<RobotStatus | null>(null)
  const [queuedCount, setQueuedCount] = useState<number | null>(null)
  const [latencyMs, setLatencyMs] = useState<number | null>(null)
  const [errorMsg, setErrorMsg] = useState<string | null>(null)

  // keepalive ループやキーイベントから最新値を読むための ref
  // （state 更新箇所で必ず ref も一緒に更新する。render 中の同期はしない）
  const motionRef = useRef(motion)
  const rpmRef = useRef(rpm)
  const enabledRef = useRef(enabled)
  const activeKeyRef = useRef<string | null>(null)

  // ---------------- DBへ指令を送る ----------------

  const sendManual = useCallback(
    async (fields: { enabled?: boolean; motion?: number; rpm?: number }) => {
      const t0 = performance.now()
      const { error } = await supabase.from('robot_manual').update(fields).eq('id', 1)
      if (error) {
        setErrorMsg(
          error.message.includes('robot_manual')
            ? 'robot_manual テーブルがありません。robot/sql/02_manual_control.sql を SQL Editor で実行してください。'
            : `指令の送信に失敗しました: ${error.message}`,
        )
        return false
      }
      setErrorMsg(null)
      setLatencyMs(Math.round(performance.now() - t0))
      return true
    },
    [],
  )

  const startMotion = useCallback(
    (m: number) => {
      if (!enabledRef.current) return
      setMotion(m)
      motionRef.current = m
      sendManual({ motion: m, rpm: rpmRef.current })
    },
    [sendManual],
  )

  const stopMotion = useCallback(() => {
    if (motionRef.current === STOP) return
    setMotion(STOP)
    motionRef.current = STOP
    sendManual({ motion: STOP })
  }, [sendManual])

  const toggleEnabled = useCallback(() => {
    const next = !enabledRef.current
    setEnabled(next)
    enabledRef.current = next
    setMotion(STOP)
    motionRef.current = STOP
    sendManual({ enabled: next, motion: STOP, rpm: rpmRef.current })
  }, [sendManual])

  // ---------------- keepalive（動作中は指令を更新し続ける） ----------------
  //
  // Pi側は「指令が1.2秒更新されなければ停止」のデッドマン方式なので、
  // ボタンを押している間はここが0.5秒ごとに同じ指令を再送して生存を示す。
  // ブラウザが落ちれば再送が止まり、Pi側が自動で停止する。

  useEffect(() => {
    const id = setInterval(() => {
      if (enabledRef.current && motionRef.current !== STOP) {
        sendManual({ motion: motionRef.current, rpm: rpmRef.current })
      }
    }, KEEPALIVE_MS)
    return () => clearInterval(id)
  }, [sendManual])

  // ---------------- 画面を離れたら止める ----------------

  useEffect(() => {
    const halt = () => {
      if (motionRef.current !== STOP) {
        motionRef.current = STOP
        setMotion(STOP)
        // ページ遷移中でも届くよう keepalive を待たず即送る
        supabase.from('robot_manual').update({ motion: STOP }).eq('id', 1).then(() => {})
      }
    }
    const onVisibility = () => {
      if (document.visibilityState === 'hidden') halt()
    }
    window.addEventListener('pagehide', halt)
    document.addEventListener('visibilitychange', onVisibility)
    return () => {
      window.removeEventListener('pagehide', halt)
      document.removeEventListener('visibilitychange', onVisibility)
      halt()
    }
  }, [])

  // ---------------- キーボード操作 ----------------

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.repeat) return
      const key = e.key.toLowerCase()
      if (key === ' ' || key === 'x') {
        e.preventDefault()
        activeKeyRef.current = null
        stopMotion()
        return
      }
      const m = KEY_MOTION[key]
      if (m !== undefined) {
        e.preventDefault()
        activeKeyRef.current = key
        startMotion(m)
      }
    }
    const onKeyUp = (e: KeyboardEvent) => {
      if (activeKeyRef.current === e.key.toLowerCase()) {
        activeKeyRef.current = null
        stopMotion()
      }
    }
    window.addEventListener('keydown', onKeyDown)
    window.addEventListener('keyup', onKeyUp)
    return () => {
      window.removeEventListener('keydown', onKeyDown)
      window.removeEventListener('keyup', onKeyUp)
    }
  }, [startMotion, stopMotion])

  // ---------------- 状態表示のポーリング（1秒ごと） ----------------

  useEffect(() => {
    let alive = true
    const fetchAll = async () => {
      const [mv, st, qc] = await Promise.all([
        supabase.from('robot_manual_v').select('enabled, motion, rpm, pi_age').eq('id', 1).single(),
        supabase.from('robot_status').select('state, current_call_id').eq('id', 1).single(),
        supabase.from('robot_calls').select('id', { count: 'exact', head: true }).eq('status', 'queued'),
      ])
      if (!alive) return
      if (mv.error) {
        setErrorMsg(
          'robot_manual テーブルがありません。robot/sql/02_manual_control.sql を SQL Editor で実行してください。',
        )
      } else if (mv.data) {
        setView(mv.data as ManualView)
      }
      if (st.data) setStatus(st.data as RobotStatus)
      if (qc.count !== null) setQueuedCount(qc.count)
    }
    fetchAll()
    const id = setInterval(fetchAll, 1000)
    return () => {
      alive = false
      clearInterval(id)
    }
  }, [])

  // ---------------- 表示用の派生値 ----------------

  const piOnline = view?.pi_age != null && view.pi_age < 3
  const state = status?.state ?? '不明'

  // 押している間だけ動く操作ボタン
  // ※コンポーネントではなく普通の関数にすること。コンポーネントにすると
  //   毎レンダーで型が変わって再マウントされ、押下中に pointerup を取りこぼす
  const holdButton = (m: number, label: string) => (
    <button
      type="button"
      disabled={!enabled}
      onPointerDown={(e) => {
        e.currentTarget.setPointerCapture(e.pointerId)
        startMotion(m)
      }}
      onPointerUp={stopMotion}
      onPointerCancel={stopMotion}
      onContextMenu={(e) => e.preventDefault()}
      className={`select-none touch-none rounded-lg font-bold text-white py-4 transition-colors
        disabled:bg-gray-300 disabled:cursor-not-allowed
        ${motion === m ? 'bg-blue-800 ring-2 ring-blue-300' : 'bg-blue-600 active:bg-blue-800'}`}
    >
      {label}
    </button>
  )

  return (
    <main className="p-6 max-w-md mx-auto">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">管理者画面</h1>
        <Link href="/" className="text-sm text-blue-600 underline">
          ← 本一覧へ
        </Link>
      </div>
      <p className="mt-1 text-sm text-gray-600">
        ロボットとアプリの連携確認用。Supabase 経由でロボを手動操作します。
      </p>

      {errorMsg && (
        <div className="mt-4 rounded-md bg-red-50 border border-red-300 p-3 text-sm text-red-700">
          {errorMsg}
        </div>
      )}

      {/* ---------------- 状態表示 ---------------- */}
      <section className="mt-4 rounded-xl border border-gray-200 bg-white p-4 shadow-sm">
        <h2 className="font-bold text-gray-900">ロボットの状態</h2>
        <dl className="mt-2 grid grid-cols-2 gap-y-2 text-sm">
          <dt className="text-gray-500">状態</dt>
          <dd className={`font-bold ${state === 'idle' ? 'text-green-600' : 'text-orange-600'}`}>
            {STATE_LABEL[state] ?? state}
          </dd>

          <dt className="text-gray-500">Pi 受信スクリプト</dt>
          <dd className={`font-bold ${piOnline ? 'text-green-600' : 'text-red-500'}`}>
            {piOnline ? '● オンライン' : '○ オフライン'}
            {view?.pi_age != null && view.pi_age < 999 && (
              <span className="ml-1 font-normal text-gray-400">({view.pi_age.toFixed(1)}秒前)</span>
            )}
          </dd>

          <dt className="text-gray-500">未処理の呼出</dt>
          <dd>{queuedCount ?? '—'} 件</dd>

          <dt className="text-gray-500">通信遅延（送信）</dt>
          <dd>{latencyMs != null ? `${latencyMs} ms` : '—'}</dd>
        </dl>
      </section>

      {/* ---------------- 手動操作 ---------------- */}
      <section className="mt-4 rounded-xl border border-gray-200 bg-white p-4 shadow-sm">
        <div className="flex items-center justify-between">
          <h2 className="font-bold text-gray-900">手動操作（ラジコンモード）</h2>
          <button
            type="button"
            onClick={toggleEnabled}
            className={`rounded-full px-4 py-1.5 text-sm font-bold text-white transition-colors ${
              enabled ? 'bg-red-600 hover:bg-red-700' : 'bg-green-600 hover:bg-green-700'
            }`}
          >
            {enabled ? '手動モードを切る' : '手動モードにする'}
          </button>
        </div>

        {enabled && !piOnline && (
          <p className="mt-2 rounded-md bg-amber-50 border border-amber-300 p-2 text-xs text-amber-800">
            Pi 側の受信スクリプト（robot/manual_control.py）が動いていません。
            指令は Supabase に届きますが、ロボは反応しません。
          </p>
        )}

        <p className="mt-3 text-xs text-gray-500">
          ボタンを<strong>押している間だけ</strong>動きます（離すと停止・通信断でも自動停止）。
          キーボード: W/S=前後 A/D=横 Q/E=回転 Space=停止
        </p>

        {/* 十字キー */}
        <div className="mt-4 grid grid-cols-3 gap-2">
          <div />
          {holdButton(1, '↑ 前進')}
          <div />
          {holdButton(3, '← 左横')}
          <button
            type="button"
            onClick={stopMotion}
            className="select-none rounded-lg bg-gray-700 py-4 font-bold text-white active:bg-gray-900"
          >
            ■ 停止
          </button>
          {holdButton(4, '右横 →')}
          <div />
          {holdButton(2, '↓ 後退')}
          <div />
          {holdButton(5, '⟲ 左回転')}
          <div />
          {holdButton(6, '右回転 ⟳')}
        </div>

        {/* 速度 */}
        <div className="mt-4">
          <label htmlFor="rpm" className="flex justify-between text-sm text-gray-700">
            <span>速度</span>
            <span className="font-bold">{rpm} rpm（約 {(rpm * 0.00419).toFixed(2)} m/s）</span>
          </label>
          <input
            id="rpm"
            type="range"
            min={RPM_MIN}
            max={RPM_MAX}
            step={1}
            value={rpm}
            disabled={!enabled}
            onChange={(e) => {
              const v = Number(e.target.value)
              setRpm(v)
              rpmRef.current = v
              // 動作中なら新しい速度をすぐ反映（停止中は次の操作から効く）
              if (motionRef.current !== STOP) {
                sendManual({ motion: motionRef.current, rpm: v })
              }
            }}
            className="mt-1 w-full"
          />
          <p className="mt-1 text-xs text-gray-400">
            上限 {RPM_MAX} rpm = 0.25 m/s（会場運用の速度上限）／ {RPM_MIN} rpm 未満はモーターが反応しない領域
          </p>
        </div>

        {/* いまの指令 */}
        <p className="mt-3 text-center text-sm text-gray-600">
          現在の指令:{' '}
          <span className="font-bold text-gray-900">
            {MOTION_LABEL[motion]}
            {motion !== STOP && ` ${rpm} rpm`}
          </span>
        </p>
      </section>

      <p className="mt-4 text-xs text-gray-400">
        ロボ側でこの操作を受け取るには、Pi（またはPC）で{' '}
        <code className="rounded bg-gray-100 px-1">python robot/manual_control.py</code>{' '}
        を起動してください（実機なしなら受信ログだけが出ます）。
      </p>
    </main>
  )
}
