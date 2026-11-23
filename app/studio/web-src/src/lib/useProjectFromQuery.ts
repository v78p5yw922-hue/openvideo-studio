/** 支持 ?project=<id> 直接打开某个项目（截图与分享链接用）。 */

import { useEffect } from 'react'
import { useSearchParams } from 'react-router-dom'
import { openProject, useStore } from '../lib/store'

export function useProjectFromQuery() {
  const [sp] = useSearchParams()
  const { pid } = useStore()
  const want = sp.get('project') || ''

  useEffect(() => {
    if (!want || want === pid) return
    void openProject(want).catch(() => undefined)
  }, [want, pid])
}
