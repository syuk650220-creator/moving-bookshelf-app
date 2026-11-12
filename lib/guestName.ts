// ゲスト名の保存・読み出しを1か所にまとめるモジュール
// 保存先は localStorage（ブラウザに残るので、リロードしても消えない）
// ※ localStorage はブラウザ専用のAPIなので、クライアントコンポーネントから使うこと

const STORAGE_KEY = 'guest_name'

// 「ゲスト」で入った人の名前。端末ごとに「ゲスト-7K2Q」のように違う名前にする。
// 全員が同じ「ゲスト」だと、ロボの呼出（1 人 1 件）で、別の端末の呼出が自分のものに見えてしまう
// （自分は呼べない・ほかの人の「本を取得した」が自分のボタンに出る）
export const GUEST_LABEL = 'ゲスト'

// ゲストの名前か（「ゲスト」だけ＝前の作りで保存した名前、も含む）
export function isGuestName(name: string | null | undefined): boolean {
  return !!name && (name === GUEST_LABEL || name.startsWith(`${GUEST_LABEL}-`))
}

// 端末ごとのゲストの名前を作る（例: ゲスト-7K2Q）
export function makeGuestName(): string {
  const chars = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'   // 見間違えやすい 0/O・1/I は使わない
  let code = ''
  try {
    const buf = new Uint32Array(4)
    crypto.getRandomValues(buf)
    for (const v of buf) code += chars[v % chars.length]
  } catch {
    // crypto が使えない環境では Math.random で代える（名前を分けるだけなので十分）
    code = ''
    for (let i = 0; i < 4; i++) code += chars[Math.floor(Math.random() * chars.length)]
  }
  return `${GUEST_LABEL}-${code}`
}

// 保存されているゲスト名を返す（未設定・取得失敗時は null）
export function getGuestName(): string | null {
  try {
    const name = localStorage.getItem(STORAGE_KEY)
    return name && name.trim() !== '' ? name : null
  } catch {
    // プライベートモードなどで localStorage が使えない場合
    return null
  }
}

// ゲスト名を保存する
export function setGuestName(name: string): void {
  try {
    localStorage.setItem(STORAGE_KEY, name.trim())
  } catch {
    // 保存できなくても画面遷移は続行できるよう、エラーは握りつぶす
  }
}
