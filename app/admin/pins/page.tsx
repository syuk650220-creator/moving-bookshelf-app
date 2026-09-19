'use client'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { supabase } from '@/lib/supabaseClient'
import Link from 'next/link'

// =====================================================================
//  管理者画面 ── 席・本棚の場所のピン管理（段階3）
//
//  stop_points を地図上のピンとして見る・直す画面。
//    id=0  kind='home' … 本棚の場所（ロボの定位置。帰る先・自己位置推定の初期位置）
//    id≥1  kind='seat' … 席（アプリの「届け先の席」に出る）
//  座標は map フレーム（m・rad）。採り方は robot/pin_tool.py（TF か RViz のクリックから登録）。
//  この画面は「数値を確認して手で直す」「本棚の場所を追加する」ためのもの。
//  ロボの現在地（robot_status.pose_*）も同じ地図に重ねて出す。
//
//  書き込みには robot/sql/03_pins_home_pose.sql の insert/update ポリシーが必要。
// =====================================================================

type Pin = {
  id: number
  label: string
  x: number
  y: number
  theta: number
  kind?: 'seat' | 'home'
  updated_at?: string | null
}

type Draft = { label: string; x: string; y: string; theta: string }

type RobotStatus = {
  state: string
  detail?: string | null
  pose_x?: number | null
  pose_y?: number | null
  pose_theta?: number | null
  pose_at?: string | null
}

const STATE_LABEL: Record<string, string> = {
  idle: '待機中', localizing: '自己位置推定中', moving: '移動中', arrived: '到着', returning: '帰還中',
}
const STATE_COLOR: Record<string, string> = {
  idle: '#16a34a', localizing: '#7c3aed', moving: '#2563eb', arrived: '#16a34a', returning: '#ea580c',
}

const POSE_STALE_SEC = 15   // これより古い現在地は「古い」として薄く出す

const isHome = (p: Pin) => p.kind === 'home' || p.id === 0
const deg = (rad: number) => (rad * 180) / Math.PI
const fmt = (v: number, d = 3) => (Number.isFinite(v) ? v.toFixed(d) : '—')

function toDraft(p: Pin): Draft {
  return { label: p.label, x: String(p.x), y: String(p.y), theta: String(p.theta) }
}

// 「90d」のように末尾 d を付けると度として受け付ける
function parseTheta(s: string): number {
  const t = s.trim().toLowerCase()
  if (t.endsWith('d')) return (parseFloat(t.slice(0, -1)) * Math.PI) / 180
  return parseFloat(t)
}

