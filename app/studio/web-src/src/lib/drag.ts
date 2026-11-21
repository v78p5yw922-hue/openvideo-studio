/**
 * 拖拽中介：镜头卡 draggable，左栏角色/场景是 drop 目标。
 * 不用 dataTransfer 读业务数据（部分浏览器限制），只用一个模块级变量。
 */

let draggingShot: string | null = null

export function setDragShot(sid: string | null) {
  draggingShot = sid
}

export function getDragShot(): string | null {
  return draggingShot
}
