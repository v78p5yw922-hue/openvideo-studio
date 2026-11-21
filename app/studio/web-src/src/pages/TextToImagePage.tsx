/** /create/text-to-image —— 文生图 */

import { AppHeader } from '../components/AppHeader'
import { AssetStudio } from '../components/AssetStudio'
import { t } from '../lib/i18n'

export function TextToImagePage() {
  return (
    <div className="page">
      <AppHeader title={t('nav.t2i')} />
      <div className="page-body flush">
        <AssetStudio />
      </div>
    </div>
  )
}
