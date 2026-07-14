'use client'
import { useRouter, useSearchParams } from 'next/navigation'

export default function SearchBox() {
  const router = useRouter()
  const searchParams = useSearchParams()

  return (
    <input
      type="text"
      placeholder="タイトル・著者で検索"
      defaultValue={searchParams.get('q') ?? ''}
      className="border p-2 rounded w-full max-w-sm"
      onChange={(e) => {
        const value = e.target.value
        const params = new URLSearchParams(searchParams.toString())
        if (value) {
          params.set('q', value)
        } else {
          params.delete('q')
        }
        router.push(`/?${params.toString()}`)
      }}
    />
  )
}