/** /create/video/rendering —— 分镜渲染（主战场） */

import { AppHeader } from '../components/AppHeader'
import { Inspector } from '../components/Inspector'
import { LeftPanel } from '../components/LeftPanel'
import { ShotGrid } from '../components/ShotGrid'
import { useAppModals } from '../lib/app-modals'
import { t } from '../lib/i18n'
import { useStore } from '../lib/store'

export function RenderPage() {
  const modals = useAppModals()
  const { detail, pid, checked } = useStore()
  const selectedIds = checked.size ? [...checked] : []

  // ShotGrid 只传 sid → 从 store 里解出 take.version 再给 playShot
  const handlePlay = (sid: string) => {
    const shot = detail?.shots.find((s) => s.shot_id === sid)
    if (!shot?.take || !pid) return
    modals.playShot(pid, sid, shot.take.version)
  }

  return (
    <div className="page">
      <AppHeader
        title={t('nav.video')}
        actions={(
          <>
            <button className="ghost" onClick={modals.openImport} title={t('header.import-tip')}>
              {t('header.import-script')}
            </button>
            <button
              className="primary"
              disabled={!detail?.shots.length}
              onClick={() => modals.openPlan(selectedIds)}
              title={t('header.generate-tip')}
            >
              {t('header.generate-all')}
            </button>
          </>
        )}
      />
      <div className="page-body flush">
        <div className="pane workspace">
          <nav className="left">
            <LeftPanel
              onOpenAsset={modals.openAsset}
              onImport={modals.openImport}
            />
          </nav>
          <ShotGrid
            onPlan={(ids) => modals.openPlan(ids)}
            onPlay={handlePlay}
            onImport={modals.openImport}
          />
          <aside className="right">
            <Inspector
              onOpenPrompt={modals.openPrompt}
              onOpenTakes={modals.openTakes}
              onPlanAll={() => modals.openPlan([])}
            />
          </aside>
        </div>
      </div>
    </div>
  )
}
