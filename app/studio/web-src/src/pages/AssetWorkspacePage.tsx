/** /assets —— 资产管理（角色和场景，按项目/分组筛选） */

import { AssetWorkspace } from '../components/AssetWorkspace'
import { useAppModals } from '../lib/app-modals'

export function AssetWorkspacePage() {
  const modals = useAppModals()
  return (
    <div className="page">
      <div className="page-body">
        <AssetWorkspace
          onOpenAsset={(kind, id, projectId) => modals.openAsset(kind, id, projectId)}
        />
      </div>
    </div>
  )
}
