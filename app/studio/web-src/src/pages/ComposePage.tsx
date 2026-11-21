/** /create/video/compose —— 合成成片 + BGM + 导出 */

import { AppHeader } from '../components/AppHeader'
import { ExportPanel } from '../components/ExportPanel'
import { useAppModals } from '../lib/app-modals'
import { t } from '../lib/i18n'

export function ComposePage() {
  const modals = useAppModals()
  return (
    <div className="page">
      <AppHeader
        title={t('nav.compose')}
      />
      <div className="page-body">
        <ExportPanel onPlayOutput={modals.playUrl} />
      </div>
    </div>
  )
}
