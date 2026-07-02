'use client'
import { use } from 'react'

export default function BookDetailPage({
  params,
}: {
  params: Promise<{ id: string }>
}) {
  const { id } = use(params)

  return <div>本のid: {id}</div>
}