function sqlForPins(pins: Pin[]): string {
  return pins
    .map((p) => {
      const label = p.label.replace(/'/g, "''")
      const kind = isHome(p) ? 'home' : 'seat'
      return (
        `insert into stop_points (id, label, x, y, theta, kind) values (${p.id}, '${label}', ${p.x}, ${p.y}, ${p.theta}, '${kind}')\n` +
        `  on conflict (id) do update set label = excluded.label, x = excluded.x, y = excluded.y, theta = excluded.theta, kind = excluded.kind;`
      )
    })
    .join('\n')
}

export default function AdminPinsPage() {
  const [pins, setPins] = useState<Pin[]>([])
  const [drafts, setDrafts] = useState<Record<number, Draft>>({})
  const [schemaOk, setSchemaOk] = useState<boolean | null>(null)   // sql/03（kind 列）が入っているか
  const [status, setStatus] = useState<RobotStatus | null>(null)
  const [busyId, setBusyId] = useState<number | null>(null)
  const [message, setMessage] = useState<{ kind: 'ok' | 'err'; text: string } | null>(null)
  const [copied, setCopied] = useState(false)
  const [now, setNow] = useState<number>(0)   // 現在地の古さの判定用（ポーリングのたびに更新）

  // ---------------- 読み込み ----------------

  const loadPins = useCallback(async () => {
    const { data, error } = await supabase.from('stop_points').select('*').order('id')
    if (error) {
      setMessage({ kind: 'err', text: `ピンの取得に失敗しました: ${error.message}` })
      return
    }
    const rows = (data ?? []) as Pin[]
    setPins(rows)
    setDrafts(Object.fromEntries(rows.map((p) => [p.id, toDraft(p)])))
    if (rows.length > 0) {
      setSchemaOk('kind' in rows[0])
    } else {
      const probe = await supabase.from('stop_points').select('kind').limit(1)
      setSchemaOk(!probe.error)
    }
  }, [])

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- マウント時の初回フェッチ（setState は await 後にのみ実行される）
    loadPins()
  }, [loadPins])

  // ロボの現在地・状態（1.5 秒ごと）
  useEffect(() => {
    let alive = true
    const fetchStatus = async () => {
      const { data } = await supabase.from('robot_status').select('*').eq('id', 1).single()
      if (!alive) return
      if (data) setStatus(data as RobotStatus)
      setNow(Date.now())
    }
    fetchStatus()
    const id = setInterval(fetchStatus, 1500)
    return () => {
      alive = false
      clearInterval(id)
    }
  }, [])

  // ---------------- 書き込み ----------------

  const explainError = (msg: string) =>
    /policy|permission|42501/i.test(msg)
      ? 'stop_points に書き込めません。robot/sql/03_pins_home_pose.sql を Supabase の SQL Editor で実行してください（insert/update ポリシーが必要です）。'
      : msg

  const savePin = async (p: Pin) => {
    const d = drafts[p.id]
    if (!d) return
    const x = parseFloat(d.x)
    const y = parseFloat(d.y)
    const theta = parseTheta(d.theta)
    if (!d.label.trim() || !Number.isFinite(x) || !Number.isFinite(y) || !Number.isFinite(theta)) {
      setMessage({ kind: 'err', text: `id=${p.id}: ラベル・x・y・theta を正しく入力してください` })
      return
    }
    setBusyId(p.id)
    setMessage(null)
    const { error } = await supabase
      .from('stop_points')
      .update({ label: d.label.trim(), x, y, theta })
      .eq('id', p.id)
    setBusyId(null)
    if (error) {
      setMessage({ kind: 'err', text: explainError(error.message) })
      return
    }
    setMessage({ kind: 'ok', text: `${d.label.trim()}（id=${p.id}）を保存しました。ロボ側は次の呼出から新しい座標を使います。` })
    await loadPins()
  }

  const addHome = async () => {
    setBusyId(0)
    setMessage(null)
    const row: Record<string, unknown> = { id: 0, label: '本棚の場所', x: 0, y: 0, theta: 0 }
    if (schemaOk) row.kind = 'home'
    const { error } = await supabase.from('stop_points').insert(row)
    setBusyId(null)
    if (error) {
      setMessage({ kind: 'err', text: explainError(error.message) })
      return
    }
    setMessage({ kind: 'ok', text: '本棚の場所（id=0）を追加しました。座標はロボを置いて pin_tool.py で取り込むか、ここで入力してください。' })
    await loadPins()
  }

  const addSeat = async () => {
    const nextId = Math.max(0, ...pins.map((p) => p.id)) + 1
    setBusyId(nextId)
    setMessage(null)
    const row: Record<string, unknown> = { id: nextId, label: `席${nextId}`, x: 0, y: 0, theta: 0 }
    if (schemaOk) row.kind = 'seat'
    const { error } = await supabase.from('stop_points').insert(row)
    setBusyId(null)
    if (error) {
      setMessage({ kind: 'err', text: explainError(error.message) })
      return
    }
    setMessage({ kind: 'ok', text: `席${nextId}（id=${nextId}）を追加しました。` })
    await loadPins()
  }

  const copySql = async () => {
    try {
      await navigator.clipboard.writeText(sqlForPins(pins))
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      /* クリップボードが使えない環境では textarea から手でコピー */
    }
  }

  // ---------------- 地図（SVG） ----------------

  const robotPose = useMemo(() => {
    if (!status || status.pose_x == null || status.pose_y == null) return null
    const ageSec = status.pose_at && now > 0 ? (now - new Date(status.pose_at).getTime()) / 1000 : null
    return {
      x: status.pose_x,
      y: status.pose_y,
      theta: status.pose_theta ?? 0,
      ageSec,
      stale: ageSec != null && ageSec > POSE_STALE_SEC,
    }
  }, [status, now])

  const map = useMemo(() => {
    const W = 640
    const pts = pins.map((p) => ({ x: p.x, y: p.y }))
    if (robotPose) pts.push({ x: robotPose.x, y: robotPose.y })
    if (pts.length === 0) pts.push({ x: 0, y: 0 })
    let minX = Math.min(...pts.map((p) => p.x))
    let maxX = Math.max(...pts.map((p) => p.x))
    let minY = Math.min(...pts.map((p) => p.y))
    let maxY = Math.max(...pts.map((p) => p.y))
    const PAD = 0.7
    minX -= PAD; maxX += PAD; minY -= PAD; maxY += PAD
    // 小さすぎる範囲は最低 3 m 四方にする
    const spanX = Math.max(maxX - minX, 3)
    const spanY = Math.max(maxY - minY, 3)
    const cx = (minX + maxX) / 2
    const cy = (minY + maxY) / 2
    minX = cx - spanX / 2; maxX = cx + spanX / 2
    minY = cy - spanY / 2; maxY = cy + spanY / 2
    const s = W / spanX                   // px / m
    const H = Math.round(spanY * s)
    const grid = spanX > 8 ? 2 : 1        // 目盛りの間隔 [m]
    const toPx = (x: number, y: number) => ({ px: (x - minX) * s, py: H - (y - minY) * s })
    const gx: number[] = []
    for (let v = Math.ceil(minX / grid) * grid; v <= maxX; v += grid) gx.push(v)
    const gy: number[] = []
    for (let v = Math.ceil(minY / grid) * grid; v <= maxY; v += grid) gy.push(v)
    return { W, H, s, toPx, gx, gy, grid }
  }, [pins, robotPose])

  const arrowLen = 26

  const hasHome = pins.some(isHome)

  return (
    <main className="p-6 max-w-3xl mx-auto">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">席・本棚のピン管理</h1>
        <Link href="/admin" className="text-sm text-blue-600 underline">
          ← 管理者画面へ
        </Link>
      </div>
      <p className="mt-1 text-sm text-gray-600">
        ロボが向かう先の座標（<code className="rounded bg-gray-100 px-1">stop_points</code>）を地図の上で確認し、手で直せます。
        座標は地図（map フレーム）の m と rad。<b>本棚の場所（id=0）</b>はロボの定位置で、受取後に帰る先・呼出時の自己位置推定の初期位置になります。
      </p>

      {schemaOk === false && (
        <div className="mt-3 rounded-md border border-amber-300 bg-amber-50 p-3 text-sm text-amber-800">
          <b>sql/03 が未適用です。</b> <code className="rounded bg-white px-1">robot/sql/03_pins_home_pose.sql</code> を
          Supabase の SQL Editor で実行すると、本棚の場所（kind=&apos;home&apos;）の区別・この画面からの書き込み・ロボの現在地表示が有効になります。
        </div>
      )}

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

      {/* ---------------- 地図 ---------------- */}
      <section className="mt-4 rounded-xl border border-gray-200 bg-white p-4 shadow-sm">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="font-bold text-gray-900">地図（map フレーム・1 マス = {map.grid} m）</h2>
          <div className="flex flex-wrap gap-3 text-xs text-gray-600">
            <span><span className="inline-block h-3 w-3 rounded-full bg-amber-500 align-middle" /> 本棚の場所</span>
            <span><span className="inline-block h-3 w-3 rounded-full bg-blue-600 align-middle" /> 席</span>
            <span><span className="inline-block h-3 w-3 rotate-45 bg-green-600 align-middle" /> ロボ（矢印は向き）</span>
          </div>
        </div>
        <p className="mt-1 text-xs text-gray-500">
          x は右、y は上。矢印はその場所でロボが向く向き（theta）。
          {status && (
            <>
              {' '}ロボ:{' '}
              <span className="font-bold" style={{ color: STATE_COLOR[status.state] ?? '#374151' }}>
                {STATE_LABEL[status.state] ?? status.state}
              </span>
              {status.detail && <span className="text-gray-500">（{status.detail}）</span>}
            </>
          )}
        </p>
        <div className="mt-2 overflow-x-auto">
          <svg
            viewBox={`0 0 ${map.W} ${map.H}`}
            className="w-full rounded-lg border border-gray-200 bg-gray-50"
            style={{ maxHeight: 480 }}
            role="img"
            aria-label="席と本棚の場所のピン、ロボの現在地"
          >
            {/* 目盛り */}
            {map.gx.map((v) => {
              const { px } = map.toPx(v, 0)
              return (
                <g key={`gx${v}`}>
                  <line x1={px} y1={0} x2={px} y2={map.H} stroke={v === 0 ? '#9ca3af' : '#e5e7eb'} strokeWidth={v === 0 ? 1.5 : 1} />
                  <text x={px + 3} y={map.H - 4} fontSize={10} fill="#9ca3af">{v}</text>
                </g>
              )
            })}
            {map.gy.map((v) => {
              const { py } = map.toPx(0, v)
              return (
                <g key={`gy${v}`}>
                  <line x1={0} y1={py} x2={map.W} y2={py} stroke={v === 0 ? '#9ca3af' : '#e5e7eb'} strokeWidth={v === 0 ? 1.5 : 1} />
                  <text x={4} y={py - 3} fontSize={10} fill="#9ca3af">{v}</text>
                </g>
              )
            })}

            {/* ピン */}
            {pins.map((p) => {
              const { px, py } = map.toPx(p.x, p.y)
              const color = isHome(p) ? '#f59e0b' : '#2563eb'
              const ax = px + arrowLen * Math.cos(p.theta)
              const ay = py - arrowLen * Math.sin(p.theta)
              return (
                <g key={p.id}>
                  <line x1={px} y1={py} x2={ax} y2={ay} stroke={color} strokeWidth={3} strokeLinecap="round" />
                  <circle cx={ax} cy={ay} r={3.5} fill={color} />
                  <circle cx={px} cy={py} r={10} fill={color} stroke="#fff" strokeWidth={2} />
                  <text x={px} y={py + 4} fontSize={11} fontWeight={700} fill="#fff" textAnchor="middle">{p.id}</text>
                  <text x={px + 14} y={py - 12} fontSize={12} fontWeight={700} fill="#111827">{p.label}</text>
                </g>
              )
            })}

            {/* ロボ */}
            {robotPose && (() => {
              const { px, py } = map.toPx(robotPose.x, robotPose.y)
              const color = robotPose.stale ? '#9ca3af' : (STATE_COLOR[status?.state ?? ''] ?? '#374151')
              const rot = -deg(robotPose.theta)
              return (
                <g transform={`translate(${px} ${py}) rotate(${rot})`}>
                  <polygon points="16,0 -10,9 -6,0 -10,-9" fill={color} stroke="#fff" strokeWidth={1.5} opacity={robotPose.stale ? 0.6 : 1} />
                </g>
              )
            })()}
          </svg>
        </div>
        {robotPose ? (
          <p className="mt-2 text-xs text-gray-500">
            ロボの現在地: x={fmt(robotPose.x, 2)} y={fmt(robotPose.y, 2)} θ={fmt(deg(robotPose.theta), 0)}°
            {robotPose.ageSec != null && (
              <span className={robotPose.stale ? 'text-amber-700' : ''}>
                （{Math.round(robotPose.ageSec)} 秒前{robotPose.stale ? '・古い値です。ブリッジが --nav2 で動いているか確認' : ''}）
              </span>
            )}
          </p>
        ) : (
          <p className="mt-2 text-xs text-gray-400">
            ロボの現在地はまだありません（ブリッジが <code className="rounded bg-gray-100 px-1">--nav2</code> で走行すると 1 秒ごとに出ます。
            <code className="rounded bg-gray-100 px-1">--simulate</code> でも動いたふりの位置が出ます）。
          </p>
        )}
      </section>

      {/* ---------------- ピン一覧（編集） ---------------- */}
      <section className="mt-4 rounded-xl border border-gray-200 bg-white p-4 shadow-sm">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="font-bold text-gray-900">ピン一覧</h2>
          <div className="flex gap-2">
            {!hasHome && (
              <button
                type="button"
                onClick={addHome}
                disabled={busyId !== null}
                className="rounded-md bg-amber-500 px-3 py-1.5 text-sm font-bold text-white hover:bg-amber-600 disabled:bg-gray-300"
              >
                ＋ 本棚の場所（id=0）
              </button>
            )}
            <button
              type="button"
              onClick={addSeat}
              disabled={busyId !== null}
              className="rounded-md border border-gray-300 px-3 py-1.5 text-sm font-bold text-gray-700 hover:bg-gray-50 disabled:bg-gray-100"
            >
              ＋ 席を追加
            </button>
          </div>
        </div>
        <p className="mt-1 text-xs text-gray-500">
          theta は rad。度で入れたいときは <code className="rounded bg-gray-100 px-1">90d</code> のように末尾に d を付けます。
          採取した値は <code className="rounded bg-gray-100 px-1">robot/pin_tool.py</code>（TF か RViz のクリック）からも登録できます。
        </p>

        {pins.length === 0 ? (
          <p className="mt-3 text-sm text-gray-400">ピンがありません</p>
        ) : (
          <ul className="mt-3 space-y-3">
            {pins.map((p) => {
              const d = drafts[p.id] ?? toDraft(p)
              const home = isHome(p)
              const thetaVal = parseTheta(d.theta)
              return (
                <li
                  key={p.id}
                  className={`rounded-lg border p-3 ${home ? 'border-amber-300 bg-amber-50/40' : 'border-gray-200'}`}
                >
                  <div className="flex items-center justify-between gap-2">
                    <div className="flex items-center gap-2">
                      <span
                        className={`inline-grid h-7 w-7 place-items-center rounded-full text-xs font-bold text-white ${
                          home ? 'bg-amber-500' : 'bg-blue-600'
                        }`}
                      >
                        {p.id}
                      </span>
                      <span className="text-xs font-bold text-gray-500">{home ? '本棚の場所（ホーム）' : '席'}</span>
                    </div>
                    {p.updated_at && (
                      <span className="text-xs text-gray-400">
                        更新 {new Date(p.updated_at).toLocaleString('ja-JP', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' })}
                      </span>
                    )}
                  </div>

                  <div className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-4">
                    <label className="text-xs text-gray-600">
                      ラベル
                      <input
                        type="text"
                        value={d.label}
                        onChange={(e) => setDrafts({ ...drafts, [p.id]: { ...d, label: e.target.value } })}
                        className="mt-0.5 w-full rounded-md border border-gray-300 px-2 py-1.5 text-sm text-gray-900"
                      />
                    </label>
                    <label className="text-xs text-gray-600">
                      x [m]
                      <input
                        type="text"
                        inputMode="decimal"
                        value={d.x}
                        onChange={(e) => setDrafts({ ...drafts, [p.id]: { ...d, x: e.target.value } })}
                        className="mt-0.5 w-full rounded-md border border-gray-300 px-2 py-1.5 text-sm text-gray-900 font-mono"
                      />
                    </label>
                    <label className="text-xs text-gray-600">
                      y [m]
                      <input
                        type="text"
                        inputMode="decimal"
                        value={d.y}
                        onChange={(e) => setDrafts({ ...drafts, [p.id]: { ...d, y: e.target.value } })}
                        className="mt-0.5 w-full rounded-md border border-gray-300 px-2 py-1.5 text-sm text-gray-900 font-mono"
                      />
                    </label>
                    <label className="text-xs text-gray-600">
                      theta [rad]{Number.isFinite(thetaVal) && <span className="text-gray-400">（{fmt(deg(thetaVal), 0)}°）</span>}
                      <input
                        type="text"
                        inputMode="decimal"
                        value={d.theta}
                        onChange={(e) => setDrafts({ ...drafts, [p.id]: { ...d, theta: e.target.value } })}
                        className="mt-0.5 w-full rounded-md border border-gray-300 px-2 py-1.5 text-sm text-gray-900 font-mono"
                      />
                    </label>
                  </div>

                  <div className="mt-2 flex items-center justify-between gap-2">
                    <span className="text-xs text-gray-400">
                      DB の値: x={fmt(p.x)} y={fmt(p.y)} theta={fmt(p.theta)}
                    </span>
                    <button
                      type="button"
                      onClick={() => savePin(p)}
                      disabled={busyId !== null}
                      className="rounded-md bg-blue-600 px-3 py-1.5 text-sm font-bold text-white hover:bg-blue-700 disabled:bg-gray-300"
                    >
                      {busyId === p.id ? '保存中…' : '保存'}
                    </button>
                  </div>
                </li>
              )
            })}
          </ul>
        )}
      </section>

      {/* ---------------- SQL（SQL Editor 派のために） ---------------- */}
      <section className="mt-4 rounded-xl border border-gray-200 bg-white p-4 shadow-sm">
        <div className="flex items-center justify-between">
          <h2 className="font-bold text-gray-900">いまの値を SQL にする</h2>
          <button
            type="button"
            onClick={copySql}
            className={`rounded-md border px-3 py-1 text-xs font-bold ${
              copied ? 'border-green-500 bg-green-50 text-green-700' : 'border-gray-300 text-gray-700 hover:bg-gray-50'
            }`}
          >
            {copied ? 'コピー済み' : 'コピー'}
          </button>
        </div>
        <p className="mt-1 text-xs text-gray-500">
          別の Supabase プロジェクトへ写すときや、RLS で書けないときは、これを SQL Editor に貼ります。
        </p>
        <textarea
          readOnly
          value={sqlForPins(pins)}
          rows={Math.min(12, Math.max(3, pins.length * 2))}
          className="mt-2 w-full rounded-md border border-gray-200 bg-gray-50 p-2 font-mono text-xs text-gray-800"
        />
      </section>

      <p className="mt-4 text-xs text-gray-400">
        ピンの採り方・当日の流れは OneDrive の「⑧ アプリ連携 段階3」と <code className="rounded bg-gray-100 px-1">robot/README.md</code> §5-4 を参照。
      </p>
    </main>
  )
}
