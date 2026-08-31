// ゲスト名の保存・読み出しを1か所にまとめるモジュール
// 保存先は localStorage（ブラウザに残るので、リロードしても消えない）
// ※ localStorage はブラウザ専用のAPIなので、クライアントコンポーネントから使うこと

const STORAGE_KEY = 'guest_name'

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
